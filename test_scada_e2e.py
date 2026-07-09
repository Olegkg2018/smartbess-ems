import os
import sys
import time
import asyncio
import subprocess

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.database.session import SessionLocal
from src.database.models import BessTelemetry, ChargeDischargePlan

async def run_e2e_test():
    print("--------------------------------------------------")
    print("SmartBESS Closed-Loop E2E Integration Test")
    print("--------------------------------------------------")
    
    # 1. Start the Modbus TCP BESS Simulator in the background
    print("Starting Modbus TCP BESS Simulator...")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.dirname(os.path.abspath(__file__))
    sim_process = subprocess.Popen(
        [sys.executable, "src/modules/scada_service/bess_simulator.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env
    )
    time.sleep(3.0) # Wait for simulator to start up
    
    if sim_process.poll() is not None:
        print("Error: Simulator failed to start. Output:")
        out, err = sim_process.communicate()
        print(f"STDOUT: {out}\nSTDERR: {err}")
        return
        
    print("Simulator started successfully (PID:", sim_process.pid, ")")
    
    # 2. Start the app lifecycle (which starts the DB, Scheduler, and SCADA Service)
    # We will trigger the FastAPI lifespan manually using ASGI/lifespan or just call the init logic
    print("Initializing Database tables and SCADA client service...")
    from src.database.init_db import init_db
    init_db()
    
    # Seed default BESS asset and a dummy charge plan for the current hour so we have a target command
    from src.database.session import SessionLocal
    from src.database.models import Organization, Asset, ChargeDischargePlan
    db = SessionLocal()
    
    org = db.query(Organization).first()
    if not org:
        org = Organization(name="SmartBESS Demo Organization", country="UA")
        db.add(org)
        db.commit()
        db.refresh(org)
        
    asset = db.query(Asset).first()
    if not asset:
        asset = Asset(
            organization_id=org.id,
            name="BESS Unit 1 (Primary)",
            capacity_mwh=1.0,
            power_mw=0.25,
            efficiency_charge=0.95,
            efficiency_discharge=0.95,
            min_soc_pct=10.0,
            max_soc_pct=90.0,
            deg_cost_per_mwh=1200.0
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        
    # Create a dummy plan for the current hour (e.g., -150 kW charge command)
    current_hour = datetime.datetime.now().replace(minute=0, second=0, microsecond=0)
    db.query(ChargeDischargePlan).filter(
        ChargeDischargePlan.asset_id == asset.id,
        ChargeDischargePlan.timestamp == current_hour
    ).delete()
    
    dummy_plan = ChargeDischargePlan(
        timestamp=current_hour,
        asset_id=asset.id,
        optimized_run_at=datetime.datetime.now(),
        target_power_mw=-0.150, # -150 kW charge command
        expected_soc_mwh=0.350,
        expected_profit_uah=150.0
    )
    db.add(dummy_plan)
    db.commit()
    db.close()
    
    print(f"Created target charge command of -150 kW for the current hour ({current_hour.hour}:00).")
    
    # 3. Start SCADA Service
    print("Starting SCADA client control loop...")
    from src.modules.scada_service.scada_service import start_scada_service, stop_scada_service
    start_scada_service()
    
    # 4. Wait for 25 seconds (allowing 2-3 poll cycles)
    print("Control loop running, waiting 25 seconds for Modbus registers to update...")
    time.sleep(25.0)
    
    # 5. Stop SCADA Service
    stop_scada_service()
    
    # 6. Verify database telemetry records
    db = SessionLocal()
    try:
        telemetry_records = db.query(BessTelemetry).order_by(BessTelemetry.timestamp.desc()).limit(5).all()
        print("\n--- Verifying Persisted Telemetry in DB ---")
        if telemetry_records:
            print(f"Found {len(telemetry_records)} telemetry records in DB:")
            for r in telemetry_records:
                print(f"Time: {r.timestamp} | SoC: {r.current_soc_mwh*1000:.1f} kWh | Power: {r.current_power_mw*1000:.1f} kW | Temp: {r.battery_temp_c:.1f}°C | SOH: {r.soh_pct:.2f}% | Status: {r.system_status}")
        else:
            print("Error: No BESS telemetry records found in database!")
            
    finally:
        db.close()
        
    # 7. Terminate Modbus simulator process
    print("\nShutting down BESS simulator process...")
    sim_process.terminate()
    sim_process.wait()
    print("Simulator process exited. E2E test finished.")

if __name__ == "__main__":
    import datetime
    asyncio.run(run_e2e_test())
