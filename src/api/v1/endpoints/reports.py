import datetime
from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from src.database.session import SessionLocal
from src.database.models import Asset, ChargeDischargePlan, BessTelemetry
from src.modules.reporting_service.services import ReportingService
from src.core.security import RoleChecker

router = APIRouter()

@router.get("/executive-summary", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_executive_summary(
    asset_id: str = Query(..., description="UUID of the BESS asset"),
    period: str = Query("month", description="One of day, week, month, year")
):
    db = SessionLocal()
    try:
        report = ReportingService.get_executive_summary_report(db, asset_id)
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error compiling executive summary: {str(e)}")
    finally:
        db.close()
