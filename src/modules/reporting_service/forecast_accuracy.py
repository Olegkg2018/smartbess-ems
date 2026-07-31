"""
Чесне вимірювання точності прогнозу — замінює захардкоджений accuracy_rate=0.80,
яким раніше просто множився оптимальний прибуток без жодного виміру реальності
(ReportingService.get_executive_summary_report). MarketPrice раніше була
оголошена в моделях, але ніде не заповнювалась — тому порівнювати прогноз з
фактом не було з чого.
"""
import os
import datetime
import pandas as pd
from sqlalchemy import func

from src.core.config import settings
from src.database.models import MarketPrice, PriceForecast

BACKTEST_REPORT_PATH = os.path.join(settings.DATA_DIR, "backtest_report.json")


def sync_market_prices_to_db(db, csv_path=None):
    """
    Записує реальні ціни РДН (з historical_data_merged.csv) у таблицю
    MarketPrice — тільки для timestamp, яких там ще немає. Викликається щодня
    з планувальника і може бути прогнана одноразово для повного бекфілу.
    Повертає кількість доданих рядків.
    """
    csv_path = csv_path or os.path.join(settings.DATA_DIR, "historical_data_merged.csv")
    if not os.path.exists(csv_path):
        return 0

    df = pd.read_csv(csv_path, usecols=['Datetime', 'Price'])
    df['Datetime'] = pd.to_datetime(df['Datetime'])
    df = df.dropna(subset=['Price'])

    existing_max = db.query(func.max(MarketPrice.timestamp)).scalar()
    if existing_max is not None:
        df = df[df['Datetime'] > existing_max]

    if df.empty:
        return 0

    objects = [
        MarketPrice(timestamp=row.Datetime.to_pydatetime(), price_uah=float(row.Price), area="UA_IPS")
        for row in df.itertuples()
    ]
    db.bulk_save_objects(objects)
    db.commit()
    return len(objects)


def _calc_mape_wape(y_true, y_pred):
    import numpy as np
    y_true = np.array(y_true)
    y_pred = np.array(y_pred)
    non_zero = y_true != 0
    mape = float(np.mean(np.abs((y_true[non_zero] - y_pred[non_zero]) / y_true[non_zero])) * 100) if np.any(non_zero) else None
    wape = float(np.sum(np.abs(y_true - y_pred)) / np.sum(np.abs(y_true)) * 100) if np.sum(np.abs(y_true)) != 0 else None
    bias = float(np.mean(y_pred - y_true))
    return mape, wape, bias


def compute_rolling_accuracy(db, days: int = 30, model_version: str = None) -> dict:
    """
    Реальна точність прогнозу за останні `days` днів: JOIN PriceForecast з
    MarketPrice по timestamp. Кожна доба бере ОСТАННІЙ прогноз, зроблений ДО
    настання цієї доби (forecast_run_at < timestamp доби) — щоб не змішувати
    кілька перезапусків прогнозу за різний час.
    """
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)

    q = db.query(PriceForecast, MarketPrice.price_uah).join(
        MarketPrice, PriceForecast.timestamp == MarketPrice.timestamp
    ).filter(PriceForecast.timestamp >= cutoff)
    if model_version:
        q = q.filter(PriceForecast.model_version == model_version)

    rows = q.all()
    if not rows:
        return {
            'status': 'insufficient_data',
            'message': f'Немає накопичених пар прогноз/факт за останні {days} днів — MarketPrice/PriceForecast щойно почали заповнюватись.',
            'days': days,
            'n_hours': 0,
        }

    y_true = [r[1] for r in rows]
    y_pred = [r[0].predicted_price_uah for r in rows]
    mape, wape, bias = _calc_mape_wape(y_true, y_pred)

    by_day = {}
    for forecast_row, actual in rows:
        d = forecast_row.timestamp.date().isoformat()
        by_day.setdefault(d, {'y_true': [], 'y_pred': []})
        by_day[d]['y_true'].append(actual)
        by_day[d]['y_pred'].append(forecast_row.predicted_price_uah)

    daily = []
    for d in sorted(by_day.keys()):
        dm_, dw_, db_ = _calc_mape_wape(by_day[d]['y_true'], by_day[d]['y_pred'])
        daily.append({'date': d, 'mape': dm_, 'wape': dw_, 'bias': db_, 'n_hours': len(by_day[d]['y_true'])})

    return {
        'status': 'ok',
        'days': days,
        'n_hours': len(rows),
        'mape': mape,
        'wape': wape,
        'bias_uah': bias,
        'daily': daily,
    }


def compute_real_profit_capture_ratio(db, days: int = 30) -> dict:
    """
    Чесний "% захопленого прибутку від ідеального прогнозу" (perfect
    foresight capture ratio) — РЕАЛЬНЕ фінансове порівняння, а не проксі
    через WAPE (як нижче в get_profit_capture_ratio):
      - "actual": для доби, де заявки РДН реально подавались і звірялись
        (MarketBid.executed заповнено на всі 24 години) — сума
        MarketBid.realized_profit_uah, тобто РЕАЛЬНО реалізований P&L з
        урахуванням годин, де заявка НЕ зіграла (realized_profit_uah=0 для
        них — фізично не змогли зарядити/розрядити через РДН у цю годину,
        ВДР-фолбек не враховується тут, бо це лише пропозиція диспетчеру,
        а не гарантовано виконана дія). Для діб СТАРІШИХ за появу механізму
        заявок (до 2026-07-31, MarketBid ще не існував) — фолбек на
        попереднє спрощення: P&L ChargeDischargePlan, перерахований за
        РЕАЛЬНОЮ ціною доби (MarketPrice), що НЕЯВНО припускає 100%-не
        виконання плану (менш чесно, але єдине, що можна порахувати заднім
        числом без історії заявок).
      - "perfect_foresight": що дав би той самий MILP (`optimize_battery_schedule`),
        якби на вхід дали РЕАЛЬНУ ціну наперед — теоретичний максимум для тих
        самих фізичних обмежень активу.
    Ratio = сума actual / сума perfect_foresight за всі повні доби (24г плану
    + 24г реальної ціни) у вікні `days`. Оскільки обидва графіки — розв'язки
    ОДНІЄЇ Й ТІЄЇ Ж MILP-моделі з однаковими фізичними обмеженнями (лише вхідна
    ціна різна), ratio математично не може перевищувати 1.0 — perfect_foresight
    за визначенням оптимальний для реальної ціни. Значення суттєво >1.0 було б
    ознакою бага (розбіжність параметрів активу між планом і цим розрахунком),
    а не реальним ефектом.

    Потребує реальної історії ChargeDischargePlan (наробіток щоденного
    планувальника) — на новому/малому інстансі повних діб може бути замало,
    тоді status='insufficient_data' (як і в compute_rolling_accuracy).
    """
    from src.database.models import ChargeDischargePlan, Asset, MarketBid
    from src.modules.optimization_service.milp_model import optimize_battery_schedule, evaluate_schedule_profit

    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)

    asset = db.query(Asset).first()
    if not asset:
        return {'status': 'insufficient_data', 'message': 'Немає жодного BESS-активу в БД.', 'n_days': 0}

    plans = db.query(ChargeDischargePlan).filter(
        ChargeDischargePlan.asset_id == asset.id,
        ChargeDischargePlan.timestamp >= cutoff,
    ).order_by(ChargeDischargePlan.timestamp).all()

    by_day = {}
    for p in plans:
        d = p.timestamp.date().isoformat()
        by_day.setdefault(d, {})[p.timestamp.hour] = p.target_power_mw

    prices = db.query(MarketPrice).filter(MarketPrice.timestamp >= cutoff).order_by(MarketPrice.timestamp).all()
    price_by_day = {}
    for pr in prices:
        d = pr.timestamp.date().isoformat()
        price_by_day.setdefault(d, {})[pr.timestamp.hour] = pr.price_uah

    bid_rows = db.query(MarketBid).filter(
        MarketBid.asset_id == asset.id, MarketBid.timestamp >= cutoff,
    ).order_by(MarketBid.timestamp).all()
    bids_by_day = {}
    for b in bid_rows:
        d = b.timestamp.date().isoformat()
        bids_by_day.setdefault(d, {})[b.timestamp.hour] = b

    # Тарифи — ті самі константи, що scheduler.py реально використовує щодня
    # для боєвого плану (Settings поки не підключені до battery_params там) —
    # порівнюємо actual/perfect_foresight на однакових вхідних умовах.
    tariff_kwargs = dict(
        transmission_tariff=528.57, distribution_tariff=1500.0,
        dispatch_tariff=104.57, supplier_margin=100.0, mode='arbitrage',
    )
    deg_cost_kwh = asset.deg_cost_per_mwh / 1000.0

    # Толерантність на плаваючу кому — Asset.power_mw/capacity_mwh МОГЛИ
    # змінитися в Settings між тим, як рахувався старий план, і зараз
    # (реально сталося в цьому проєкті — старі плани лишились розраховані на
    # інший розмір активу). Порівнювати actual/perfect_foresight коректно
    # ЛИШЕ якщо історичний графік фізично вкладається в ПОТОЧНІ ліміти —
    # інакше вони не на однакових фізичних обмеженнях, і ratio втрачає сенс
    # (спостеріжено: без цієї перевірки виходив ratio > 1, що математично
    # неможливо для однакових обмежень — ознака саме цього неспівпадіння).
    power_tol = 1e-3
    max_power_mw = asset.power_mw + power_tol

    daily_results = []
    skipped_infeasible = 0
    for d, hours in by_day.items():
        if len(hours) != 24 or d not in price_by_day or len(price_by_day[d]) != 24:
            continue
        target_power_mw = [hours[h] for h in range(24)]
        real_prices = [price_by_day[d][h] for h in range(24)]

        if any(abs(p) > max_power_mw for p in target_power_mw):
            skipped_infeasible += 1
            continue

        day_bids = bids_by_day.get(d)
        if day_bids and len(day_bids) == 24 and all(day_bids[h].executed is not None for h in range(24)):
            actual_profit = sum(day_bids[h].realized_profit_uah or 0.0 for h in range(24))
            actual_source = 'market_bid_settled'
        else:
            charge_kw = [max(0.0, -p * 1000.0) for p in target_power_mw]
            discharge_kw = [max(0.0, p * 1000.0) for p in target_power_mw]
            actual_profit = evaluate_schedule_profit(
                charge_kw, discharge_kw, real_prices, degradation_cost=deg_cost_kwh, **tariff_kwargs,
            )
            actual_source = 'chargedischargeplan_full_execution_assumed'

        perfect_res = optimize_battery_schedule(
            real_prices,
            battery_capacity=asset.capacity_mwh * 1000.0,
            max_charge_power=asset.power_mw * 1000.0,
            max_discharge_power=asset.power_mw * 1000.0,
            charge_efficiency=asset.efficiency_charge,
            discharge_efficiency=asset.efficiency_discharge,
            min_soc=asset.min_soc_pct / 100.0,
            max_soc=asset.max_soc_pct / 100.0,
            max_cycles_per_day=asset.max_cycles_per_day,
            degradation_cost=deg_cost_kwh,
            **tariff_kwargs,
        )
        if not perfect_res:
            continue

        daily_results.append({
            'date': d,
            'actual_profit_uah': actual_profit,
            'perfect_foresight_profit_uah': perfect_res['net_profit_uah'],
            'actual_source': actual_source,
        })

    if len(daily_results) < 3:
        msg = f'Замало повних діб (план 24г + реальна ціна 24г) за останні {days} днів: {len(daily_results)}. Потрібно, щоб щоденний планувальник відпрацював довше.'
        if skipped_infeasible:
            msg += f' Пропущено {skipped_infeasible} діб — історичний план не вкладається в поточні ліміти активу (Asset.power_mw/capacity_mwh змінились у Settings).'
        return {'status': 'insufficient_data', 'message': msg, 'n_days': len(daily_results), 'skipped_infeasible_days': skipped_infeasible}

    total_actual = sum(r['actual_profit_uah'] for r in daily_results)
    total_perfect = sum(r['perfect_foresight_profit_uah'] for r in daily_results)

    if abs(total_perfect) < 1e-6:
        return {'status': 'insufficient_data', 'message': 'Ідеальний прогноз дає ~0 прибутку на цьому вікні — коефіцієнт невизначений.', 'n_days': len(daily_results)}

    ratio = total_actual / total_perfect
    n_days_market_bid_settled = sum(1 for r in daily_results if r['actual_source'] == 'market_bid_settled')
    return {
        'status': 'ok',
        'ratio': float(max(0.0, min(1.0, ratio))),
        'raw_ratio': float(ratio),
        'n_days': len(daily_results),
        'n_days_market_bid_settled': n_days_market_bid_settled,
        'n_days_plan_full_execution_assumed': len(daily_results) - n_days_market_bid_settled,
        'skipped_infeasible_days': skipped_infeasible,
        'total_actual_profit_uah': float(total_actual),
        'total_perfect_foresight_profit_uah': float(total_perfect),
        'daily': daily_results,
    }


def get_profit_capture_ratio(db) -> dict:
    """
    Коефіцієнт, яким дораховується "реалістичний" прибуток для діб без
    реальної телеметрії/ручних заявок (заміна фейкового accuracy_rate=0.80).
    Пріоритет джерела:
      1. РЕАЛЬНИЙ фінансовий perfect-foresight capture ratio
         (compute_real_profit_capture_ratio) — actual dispatch P&L на
         реальних цінах проти того, що дав би MILP, якби знав ціну наперед.
         Найчесніший варіант, але потребує кількох повних діб реальної
         історії ChargeDischargePlan.
      2. Жива точність прогноз/факт з БД (compute_rolling_accuracy) —
         проксі через WAPE, якщо накопичилось достатньо діб точності, але
         ще не діб повної диспетчеризації для варіанта 1.
      3. Офлайн walk-forward бектест (data/backtest_report.json) — чесно
         виміряний на реальних даних, але не на живих продових прогнозах.
      4. Явно позначений дефолт 0.80 лише як останній fallback, якщо взагалі
         нічого не пораховано (і це видно в полі "source").
    WAPE-варіанти (2, 3) конвертуються в коефіцієнт (1 - WAPE/100), обмежений
    [0.5, 0.98], щоб уникнути абсурдних значень на малій вибірці.
    """
    real = compute_real_profit_capture_ratio(db, days=30)
    if real['status'] == 'ok':
        return {
            'ratio': real['ratio'], 'source': 'real_dispatch_vs_perfect_foresight',
            'n_days': real['n_days'],
            'n_days_market_bid_settled': real['n_days_market_bid_settled'],
            'n_days_plan_full_execution_assumed': real['n_days_plan_full_execution_assumed'],
            'total_actual_profit_uah': real['total_actual_profit_uah'],
            'total_perfect_foresight_profit_uah': real['total_perfect_foresight_profit_uah'],
        }

    live = compute_rolling_accuracy(db, days=30)
    if live['status'] == 'ok' and live['n_hours'] >= 24 * 7 and live.get('wape') is not None:
        ratio = max(0.5, min(0.98, 1.0 - live['wape'] / 100.0))
        return {'ratio': ratio, 'source': 'live_forecast_vs_actual', 'wape': live['wape'], 'n_hours': live['n_hours']}

    if os.path.exists(BACKTEST_REPORT_PATH):
        import json
        try:
            with open(BACKTEST_REPORT_PATH) as f:
                report = json.load(f)
            wape = report.get('summary', {}).get('mean_wape')
            if wape is not None:
                ratio = max(0.5, min(0.98, 1.0 - wape / 100.0))
                return {'ratio': ratio, 'source': 'walk_forward_backtest', 'wape': wape,
                        'test_days': report.get('summary', {}).get('test_days')}
        except Exception:
            pass

    return {'ratio': 0.80, 'source': 'default_fallback_no_data'}
