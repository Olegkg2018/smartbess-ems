import os
import json
import datetime
import pandas as pd
import numpy as np
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from src.core.security import RoleChecker
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager

import src.modules.market_data_service.data_manager as dm
import src.modules.forecast_service.ml_pipeline as mt
import src.modules.optimization_service.milp_model as opt
from src.database.session import SessionLocal, get_db
from src.database.init_db import init_db
from src.database.models import Organization, Asset, MarketPrice, PriceForecast, BessTelemetry, ChargeDischargePlan

from src.tasks.scheduler import start_scheduler, shutdown_scheduler
from src.modules.scada_service.scada_service import start_scada_service, stop_scada_service

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize DB tables
    init_db()
    
    # 2. Seed default Organization and Asset if database is empty
    db = SessionLocal()
    try:
        org = db.query(Organization).first()
        if not org:
            org = Organization(name="SmartBESS Demo Organization", country="UA")
            db.add(org)
            db.commit()
            db.refresh(org)
            print(f"Created default organization: {org.name} ({org.id})")
            
        asset = db.query(Asset).first()
        if not asset:
            asset = Asset(
                organization_id=org.id,
                name="BESS Unit 1 (Primary)",
                capacity_mwh=1.0,  # 1000 kWh = 1 MWh
                power_mw=0.25,     # 250 kW = 0.25 MW
                efficiency_charge=0.95,
                efficiency_discharge=0.95,
                min_soc_pct=10.0,
                max_soc_pct=90.0,
                deg_cost_per_mwh=1200.0 # 1.20 UAH/kWh = 1200 UAH/MWh
            )
            db.add(asset)
            db.commit()
            db.refresh(asset)
            print(f"Created default BESS asset: {asset.name} ({asset.id})")
    except Exception as e:
        print(f"Error seeding default database records: {e}")
    finally:
        db.close()
        
    # 3. Start the daily background scheduler
    start_scheduler()
    
    # 4. Start the Modbus BESS simulator
    import threading
    from src.modules.scada_service.bess_simulator import run_simulator_process
    t_sim = threading.Thread(target=run_simulator_process, daemon=True)
    t_sim.start()
    print("FastAPI Lifespan: Started BESS Modbus TCP simulator.")
    
    # 5. Start the SCADA telemetry and control service
    start_scada_service()
    
    yield

    # 5. Shutdown services on app stop
    shutdown_scheduler()
    stop_scada_service()

app = FastAPI(title="SmartBESS Energy Arbitrage Platform", lifespan=lifespan)

from src.api.v1.api import api_router
app.include_router(api_router, prefix="/api/v1")

# Serve React static assets if built
react_dist_dir = os.path.join(os.path.dirname(__file__), "frontend", "dist")
if os.path.exists(react_dist_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(react_dist_dir, "assets")), name="assets")

# Ensure data directory exists
os.makedirs(dm.DATA_DIR, exist_ok=True)

class ForecastRequest(BaseModel):
    date: str
    lat: Optional[float] = dm.LAT
    lon: Optional[float] = dm.LON
    openweather_api_key: Optional[str] = None
    selected_model: Optional[str] = "lightgbm"
    
    # Market factors
    gas_price: Optional[float] = 35.0
    nuclear_outage: Optional[float] = 0.15
    solar_strike: Optional[float] = 0.0
    market_coeff: Optional[float] = 1.0
    vdr_volume: Optional[float] = 1.0
    grid_import_export: Optional[float] = 0.0
    
    # Battery parameters
    battery_capacity: Optional[float] = 1000.0
    max_charge_power: Optional[float] = 250.0
    max_discharge_power: Optional[float] = 250.0
    charge_efficiency: Optional[float] = 95.0
    discharge_efficiency: Optional[float] = 95.0
    initial_soc: Optional[float] = 20.0
    min_soc: Optional[float] = 10.0
    max_soc: Optional[float] = 90.0
    max_cycles_per_day: Optional[float] = 1.5
    degradation_cost: Optional[float] = 1.20
    transmission_tariff: Optional[float] = 528.57
    distribution_tariff: Optional[float] = 1500.0
    dispatch_tariff: Optional[float] = 104.57
    supplier_margin: Optional[float] = 100.0
    mode: Optional[str] = "arbitrage"

@app.get("/", response_class=HTMLResponse)
async def read_index():
    react_index = os.path.join(os.path.dirname(__file__), "frontend", "dist", "index.html")
    if os.path.exists(react_index):
        with open(react_index, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
            
    index_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="Index template not found")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), status_code=200)

@app.get("/api/metrics", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_metrics():
    metrics_path = os.path.join(dm.DATA_DIR, "metrics_report.json")
    if os.path.exists(metrics_path):
        with open(metrics_path, "r") as f:
            return json.load(f)
    return JSONResponse(status_code=404, content={"error": "Metrics not available yet. Model must be trained."})

@app.post("/api/retrain", dependencies=[Depends(RoleChecker(["Manager", "Admin"]))])
async def retrain():
    try:
        metrics = mt.train_models()
        return {
            "success": True,
            "message": "Model retrained successfully!",
            "metrics": metrics
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={
            "success": False,
            "message": f"Error during training: {str(e)}"
        })

@app.get("/api/db_status", dependencies=[Depends(RoleChecker(["Admin", "Manager"]))])
async def db_status():
    try:
        report = dm.verify_data_completeness()
        
        # Add database table record counts to the report
        db = SessionLocal()
        try:
            report['details']['db_price_forecasts_count'] = db.query(PriceForecast).count()
            report['details']['db_bess_plans_count'] = db.query(ChargeDischargePlan).count()
            report['details']['db_assets_count'] = db.query(Asset).count()
            report['details']['db_organizations_count'] = db.query(Organization).count()
        except Exception as e:
            print(f"Error querying database record counts: {e}")
        finally:
            db.close()
            
        return report
    except Exception as e:
        return JSONResponse(status_code=500, content={
            "status": "ERROR",
            "errors": [f"Ошибка сервера: {str(e)}"],
            "warnings": [],
            "details": {}
        })

@app.post("/api/forecast", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def forecast(req: ForecastRequest):
    try:
        # Extract inputs
        target_date_str = req.date
        lat = req.lat
        lon = req.lon
        api_key = req.openweather_api_key or dm.OPENWEATHER_KEY
        
        # Market factors
        factors = {
            'Gas_Price': req.gas_price,
            'Nuclear_Outage': req.nuclear_outage,
            'Solar_Strike': req.solar_strike,
            'Market_Coeff': req.market_coeff,
            'VDR_Volume': req.vdr_volume,
            'Grid_Import_Export': req.grid_import_export
        }
        
        # Battery options
        battery_params = {
            'battery_capacity': req.battery_capacity,
            'max_charge_power': req.max_charge_power,
            'max_discharge_power': req.max_discharge_power,
            'charge_efficiency': req.charge_efficiency / 100.0,
            'discharge_efficiency': req.discharge_efficiency / 100.0,
            'initial_soc': req.initial_soc / 100.0,
            'min_soc': req.min_soc / 100.0,
            'max_soc': req.max_soc / 100.0,
            'max_cycles_per_day': req.max_cycles_per_day,
            'degradation_cost': req.degradation_cost,
            'transmission_tariff': req.transmission_tariff,
            'distribution_tariff': req.distribution_tariff,
            'dispatch_tariff': req.dispatch_tariff,
            'supplier_margin': req.supplier_margin,
            'mode': req.mode
        }
        
        target_date = datetime.datetime.strptime(target_date_str, '%Y-%m-%d').date()
        
        # Real-time synchronization
        dm.sync_realtime_data()
        
        # Make sure merged data exists
        if not os.path.exists(dm.MERGED_DATA_PATH):
            print("Merged historical data not found. Building...")
            dm.get_combined_historical_data()
            
        df_hist = pd.read_csv(dm.MERGED_DATA_PATH)
        df_hist['Datetime'] = pd.to_datetime(df_hist['Datetime'])
        
        # Verify database completeness
        data_status = dm.verify_data_completeness()
        
        # Check if we have actual data for target date
        day_mask = df_hist['Datetime'].dt.date == target_date
        df_day = df_hist[day_mask].sort_values('Datetime')
        is_historical = len(df_day) == 24
        
        # 1. Weather forecast for target date
        if is_historical:
            weather_forecast = pd.DataFrame({
                'Temperature': df_day['Temperature'].values,
                'Cloud_Cover': df_day['Cloud_Cover'].values,
                'Wind_Speed': df_day['Wind_Speed'].values,
                'Shortwave_Radiation': df_day['Shortwave_Radiation'].values
            })
            actual_prices = df_day['Price'].tolist()
            print(f"Loading historical prices and weather for {target_date_str}")
        else:
            print(f"Fetching weather forecast for {target_date_str}")
            weather_forecast = dm.fetch_weather_forecast(lat, lon, api_key)
            actual_prices = None
            try:
                df_prices = dm.fetch_oree_prices_for_month(target_date.month, target_date.year)
                if not df_prices.empty:
                    df_prices['Datetime'] = pd.to_datetime(df_prices['Datetime'])
                    day_mask_prices = df_prices['Datetime'].dt.date == target_date
                    df_day_prices = df_prices[day_mask_prices].sort_values('Datetime')
                    if len(df_day_prices) == 24:
                        actual_prices = df_day_prices['Price'].tolist()
                        print(f"Loaded actual prices for tomorrow/target date {target_date_str}")
            except Exception as e:
                print(f"Error loading actual prices: {e}")
                
        # 2. Get past prices for lags
        target_dt_start = pd.to_datetime(target_date)
        hist_before_target = df_hist[df_hist['Datetime'] < target_dt_start].sort_values('Datetime')
        
        if len(hist_before_target) >= 168:
            last_prices = hist_before_target['Price'].iloc[-168:].tolist()
        else:
            print("Warning: Insufficient history. Using default padding.")
            last_prices = df_hist['Price'].iloc[:168].tolist()
            
        # 3. Predict prices using all models (LightGBM, XGBoost, MLP)
        prediction_results = mt.predict_next_day(target_date, weather_forecast, last_prices, factors)
        
        selected_model = req.selected_model
        # Use LightGBM if not specified or unrecognized
        if selected_model not in ['lightgbm', 'xgboost', 'mlp']:
            selected_model = 'lightgbm'
            
        predicted_prices = prediction_results[selected_model]
        
        # 4. Run optimization
        optimization_results = opt.optimize_battery_schedule(predicted_prices, **battery_params)
        
        # Run detailed scenarios and risk analysis (base, pessimistic, aggressive, VaR)
        scenarios_analysis = opt.optimize_with_scenarios_and_risks(predicted_prices, **battery_params)
        
        # Run optimization on actual prices if available
        actual_optimization = None
        if actual_prices:
            actual_optimization = opt.optimize_battery_schedule(actual_prices, **battery_params)
            
        # 5. Save results to Database (Persistence Layer)
        db = SessionLocal()
        try:
            asset = db.query(Asset).first()
            if asset:
                for t in range(24):
                    forecast_time = target_dt_start + datetime.timedelta(hours=t)
                    
                    # Store Price Forecast
                    db.query(PriceForecast).filter(
                        PriceForecast.timestamp == forecast_time,
                        PriceForecast.forecast_run_at == target_dt_start
                    ).delete()
                    
                    pf = PriceForecast(
                        timestamp=forecast_time,
                        forecast_run_at=target_dt_start,
                        model_version=selected_model,
                        predicted_price_uah=float(prediction_results[selected_model][t])
                    )
                    db.add(pf)
                    
                    # Store Charge/Discharge Plan
                    db.query(ChargeDischargePlan).filter(
                        ChargeDischargePlan.timestamp == forecast_time,
                        ChargeDischargePlan.asset_id == asset.id,
                        ChargeDischargePlan.optimized_run_at == target_dt_start
                    ).delete()
                    
                    charge_val = optimization_results['charge'][t]
                    discharge_val = optimization_results['discharge'][t]
                    target_power = -charge_val if charge_val > 0 else discharge_val
                    
                    plan_entry = ChargeDischargePlan(
                        timestamp=forecast_time,
                        asset_id=asset.id,
                        optimized_run_at=target_dt_start,
                        target_power_mw=float(target_power / 1000.0), # convert kW to MW
                        expected_soc_mwh=float(optimization_results['soc'][t+1] / 1000.0), # convert kWh to MWh
                        expected_profit_uah=float(optimization_results['net_profit_uah'] / 24.0) # hourly share
                    )
                    db.add(plan_entry)
                    
                    # Store Bess Telemetry if it is historical data
                    if is_historical:
                        db.query(BessTelemetry).filter(
                            BessTelemetry.timestamp == forecast_time,
                            BessTelemetry.asset_id == asset.id
                        ).delete()
                        
                        tel = BessTelemetry(
                            timestamp=forecast_time,
                            asset_id=asset.id,
                            current_soc_mwh=float(optimization_results['soc'][t+1] / 1000.0),
                            current_power_mw=float(target_power / 1000.0),
                            battery_temp_c=float(25.0 + 5.0 * np.sin(np.pi * (t - 6) / 12) if 6 <= t <= 18 else 20.0),
                            soh_pct=float(100.0 - (optimization_results['cycles_used'] * 0.005)), # mock degradation drift
                            system_status="NORMAL"
                        )
                        db.add(tel)
                        
                db.commit()
                print("Forecast and optimization schedule saved to database successfully.")
        except Exception as dbe:
            db.rollback()
            print(f"Error saving results to database: {dbe}")
        finally:
            db.close()
            
        # 6. Explanations
        surplus_hours = []
        high_price_hours = []
        explanation_bullets = []
        
        surplus_hours_idx = []
        for h in range(24):
            model_p = prediction_results[selected_model][h]
            if model_p <= 15.0:
                surplus_hours.append(h + 1)
                surplus_hours_idx.append(h)
            elif model_p >= 6000.0:
                high_price_hours.append(h + 1)
                
        if surplus_hours:
            explanation_bullets.append(
                f"**Енергетичний профіцит** очікується в годинах: {', '.join(map(str, surplus_hours))}. "
                f"У цей період прогнозується падіння ціни до **10 грн/МВт-год**. Основні фактори: "
                f"висока сонячна радіація (макс. {weather_forecast['Shortwave_Radiation'].max():.1f} Вт/м²), "
                f"низька хмарність ({weather_forecast['Cloud_Cover'].iloc[surplus_hours_idx[0]]:.1f}%) та низьке споживання (вихідний день або низький ринковий коефіцієнт)."
            )
        else:
            explanation_bullets.append(
                "**Профіцит енергії (ціна 10 грн) не очікується.** Для його виникнення необхідні: "
                "сонячна радіація > 500 Вт/м², хмарність < 25%, відсутність пошкоджень СЕС та низьке базове споживання."
            )
            
        if high_price_hours:
            explanation_bullets.append(
                f"**Пікові ціни (вище 6000 грн)** прогнозуються в годинах: {', '.join(map(str, high_price_hours))}. "
                f"Це пов'язано з високою вартістю теплової генерації (газ TTF = {factors['Gas_Price']} EUR/MWh) та високим дефіцитом потужності (частка виведених АЕС = {factors['Nuclear_Outage']*100:.1f}%)."
            )
        else:
            max_p_hour = np.argmax(prediction_results[selected_model])
            max_p = np.max(prediction_results[selected_model])
            explanation_bullets.append(
                f"Максимальна ціна прогнозується в {max_p_hour + 1} годині на рівні **{max_p:.2f} грн/МВт-год**. "
                f"Вона обумовлена вечірнім піком споживання та цінами на газ."
            )
            
        if factors['Solar_Strike'] > 0.0:
            explanation_bullets.append(
                f"**Фактор прильотів по СЕС ({factors['Solar_Strike']*100:.0f}%):** Знижує сонячную генерацію в системі. "
                "Це зменшує ймовірність денного профіциту та утримує ціни вище мінімуму."
            )
            
        if factors['Nuclear_Outage'] > 0.30:
            explanation_bullets.append(
                f"**Критичний дефіцит АЕС ({factors['Nuclear_Outage']*100:.0f}%):** Виведення базових блоків "
                "призводить до зростання цін у всі години доби."
            )
            
        actual_factors = None
        if is_historical:
            actual_factors = {
                'gas_price': float(df_day['Gas_Price'].mean()),
                'nuclear_outage': float(df_day['Nuclear_Outage'].mean() * 100.0),
                'solar_strike': float(df_day['Solar_Strike'].mean() * 100.0),
                'market_coeff': float(df_day['Market_Coeff'].mean()),
                'vdr_volume': float(df_day['VDR_Volume'].mean()),
                'grid_import_export': float(df_day['Grid_Import_Export'].mean())
            }
            
        return {
            'date': target_date_str,
            'is_historical': is_historical,
            'actual_factors': actual_factors,
            'data_status': data_status,
            'weather': {
                'hours': list(range(24)),
                'temp': weather_forecast['Temperature'].tolist(),
                'clouds': weather_forecast['Cloud_Cover'].tolist(),
                'wind': weather_forecast['Wind_Speed'].tolist(),
                'radiation': weather_forecast['Shortwave_Radiation'].tolist(),
            },
            'forecast': {
                'hours': prediction_results['hours'],
                'lightgbm': prediction_results['lightgbm'],
                'xgboost': prediction_results['xgboost'],
                'mlp': prediction_results['mlp'],
                'actual': actual_prices
            },
            'optimization': optimization_results,
            'actual_optimization': actual_optimization,
            'scenarios_analysis': scenarios_analysis,
            'explanations': explanation_bullets
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=5000)
