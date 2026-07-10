import datetime
import uuid
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel
from typing import Optional, List

from src.core.config import settings
from src.database.session import SessionLocal
from src.database.models import Asset, ChargeDischargePlan, PriceForecast, ManualOverride
import src.modules.optimization_service.milp_model as opt
from src.core.redis import set_job_status, get_job_status
from src.core.security import RoleChecker

router = APIRouter()

class RunOptimizationRequest(BaseModel):
    asset_id: str
    target_date: str
    initial_soc_pct: Optional[float] = 20.0
    mode: Optional[str] = "arbitrage"
    simulations_count: Optional[int] = 50

def run_optimization_background_job(
    job_id: str,
    asset_id_str: str,
    target_date_str: str,
    initial_soc_pct: float,
    mode_str: str,
    simulations_count: int
):
    job = get_job_status(job_id) or {}
    job["status"] = "running"
    set_job_status(job_id, job)
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == asset_id_str).first()
        if not asset:
            asset = db.query(Asset).first() # Fallback to first asset
            
        if not asset:
            raise ValueError("No asset found in database")
            
        target_dt_start = datetime.datetime.strptime(target_date_str, '%Y-%m-%d')
        
        # Load forecast prices from DB or generate mock if empty
        forecasts = db.query(PriceForecast).filter(
            PriceForecast.forecast_run_at == target_dt_start
        ).order_by(PriceForecast.timestamp).all()
        
        price_lower = None
        price_upper = None
        if len(forecasts) == 24:
            prices = [f.predicted_price_uah for f in forecasts]
            # P10/P90 conformal-калібрований інтервал (Фаза 3), якщо forecast/run
            # його порахував — реальні квантилі замість вигаданого ±1.64σ.
            if all(f.lower_bound_uah is not None and f.upper_bound_uah is not None for f in forecasts):
                price_lower = [f.lower_bound_uah for f in forecasts]
                price_upper = [f.upper_bound_uah for f in forecasts]
        else:
            # Generate mock prices if DB is empty
            prices = [
                3000.0, 2800.0, 2000.0, 1500.0, 1000.0, 800.0,
                2000.0, 3500.0, 4500.0, 4000.0, 3200.0, 2500.0,
                2000.0, 1800.0, 1200.0, 1000.0, 1500.0, 2200.0,
                4500.0, 6000.0, 7500.0, 8500.0, 6500.0, 4500.0
            ]

        battery_params = {
            'battery_capacity': asset.capacity_mwh * 1000.0,
            'max_charge_power': asset.power_mw * 1000.0,
            'max_discharge_power': asset.power_mw * 1000.0,
            'charge_efficiency': asset.efficiency_charge,
            'discharge_efficiency': asset.efficiency_discharge,
            'initial_soc': initial_soc_pct / 100.0,
            'min_soc': asset.min_soc_pct / 100.0,
            'max_soc': asset.max_soc_pct / 100.0,
            'max_cycles_per_day': 1.5,
            'degradation_cost': asset.deg_cost_per_mwh / 1000.0,
            'transmission_tariff': 528.57,
            'distribution_tariff': 1500.0,
            'dispatch_tariff': 104.57,
            'supplier_margin': 100.0,
            'mode': mode_str
        }
        
        # Run scenarios and VaR
        scenarios_results = opt.optimize_with_scenarios_and_risks(
            prices=prices,
            price_lower=price_lower,
            price_upper=price_upper,
            num_simulations=simulations_count,
            **battery_params
        )
        
        # Save optimal base schedule to database
        base_sched = scenarios_results['scenarios']['base']
        for t in range(24):
            forecast_time = target_dt_start + datetime.timedelta(hours=t)
            db.query(ChargeDischargePlan).filter(
                ChargeDischargePlan.timestamp == forecast_time,
                ChargeDischargePlan.asset_id == asset.id,
                ChargeDischargePlan.optimized_run_at == target_dt_start
            ).delete()
            
            sched_item = base_sched['schedule'][t]
            plan_entry = ChargeDischargePlan(
                timestamp=forecast_time,
                asset_id=asset.id,
                optimized_run_at=target_dt_start,
                target_power_mw=sched_item['power_kw'] / 1000.0,
                expected_soc_mwh=sched_item['soc_kwh'] / 1000.0,
                expected_profit_uah=sched_item['hourly_p_l_uah']
            )
            db.add(plan_entry)
            
        db.commit()
        
        job["status"] = "completed"
        job["progress"] = 100
        job["result"] = scenarios_results
        set_job_status(job_id, job)
    except Exception as e:
        db.rollback()
        job["status"] = "failed"
        job["error"] = str(e)
        set_job_status(job_id, job)
    finally:
        db.close()

@router.post("/run", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def run_optimization(req: RunOptimizationRequest, background_tasks: BackgroundTasks):
    job_id = f"job_opt_{uuid.uuid4().hex[:8]}"
    created_at = datetime.datetime.utcnow().isoformat() + "Z"
    job = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "created_at": created_at
    }
    set_job_status(job_id, job)
    
    background_tasks.add_task(
        run_optimization_background_job,
        job_id,
        req.asset_id,
        req.target_date,
        req.initial_soc_pct,
        req.mode,
        req.simulations_count
    )
    
    return {
        "job_id": job_id,
        "status": "pending",
        "created_at": created_at,
        "message": "Задача оптимизации BESS добавлена в очередь.",
        "links": {
            "status_url": f"/api/v1/jobs/{job_id}"
        }
    }

@router.get("/plans", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_plans(asset_id: str, date: str):
    db = SessionLocal()
    try:
        target_dt = datetime.datetime.strptime(date, '%Y-%m-%d')
        plans = db.query(ChargeDischargePlan).filter(
            ChargeDischargePlan.asset_id == asset_id,
            ChargeDischargePlan.optimized_run_at == target_dt
        ).order_by(ChargeDischargePlan.timestamp).all()
        
        if not plans:
            raise HTTPException(status_code=404, detail="No optimization plans found for selected asset and date")
            
        return {
            "asset_id": asset_id,
            "date": date,
            "schedule": [
                {
                    "timestamp": p.timestamp.isoformat() + "Z",
                    "target_power_mw": p.target_power_mw,
                    "expected_soc_mwh": p.expected_soc_mwh,
                    "expected_profit_uah": p.expected_profit_uah
                }
                for p in plans
            ]
        }
    finally:
        db.close()

class HourlyOverrideItem(BaseModel):
    hour: int
    power_mw: float
    price_uah: float

class SaveOverridesRequest(BaseModel):
    asset_id: str
    date: str
    overrides: List[HourlyOverrideItem]

@router.get("/manual-overrides", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_manual_overrides(asset_id: str, date: str):
    db = SessionLocal()
    try:
        target_date = datetime.datetime.strptime(date, '%Y-%m-%d').date()
        dt_start = datetime.datetime.combine(target_date, datetime.time.min)
        dt_end = datetime.datetime.combine(target_date, datetime.time.max)
        
        # Load overrides
        overrides = db.query(ManualOverride).filter(
            ManualOverride.asset_id == asset_id,
            ManualOverride.timestamp >= dt_start,
            ManualOverride.timestamp <= dt_end
        ).order_by(ManualOverride.timestamp).all()
        
        # Also query active optimization plans for pre-filling
        plans = db.query(ChargeDischargePlan).filter(
            ChargeDischargePlan.asset_id == asset_id,
            ChargeDischargePlan.optimized_run_at == dt_start
        ).order_by(ChargeDischargePlan.timestamp).all()
        
        # Create map of hour -> override
        override_map = {}
        for o in overrides:
            override_map[o.timestamp.hour] = o
            
        plan_map = {}
        for p in plans:
            plan_map[p.timestamp.hour] = p
            
        # Get base market prices: реальна ціна за факт (якщо доба вже минула),
        # інакше — реальний збережений прогноз (PriceForecast), і лише як
        # останній fallback — умовна константа (немає ні факту, ні прогнозу).
        import pandas as pd
        import os
        csv_path = os.path.join(settings.DATA_DIR, "historical_data_merged.csv")
        if not os.path.exists(csv_path):
            csv_path = "/home/oleg/agy_energo/data/historical_data_merged.csv"

        day_prices = None
        try:
            if os.path.exists(csv_path):
                df = pd.read_csv(csv_path)
                df['Datetime'] = pd.to_datetime(df['Datetime'])
                df_day = df[df['Datetime'].dt.date == target_date].sort_values('Datetime')
                if len(df_day) >= 24:
                    day_prices = df_day['Price'].tolist()
        except Exception:
            pass

        if day_prices is None:
            forecasts = db.query(PriceForecast).filter(
                PriceForecast.forecast_run_at == dt_start
            ).order_by(PriceForecast.timestamp).all()
            if len(forecasts) == 24:
                day_prices = [f.predicted_price_uah for f in forecasts]

        if day_prices is None:
            day_prices = [3000.0] * 24
            
        schedule = []
        for hour in range(24):
            dt_hour = dt_start + datetime.timedelta(hours=hour)
            o = override_map.get(hour)
            p = plan_map.get(hour)
            
            # Default values
            default_power = p.target_power_mw if p else 0.0
            default_price = day_prices[hour]
            
            schedule.append({
                "hour": hour,
                "timestamp": dt_hour.isoformat() + "Z",
                "power_mw": o.power_mw if o else default_power,
                "price_uah": o.price_uah if o else default_price,
                "is_overridden": o is not None
            })
            
        return {
            "asset_id": asset_id,
            "date": date,
            "overrides": schedule
        }
    finally:
        db.close()

@router.post("/manual-overrides", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def save_manual_overrides(req: SaveOverridesRequest):
    db = SessionLocal()
    try:
        target_date = datetime.datetime.strptime(req.date, '%Y-%m-%d').date()
        dt_start = datetime.datetime.combine(target_date, datetime.time.min)
        dt_end = datetime.datetime.combine(target_date, datetime.time.max)
        
        # 1. Delete existing overrides for this day
        db.query(ManualOverride).filter(
            ManualOverride.asset_id == req.asset_id,
            ManualOverride.timestamp >= dt_start,
            ManualOverride.timestamp <= dt_end
        ).delete()
        
        # 2. Insert new overrides
        for item in req.overrides:
            timestamp = dt_start + datetime.timedelta(hours=item.hour)
            override = ManualOverride(
                timestamp=timestamp,
                asset_id=req.asset_id,
                power_mw=item.power_mw,
                price_uah=item.price_uah
            )
            db.add(override)
            
        db.commit()
        
        # 3. Clear/Invalidate C-level cache file for this asset so it recalculates instantly!
        import os
        cache_path = os.path.join(settings.DATA_DIR, f"executive_cache_{req.asset_id}.json")
        if os.path.exists(cache_path):
            try:
                import json
                with open(cache_path, "r") as f:
                    cached_days = json.load(f)
                
                # Delete this date's cached profit
                date_key = req.date
                if date_key in cached_days:
                    del cached_days[date_key]
                    
                with open(cache_path, "w") as f:
                    json.dump(cached_days, f)
            except Exception:
                try:
                    os.remove(cache_path)
                except:
                    pass
                    
        return {
            "status": "success",
            "message": f"Manual overrides saved successfully for date {req.date}."
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error saving overrides: {str(e)}")
    finally:
        db.close()

class SystemSettingsModel(BaseModel):
    launch_date: str
    osr: str
    voltage_class: int
    margin: float
    capacity_kw: float
    power_kw: float
    efficiency_pct: float

@router.get("/settings", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_system_settings():
    """
    launch_date/osr/voltage_class/margin — з JSON (не мають окремого поля в
    Asset). capacity_kw/power_kw/efficiency_pct — З ТАБЛИЦІ Asset, тієї самої,
    яку реально використовують optimization/run і scheduler для розрахунку
    графіка. Раніше ці два джерела були розсинхронізовані: форма показувала
    значення з JSON, а MILP рахував на зовсім інших числах з Asset — диспетчер
    бачив налаштування "4000 кВт-год", а графік будувався на застарілих 1000
    (реальний баг, знайдений і виправлений).
    """
    import json
    import os
    path = os.path.join(settings.DATA_DIR, "system_settings.json")

    data = {
        "launch_date": settings.BESS_LAUNCH_DATE,
        "osr": "dtek_kiev",
        "voltage_class": 1,
        "margin": 100.0,
    }

    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                saved = json.load(f)
                for key in ("launch_date", "osr", "voltage_class", "margin"):
                    if key in saved:
                        data[key] = saved[key]
        except Exception:
            pass

    db = SessionLocal()
    try:
        asset = db.query(Asset).first()
        if asset:
            data["capacity_kw"] = asset.capacity_mwh * 1000.0
            data["power_kw"] = asset.power_mw * 1000.0
            data["efficiency_pct"] = ((asset.efficiency_charge + asset.efficiency_discharge) / 2.0) * 100.0
        else:
            data["capacity_kw"] = 2000.0
            data["power_kw"] = 1000.0
            data["efficiency_pct"] = 95.0
    finally:
        db.close()

    return data

@router.post("/settings", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def save_system_settings(req: SystemSettingsModel):
    import json
    import os
    path = os.path.join(settings.DATA_DIR, "system_settings.json")

    db = SessionLocal()
    try:
        data = {
            "launch_date": req.launch_date,
            "osr": req.osr,
            "voltage_class": req.voltage_class,
            "margin": req.margin,
        }

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)

        # Технічні параметри батареї — пишемо в Asset, а не тільки в JSON:
        # саме звідси їх бере MILP-оптимізатор (optimization/run, scheduler).
        asset = db.query(Asset).first()
        if asset:
            asset.capacity_mwh = req.capacity_kw / 1000.0
            asset.power_mw = req.power_kw / 1000.0
            eff = req.efficiency_pct / 100.0
            asset.efficiency_charge = eff
            asset.efficiency_discharge = eff
            db.commit()

        # Invalidate executive cache file
        cache_dir = settings.DATA_DIR
        for file in os.listdir(cache_dir):
            if file.startswith("executive_cache_"):
                try:
                    os.remove(os.path.join(cache_dir, file))
                except:
                    pass

        return {
            "status": "success",
            "message": "System settings saved successfully and cache cleared."
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error saving settings: {str(e)}")
    finally:
        db.close()
