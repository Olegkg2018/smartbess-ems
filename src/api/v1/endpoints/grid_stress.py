import datetime
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from src.database.session import SessionLocal
from src.database.models import GridStressOverride
from src.core.security import RoleChecker
from fastapi import Depends
import src.modules.external_data_service.telegram_public as ext_tg

router = APIRouter()

class GridStressOverrideModel(BaseModel):
    date: str
    forced_restriction_queues: Optional[float] = None
    note: Optional[str] = None

@router.get("", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_grid_stress(date: str):
    """
    Показує, що РЕАЛЬНО відомо про обсяг ГПВ на дату — автоматичний сигнал з
    Telegram-постів Укренерго (якщо за цей день був опублікований пост із
    числом), інакше ручне значення диспетчера (якщо збережене), інакше
    відсутнє. source дозволяє диспетчеру бачити, звідки взялось число,
    а не сприймати його як гарантовано точне.
    """
    auto_signal = ext_tg.daily_grid_stress_signal().get(date, {})
    auto_queues = auto_signal.get("forced_restriction_queues")

    db = SessionLocal()
    try:
        target_dt = datetime.datetime.strptime(date, '%Y-%m-%d')
        override = db.query(GridStressOverride).filter(GridStressOverride.date == target_dt).first()

        if auto_queues is not None:
            source = "auto_telegram"
            effective_queues = auto_queues
        elif override is not None and override.forced_restriction_queues is not None:
            source = "manual"
            effective_queues = override.forced_restriction_queues
        else:
            source = "none"
            effective_queues = None

        return {
            "date": date,
            "forced_restriction_queues": effective_queues,
            "source": source,
            "auto_available": auto_queues is not None,
            "has_manual_override": override is not None and override.forced_restriction_queues is not None,
            "manual_forced_restriction_queues": override.forced_restriction_queues if override else None,
            "manual_note": override.note if override else None,
            # Додатковий контекст з автоматичного сигналу (лише для показу,
            # не редагується вручну) — тренд споживання й масштаб знеструмлень.
            "auto_consumption_trend": auto_signal.get("consumption_trend"),
            "auto_settlements_affected": auto_signal.get("settlements_affected"),
            "auto_oblasts_affected": auto_signal.get("oblasts_affected"),
        }
    finally:
        db.close()

@router.post("", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def save_grid_stress(req: GridStressOverrideModel):
    db = SessionLocal()
    try:
        target_dt = datetime.datetime.strptime(req.date, '%Y-%m-%d')
        row = db.query(GridStressOverride).filter(GridStressOverride.date == target_dt).first()
        if not row:
            row = GridStressOverride(date=target_dt)
            db.add(row)
        row.forced_restriction_queues = req.forced_restriction_queues
        row.note = req.note
        db.commit()
        return {"status": "success", "message": f"Ручну оцінку обсягу ГПВ на {req.date} збережено."}
    finally:
        db.close()

@router.delete("", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def clear_grid_stress(date: str):
    db = SessionLocal()
    try:
        target_dt = datetime.datetime.strptime(date, '%Y-%m-%d')
        row = db.query(GridStressOverride).filter(GridStressOverride.date == target_dt).first()
        if row:
            db.delete(row)
            db.commit()
        return {"status": "success", "message": f"Ручну оцінку обсягу ГПВ на {date} видалено."}
    finally:
        db.close()
