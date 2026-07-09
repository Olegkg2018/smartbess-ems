import datetime
import uuid
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel
from typing import Optional

from src.core.config import settings
from src.core.security import RoleChecker
import src.modules.market_data_service.data_manager as dm
import src.modules.forecast_service.ml_pipeline as mt
from src.database.session import SessionLocal

router = APIRouter()

from src.core.redis import set_job_status, get_job_status

class RunForecastRequest(BaseModel):
    target_date: str
    selected_model: Optional[str] = "lightgbm"
    gas_price_eur_mwh: Optional[float] = 35.0
    nuclear_outage_pct: Optional[float] = 0.15

def run_forecast_background_job(job_id: str, target_date_str: str, selected_model: str, gas_price: float, nuclear_outage: float):
    job = get_job_status(job_id) or {}
    job["status"] = "running"
    set_job_status(job_id, job)
    try:
        # Load weather forecast
        weather_forecast = dm.fetch_weather_forecast()
        
        # Load history
        import pandas as pd
        df_hist = pd.read_csv(dm.MERGED_DATA_PATH)
        df_hist['Datetime'] = pd.to_datetime(df_hist['Datetime'])
        target_dt_start = pd.to_datetime(target_date_str)
        hist_before_target = df_hist[df_hist['Datetime'] < target_dt_start].sort_values('Datetime')
        
        if len(hist_before_target) >= 168:
            last_prices = hist_before_target['Price'].iloc[-168:].tolist()
        else:
            last_prices = df_hist['Price'].iloc[:168].tolist()
            
        factors = {
            'Gas_Price': gas_price,
            'Nuclear_Outage': nuclear_outage,
            'Solar_Strike': 0.0,
            'Market_Coeff': 1.0,
            'VDR_Volume': 1.0,
            'Grid_Import_Export': 0.0
        }
        
        # Run prediction
        prediction_results = mt.predict_next_day(target_date_str, weather_forecast, last_prices, factors)
        
        job["status"] = "completed"
        job["progress"] = 100
        job["result"] = prediction_results[selected_model]
        set_job_status(job_id, job)
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        set_job_status(job_id, job)

@router.post("/run", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def run_forecast(req: RunForecastRequest, background_tasks: BackgroundTasks):
    job_id = f"job_fc_{uuid.uuid4().hex[:8]}"
    created_at = datetime.datetime.utcnow().isoformat() + "Z"
    job = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "created_at": created_at
    }
    set_job_status(job_id, job)
    
    background_tasks.add_task(
        run_forecast_background_job,
        job_id,
        req.target_date,
        req.selected_model,
        req.gas_price_eur_mwh,
        req.nuclear_outage_pct
    )
    
    return {
        "job_id": job_id,
        "status": "pending",
        "created_at": created_at,
        "message": "Расчет прогноза цен РДН запущен.",
        "links": {
            "status_url": f"/api/v1/jobs/{job_id}"
        }
    }

@router.get("/latest", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_latest_forecast():
    # Return latest forecast from DB
    from src.database.models import PriceForecast
    db = SessionLocal()
    try:
        latest = db.query(PriceForecast).order_by(PriceForecast.forecast_run_at.desc(), PriceForecast.timestamp).limit(24).all()
        if not latest:
            raise HTTPException(status_code=404, detail="No forecasts found in DB")
            
        return {
            "forecast_run_at": latest[0].forecast_run_at.isoformat() + "Z",
            "hours": [i for i in range(len(latest))],
            "predicted_prices_uah": [f.predicted_price_uah for f in latest]
        }
    finally:
        db.close()
