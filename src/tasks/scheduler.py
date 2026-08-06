import datetime
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler

import src.modules.market_data_service.data_manager as dm
import src.modules.forecast_service.ml_pipeline as mt
import src.modules.optimization_service.milp_model as opt
from src.modules.reporting_service.forecast_accuracy import sync_market_prices_to_db
import src.modules.external_data_service.telegram_bot as telegram_bot
from src.modules.scada_service.soc_state import get_current_soc_fraction
from src.database.session import SessionLocal
from src.database.models import Asset, PriceForecast, ChargeDischargePlan

def run_daily_forecast_and_optimization():
    print(f"[{datetime.datetime.now()}] Background Scheduler: Starting daily forecast and optimization job...")
    db = SessionLocal()
    try:
        # Determine target date: tomorrow
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
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
        target_dt_start = pd.to_datetime(tomorrow_str)
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
            forecast_time = target_dt_start + datetime.timedelta(hours=t)
            
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
            
        db.commit()
        print(f"[{datetime.datetime.now()}] Background Scheduler: Successfully completed daily forecast and BESS optimization plan.")
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
        dm.sync_realtime_data(force=True)
        metrics = mt.train_models()
        print(f"Nightly retrain: train_models metrics: {metrics}")
        qmetrics = mt.train_quantile_models()
        print(f"Nightly retrain: train_quantile_models: {qmetrics}")
        print(f"[{datetime.datetime.now()}] Background Scheduler: Nightly model retrain completed successfully.")
    except Exception as e:
        print(f"Error in nightly model retrain job: {e}")

def run_bid_reminder_check():
    """
    Читає ІСНУЮЧИЙ стан заявок (сьогодні/завтра) і шле Telegram-нагадування
    диспетчеру зі списком конкретних дій — НІКОЛИ сама не генерує і не
    звіряє заявки (це відкладена користувачем автоматизація), тільки читає
    вже наявні MarketBid через build_daily_action_summary.
    """
    print(f"[{datetime.datetime.now()}] Background Scheduler: Checking bid reminder...")
    try:
        result = telegram_bot.check_and_send_bid_reminder()
        print(f"[{datetime.datetime.now()}] Bid reminder check result: {result}")
    except Exception as e:
        print(f"Warning: bid reminder check failed: {e}")

scheduler = BackgroundScheduler()

def start_scheduler():
    if not scheduler.running:
        # Run daily at 06:00 (moved from 17:30 on 2026-08-04, by request) — the
        # target-date formula below (tomorrow = today + 1) is unchanged, this is
        # purely a time-of-day move. Rationale: the real RDN clearing price for
        # the target date isn't published until ~13:00+, so 06:00 still runs well
        # before that; it also runs AFTER a full night of data accumulation
        # (Open-Meteo weather forecast refreshes, overnight Telegram grid-stress
        # posts) that a 17:30-the-day-before run would have missed — sync_realtime_data
        # below simply picks up whatever is freshest at run time.
        scheduler.add_job(run_daily_forecast_and_optimization, 'cron', hour=6, minute=0, id='daily_bess_opt')
        # 02:00 — ~4 hours of buffer before the 06:00 forecast job, so the fresh
        # model is already on disk in time for the same morning's forecast. Live
        # end-to-end run on 2026-08-04 (VPS, in-container) took ~13.7 min total
        # (sync 673.6s dominated by external API latency + train_models 132.1s +
        # train_quantile_models 13.8s) — comfortably inside the window.
        scheduler.add_job(run_nightly_model_retrain, 'cron', hour=2, minute=0, id='nightly_model_retrain')
        # 10:00 — ~2 год запасу до закриття воріт РДН (12:00), достатньо часу,
        # щоб заявки на завтра вже були сформовані (диспетчер робить це вручну
        # після 06:00). Як і для 06:00/02:00 джоб: відповідність "10:00
        # контейнера" реальному київському часу НЕ перевірена (те саме
        # застереження, що в CLAUDE.md п.17) — якщо контейнер працює не в
        # Europe/Kyiv, скоригувати hour вручну.
        scheduler.add_job(run_bid_reminder_check, 'cron', hour=10, minute=0, id='bid_reminder_check')
        scheduler.start()
        print("Background Scheduler started successfully.")

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        print("Background Scheduler shut down.")
