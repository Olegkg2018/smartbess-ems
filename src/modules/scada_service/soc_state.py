import datetime
from typing import Optional
from sqlalchemy.orm import Session

from src.database.models import Asset, BessTelemetry, InitialSocOverride


def get_current_soc_fraction(db: Session, asset: Asset, target_date: Optional[str] = None) -> float:
    """
    SoC (частка ємності 0.0-1.0), що використовується як initial_soc для
    day-ahead оптимізації на target_date. Пріоритет джерел:

    1. InitialSocOverride на target_date — диспетчер вручну вказав ємність
       на 00:00 цієї доби (напр. коли реального зв'язку зі SCADA/BESS немає —
       фолбек нижче міг би дати неправильне значення, а диспетчер знає
       реальний стан з іншого джерела).
    2. Останній запис BessTelemetry (реальне Modbus-опитування, оновлюється
       кожні ~10с в scada_service.py).
    3. Фолбек 0.20 — лише якщо немає ні ручного значення, ні телеметрії
       (холодний старт системи).

    Раніше тут завжди був захардкоджений 0.20 незалежно від реального стану —
    "щоденний план вважав, що батарея завжди починає добу на 20%", хоча
    попередня доба реально могла завершитись на іншому рівні (типово на
    min_soc, бо MILP форсує розряд до min_soc в кінці кожної доби) — звідси
    "розряд о 1:00", хоча батарея вже порожня з учора.
    """
    if target_date and asset.capacity_mwh > 0:
        try:
            target_dt = datetime.datetime.strptime(target_date, '%Y-%m-%d')
        except (TypeError, ValueError):
            target_dt = None
        if target_dt is not None:
            override = (
                db.query(InitialSocOverride)
                .filter(InitialSocOverride.date == target_dt, InitialSocOverride.asset_id == asset.id)
                .first()
            )
            if override is not None:
                fraction = (override.capacity_kwh / 1000.0) / asset.capacity_mwh
                min_frac = asset.min_soc_pct / 100.0
                max_frac = asset.max_soc_pct / 100.0
                return max(min_frac, min(max_frac, fraction))

    tel = (
        db.query(BessTelemetry)
        .filter(BessTelemetry.asset_id == asset.id)
        .order_by(BessTelemetry.timestamp.desc())
        .first()
    )
    if tel is None or asset.capacity_mwh <= 0:
        return 0.20

    fraction = tel.current_soc_mwh / asset.capacity_mwh
    min_frac = asset.min_soc_pct / 100.0
    max_frac = asset.max_soc_pct / 100.0
    return max(min_frac, min(max_frac, fraction))
