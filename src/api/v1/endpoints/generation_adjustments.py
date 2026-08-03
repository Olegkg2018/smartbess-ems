import datetime
import uuid
from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from src.database.session import SessionLocal
from src.database.models import GenerationAdjustment, PriceForecast
from src.core.security import RoleChecker
from src.core.redis import set_job_status

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
        # застарілим, незалежно від того, яким шляхом збережено цей запис
        # (реальний баг, знайдений 2026-08-03 — диспетчер зберіг корекцію,
        # прогноз не змінився). Бекенд сам ставить той самий job у чергу, що
        # й ручна кнопка "Перерахувати" (run_forecast_background_job) — не
        # дублює логіку. Якщо диспетчер після цього ще раз явно натисне
        # "Зберегти і перерахувати прогноз" — запуститься другий, надлишковий,
        # але безпечний перерахунок з тими самими вхідними даними.
        existing = db.query(PriceForecast).filter(PriceForecast.forecast_run_at == target_dt).first()
        recompute_job_id = None
        if existing:
            from src.api.v1.endpoints.forecast import run_forecast_background_job
            recompute_job_id = f"job_fc_{uuid.uuid4().hex[:8]}"
            set_job_status(recompute_job_id, {
                "job_id": recompute_job_id, "status": "pending", "progress": 0,
                "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            })
            background_tasks.add_task(
                run_forecast_background_job, recompute_job_id, req.date, existing.model_version,
            )

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
