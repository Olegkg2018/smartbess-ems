import datetime
import time
import threading
from pymodbus.client import ModbusTcpClient
from sqlalchemy.orm import Session

from src.database.session import SessionLocal
from src.database.models import Asset, BessTelemetry, ChargeDischargePlan

# SCADA configuration
BESS_IP = "127.0.0.1"
BESS_PORT = 5020

scada_thread = None
stop_flag = False

def poll_bess_and_control():
    print("SCADA: Starting EMS control loop...")
    client = ModbusTcpClient(BESS_IP, port=BESS_PORT)
    
    while not stop_flag:
        db = SessionLocal()
        try:
            connected = client.connect()
            if not connected:
                print(f"SCADA Error: Could not connect to BESS at {BESS_IP}:{BESS_PORT}")
                time.sleep(10.0)
                continue
                
            res = client.read_holding_registers(0, count=6)
            if res.isError():
                print(f"SCADA Error: Failed to read BESS registers: {res}")
                client.close()
                time.sleep(10.0)
                continue
                
            state = res.registers[0]
            soc_raw = res.registers[1]
            power_raw = res.registers[2]
            temp_raw = res.registers[3]
            soh_raw = res.registers[4]
            
            soc_pct = soc_raw / 10.0
            if power_raw > 32767:
                power_kw = power_raw - 65536
            else:
                power_kw = power_raw
            temp_c = temp_raw / 10.0
            soh_pct = soh_raw / 10.0
            
            states_map = {0: "STANDBY", 1: "CHARGING", 2: "DISCHARGING", 3: "FAULT"}
            system_status = states_map.get(state, "UNKNOWN")
            
            asset = db.query(Asset).first()
            if not asset:
                print("SCADA: No asset found in database, skipping telemetry write.")
                client.close()
                time.sleep(10.0)
                continue
                
            now_dt = datetime.datetime.utcnow().replace(second=0, microsecond=0)
            db.query(BessTelemetry).filter(
                BessTelemetry.timestamp == now_dt,
                BessTelemetry.asset_id == asset.id
            ).delete()
            
            tel = BessTelemetry(
                timestamp=now_dt,
                asset_id=asset.id,
                current_soc_mwh=(soc_pct / 100.0) * asset.capacity_mwh,
                current_power_mw=power_kw / 1000.0,
                battery_temp_c=temp_c,
                soh_pct=soh_pct,
                system_status=system_status
            )
            db.add(tel)
            db.commit()
            
            current_hour = datetime.datetime.now().replace(minute=0, second=0, microsecond=0)
            plan = db.query(ChargeDischargePlan).filter(
                ChargeDischargePlan.asset_id == asset.id,
                ChargeDischargePlan.timestamp == current_hour
            ).order_by(ChargeDischargePlan.optimized_run_at.desc()).first()
            
            target_power_kw = 0
            if plan:
                target_power_kw = int(plan.target_power_mw * 1000.0)
                print(f"SCADA: Found optimization plan for hour {current_hour.hour}:00. Action power command = {target_power_kw} kW.")
            else:
                print(f"SCADA Warning: No optimization plan found for current hour {current_hour.hour}:00. Setting BESS to Standby.")
                
            cmd_val = target_power_kw
            if cmd_val < 0:
                cmd_val += 65536
            client.write_register(5, cmd_val)
        except Exception as e:
            if db:
                db.rollback()
            print(f"SCADA control loop error: {e}")
        finally:
            if db:
                db.close()
        time.sleep(10.0)
        
    client.close()
    print("SCADA: EMS control loop stopped.")

def start_scada_service():
    global scada_thread, stop_flag
    stop_flag = False
    if scada_thread is None or not scada_thread.is_alive():
        scada_thread = threading.Thread(target=poll_bess_and_control, daemon=True)
        scada_thread.start()
        print("SCADA: EMS client thread started successfully.")

def stop_scada_service():
    global stop_flag
    stop_flag = True
    print("SCADA: Requesting shutdown of EMS client thread...")
