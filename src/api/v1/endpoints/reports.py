import datetime
from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from src.database.session import SessionLocal
from src.database.models import Asset, ChargeDischargePlan, BessTelemetry
from src.modules.reporting_service.services import ReportingService
from src.modules.reporting_service.forecast_accuracy import compute_rolling_accuracy, get_profit_capture_ratio
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

@router.get("/forecast-accuracy", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_forecast_accuracy(
    days: int = Query(30, description="Скільки останніх днів порівняти прогноз/факт")
):
    db = SessionLocal()
    try:
        live = compute_rolling_accuracy(db, days=days)
        ratio = get_profit_capture_ratio(db)
        return {"live_accuracy": live, "profit_capture_ratio": ratio}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error computing forecast accuracy: {str(e)}")
    finally:
        db.close()

@router.get("/market-conditions", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_market_conditions():
    """
    Реальний операційний знімок "на зараз" для панелі диспетчера: остання
    зібрана ціна газу, транскордонний нетто-експорт (ENTSO-E) та keyword-сигнал
    з публічних каналів Укренерго/Міненерго. Замінює мертві повзунки
    "Ринкові фактори прогнозування", які раніше нічого насправді не міняли.
    """
    import os
    import pandas as pd
    from src.core.config import settings
    import src.modules.external_data_service.telegram_public as ext_tg

    result = {
        "gas_price_eur_mwh": None,
        "gas_price_as_of": None,
        "grid_net_export_mw": None,
        "grid_net_export_as_of": None,
        "grid_stress_today": {"grid_stress_high": 0, "grid_stress_medium": 0, "mentions": 0},
        "latest_posts": [],
    }

    csv_path = os.path.join(settings.DATA_DIR, "historical_data_merged.csv")
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path, usecols=["Datetime", "Gas_Price_EUR_MWh", "Grid_Net_Export_MW"])
            df["Datetime"] = pd.to_datetime(df["Datetime"])

            gas_series = df.dropna(subset=["Gas_Price_EUR_MWh"])
            if not gas_series.empty:
                last = gas_series.iloc[-1]
                result["gas_price_eur_mwh"] = float(last["Gas_Price_EUR_MWh"])
                result["gas_price_as_of"] = last["Datetime"].isoformat()

            flow_series = df.dropna(subset=["Grid_Net_Export_MW"])
            if not flow_series.empty:
                last = flow_series.iloc[-1]
                result["grid_net_export_mw"] = float(last["Grid_Net_Export_MW"])
                result["grid_net_export_as_of"] = last["Datetime"].isoformat()
        except Exception as e:
            result["error"] = f"Error reading market conditions from CSV: {str(e)}"

    today_str = datetime.datetime.utcnow().date().isoformat()
    stress = ext_tg.daily_grid_stress_signal()
    if today_str in stress:
        result["grid_stress_today"] = stress[today_str]

    result["latest_posts"] = ext_tg.get_latest_posts(n=3)

    return result
