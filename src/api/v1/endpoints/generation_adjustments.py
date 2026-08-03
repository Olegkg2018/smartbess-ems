import datetime
from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from src.database.session import SessionLocal
from src.database.models import GenerationAdjustment
from src.core.security import RoleChecker

router = APIRouter()

class GenerationAdjustmentModel(BaseModel):
    date: str
    nuclear_pct: float = 100.0
    hydro_pct: float = 100.0
    solar_pct: float = 100.0
    wind_pct: float = 100.0
    note: Optional[str] = None

@router.get("", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_generation_adjustment(date: str):
    """
    Повертає ручну корекцію доступності генерації на дату (100% скрізь, якщо
    диспетчер нічого не вказував — це нейтральне значення, forecast/run без
    змін).
    """
    db = SessionLocal()
    try:
        target_dt = datetime.datetime.strptime(date, '%Y-%m-%d')
        row = db.query(GenerationAdjustment).filter(GenerationAdjustment.date == target_dt).first()
        if not row:
            return {
                "date": date, "nuclear_pct": 100.0, "hydro_pct": 100.0,
                "solar_pct": 100.0, "wind_pct": 100.0, "note": None, "is_default": True,
            }
        return {
            "date": date, "nuclear_pct": row.nuclear_pct, "hydro_pct": row.hydro_pct,
            "solar_pct": row.solar_pct, "wind_pct": row.wind_pct, "note": row.note,
            "is_default": False,
        }
    finally:
        db.close()

@router.post("", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def save_generation_adjustment(req: GenerationAdjustmentModel, background_tasks: BackgroundTasks):
    db = SessionLocal()
    try:
        target_dt = datetime.datetime.strptime(req.date, '%Y-%m-%d')
        row = db.query(GenerationAdjustment).filter(GenerationAdjustment.date == target_dt).first()
        if not row:
            row = GenerationAdjustment(date=target_dt)
            db.add(row)
        row.nuclear_pct = req.nuclear_pct
        row.hydro_pct = req.hydro_pct
        row.solar_pct = req.solar_pct
        row.wind_pct = req.wind_pct
        row.note = req.note
        db.commit()

        # Якщо на цю дату вже є збережений PriceForecast (щоденна 17:30 джоба
        # або попередній ручний /forecast/run) — він порахований на СТАРІЙ
        # корекції (або без неї) і без явного перерахунку лишиться мовчки
        # застарілим (реальний баг, знайдений 2026-08-03). Спільний хелпер з
        # forecast.py сам ставить той самий job у чергу, що й ручна кнопка
        # "Перерахувати" — не дублює логіку.
        from src.api.v1.endpoints.forecast import trigger_forecast_recompute_if_exists
        recompute_job_id = trigger_forecast_recompute_if_exists(db, background_tasks, req.date, target_dt)

        return {
            "status": "success",
            "message": f"Корекцію генерації на {req.date} збережено." + (
                " Прогноз на цю дату вже існував — перерахунок запущено автоматично."
                if recompute_job_id else ""
            ),
            "recompute_triggered": bool(recompute_job_id),
            "recompute_job_id": recompute_job_id,
        }
    finally:
        db.close()
