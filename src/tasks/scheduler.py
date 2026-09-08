import datetime
import json
import os
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler

import src.modules.market_data_service.data_manager as dm
import src.modules.forecast_service.ml_pipeline as mt
from src.modules.forecast_service.forecast_persistence import persist_forecast_run
import src.modules.optimization_service.milp_model as opt
from src.modules.reporting_service.forecast_accuracy import (
    sync_market_prices_to_db, sync_today_market_prices_from_oree, sync_today_idm_prices_from_oree,
)
import src.modules.external_data_service.telegram_bot as telegram_bot
from src.modules.scada_service.soc_state import get_current_soc_fraction
from src.modules.bidding_service.services import (
    generate_bids_for_date, submit_bids_for_date, settle_bids_for_date, submit_idm_fallback_bids_for_date,
    reconcile_idm_fallback_for_date,
)
from src.database.session import SessionLocal
from src.database.models import Asset, PriceForecast, ChargeDischargePlan, MarketPrice
from src.core.config import settings
from src.core.time_utils import kyiv_to_utc, utc_to_kyiv, kyiv_day_bounds


def _auto_dispatch_enabled() -> bool:
    """Читає прапорець з system_settings.json (SystemSettingsModel-патерн,
    той самий, що telegram_bot.py::_bid_reminder_enabled) — за замовчуванням
    False, власник батареї свідомо вмикає автоматичну подачу заявок
    ("віртуальний диспетчер", CLAUDE.md п.41)."""
    path = os.path.join(settings.DATA_DIR, "system_settings.json")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return bool(json.load(f).get("auto_dispatch_enabled", False))
        except Exception:
            pass
    return False

def run_daily_forecast_and_optimization():
    print(f"[{datetime.datetime.now()}] Background Scheduler: Starting daily forecast and optimization job...")
    db = SessionLocal()
    try:
        # Determine target date: tomorrow — за РЕАЛЬНОЮ київською датою
        # (CLAUDE.md хронологія п.26/27), а не `date.today()` контейнера
        # (UTC) — інакше біля півночі Kyiv "завтра" вважалось би на добу
        # пізніше/раніше реальної торгової дати.
        today_kyiv = utc_to_kyiv(datetime.datetime.utcnow()).date()
        tomorrow = today_kyiv + datetime.timedelta(days=1)
        tomorrow_str = tomorrow.strftime('%Y-%m-%d')
        print(f"Target date for optimization: {tomorrow_str}")
        
        # 1. Sync real-time data (ціни РДН/ВДР, погода, реальний Gas_Price/Telegram-сигнал)
        dm.sync_realtime_data(force=True)

        # 1b. Записати вчорашню/сьогоднішню реальну ціну в MarketPrice —
        # це і є "факт", з яким завтра звірятиметься сьогоднішній прогноз.
        n_new_prices = sync_market_prices_to_db(db)
        if n_new_prices:
            print(f"Synced {n_new_prices} new MarketPrice rows.")

        # 1c. Реальний push-алерт диспетчеру в Telegram, якщо сьогодні
        # виявлено grid-stress сигнал (аварії/обстріли з постів Укренерго) —
        # best-effort, не валить основний job при збої сповіщення.
        try:
            alert_result = telegram_bot.check_and_send_grid_stress_alert()
            if alert_result.get("sent"):
                print(f"Telegram alert sent: severity={alert_result.get('severity')}")
        except Exception as alert_err:
            print(f"Warning: grid-stress Telegram alert failed: {alert_err}")

        # 2. Load weather forecast
        weather_forecast = dm.fetch_weather_forecast()
        
        # 3. Get last 168 hours of prices for lags
        df_hist = pd.read_csv(dm.MERGED_DATA_PATH)
        df_hist['Datetime'] = pd.to_datetime(df_hist['Datetime'])
        # Справжня UTC-мить київської півночі цільової дати (CLAUDE.md
        # п.26/27) — заміна наївного `pd.to_datetime(tomorrow_str)`, який
        # раніше давав UTC-північ (тобто фактично вже ~3г КИЇВСЬКОГО ранку
        # цільової доби) замість справжньої київської півночі.
        target_dt_start = kyiv_to_utc(tomorrow_str, 0)
        hist_before_target = df_hist[df_hist['Datetime'] < target_dt_start].sort_values('Datetime')
        
        if len(hist_before_target) >= 168:
            last_prices = hist_before_target['Price'].iloc[-168:].tolist()
        else:
            last_prices = df_hist['Price'].iloc[:168].tolist()
            
        # 4. Run prediction (using LightGBM). Раніше тут стояли хардкод-константи
        # (Gas_Price=35.0, Nuclear_Outage=0.15 і т.д.) — модель щодня "прогнозувала",
        # ніби на ринку нічого не змінюється. Реальні ринкові фактори тепер вже
        # всередині historical_data_merged.csv (IDM-спред, Solar_Gen/Wind_Gen з
        # реальної погоди) і враховуються моделлю напряму, без ручних факторів.
        prediction_results = mt.predict_next_day(tomorrow_str, weather_forecast, last_prices)
        predicted_prices = prediction_results['lightgbm']

        # P10/P90 conformal-калібрований інтервал невизначеності (Фаза 3)
        try:
            price_band = mt.predict_price_band(tomorrow_str, weather_forecast, last_prices)
        except Exception as band_err:
            print(f"Warning: could not compute price band in scheduler: {band_err}")
            price_band = None

        # 5. Run battery optimization
        asset = db.query(Asset).first()
        if not asset:
            print("Warning: No BESS asset found in database. Skipping optimization.")
            return
            
        battery_params = {
            'battery_capacity': asset.capacity_mwh * 1000.0, # convert MW to kW
            'max_charge_power': asset.power_mw * 1000.0,
            'max_discharge_power': asset.power_mw * 1000.0,
            'charge_efficiency': asset.efficiency_charge,
            'discharge_efficiency': asset.efficiency_discharge,
            # Реальний поточний SoC з SCADA-телеметрії, а не захардкоджені
            # 20% щодня — інакше план щодня "забуває", де реально закінчила
            # попередня доба (soc_state.get_current_soc_fraction).
            'initial_soc': get_current_soc_fraction(db, asset, target_date=tomorrow_str),
            'min_soc': asset.min_soc_pct / 100.0,
            'max_soc': asset.max_soc_pct / 100.0,
            # Настроюється в Settings (Asset.max_cycles_per_day) — MILP сам
            # використає другий цикл лише якщо денний спред цін це реально
            # окуповує (обмеження лише СТЕЛЯ можливостей, не примус).
            'max_cycles_per_day': asset.max_cycles_per_day,
            'degradation_cost': asset.deg_cost_per_mwh / 1000.0,
            'transmission_tariff': 528.57,
            'distribution_tariff': 1500.0,
            'dispatch_tariff': 104.57,
            'supplier_margin': 100.0,
            'mode': 'arbitrage'
        }
        
        optimization_results = opt.optimize_battery_schedule(predicted_prices, **battery_params)
        
        # 6. Persist results in DB
        for t in range(24):
            # Справжня UTC-мить КИЇВСЬКОЇ години t (не наївна арифметика
            # від UTC-півночі — CLAUDE.md п.26/27, DST-aware).
            forecast_time = kyiv_to_utc(tomorrow_str, t)
            
            # Save forecast
            db.query(PriceForecast).filter(
                PriceForecast.timestamp == forecast_time,
                PriceForecast.forecast_run_at == target_dt_start
            ).delete()
            
            pf = PriceForecast(
                timestamp=forecast_time,
                forecast_run_at=target_dt_start,
                model_version='lightgbm',
                predicted_price_uah=predicted_prices[t],
                lower_bound_uah=float(price_band['lower_uah'][t]) if price_band else None,
                upper_bound_uah=float(price_band['upper_uah'][t]) if price_band else None,
            )
            db.add(pf)

            # Save Plan
            db.query(ChargeDischargePlan).filter(
                ChargeDischargePlan.timestamp == forecast_time,
                ChargeDischargePlan.asset_id == asset.id,
                ChargeDischargePlan.optimized_run_at == target_dt_start
            ).delete()
            
            sched_item = optimization_results['schedule'][t]
            target_power = sched_item['power_kw']
            soc_kwh = sched_item['soc_kwh']
            
            plan_entry = ChargeDischargePlan(
                timestamp=forecast_time,
                asset_id=asset.id,
                optimized_run_at=target_dt_start,
                target_power_mw=target_power / 1000.0,
                expected_soc_mwh=soc_kwh / 1000.0,
                expected_profit_uah=sched_item['hourly_p_l_uah']
            )
            db.add(plan_entry)

        # Неізмінна історія (ForecastRun/ForecastRunHour) — окремо від
        # PriceForecast вище, яка й далі перезаписується щоразу.
        persist_forecast_run(db, target_dt_start, 'lightgbm', predicted_prices, price_band, trigger='scheduler_06:00')

        db.commit()
        print(f"[{datetime.datetime.now()}] Background Scheduler: Successfully completed daily forecast and BESS optimization plan.")

        # 7. Генерація чернетки заявок одразу після плану (та сама транзакція,
        # уникає ризику розсинхронізації розкладу) — ЗАВЖДИ, незалежно від
        # режиму диспетчера (та сама філософія, що вже описана в MEMORY.md §8:
        # "програма керує процесом, готує пропозицію" — дispatcher бачить її у
        # BidActionCenter/Telegram незалежно від режиму). ПОДАЧА — окрема
        # настроювана дія `auto_submit_bids` (див. DISPATCHER_ACTIONS нижче,
        # "настроюваний сценарій віртуального диспетчера", 2026-08-26) —
        # свідомо НЕ тут, щоб дати реальному диспетчеру вікно на ручну правку
        # суми до дедлайну заявки (~12:00), а не подавати миттєво о 06:00.
        try:
            bid_result = generate_bids_for_date(db, asset, target_dt_start)
            print(f"Bid generation: {bid_result.get('status')}, n_bids={bid_result.get('n_bids')}")
        except Exception as bid_err:
            db.rollback()
            print(f"Warning: automated bid generation failed: {bid_err}")
    except Exception as e:
        db.rollback()
        print(f"Error in background scheduler job: {e}")
    finally:
        db.close()

def run_nightly_model_retrain():
    """
    Щоденне автоматичне перенавчання точкової (LightGBM/XGBoost/MLP) та
    quantile (P10/P90) моделей — до 2026-08-04 модель перенавчалась лише вручну й
    локально (див. CLAUDE.md хронологія п.13/14), тому джоба з 06:00 могла
    місяцями рахувати прогноз на застарілих вагах. Живий тест
    в контейнері smartbess-platform на VPS 2026-08-04 показав пік пам'яті
    лише 597.9MiB/900MiB (66%, реальний запас ~300MB) і жодного впливу на
    відповідність живого API (перевірено паралельними запитами) — тому
    безпечно ганяти цей самий цикл щоночі прямо в проді, без окремого
    боксу. Не валить процес при будь-якій помилці (мережа, тимчасова
    недоступність джерела тощо) — старі `.pkl` лишаються робочими завдяки
    атомарному запису (`_atomic_pickle_dump`/`_atomic_json_dump` в
    ml_pipeline.py), просто повторна спроба наступної ночі.
    """
    print(f"[{datetime.datetime.now()}] Background Scheduler: Starting nightly model retrain job...")
    try:
        # 2026-09-08: return value раніше ігнорувався — якщо sync мовчки не
        # оновлював historical_data_merged.csv (мережева помилка oree.com.ua/
        # Open-Meteo, тепер видима в логах — data_manager.py), train_models()
        # усе одно тренувався на застарілому файлі, і джоба звітувала
        # "completed successfully". Знайдено: 8 ночей поспіль (30.08-07.09)
        # побайтово ідентичні метрики навчання — ознака саме цього. Тепер
        # хоча б голосно попереджаємо в логах, щоб таке більше не було
        # непомітним.
        synced = dm.sync_realtime_data(force=True)
        if not synced:
            print(f"[{datetime.datetime.now()}] Warning: sync_realtime_data(force=True) returned False — "
                  f"historical_data_merged.csv NOT updated, train_models() will train on the existing (possibly stale) file.")
        metrics = mt.train_models()
        print(f"Nightly retrain: train_models metrics: {metrics}")
        qmetrics = mt.train_quantile_models()
        print(f"Nightly retrain: train_quantile_models: {qmetrics}")
        print(f"[{datetime.datetime.now()}] Background Scheduler: Nightly model retrain completed successfully.")
    except Exception as e:
        print(f"Error in nightly model retrain job: {e}")

def run_intraday_price_sync():
    """
    Легкий інтрадей-досинк market_prices за СЬОГОДНІ (CLAUDE.md хронологія
    п.24-25) — на відміну від run_daily_forecast_and_optimization (важкий,
    раз на добу о 06:00, і рахує на ЗАВТРА), ця джоба лише перевіряє, чи
    oree.com.ua вже опублікував ще не засинхронізовані години СЬОГОДНІШНЬОЇ
    доби (напр. якщо публікація відбулась частинами), і одразу дописує їх —
    без важкої погоди/gas/Telegram синхронізації. Best-effort: помилка мережі
    не валить процес, наступна спроба через INTRADAY_PRICE_SYNC_MINUTES.

    2026-08-28 ("Загальний дохід" у звіті): та сама логіка тепер ще й для
    ВДР (sync_today_idm_prices_from_oree, окрема таблиця IdmPrice — ВДР не
    аукціон єдиної ціни, MEMORY.md §8) — і одразу після цього другий прохід
    звірки (reconcile_idm_fallback_for_date), який замінює ОЦІНКУ
    ВДР-фолбека (settle_bids_for_date, порахована заздалегідь) на РЕАЛЬНУ
    середньозважену ціну, щойно вона з'явилась. Реконсилюється і СЬОГОДНІ,
    і ВЧОРА — ВДР для останніх годин доби публікується з невеликим лагом,
    інколи вже після півночі.
    """
    db = SessionLocal()
    try:
        n = sync_today_market_prices_from_oree(db)
        if n:
            print(f"[{datetime.datetime.now()}] Intraday price sync: {n} new MarketPrice rows for today.")
    except Exception as e:
        print(f"Warning: intraday price sync failed: {e}")

    try:
        n_idm = sync_today_idm_prices_from_oree(db)
        if n_idm:
            print(f"[{datetime.datetime.now()}] Intraday IDM sync: {n_idm} new IdmPrice rows for today.")
    except Exception as e:
        print(f"Warning: intraday IDM sync failed: {e}")

    try:
        asset = db.query(Asset).first()
        if asset:
            today = datetime.datetime.utcnow()
            yesterday = today - datetime.timedelta(days=1)
            for d in (yesterday, today):
                result = reconcile_idm_fallback_for_date(db, asset, d)
                if result.get('n_reconciled'):
                    print(f"[{datetime.datetime.now()}] IDM fallback reconciled: {result}")
    except Exception as e:
        print(f"Warning: IDM fallback reconciliation failed: {e}")

    db.close()

def run_bid_reminder_check():
    """
    Читає ІСНУЮЧИЙ стан заявок (сьогодні/завтра) і шле Telegram-нагадування
    диспетчеру зі списком конкретних дій — сама нічого не генерує й не
    звіряє (генерація/подача/звірка автоматизовані окремо — див.
    DISPATCHER_ACTIONS/reschedule_virtual_dispatcher_jobs, "настроюваний
    сценарій віртуального диспетчера", 2026-08-26), тільки читає вже наявні
    MarketBid через build_daily_action_summary.
    """
    print(f"[{datetime.datetime.now()}] Background Scheduler: Checking bid reminder...")
    try:
        result = telegram_bot.check_and_send_bid_reminder()
        print(f"[{datetime.datetime.now()}] Bid reminder check result: {result}")
    except Exception as e:
        print(f"Warning: bid reminder check failed: {e}")

def run_drift_check():
    """
    Легкий drift-monitoring (CLAUDE.md п.31, MEMORY.md §6a) — раз на добу
    порівнює тижневий WAPE із власною 60-денною базою через
    forecast_accuracy.check_forecast_drift і шле Telegram-алерт, якщо
    погіршення суттєве. Дешева перевірка (лише SQL-агрегація по вже
    накопичених MarketPrice/PriceForecast, без перенавчання моделі).
    """
    print(f"[{datetime.datetime.now()}] Background Scheduler: Checking forecast drift...")
    try:
        result = telegram_bot.check_and_send_drift_alert()
        print(f"[{datetime.datetime.now()}] Drift check result: {result}")
    except Exception as e:
        print(f"Warning: drift check failed: {e}")

def run_auto_submit_bids():
    """
    Настроювана дія `auto_submit_bids` (дефолт 11:30, "настроюваний сценарій
    віртуального диспетчера", 2026-08-26) — якщо `auto_dispatch_enabled` і є
    заявки на завтра, ще не подані (`external_order_id IS NULL`) — подати.
    Дефолтний час (11:30, не одразу о 06:00) свідомо дає реальному
    диспетчеру вікно на ручну правку суми до дедлайну заявки (~12:00) —
    якщо диспетчер уже сам щось подав/змінив до цього моменту,
    `submit_bids_for_date` ідемпотентно пропустить вже подані заявки.
    """
    print(f"[{datetime.datetime.now()}] Background Scheduler: auto_submit_bids...")
    if not _auto_dispatch_enabled():
        print("auto_submit_bids: auto-dispatch disabled (Settings) — skipping, dispatcher submits manually.")
        return
    db = SessionLocal()
    try:
        asset = db.query(Asset).first()
        if not asset:
            print("Warning: No BESS asset found in database. Skipping.")
            return
        tomorrow_str = (utc_to_kyiv(datetime.datetime.utcnow()).date() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        target_dt = kyiv_to_utc(tomorrow_str, 0)
        result = submit_bids_for_date(db, asset, target_dt)
        print(f"auto_submit_bids for {tomorrow_str}: {result.get('status')}, n_submitted={result.get('n_submitted')}")
    except Exception as e:
        db.rollback()
        print(f"Error in auto_submit_bids job: {e}")
    finally:
        db.close()


def run_reconcile_bids():
    """
    Настроювана дія `reconcile_bids` (дефолт 13:00, "настроюваний сценарій
    віртуального диспетчера", 2026-08-26) — звіряє заявки на ЗАВТРА з
    реальною ціною закриття РДН, який щойно (о 12:00) закрив ворота на
    завтрашню добу. РДН — аукціон "на добу наперед": ціна закриття відома
    ОДРАЗУ після закриття воріт, а не після фізичного настання доби D —
    тому звіряти можна вже СЬОГОДНІ (раніша версія цієї джоби помилково
    звіряла ВЧОРАШНІ заявки НАСТУПНОГО дня о 05:30 — на той момент усі
    можливі вікна ВДР-фолбеку для тих годин уже фізично закрились).
    Виконується ЗАВЖДИ (інформаційно для диспетчера, незалежно від режиму),
    не гейтиться `auto_dispatch_enabled`. Той самий патерн побудови
    actual_prices_by_hour, що вже є в bids.py::settle_bids (спершу
    MarketPrice з БД, якщо не всі 24 години — dm.fetch_oree_prices_for_month()
    як фолбек). Якщо й тоді не 24/24 — ціна ще не опублікована повністю,
    чесно пропускаємо (не валимо процес).
    """
    print(f"[{datetime.datetime.now()}] Background Scheduler: reconcile_bids...")
    db = SessionLocal()
    try:
        asset = db.query(Asset).first()
        if not asset:
            print("Warning: No BESS asset found in database. Skipping reconciliation.")
            return

        tomorrow_str = (utc_to_kyiv(datetime.datetime.utcnow()).date() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        target_dt = kyiv_to_utc(tomorrow_str, 0)
        day_start, day_end = kyiv_day_bounds(tomorrow_str)

        rows = db.query(MarketPrice).filter(
            MarketPrice.timestamp >= day_start, MarketPrice.timestamp < day_end,
        ).order_by(MarketPrice.timestamp).all()
        actual_by_hour = {utc_to_kyiv(r.timestamp).hour: r.price_uah for r in rows}

        if len(actual_by_hour) != 24:
            df_month = dm.fetch_oree_prices_for_month(day_start.month, day_start.year)
            df_month_next = dm.fetch_oree_prices_for_month(day_end.month, day_end.year)
            if not df_month_next.empty:
                df_month = pd.concat([df_month, df_month_next]).drop_duplicates(subset=['Datetime']) if not df_month.empty else df_month_next
            if not df_month.empty:
                df_month['Datetime'] = pd.to_datetime(df_month['Datetime'])
                df_day = df_month[(df_month['Datetime'] >= day_start) & (df_month['Datetime'] < day_end)]
                actual_by_hour = {utc_to_kyiv(dt.to_pydatetime()).hour: price for dt, price in zip(df_day['Datetime'], df_day['Price'])}

        if len(actual_by_hour) != 24:
            print(f"reconcile_bids: real РДН closing price for {tomorrow_str} not fully published yet ({len(actual_by_hour)}/24h) — skipping, will retry at next scheduled run.")
            return

        result = settle_bids_for_date(db, asset, target_dt, actual_by_hour)
        print(f"[{datetime.datetime.now()}] reconcile_bids for {tomorrow_str}: {result.get('status')}, "
              f"n_settled={result.get('n_settled')}, total_realized_profit_uah={result.get('total_realized_profit_uah')}")
    except Exception as e:
        db.rollback()
        print(f"Error in reconcile_bids job: {e}")
    finally:
        db.close()


def run_auto_submit_idm_fallback():
    """
    Настроювана дія `auto_submit_idm_fallback` (дефолт 14:45, "настроюваний
    сценарій віртуального диспетчера", 2026-08-26) — якщо `auto_dispatch_enabled`
    і є заявки на завтра, для яких `reconcile_bids` вже позначив
    `idm_fallback_suggested=True`, а диспетчер ще НЕ підтвердив сам
    (`idm_fallback_acknowledged`, `POST /bids/idm-fallback/acknowledge`) і
    ще не подано (`idm_external_order_id IS NULL`) — подати емульовано на
    ВДР. Дефолтний час — до відкритого з 15:00 вікна ВДР (MEMORY.md §8),
    даючи диспетчеру ~1.5-2 год від reconcile_bids (13:00) на реакцію.
    """
    print(f"[{datetime.datetime.now()}] Background Scheduler: auto_submit_idm_fallback...")
    if not _auto_dispatch_enabled():
        print("auto_submit_idm_fallback: auto-dispatch disabled (Settings) — skipping, dispatcher handles ВДР manually.")
        return
    db = SessionLocal()
    try:
        asset = db.query(Asset).first()
        if not asset:
            print("Warning: No BESS asset found in database. Skipping.")
            return
        tomorrow_str = (utc_to_kyiv(datetime.datetime.utcnow()).date() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        target_dt = kyiv_to_utc(tomorrow_str, 0)
        result = submit_idm_fallback_bids_for_date(db, asset, target_dt)
        print(f"auto_submit_idm_fallback for {tomorrow_str}: {result.get('status')}, n_submitted={result.get('n_submitted')}")
    except Exception as e:
        db.rollback()
        print(f"Error in auto_submit_idm_fallback job: {e}")
    finally:
        db.close()


# Реєстр дій "настроюваного сценарію віртуального диспетчера" (2026-08-26) —
# розширюваний: нову дію додати пізніше means написати функцію й один рядок
# тут, БЕЗ зміни формату system_settings.json чи фронтенду (select уже
# ітерує цей самий реєстр). (fn, людська назва, дефолтні hour/minute).
DISPATCHER_ACTIONS = {
    'forecast_and_plan': (run_daily_forecast_and_optimization, 'Прогноз + MILP-план + чернетка заявок', 6, 0),
    'auto_submit_bids': (run_auto_submit_bids, 'Автоподача заявок РДН (якщо ще не подані)', 11, 30),
    'reconcile_bids': (run_reconcile_bids, 'Звірка заявок з ціною закриття РДН', 13, 0),
    'auto_submit_idm_fallback': (run_auto_submit_idm_fallback, 'Автоподача ВДР-заявки для невиконаних годин', 14, 45),
}


def _load_dispatcher_schedule() -> list:
    """Читає virtual_dispatcher_schedule з system_settings.json (той самий
    патерн, що _auto_dispatch_enabled) — якщо не збережено, повертає дефолт
    із DISPATCHER_ACTIONS (усі 4 дії увімкнені за дефолтним часом)."""
    default = [
        {'action': action_id, 'hour': h, 'minute': m, 'enabled': True}
        for action_id, (_, _, h, m) in DISPATCHER_ACTIONS.items()
    ]
    path = os.path.join(settings.DATA_DIR, "system_settings.json")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                saved = json.load(f).get("virtual_dispatcher_schedule")
            if saved:
                return saved
        except Exception:
            pass
    return default


def reschedule_virtual_dispatcher_jobs():
    """Перебудовує APScheduler-джоби дій диспетчера за поточним розкладом
    (system_settings.json) — викликається і при старті, і одразу після
    POST /optimization/dispatcher-schedule (живе застосування, без
    рестарту сервера — на відміну від BESS-підключення, це лише cron-час).
    Невідомий action_id (напр. застаріле збережене значення) чесно
    пропускається з попередженням, не валить решту розкладу."""
    schedule = _load_dispatcher_schedule()
    seen_ids = set()
    for item in schedule:
        action_id = item.get('action')
        entry = DISPATCHER_ACTIONS.get(action_id)
        if not entry:
            print(f"Warning: unknown dispatcher action {action_id!r} in schedule — skipping.")
            continue
        fn, _label, _dh, _dm = entry
        job_id = f'vdispatch_{action_id}'
        seen_ids.add(job_id)
        if not item.get('enabled', True):
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
            continue
        scheduler.add_job(
            fn, 'cron', hour=int(item['hour']), minute=int(item['minute']),
            id=job_id, replace_existing=True,
        )
    # Прибрати джоби для дій, яких більше нема в збереженому розкладі.
    for action_id in DISPATCHER_ACTIONS:
        job_id = f'vdispatch_{action_id}'
        if job_id not in seen_ids and scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
    print(f"[{datetime.datetime.now()}] Virtual dispatcher schedule applied: {[j for j in seen_ids]}")

# 2026-08-27: знайдено користувачем наживо — контейнер працює на UTC (жодного
# TZ env var не виставлено), а BackgroundScheduler() без явного timezone
# реєструє cron 'hour'/'minute' у ЛОКАЛЬНОМУ часі КОНТЕЙНЕРА, тобто UTC, а не
# Kyiv. Усі "13:00" в сценарії диспетчера (і всі інші cron-джоби нижче: 02:00,
# 10:00, 07:30) РЕАЛЬНО спрацьовували на 2-3г пізніше (EEST/EET) за те, що
# планувалось — конкретний живий випадок: reconcile_bids, налаштований на
# 13:00, ще не спрацював о 13:31 Kyiv, бо реально чекав 13:00 UTC = 16:00
# Kyiv. Цей ризик був відомий і задокументований коментарями нижче ("НЕ
# перевірено") ще з 2026-08-06/17/24, але залишався неперевіреним, доки не
# проявився на практиці. Виправлено раз, тут — `timezone` на самому
# BackgroundScheduler застосовується до ВСІХ джоб, зареєстрованих на ньому,
# без потреби чіпати кожен окремий add_job.
scheduler = BackgroundScheduler(timezone='Europe/Kyiv')

def start_scheduler():
    if not scheduler.running:
        # "Настроюваний сценарій віртуального диспетчера" (2026-08-26) —
        # forecast_and_plan/auto_submit_bids/reconcile_bids/auto_submit_idm_fallback
        # реєструються тут за розкладом із system_settings.json (дефолт —
        # 06:00/11:30/13:00/14:45, DISPATCHER_ACTIONS вище). Rationale за
        # дефолтний час forecast_and_plan (06:00, було колись 17:30, п.
        # 2026-08-04): реальна ціна закриття РДН на цільову дату невідома
        # до ~12:00+, тож 06:00 лишається задовго до цього; також після
        # повної ночі накопичення даних (Open-Meteo/Telegram) — sync_realtime_data
        # всередині job'и бере найсвіжіше на момент запуску.
        reschedule_virtual_dispatcher_jobs()
        # 02:00 — ~4 hours of buffer before the 06:00 forecast job, so the fresh
        # model is already on disk in time for the same morning's forecast. Live
        # end-to-end run on 2026-08-04 (VPS, in-container) took ~13.7 min total
        # (sync 673.6s dominated by external API latency + train_models 132.1s +
        # train_quantile_models 13.8s) — comfortably inside the window.
        scheduler.add_job(run_nightly_model_retrain, 'cron', hour=2, minute=0, id='nightly_model_retrain')
        # 10:00 Kyiv — ~2 год запасу до закриття воріт РДН (12:00), достатньо
        # часу, щоб заявки на завтра вже були сформовані (диспетчер робить це
        # вручну після 06:00). Раніше тут було застереження "відповідність
        # контейнера реальному київському часу НЕ перевірена" — перевірено й
        # виправлено 2026-08-27 (`timezone='Europe/Kyiv'` на самому
        # scheduler'і вище), тепер `hour=10` дійсно означає 10:00 Kyiv.
        scheduler.add_job(run_bid_reminder_check, 'cron', hour=10, minute=0, id='bid_reminder_check')
        # 07:30 — через годину після ранкового прогнозу (06:00), достатньо
        # часу, щоб свіжий PriceForecast уже був у БД. Дешева перевірка —
        # не потребує окремого "вікна", час обрано щоб не накладатись на
        # інші джоби.
        scheduler.add_job(run_drift_check, 'cron', hour=7, minute=30, id='forecast_drift_check')
        # Кожні 30 хв — легкий одиночний POST до oree.com.ua (не важкий
        # sync_realtime_data), щоб дописувати ще не засинхронізовані
        # години СЬОГОДНІШНЬОЇ доби одразу, як оператор ринку їх публікує
        # (звіт диспетчера 2026-08-23, CLAUDE.md п.24-25) — раніше це
        # чекало наступного 06:00 job.
        scheduler.add_job(run_intraday_price_sync, 'interval', minutes=30, id='intraday_price_sync')
        scheduler.start()
        print("Background Scheduler started successfully.")

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        print("Background Scheduler shut down.")
