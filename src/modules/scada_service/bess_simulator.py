import time
import threading
import math
from pymodbus.server import StartAsyncTcpServer
from pymodbus.pdu.device import ModbusDeviceIdentification
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusDeviceContext, ModbusServerContext
import asyncio

from src.core.config import settings
from src.database.session import SessionLocal
from src.database.models import Asset

# Фолбек-значення лише якщо в БД ще немає жодного Asset (холодний старт до
# сідування в app.py lifespan) — у проді відразу перезаписується реальними
# capacity_mwh/power_mw/min_soc_pct/max_soc_pct з Asset, щоб симулятор не
# розходився з тим, що реально налаштовано в Settings і що використовує MILP
# (раніше тут були захардкожені 1000 кВт·год/250 кВт — фізична симуляція
# мовчки обрізала будь-яку команду понад 250 кВт навіть коли реальний Asset
# налаштований на 1000 кВт, і SoC-траєкторія розходилась з планом MILP).
CAPACITY_KWH = 1000.0
MAX_POWER_KW = 250.0
MIN_SOC_FRACTION = 0.10
MAX_SOC_FRACTION = 0.90
EFFICIENCY = 0.95
AMBIENT_TEMP = 20.0 # °C

def _load_asset_limits():
    global CAPACITY_KWH, MAX_POWER_KW, MIN_SOC_FRACTION, MAX_SOC_FRACTION
    db = SessionLocal()
    try:
        asset = db.query(Asset).first()
        if asset:
            CAPACITY_KWH = asset.capacity_mwh * 1000.0
            MAX_POWER_KW = asset.power_mw * 1000.0
            MIN_SOC_FRACTION = asset.min_soc_pct / 100.0
            MAX_SOC_FRACTION = asset.max_soc_pct / 100.0
    finally:
        db.close()

# Datastore block (holding registers, 6 registers starting at address 1)
block = ModbusSequentialDataBlock(1, [0, 200, 0, 200, 1000, 0])

def run_physical_simulation():
    print("SCADA: Starting battery physical simulation thread...")
    _load_asset_limits()
    soc_kwh = CAPACITY_KWH * 0.20  # старт на 20% реальної ємності Asset
    soh = 100.0      # 100%
    temp = AMBIENT_TEMP
    dt = 1.0 / 3600.0

    while True:
        try:
            values = block.simdata[0].values
            target_power_raw = values[5]
            if target_power_raw > 32767:
                target_power = target_power_raw - 65536
            else:
                target_power = target_power_raw

            target_power = max(-MAX_POWER_KW, min(MAX_POWER_KW, target_power))
            current_power = 0.0
            state = 0

            if target_power < 0:
                if soc_kwh >= CAPACITY_KWH * MAX_SOC_FRACTION:
                    soc_kwh = CAPACITY_KWH * MAX_SOC_FRACTION
                    current_power = 0.0
                    state = 0
                else:
                    current_power = target_power
                    soc_kwh += abs(current_power) * EFFICIENCY * dt
                    state = 1
            elif target_power > 0:
                if soc_kwh <= CAPACITY_KWH * MIN_SOC_FRACTION:
                    soc_kwh = CAPACITY_KWH * MIN_SOC_FRACTION
                    current_power = 0.0
                    state = 0
                else:
                    current_power = target_power
                    soc_kwh -= (current_power / EFFICIENCY) * dt
                    state = 2
            else:
                current_power = 0.0
                state = 0
                
            loss = abs(current_power) * (1.0 - EFFICIENCY)
            heating_rate = loss * 0.15
            cooling_rate = (temp - AMBIENT_TEMP) * 0.02
            temp += (heating_rate - cooling_rate) * 1.0
            
            if abs(current_power) > 0:
                throughput = abs(current_power) * dt
                degradation = (throughput / CAPACITY_KWH) * 0.0005
                soh = max(0.0, soh - degradation)
                
            soc_pct_reg = int((soc_kwh / CAPACITY_KWH) * 1000)
            power_reg = int(current_power)
            if power_reg < 0:
                power_reg += 65536
            temp_reg = int(temp * 10)
            soh_reg = int(soh * 10)
            
            block.simdata[0].values[:6] = [state, soc_pct_reg, power_reg, temp_reg, soh_reg, values[5]]
        except Exception as e:
            print(f"Error in BESS simulation step: {e}")
        time.sleep(1.0)

async def start_modbus_server():
    store = ModbusDeviceContext(hr=block, ir=block, co=block, di=block)
    context = ModbusServerContext(devices=store, single=True)
    
    identity = ModbusDeviceIdentification()
    identity.VendorName = 'SmartBESS'
    identity.ProductCode = 'SB-1000'
    identity.VendorUrl = 'https://github.com/Olegkg2018/ua-energy-arbitrage'
    identity.ProductName = 'BESS Simulator'
    identity.ModelName = 'SmartBESS 1.0'
    
    print("SCADA: Starting Modbus TCP Server on 127.0.0.1:5020...")
    await StartAsyncTcpServer(context=context, identity=identity, address=("127.0.0.1", 5020))

def run_simulator_process():
    t = threading.Thread(target=run_physical_simulation, daemon=True)
    t.start()
    asyncio.run(start_modbus_server())

if __name__ == "__main__":
    run_simulator_process()
