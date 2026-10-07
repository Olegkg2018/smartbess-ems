import datetime
from typing import Optional
from sqlalchemy.orm import Session

from src.database.models import Asset, BessTelemetry, InitialSocOverride, ChargeDischargePlan, MarketBidSocFeasibility
from src.core.time_utils import kyiv_to_utc, utc_to_kyiv, kyiv_day_bounds


def previous_day_calculated_fraction(db: Session, asset: Asset, target_dt: datetime.datetime) -> Optional[float]:
    """Ємність на кінець попередньої доби за вже порахованим MILP-планом
    (ChargeDischargePlan.expected_soc_mwh останньої години попередньої доби,
    optimized_run_at == та доба). Це розрахункове значення (яким план ЗАДУМАВ
    завершити добу), а не факт реального ручного диспетчингу — але значно
    точніше за сліпий фолбек 0.20, і не залежить від того, чи є SCADA.

    `target_dt` — вже kyiv_to_utc(date_str, 0) (рахує викликач). Попередню
    добу рахуємо через дату-рядок, а не простим `-timedelta(days=1)` — на
    добу переходу DST різниця в UTC між двома північчами не рівно 24г
    (CLAUDE.md п.26/27)."""
    prev_date_str = (utc_to_kyiv(target_dt).date() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    prev_dt = kyiv_to_utc(prev_date_str, 0)
    prev_last = (
        db.query(ChargeDischargePlan)
        .filter(ChargeDischargePlan.asset_id == asset.id, ChargeDischargePlan.optimized_run_at == prev_dt)
        .order_by(ChargeDischargePlan.timestamp.desc())
        .first()
    )
    if prev_last is None:
        return None
    return prev_last.expected_soc_mwh / asset.capacity_mwh


def previous_day_realized_fraction(db: Session, asset: Asset, target_dt: datetime.datetime) -> Optional[float]:
    """Реальний кінець попередньої доби за ВИКОНАНИМИ заявками РДН
    (MarketBidSocFeasibility.soc_after_mwh останньої години, пише
    settle_bids_for_date). На відміну від previous_day_calculated_fraction
    враховує невиконані заявки: якщо вечірній продаж не пройшов, енергія
    лишилась у батареї й переходить у наступну добу. None — якщо попередня
    доба ще не звірена повністю (немає рядка на її останню годину)."""
    prev_date_str = (utc_to_kyiv(target_dt).date() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    day_start, day_end = kyiv_day_bounds(prev_date_str)
    last = (
        db.query(MarketBidSocFeasibility)
        .filter(MarketBidSocFeasibility.asset_id == asset.id,
                MarketBidSocFeasibility.timestamp >= day_start,
                MarketBidSocFeasibility.timestamp < day_end)
        .order_by(MarketBidSocFeasibility.timestamp.desc())
        .first()
    )
    if last is None or last.timestamp != day_end - datetime.timedelta(hours=1):
        return None
    return last.soc_after_mwh / asset.capacity_mwh


def _bess_is_simulator() -> bool:
    import json
    import os
    from src.core.config import settings
    try:
        with open(os.path.join(settings.DATA_DIR, "system_settings.json")) as f:
            return json.load(f).get("bess_connection_type", "simulator") == "simulator"
    except Exception:
        return True


def get_start_of_day_soc(db: Session, asset: Asset, target_date: str, use_override: bool = True):
    """(частка 0..1, джерело) — SoC РІВНО на 00:00 target_date (київська доба).

    Пріоритет:
    1) manual — InitialSocOverride диспетчера;
    2) scada_midnight — телеметрія реальної батареї на 00:00 (лише для
       реального обладнання: симулятор фізичної правди не знає);
    3) realized_previous_day — реальний кінець попередньої доби за
       виконаними заявками (невиконані заявки змінюють залишок);
    4) calculated_previous_day — плановий кінець попередньої доби;
    5) scada_telemetry — остання телеметрія (лише якщо нічого вище нема);
    6) fallback_default — 20%.

    Раніше для сьогоднішньої дати тут бралась ОСТАННЯ телеметрія ("зараз"),
    тож уранці під час заряду "SoC на 00:00" показував, напр., 756 замість
    реальних 400; а план на завтра стартував з ПЛАНОВОГО кінця доби, навіть
    якщо вечірні продажі не виконались і батарея лишилась зарядженою."""
    min_frac = asset.min_soc_pct / 100.0
    max_frac = asset.max_soc_pct / 100.0

    def clamp(x):
        return max(min_frac, min(max_frac, x))

    if asset.capacity_mwh <= 0:
        return 0.20, 'fallback_default'
    target_dt = kyiv_to_utc(target_date, 0)

    override = (
        db.query(InitialSocOverride)
        .filter(InitialSocOverride.date == target_dt, InitialSocOverride.asset_id == asset.id)
        .first()
    ) if use_override else None
    if override is not None:
        return clamp((override.capacity_kwh / 1000.0) / asset.capacity_mwh), 'manual'

    if target_dt <= datetime.datetime.utcnow() and not _bess_is_simulator():
        tel = (
            db.query(BessTelemetry)
            .filter(BessTelemetry.asset_id == asset.id,
                    BessTelemetry.timestamp >= target_dt,
                    BessTelemetry.timestamp < target_dt + datetime.timedelta(minutes=15))
            .order_by(BessTelemetry.timestamp)
            .first()
        )
        if tel is not None:
            return clamp(tel.current_soc_mwh / asset.capacity_mwh), 'scada_midnight'

    fraction = previous_day_realized_fraction(db, asset, target_dt)
    if fraction is not None:
        return clamp(fraction), 'realized_previous_day'

    fraction = previous_day_calculated_fraction(db, asset, target_dt)
    if fraction is not None:
        return clamp(fraction), 'calculated_previous_day'

    tel = _latest_telemetry(db, asset)
    if tel is not None:
        return clamp(tel.current_soc_mwh / asset.capacity_mwh), 'scada_telemetry'
    return 0.20, 'fallback_default'


def _latest_telemetry(db: Session, asset: Asset):
    return (
        db.query(BessTelemetry)
        .filter(BessTelemetry.asset_id == asset.id)
        .order_by(BessTelemetry.timestamp.desc())
        .first()
    )


def get_current_soc_fraction(db: Session, asset: Asset, target_date: Optional[str] = None, include_midnight_override: bool = True) -> float:
    """SoC (частка 0..1) як initial_soc для оптимізації target_date.

    include_midnight_override=True (звичайний випадок — горизонт з 00:00):
    SoC на початок доби, get_start_of_day_soc.

    include_midnight_override=False — частковий перерахунок "від зараз"
    (п.46c-e): потрібен стан батареї САМЕ ЗАРАЗ — жива телеметрія, інакше
    той самий ланцюжок, що й для початку доби (InitialSocOverride тут не
    застосовується — він означає 00:00, не "зараз")."""
    if not include_midnight_override:
        tel = _latest_telemetry(db, asset)
        if tel is not None and asset.capacity_mwh > 0:
            return max(asset.min_soc_pct / 100.0, min(asset.max_soc_pct / 100.0, tel.current_soc_mwh / asset.capacity_mwh))
    if target_date is None:
        target_date = utc_to_kyiv(datetime.datetime.utcnow()).strftime('%Y-%m-%d')
    return get_start_of_day_soc(db, asset, target_date, use_override=include_midnight_override)[0]
