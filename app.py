import os
import json
import datetime
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from src.core.security import RoleChecker
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

import src.modules.market_data_service.data_manager as dm
import src.modules.forecast_service.ml_pipeline as mt
from src.database.session import SessionLocal
from src.database.init_db import init_db
from src.database.models import Organization, Asset, PriceForecast, ChargeDischargePlan

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

def _serve_index() -> HTMLResponse:
    react_index = os.path.join(os.path.dirname(__file__), "frontend", "dist", "index.html")
    if os.path.exists(react_index):
        with open(react_index, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)

    index_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="Index template not found")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), status_code=200)

@app.get("/", response_class=HTMLResponse)
async def read_index():
    return _serve_index()

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

# SPA catch-all — МАЄ бути останнім зареєстрованим маршрутом. React Router
# (Фаза 2) працює на client-side маршрутах типу /dispatcher/asset,
# /director/executive тощо — без цього прямий перехід за URL або F5 на такому
# шляху повертав 404, бо FastAPI не мав під нього жодного route (працювала
# лише навігація через клік у самому додатку). Виявлено й виправлено під час
# перевірки в реальному Docker-деплої.
@app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
async def spa_catch_all(full_path: str):
    if full_path.startswith("api/") or full_path.startswith("assets/"):
        raise HTTPException(status_code=404, detail="Not found")
    return _serve_index()

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=5000)
