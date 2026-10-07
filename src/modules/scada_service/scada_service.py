import datetime
import json
import os
import time
import threading
from pymodbus.client import ModbusTcpClient, ModbusSerialClient
from sqlalchemy.orm import Session

from src.core.config import settings
from src.database.session import SessionLocal
from src.database.models import Asset, BessTelemetry, ChargeDischargePlan, ManualOverride, MarketBid
from src.modules.scada_service.device_profiles import DEFAULT_PROFILE, PROFILES, ProfileError, get_profile

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
    'device_profile': DEFAULT_PROFILE,
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
    'device_profile': 'bess_device_profile',
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
    if cfg['device_profile'] not in PROFILES:
        cfg['device_profile'] = DEFAULT_PROFILE
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


def _bid_not_executed(db, asset_id, hour_utc) -> bool:
    bid = db.query(MarketBid).filter(MarketBid.asset_id == asset_id, MarketBid.timestamp == hour_utc).first()
    return bid is not None and bid.bid_type in ('buy', 'sell') and bid.executed is False


def poll_bess_and_control():
    cfg = load_bess_connection_settings()
    unit_id = cfg['unit_id']
    client, target_desc = _build_client(cfg)
    # Симулятор завжди говорить власною 6-регістровою картою — профіль
    # обладнання застосовується лише до реального підключення (tcp/serial).
    profile_key = DEFAULT_PROFILE if cfg['connection_type'] == 'simulator' else cfg['device_profile']
    profile = get_profile(profile_key)
    print(f"SCADA: Starting EMS control loop (target {target_desc}, unit_id={unit_id}, profile={profile.key})...")

    # 2026-09-28: цикл іде кожні 10с — друкуємо рішення лише при ЗМІНІ
    # (година/джерело/потужність), а не 8640 разів на добу (було ~5300
    # однакових "No optimization plan" в лозі за добу).
    last_decision = None
    while not stop_flag:
        db = SessionLocal()
        try:
            connected = client.connect()
            if not connected:
                print(f"SCADA Error: Could not connect to BESS at {target_desc}")
                time.sleep(10.0)
                continue

            # Профіль пристрою (2026-09-29, device_profiles.py) — як читати
            # телеметрію й віддавати команду конкретному обладнанню.
            try:
                tel_in = profile.read(client, unit_id)
            except ProfileError as e:
                print(f"SCADA Error: Failed to read BESS registers ({profile.key}): {e}")
                client.close()
                time.sleep(10.0)
                continue

            soc_pct = tel_in.soc_pct
            power_kw = tel_in.power_kw
            temp_c = tel_in.temp_c
            soh_pct = tel_in.soh_pct
            system_status = tel_in.status
            
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
                decision = ('override', current_hour, target_power_kw)
                msg = f"SCADA: Manual override for hour {current_hour.hour}:00. Action power command = {target_power_kw} kW."
            elif plan and _bid_not_executed(db, asset.id, current_hour):
                # Заявку РДН на цю годину не виконано — енергію не куплено/не
                # продано, фізичний заряд/розряд створив би небаланс. Якщо
                # диспетчер домовився на ВДР — він ставить ручну команду вище.
                decision = ('bid_not_executed', current_hour, 0)
                msg = f"SCADA: DAM bid for hour {current_hour.hour}:00 not executed — standby instead of plan {int(plan.target_power_mw * 1000.0)} kW."
            elif plan:
                target_power_kw = int(plan.target_power_mw * 1000.0)
                decision = ('plan', current_hour, target_power_kw)
                msg = f"SCADA: Found optimization plan for hour {current_hour.hour}:00. Action power command = {target_power_kw} kW."
            else:
                decision = ('none', current_hour, 0)
                msg = f"SCADA Warning: No optimization plan found for current hour {current_hour.hour}:00. Setting BESS to Standby."
            if decision != last_decision:
                print(msg)
                last_decision = decision
                
            profile.write_power(client, unit_id, target_power_kw)
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
