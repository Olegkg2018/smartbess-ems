import datetime
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler

import src.modules.market_data_service.data_manager as dm
import src.modules.forecast_service.ml_pipeline as mt
import src.modules.optimization_service.milp_model as opt
from src.database.session import SessionLocal
from src.database.models import Asset, PriceForecast, ChargeDischargePlan

def run_daily_forecast_and_optimization():
    print(f"[{datetime.datetime.now()}] Background Scheduler: Starting daily forecast and optimization job...")
    db = SessionLocal()
    try:
        # Determine target date: tomorrow
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        tomorrow_str = tomorrow.strftime('%Y-%m-%d')
        print(f"Target date for optimization: {tomorrow_str}")
        
        # 1. Sync real-time data
        dm.sync_realtime_data(force=True)
        
        # 2. Load weather forecast
        weather_forecast = dm.fetch_weather_forecast()
        
        # 3. Get last 168 hours of prices for lags
        df_hist = pd.read_csv(dm.MERGED_DATA_PATH)
        df_hist['Datetime'] = pd.to_datetime(df_hist['Datetime'])
        target_dt_start = pd.to_datetime(tomorrow_str)
        hist_before_target = df_hist[df_hist['Datetime'] < target_dt_start].sort_values('Datetime')
        
        if len(hist_before_target) >= 168:
            last_prices = hist_before_target['Price'].iloc[-168:].tolist()
        else:
            last_prices = df_hist['Price'].iloc[:168].tolist()
            
        # 4. Run prediction (using LightGBM)
        factors = {
            'Gas_Price': 35.0,
            'Nuclear_Outage': 0.15,
            'Solar_Strike': 0.0,
            'Market_Coeff': 1.0,
            'VDR_Volume': 1.0,
            'Grid_Import_Export': 0.0
        }
        prediction_results = mt.predict_next_day(tomorrow_str, weather_forecast, last_prices, factors)
        predicted_prices = prediction_results['lightgbm']
        
        # 5. Run battery optimization
        asset = db.query(Asset).first()
        if not asset:
            print("Warning: No BESS asset found in database. Skipping optimization.")
            return
            
        battery_params = {
            'battery_capacity': asset.capacity_mwh * 1000.0, # convert MW to kW
            'max_charge_power': asset.power_mw * 1000.0,
            'max_discharge_power': asset.power_mw * 1000.0,
            'charge_efficiency': asset.efficiency_charge,
            'discharge_efficiency': asset.efficiency_discharge,
            'initial_soc': 0.20, # Start at 20% SoC
            'min_soc': asset.min_soc_pct / 100.0,
            'max_soc': asset.max_soc_pct / 100.0,
            'max_cycles_per_day': 1.5,
            'degradation_cost': asset.deg_cost_per_mwh / 1000.0,
            'transmission_tariff': 528.57,
            'distribution_tariff': 1500.0,
            'dispatch_tariff': 104.57,
            'supplier_margin': 100.0,
            'mode': 'arbitrage'
        }
        
        optimization_results = opt.optimize_battery_schedule(predicted_prices, **battery_params)
        
        # 6. Persist results in DB
        for t in range(24):
            forecast_time = target_dt_start + datetime.timedelta(hours=t)
            
            # Save forecast
            db.query(PriceForecast).filter(
                PriceForecast.timestamp == forecast_time,
                PriceForecast.forecast_run_at == target_dt_start
            ).delete()
            
            pf = PriceForecast(
                timestamp=forecast_time,
                forecast_run_at=target_dt_start,
                model_version='lightgbm',
                predicted_price_uah=predicted_prices[t]
            )
            db.add(pf)
            
            # Save Plan
            db.query(ChargeDischargePlan).filter(
                ChargeDischargePlan.timestamp == forecast_time,
                ChargeDischargePlan.asset_id == asset.id,
                ChargeDischargePlan.optimized_run_at == target_dt_start
            ).delete()
            
            sched_item = optimization_results['schedule'][t]
            target_power = sched_item['power_kw']
            soc_kwh = sched_item['soc_kwh']
            
            plan_entry = ChargeDischargePlan(
                timestamp=forecast_time,
                asset_id=asset.id,
                optimized_run_at=target_dt_start,
                target_power_mw=target_power / 1000.0,
                expected_soc_mwh=soc_kwh / 1000.0,
                expected_profit_uah=sched_item['hourly_p_l_uah']
            )
            db.add(plan_entry)
            
        db.commit()
        print(f"[{datetime.datetime.now()}] Background Scheduler: Successfully completed daily forecast and BESS optimization plan.")
    except Exception as e:
        db.rollback()
        print(f"Error in background scheduler job: {e}")
    finally:
        db.close()

scheduler = BackgroundScheduler()

def start_scheduler():
    if not scheduler.running:
        # Run daily at 17:30 (5:30 PM) which is when Operator Rynka publishes tomorrow's RDN prices
        scheduler.add_job(run_daily_forecast_and_optimization, 'cron', hour=17, minute=30, id='daily_bess_opt')
        scheduler.start()
        print("Background Scheduler started successfully.")

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        print("Background Scheduler shut down.")
