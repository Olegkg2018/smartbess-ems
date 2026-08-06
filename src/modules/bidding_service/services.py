"""
Реальна механіка подачі заявок на РДН (аукціон єдиної ціни) — див.
докстрінг MarketBid у database/models.py для повного пояснення правил
виконання. Сьогодні (2026-07-31) диспетчер РУЧНО вносить заявку в кабінет
oree.com.ua на основі того, що показує цей модуль — автоматичного подання
через API немає (майбутня робота, не зроблено).
"""
import datetime
import pandas as pd

from src.database.models import ChargeDischargePlan, PriceForecast, MarketBid, BidMarginOverride
from src.modules.optimization_service.milp_model import evaluate_schedule_profit
import src.modules.forecast_service.ml_pipeline as mt

DEFAULT_MARGIN_PCT = 2.0

# Легальні межі ціни заявки на OREE (Правила ринку РДН/ВДР, НКРЕКП №308,
# ред. №1169/24.06.2019 зі змінами №832/02.05.2023) — 10.00-50000.00 грн/МВт·год.
# Це ІНША межа, ніж PRICE_FLOOR/PRICE_CAP=16000.0 у ml_pipeline.py/milp_model.py —
# ті клипають ПРОГНОЗ ціни, а не саму ціну заявки, що піде в кабінет oree.com.ua.
# Джерело: MEMORY.md §8.
OREE_BID_PRICE_MIN_UAH = 10.0
OREE_BID_PRICE_MAX_UAH = 50000.0


def clamp_bid_price_to_oree_bounds(raw_price_uah: float) -> tuple:
    """Обмежує ціну заявки легальними межами OREE. Повертає (clamped_price, was_clamped)."""
    clamped = min(max(raw_price_uah, OREE_BID_PRICE_MIN_UAH), OREE_BID_PRICE_MAX_UAH)
    return clamped, clamped != raw_price_uah

# Ті самі тарифи, що scheduler.py реально використовує для боєвого плану
# (Settings поки не підключені до battery_params там) — щоб P&L заявки
# рахувався на однакових умовах з тим, що реально диспетчерувалось.
TARIFF_KWARGS = dict(
    transmission_tariff=528.57, distribution_tariff=1500.0,
    dispatch_tariff=104.57, supplier_margin=100.0, mode='arbitrage',
)


def get_margin_pct(db, asset_id: str, target_date: datetime.datetime) -> float:
    override = db.query(BidMarginOverride).filter(
        BidMarginOverride.asset_id == asset_id, BidMarginOverride.date == target_date,
    ).first()
    return override.margin_pct if override else DEFAULT_MARGIN_PCT


def generate_bids_for_date(db, asset, target_date: datetime.datetime, margin_pct: float = None) -> dict:
    """
    Будує заявки РДН на target_date з уже порахованого MILP-графіка
    (ChargeDischargePlan) + прогнозної ціни (PriceForecast) на ту саму добу,
    зсунутих на margin_pct (bid_margin_overrides, якщо збережено дispatcher'ом,
    інакше DEFAULT_MARGIN_PCT). Не запускає прогноз/оптимізацію заново —
    вимагає, щоб вони вже були пораховані (як і /optimization/plans).
    """
    if margin_pct is None:
        margin_pct = get_margin_pct(db, asset.id, target_date)

    plans = db.query(ChargeDischargePlan).filter(
        ChargeDischargePlan.asset_id == asset.id,
        ChargeDischargePlan.optimized_run_at == target_date,
    ).order_by(ChargeDischargePlan.timestamp).all()
    if not plans:
        return {'status': 'no_plan', 'message': f'Немає порахованого MILP-графіка на {target_date.date()} — спочатку запустіть прогноз/оптимізацію.'}

    forecasts = db.query(PriceForecast).filter(
        PriceForecast.forecast_run_at == target_date,
    ).order_by(PriceForecast.timestamp).all()
    forecast_by_hour = {f.timestamp.hour: f.predicted_price_uah for f in forecasts}
    if len(forecast_by_hour) != 24:
        return {'status': 'no_forecast', 'message': f'Немає повного прогнозу цін на {target_date.date()} (є {len(forecast_by_hour)}/24 годин).'}

    bids = []
    for p in plans:
        hour = p.timestamp.hour
        forecast_price = forecast_by_hour.get(hour)
        if forecast_price is None:
            continue

        if p.target_power_mw > 0.001:
            bid_type = 'sell'
            volume_kw = p.target_power_mw * 1000.0
            bid_price_raw = forecast_price * (1.0 - margin_pct / 100.0)
        elif p.target_power_mw < -0.001:
            bid_type = 'buy'
            volume_kw = -p.target_power_mw * 1000.0
            bid_price_raw = forecast_price * (1.0 + margin_pct / 100.0)
        else:
            bid_type = 'standby'
            volume_kw = 0.0
            bid_price_raw = forecast_price

        bid_price, _ = clamp_bid_price_to_oree_bounds(bid_price_raw)

        row = db.query(MarketBid).filter(
            MarketBid.asset_id == asset.id, MarketBid.timestamp == p.timestamp,
        ).first()
        if not row:
            row = MarketBid(asset_id=asset.id, timestamp=p.timestamp)
            db.add(row)
        row.bid_type = bid_type
        row.volume_kw = volume_kw
        row.forecast_price_uah = forecast_price
        row.margin_pct = margin_pct
        row.bid_price_uah = bid_price
        # Нова заявка — попередній стан розрахунку (якщо доба вже колись
        # заселювалась) більше не дійсний, доки не прийде нова факт-ціна.
        row.actual_price_uah = None
        row.executed = None
        row.realized_profit_uah = None
        row.idm_fallback_suggested = False
        row.idm_fallback_price_uah = None
        row.idm_fallback_profit_uah = None
        row.settled_at = None
        bids.append(row)

    db.commit()
    bid_dicts = [_bid_to_dict(b) for b in bids]
    return {
        'status': 'ok',
        'date': target_date.date().isoformat(),
        'margin_pct': margin_pct,
        'n_bids': len(bids),
        'n_price_clamped': sum(1 for d in bid_dicts if d['bid_price_legally_clamped']),
        'bids': bid_dicts,
    }


def settle_bids_for_date(db, asset, target_date: datetime.datetime, actual_prices_by_hour: dict) -> dict:
    """
    Звіряє вже подані заявки (MarketBid) з РЕАЛЬНОЮ ціною РДН
    (actual_prices_by_hour: {hour: ціна}) і визначає, чи заявка "зіграла":
    - sell виконується, якщо bid_price_uah <= actual — продаж за actual.
    - buy виконується, якщо bid_price_uah >= actual — купівля за actual.
    Для НЕ виконаних заявок пропонує альтернативу на ВДР (оцінка ціни через
    ml_pipeline.estimate_idm_price_for_hour, бо реальної ціни ВДР на цю
    годину ще нема — ВДР ще не відбувся).
    """
    bids = db.query(MarketBid).filter(
        MarketBid.asset_id == asset.id,
        MarketBid.timestamp >= target_date,
        MarketBid.timestamp < target_date + datetime.timedelta(days=1),
    ).order_by(MarketBid.timestamp).all()
    if not bids:
        return {'status': 'no_bids', 'message': f'Немає поданих заявок на {target_date.date()} — спочатку згенеруйте їх.'}

    deg_cost_kwh = asset.deg_cost_per_mwh / 1000.0
    settled = []
    for b in bids:
        hour = b.timestamp.hour
        actual = actual_prices_by_hour.get(hour)
        if actual is None:
            continue
        b.actual_price_uah = actual

        if b.bid_type == 'standby':
            b.executed = True
            b.realized_profit_uah = 0.0
            b.idm_fallback_suggested = False
        elif b.bid_type == 'sell':
            b.executed = b.bid_price_uah <= actual
            if b.executed:
                b.realized_profit_uah = evaluate_schedule_profit(
                    [0.0], [b.volume_kw], [actual], degradation_cost=deg_cost_kwh, **TARIFF_KWARGS,
                )
                b.idm_fallback_suggested = False
            else:
                b.realized_profit_uah = 0.0
                idm_price = mt.estimate_idm_price_for_hour(actual, as_of_date=target_date)
                b.idm_fallback_suggested = True
                b.idm_fallback_price_uah = idm_price
                b.idm_fallback_profit_uah = evaluate_schedule_profit(
                    [0.0], [b.volume_kw], [idm_price], degradation_cost=deg_cost_kwh, **TARIFF_KWARGS,
                )
        elif b.bid_type == 'buy':
            b.executed = b.bid_price_uah >= actual
            if b.executed:
                b.realized_profit_uah = evaluate_schedule_profit(
                    [b.volume_kw], [0.0], [actual], degradation_cost=deg_cost_kwh, **TARIFF_KWARGS,
                )
                b.idm_fallback_suggested = False
            else:
                b.realized_profit_uah = 0.0
                idm_price = mt.estimate_idm_price_for_hour(actual, as_of_date=target_date)
                b.idm_fallback_suggested = True
                b.idm_fallback_price_uah = idm_price
                b.idm_fallback_profit_uah = evaluate_schedule_profit(
                    [b.volume_kw], [0.0], [idm_price], degradation_cost=deg_cost_kwh, **TARIFF_KWARGS,
                )

        b.settled_at = datetime.datetime.utcnow()
        settled.append(b)

    db.commit()

    total_realized = sum(b.realized_profit_uah or 0.0 for b in settled)
    n_executed = sum(1 for b in settled if b.executed)
    n_failed = sum(1 for b in settled if b.executed is False)
    return {
        'status': 'ok',
        'date': target_date.date().isoformat(),
        'n_settled': len(settled),
        'n_executed': n_executed,
        'n_failed_needs_idm': n_failed,
        'total_realized_profit_uah': float(total_realized),
        'bids': [_bid_to_dict(b) for b in settled],
    }


def _bid_to_dict(b: MarketBid) -> dict:
    price_clamped = (
        b.bid_price_uah <= OREE_BID_PRICE_MIN_UAH + 1e-6
        or b.bid_price_uah >= OREE_BID_PRICE_MAX_UAH - 1e-6
    )
    return {
        'hour': b.timestamp.hour,
        'bid_type': b.bid_type,
        'volume_kw': b.volume_kw,
        'forecast_price_uah': b.forecast_price_uah,
        'margin_pct': b.margin_pct,
        'bid_price_uah': b.bid_price_uah,
        'actual_price_uah': b.actual_price_uah,
        'executed': b.executed,
        'realized_profit_uah': b.realized_profit_uah,
        'idm_fallback_suggested': b.idm_fallback_suggested,
        'idm_fallback_price_uah': b.idm_fallback_price_uah,
        'idm_fallback_profit_uah': b.idm_fallback_profit_uah,
        'bid_price_legally_clamped': price_clamped,
        'oree_bid_price_bounds_uah': {'min': OREE_BID_PRICE_MIN_UAH, 'max': OREE_BID_PRICE_MAX_UAH},
    }


def build_daily_action_summary(db, asset, target_date: datetime.datetime) -> dict:
    """
    "Що робити зараз" по вже ІСНУЮЧОМУ стану MarketBid на target_date.
    Тільки ЧИТАЄ — нічого не генерує й не звіряє сама (щоб не перетворитись
    на приховану автоматизацію подачі заявок, явно відкладену користувачем).
    """
    bids = db.query(MarketBid).filter(
        MarketBid.asset_id == asset.id,
        MarketBid.timestamp >= target_date,
        MarketBid.timestamp < target_date + datetime.timedelta(days=1),
    ).order_by(MarketBid.timestamp).all()

    date_str = target_date.date().isoformat()
    actions = []

    if not bids:
        actions.append({
            'severity': 'action', 'hour': None,
            'text': f'Заявки РДН на {date_str} ще не сформовано — натисніть «Сформувати заявки зараз» (потрібен готовий прогноз/MILP-графік на цю дату).',
        })
        return {'status': 'ok', 'date': date_str, 'has_bids': False, 'all_settled': False,
                'n_total': 0, 'n_executed': 0, 'n_needs_idm_action': 0, 'n_price_clamped': 0, 'actions': actions}

    dicts = [_bid_to_dict(b) for b in bids if b.bid_type != 'standby']
    n_unsettled = sum(1 for d in dicts if d['executed'] is None)
    n_executed = sum(1 for d in dicts if d['executed'] is True)
    n_needs_idm = sum(1 for d in dicts if d['executed'] is False and d['idm_fallback_suggested'])
    n_clamped = sum(1 for d in dicts if d['bid_price_legally_clamped'])

    if n_unsettled > 0:
        actions.append({
            'severity': 'action', 'hour': None,
            'text': f'Подайте вручну {n_unsettled} заявок РДН на {date_str} у кабінеті oree.com.ua (закриття воріт — 12:00 напередодні). Після публікації факту ціни (~13:00) натисніть «Звірити з фактом OREE».',
        })

    if n_clamped > 0:
        actions.append({
            'severity': 'warning', 'hour': None,
            'text': f'{n_clamped} год. з ціною заявки, скоригованою до законної межі OREE (10.00–50000.00 грн/МВт·год) — перевірте вручну перед поданням.',
        })

    for d in dicts:
        if d['executed'] is False and d['idm_fallback_suggested']:
            actions.append({
                'severity': 'warning', 'hour': d['hour'],
                'text': (f"Год {d['hour']}: заявка РДН НЕ виконана (заявлено {round(d['bid_price_uah'])}, факт {round(d['actual_price_uah'] or 0)} грн/МВт·год) — "
                         f"подайте на ВДР {round(d['volume_kw'])} кВт за ~{round(d['idm_fallback_price_uah'] or 0)} грн/МВт·год "
                         f"(очікуваний прибуток {round(d['idm_fallback_profit_uah'] or 0)} грн)."),
            })

    if not actions and n_executed > 0:
        actions.append({'severity': 'ok', 'hour': None, 'text': f'Усі {n_executed} заявок РДН на {date_str} виконано — дій не потрібно.'})

    return {
        'status': 'ok', 'date': date_str, 'has_bids': True, 'all_settled': n_unsettled == 0,
        'n_total': len(dicts), 'n_executed': n_executed, 'n_needs_idm_action': n_needs_idm,
        'n_price_clamped': n_clamped, 'actions': actions,
    }
