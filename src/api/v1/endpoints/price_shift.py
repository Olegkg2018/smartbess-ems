import datetime
from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from src.database.session import SessionLocal
from src.database.models import PriceShiftOverride
from src.core.security import RoleChecker

router = APIRouter()

class PriceShiftModel(BaseModel):
    date: str
    shift_pct: float = 0.0
    note: Optional[str] = None

@router.get("", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_price_shift(date: str):
    """
    Повертає ручний відсотковий зсув прогнозу ціни на дату (0% — нейтрально,
    якщо диспетчер нічого не вказував).
    """
    db = SessionLocal()
    try:
        target_dt = datetime.datetime.strptime(date, '%Y-%m-%d')
        row = db.query(PriceShiftOverride).filter(PriceShiftOverride.date == target_dt).first()
        if not row:
            return {"date": date, "shift_pct": 0.0, "note": None, "is_default": True}
        return {"date": date, "shift_pct": row.shift_pct, "note": row.note, "is_default": False}
    finally:
        db.close()

@router.post("", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def save_price_shift(req: PriceShiftModel, background_tasks: BackgroundTasks):
    db = SessionLocal()
    try:
        target_dt = datetime.datetime.strptime(req.date, '%Y-%m-%d')
        row = db.query(PriceShiftOverride).filter(PriceShiftOverride.date == target_dt).first()
        if not row:
            row = PriceShiftOverride(date=target_dt)
            db.add(row)
        row.shift_pct = req.shift_pct
        row.note = req.note
        db.commit()

        # Той самий спільний хелпер, що й у generation_adjustments.py — щоб
        # збережений зсув реально дійшов до вже порахованого PriceForecast,
        # а не лишився мовчки застосованим лише до майбутніх перерахунків.
        from src.api.v1.endpoints.forecast import trigger_forecast_recompute_if_exists
        recompute_job_id = trigger_forecast_recompute_if_exists(db, background_tasks, req.date, target_dt)

        return {
            "status": "success",
            "message": f"Ручний зсув прогнозу на {req.date} збережено." + (
                " Прогноз на цю дату вже існував — перерахунок запущено автоматично."
                if recompute_job_id else ""
            ),
            "recompute_triggered": bool(recompute_job_id),
            "recompute_job_id": recompute_job_id,
        }
    finally:
        db.close()
