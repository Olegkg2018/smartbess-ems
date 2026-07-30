import datetime
from typing import Optional
from sqlalchemy.orm import Session

from src.database.models import Asset, BessTelemetry, InitialSocOverride, ChargeDischargePlan


def previous_day_calculated_fraction(db: Session, asset: Asset, target_dt: datetime.datetime) -> Optional[float]:
    """Ємність на кінець попередньої доби за вже порахованим MILP-планом
    (ChargeDischargePlan.expected_soc_mwh останньої години попередньої доби,
    optimized_run_at == та доба). Це розрахункове значення (яким план ЗАДУМАВ
    завершити добу), а не факт реального ручного диспетчингу — але значно
    точніше за сліпий фолбек 0.20, і не залежить від того, чи є SCADA."""
    prev_dt = target_dt - datetime.timedelta(days=1)
    prev_last = (
        db.query(ChargeDischargePlan)
        .filter(ChargeDischargePlan.asset_id == asset.id, ChargeDischargePlan.optimized_run_at == prev_dt)
        .order_by(ChargeDischargePlan.timestamp.desc())
        .first()
    )
    if prev_last is None:
        return None
    return prev_last.expected_soc_mwh / asset.capacity_mwh


def get_current_soc_fraction(db: Session, asset: Asset, target_date: Optional[str] = None) -> float:
    """
    SoC (частка ємності 0.0-1.0), що використовується як initial_soc для
    day-ahead оптимізації на target_date. Пріоритет джерел:

    1. InitialSocOverride на target_date — диспетчер вручну вказав ємність
       на 00:00 цієї доби (напр. коли реального зв'язку зі SCADA/BESS немає —
       фолбек нижче міг би дати неправильне значення, а диспетчер знає
       реальний стан з іншого джерела).
    2. Останній запис BessTelemetry (реальне Modbus-опитування, оновлюється
       кожні ~10с в scada_service.py) — фізичний вимір, точніший за будь-який
       розрахунок, коли він реально доступний.
    3. Розрахункова ємність на кінець попередньої доби (MILP-план на цю дату,
       остання година) — якщо ні ручного значення, ні телеметрії немає.
    4. Фолбек 0.20 — лише якщо взагалі нічого з вищого немає (холодний старт
       системи, ще жодного разу нічого не рахували).

    Раніше тут завжди був захардкоджений 0.20 незалежно від реального стану —
    "щоденний план вважав, що батарея завжди починає добу на 20%", хоча
    попередня доба реально могла завершитись на іншому рівні (типово на
    min_soc, бо MILP форсує розряд до min_soc в кінці кожної доби) — звідси
    "розряд о 1:00", хоча батарея вже порожня з учора.
    """
    target_dt = None
    if target_date and asset.capacity_mwh > 0:
        try:
            target_dt = datetime.datetime.strptime(target_date, '%Y-%m-%d')
        except (TypeError, ValueError):
            target_dt = None

    min_frac = asset.min_soc_pct / 100.0
    max_frac = asset.max_soc_pct / 100.0

    if target_dt is not None:
        override = (
            db.query(InitialSocOverride)
            .filter(InitialSocOverride.date == target_dt, InitialSocOverride.asset_id == asset.id)
            .first()
        )
        if override is not None:
            fraction = (override.capacity_kwh / 1000.0) / asset.capacity_mwh
            return max(min_frac, min(max_frac, fraction))

    tel = (
        db.query(BessTelemetry)
        .filter(BessTelemetry.asset_id == asset.id)
        .order_by(BessTelemetry.timestamp.desc())
        .first()
    )
    if tel is not None and asset.capacity_mwh > 0:
        fraction = tel.current_soc_mwh / asset.capacity_mwh
        return max(min_frac, min(max_frac, fraction))

    if target_dt is not None and asset.capacity_mwh > 0:
        fraction = previous_day_calculated_fraction(db, asset, target_dt)
        if fraction is not None:
            return max(min_frac, min(max_frac, fraction))

    return 0.20
