import datetime
import json
import os
import time
import threading
from pymodbus.client import ModbusTcpClient, ModbusSerialClient
from sqlalchemy.orm import Session

from src.core.config import settings
from src.database.session import SessionLocal
from src.database.models import Asset, BessTelemetry, ChargeDischargePlan, ManualOverride

scada_thread = None
stop_flag = False

# Дефолти дзеркалять DEFAULT_BESS_* у optimization.py — не імпортуємо звідти
# напряму, щоб не тягнути весь FastAPI-роутер у SCADA-модуль (той самий
# принцип ізоляції, що вже є для telegram_bot.py::_bid_reminder_enabled).
_BESS_CONNECTION_DEFAULTS = {
    'connection_type': 'simulator',
    'tcp_host': '127.0.0.1',
    'tcp_port': 502,
    'serial_port': '',
    'serial_baudrate': 9600,
    'serial_parity': 'N',
    'serial_stopbits': 1,
    'serial_bytesize': 8,
    'unit_id': 1,
}
_BESS_SETTINGS_KEY_MAP = {
    'connection_type': 'bess_connection_type',
    'tcp_host': 'bess_tcp_host',
    'tcp_port': 'bess_tcp_port',
    'serial_port': 'bess_serial_port',
    'serial_baudrate': 'bess_serial_baudrate',
    'serial_parity': 'bess_serial_parity',
    'serial_stopbits': 'bess_serial_stopbits',
    'serial_bytesize': 'bess_serial_bytesize',
    'unit_id': 'bess_modbus_unit_id',
}


def load_bess_connection_settings() -> dict:
    """Читає підключення реальної батареї з system_settings.json (той самий
    патерн, що telegram_bot.py::_bid_reminder_enabled/scheduler.py::
    _auto_dispatch_enabled) — 2026-08-26. Невідомий/відсутній
    connection_type чесно фолбечить на 'simulator' (не падає, не вигадує)."""
    cfg = dict(_BESS_CONNECTION_DEFAULTS)
    path = os.path.join(settings.DATA_DIR, "system_settings.json")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                saved = json.load(f)
            for local_key, json_key in _BESS_SETTINGS_KEY_MAP.items():
                if saved.get(json_key) is not None:
                    cfg[local_key] = saved[json_key]
        except Exception:
            pass
    if cfg['connection_type'] not in ('simulator', 'tcp', 'serial', 'disabled'):
        cfg['connection_type'] = 'simulator'
    return cfg


def _build_client(cfg: dict):
    if cfg['connection_type'] == 'serial':
        return ModbusSerialClient(
            cfg['serial_port'], baudrate=cfg['serial_baudrate'],
            parity=cfg['serial_parity'], stopbits=cfg['serial_stopbits'],
            bytesize=cfg['serial_bytesize'],
        ), f"serial:{cfg['serial_port']}@{cfg['serial_baudrate']}"
    if cfg['connection_type'] == 'tcp':
        return ModbusTcpClient(cfg['tcp_host'], port=cfg['tcp_port']), f"{cfg['tcp_host']}:{cfg['tcp_port']}"
    # 'simulator' (і фолбек для 'disabled', яке start_scada_service узагалі
    # не запускає — див. app.py) — наш власний симулятор, завжди 127.0.0.1:5020.
    return ModbusTcpClient('127.0.0.1', port=5020), '127.0.0.1:5020 (simulator)'


def poll_bess_and_control():
    cfg = load_bess_connection_settings()
    unit_id = cfg['unit_id']
    client, target_desc = _build_client(cfg)
    print(f"SCADA: Starting EMS control loop (target {target_desc}, unit_id={unit_id})...")

    while not stop_flag:
        db = SessionLocal()
        try:
            connected = client.connect()
            if not connected:
                print(f"SCADA Error: Could not connect to BESS at {target_desc}")
                time.sleep(10.0)
                continue

            res = client.read_holding_registers(0, count=6, device_id=unit_id)
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

            # Ручний оверрайд диспетчера (2026-08-26, "віртуальний диспетчер")
            # — раніше цей контур брав команду ЛИШЕ з ChargeDischargePlan,
            # ігноруючи ManualOverride (реальний, задокументований розрив,
            # CLAUDE.md п.19). Той самий пріоритет "оверрайд > план", що вже
            # перевірений в optimization.py::get_plans/manual-overrides.
            override = db.query(ManualOverride).filter(
                ManualOverride.asset_id == asset.id,
                ManualOverride.timestamp == current_hour,
            ).first()
            plan = db.query(ChargeDischargePlan).filter(
                ChargeDischargePlan.asset_id == asset.id,
                ChargeDischargePlan.timestamp == current_hour
            ).order_by(ChargeDischargePlan.optimized_run_at.desc()).first()

            target_power_kw = 0
            if override:
                target_power_kw = int(override.power_mw * 1000.0)
                print(f"SCADA: Manual override for hour {current_hour.hour}:00. Action power command = {target_power_kw} kW.")
            elif plan:
                target_power_kw = int(plan.target_power_mw * 1000.0)
                print(f"SCADA: Found optimization plan for hour {current_hour.hour}:00. Action power command = {target_power_kw} kW.")
            else:
                print(f"SCADA Warning: No optimization plan found for current hour {current_hour.hour}:00. Setting BESS to Standby.")
                
            cmd_val = target_power_kw
            if cmd_val < 0:
                cmd_val += 65536
            client.write_register(5, cmd_val, device_id=unit_id)
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
