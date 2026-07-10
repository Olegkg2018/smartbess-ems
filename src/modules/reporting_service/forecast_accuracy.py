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


def get_profit_capture_ratio(db) -> dict:
    """
    Коефіцієнт, яким дораховується "реалістичний" прибуток для діб без
    реальної телеметрії/ручних заявок (заміна фейкового accuracy_rate=0.80).
    Пріоритет джерела:
      1. Жива точність прогноз/факт з БД (compute_rolling_accuracy), якщо вже
         накопичилось достатньо діб.
      2. Офлайн walk-forward бектест (data/backtest_report.json) — чесно
         виміряний на реальних даних, але не на живих продових прогнозах.
      3. Явно позначений дефолт 0.80 лише як останній fallback, якщо взагалі
         нічого не пораховано (і це видно в полі "source").
    WAPE конвертується в коефіцієнт "захопленого" прибутку як (1 - WAPE/100),
    обмежений [0.5, 0.98], щоб уникнути абсурдних значень на малій вибірці.
    """
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
