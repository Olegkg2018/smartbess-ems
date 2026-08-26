import time
import threading
import math
from pymodbus.server import StartAsyncTcpServer
from pymodbus.pdu.device import ModbusDeviceIdentification
from pymodbus.simulator import SimData, SimDevice, DataType
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

# Реєстри 0-5: [state, soc×10, power_kw (i16, unsigned wire repr), temp×10,
# soh×10, target_power_cmd]. Спільний стан між фізичним циклом (пише) і
# device_action (сервує клієнтам) — прості int-присвоєння в списку, GIL
# робить це безпечним для одного читача/одного писача без явного лока.
#
# 2026-08-26: перехід зі старого ModbusSequentialDataBlock +
# `block.simdata[0].values[:6] = [...]` на SimData/SimDevice + action —
# перший підхід у встановленій версії pymodbus (3.13.1, requirements.txt
# не фіксує версію) НЕ проганяв записані сервером значення до клієнта
# через мережу (deprecated API, реально мовчки не працює — перевірено
# прямим TCP-підключенням: клієнт завжди бачив початкові статичні
# значення, попри працюючий фізичний цикл). SimData/SimDevice —
# задокументований, реально перевірений робочий шлях у цій версії.
_regs = [0, 200, 0, 200, 1000, 0]


async def _device_action(function_code, start_address, address, count, current_registers, set_values):
    offset = address - start_address
    if set_values is not None:
        for i, v in enumerate(set_values):
            idx = offset + i
            if 0 <= idx < len(_regs):
                _regs[idx] = v
        return None
    for i in range(count):
        idx = offset + i
        if idx < len(_regs):
            current_registers[i] = _regs[idx]
    return None


def run_physical_simulation():
    print("SCADA: Starting battery physical simulation thread...")
    _load_asset_limits()
    soc_kwh = CAPACITY_KWH * 0.20  # старт на 20% реальної ємності Asset
    soh = 100.0      # 100%
    temp = AMBIENT_TEMP
    dt = 1.0 / 3600.0

    while True:
        try:
            target_power_raw = _regs[5]
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

            _regs[0] = state
            _regs[1] = soc_pct_reg
            _regs[2] = power_reg
            _regs[3] = temp_reg
            _regs[4] = soh_reg
            # _regs[5] (target_power_cmd) навмисно НЕ чіпаємо тут — це
            # WRITE-регістр клієнта, читаний вище через target_power_raw.
        except Exception as e:
            print(f"Error in BESS simulation step: {e}")
        time.sleep(1.0)


async def start_modbus_server():
    identity = ModbusDeviceIdentification()
    identity.VendorName = 'SmartBESS'
    identity.ProductCode = 'SB-1000'
    identity.VendorUrl = 'https://github.com/Olegkg2018/ua-energy-arbitrage'
    identity.ProductName = 'BESS Simulator'
    identity.ModelName = 'SmartBESS 1.0'

    device = SimDevice(
        id=1,
        simdata=[SimData(address=0, count=6, values=list(_regs), datatype=DataType.REGISTERS)],
        action=_device_action,
        identity=identity,
    )
    host = settings.BESS_MODBUS_HOST
    port = settings.BESS_MODBUS_PORT
    print(f"SCADA: Starting Modbus TCP Server on {host}:{port}...")
    await StartAsyncTcpServer(context=device, address=(host, port))

def run_simulator_process():
    t = threading.Thread(target=run_physical_simulation, daemon=True)
    t.start()
    asyncio.run(start_modbus_server())

if __name__ == "__main__":
    run_simulator_process()
