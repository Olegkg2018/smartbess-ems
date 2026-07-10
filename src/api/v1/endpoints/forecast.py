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

def run_forecast_background_job(job_id: str, target_date_str: str, selected_model: str):
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

        # Раніше тут вимагався ручний ввід Gas_Price/Nuclear_Outage від оператора
        # (фейкові фактори). Модель тепер використовує реальні дані напряму.
        prediction_results = mt.predict_next_day(target_date_str, weather_forecast, last_prices)
        predicted_prices = prediction_results[selected_model]

        # P10/P90 conformal-калібрований інтервал невизначеності (Фаза 3) —
        # заповнюємо lower_bound_uah/upper_bound_uah, які раніше в моделі були
        # оголошені, але ніколи не використовувались.
        try:
            price_band = mt.predict_price_band(target_date_str, weather_forecast, last_prices)
        except Exception as band_err:
            print(f"Warning: could not compute price band: {band_err}")
            price_band = None

        # Персистимо в PriceForecast — без цього /api/v1/optimization/run не
        # знаходить прогноз і мовчки підставляє захардкоджені mock-ціни
        # (реальний баг, знайдений і виправлений під час Фази 2).
        from src.database.models import PriceForecast
        db = SessionLocal()
        try:
            for t in range(24):
                forecast_time = target_dt_start + datetime.timedelta(hours=t)
                db.query(PriceForecast).filter(
                    PriceForecast.timestamp == forecast_time,
                    PriceForecast.forecast_run_at == target_dt_start
                ).delete()
                db.add(PriceForecast(
                    timestamp=forecast_time,
                    forecast_run_at=target_dt_start,
                    model_version=selected_model,
                    predicted_price_uah=float(predicted_prices[t]),
                    lower_bound_uah=float(price_band['lower_uah'][t]) if price_band else None,
                    upper_bound_uah=float(price_band['upper_uah'][t]) if price_band else None,
                ))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        job["status"] = "completed"
        job["progress"] = 100
        job["result"] = predicted_prices
        job["price_band"] = price_band
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
async def get_latest_forecast(target_date: Optional[str] = None):
    """
    Без target_date — глобально останній прогноз (будь-яка дата). З
    target_date — прогноз саме для цієї доби (forecast_run_at == та північ).
    Без цього фільтра, якщо в БД є прогнози на кілька різних майбутніх дат
    (напр. тестові запуски наперед), ендпоінт міг мовчки повернути прогноз
    НЕ на ту дату, яку зараз показує UI — реальний баг, знайдений під час
    перевірки P10/P90 полоси у Фазі 3.
    """
    from src.database.models import PriceForecast
    db = SessionLocal()
    try:
        query = db.query(PriceForecast)
        if target_date:
            target_dt_start = datetime.datetime.strptime(target_date, '%Y-%m-%d')
            query = query.filter(PriceForecast.forecast_run_at == target_dt_start)
        latest = query.order_by(PriceForecast.forecast_run_at.desc(), PriceForecast.timestamp).limit(24).all()
        if not latest:
            raise HTTPException(status_code=404, detail="No forecasts found in DB")
            
        return {
            "forecast_run_at": latest[0].forecast_run_at.isoformat() + "Z",
            "hours": [i for i in range(len(latest))],
            "predicted_prices_uah": [f.predicted_price_uah for f in latest],
            "lower_bound_uah": [f.lower_bound_uah for f in latest],
            "upper_bound_uah": [f.upper_bound_uah for f in latest],
        }
    finally:
        db.close()

@router.get("/actual", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_actual_prices(target_date: str):
    """
    Реальна опублікована ціна РДН з oree.com.ua для конкретної доби — щоб
    диспетчер бачив прогноз ПОРЯД із фактом на тому самому графіку, як тільки
    факт опублікований (зазвичай наступного дня після торгів). "Якщо є" —
    якщо доба ще не настала або оператор ринку ще не опублікував ціни,
    available=false, а не вигадане значення.
    """
    import pandas as pd
    from src.database.models import MarketPrice

    try:
        target_dt = datetime.datetime.strptime(target_date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="target_date має бути у форматі YYYY-MM-DD")

    db = SessionLocal()
    try:
        # 1. Спершу локальна БД (щодня поповнюється sync_market_prices_to_db)
        rows = db.query(MarketPrice).filter(
            MarketPrice.timestamp >= target_dt,
            MarketPrice.timestamp < target_dt + datetime.timedelta(days=1)
        ).order_by(MarketPrice.timestamp).all()

        if len(rows) == 24:
            return {
                "date": target_date,
                "available": True,
                "source": "db",
                "hours": list(range(24)),
                "actual_prices_uah": [r.price_uah for r in rows],
            }

        # 2. "Якщо є" на oree.com.ua, але ще не потрапило в локальну БД —
        # живий запит до того самого джерела, яким тренується модель.
        df_month = dm.fetch_oree_prices_for_month(target_dt.month, target_dt.year)
        if not df_month.empty:
            df_month['Datetime'] = pd.to_datetime(df_month['Datetime'])
            df_day = df_month[
                (df_month['Datetime'] >= target_dt) & (df_month['Datetime'] < target_dt + datetime.timedelta(days=1))
            ].sort_values('Datetime')
            if len(df_day) == 24:
                return {
                    "date": target_date,
                    "available": True,
                    "source": "oree.com.ua (live)",
                    "hours": list(range(24)),
                    "actual_prices_uah": df_day['Price'].tolist(),
                }

        return {"date": target_date, "available": False, "hours": [], "actual_prices_uah": []}
    finally:
        db.close()
