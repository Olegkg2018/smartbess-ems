import time
import threading
import math
from pymodbus.server import StartAsyncTcpServer
from pymodbus.pdu.device import ModbusDeviceIdentification
from pymodbus.simulator import SimData, SimDevice, DataType
import asyncio

from src.database.session import SessionLocal
from src.database.models import Asset, BessTelemetry

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


def _load_last_telemetry():
    """Останній реальний запис BessTelemetry (записується scada_service.py
    щоразу після успішного опитування) — точка відновлення для симулятора
    після перезапуску процесу (2026-08-26, знайдено користувачем: рестарт
    контейнера — напр. під час деплою — скидав SoC на захардкоджені 20%,
    хоча реальна батарея фізично НЕ втрачає заряд/знос через перезапуск
    керуючого софту). None, якщо телеметрії ще жодного разу не було
    (справжній холодний старт — тоді і лишається дефолт 20%/100%)."""
    db = SessionLocal()
    try:
        asset = db.query(Asset).first()
        if not asset:
            return None
        return (
            db.query(BessTelemetry)
            .filter(BessTelemetry.asset_id == asset.id)
            .order_by(BessTelemetry.timestamp.desc())
            .first()
        )
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
    last_tel = _load_last_telemetry()
    if last_tel is not None:
        soc_kwh = max(0.0, min(CAPACITY_KWH, last_tel.current_soc_mwh * 1000.0))
        soh = last_tel.soh_pct if last_tel.soh_pct is not None else 100.0
        temp = last_tel.battery_temp_c if last_tel.battery_temp_c is not None else AMBIENT_TEMP
        print(f"SCADA: Resuming simulated battery from last known telemetry — SoC={soc_kwh:.1f} kWh, SoH={soh:.2f}%, temp={temp:.1f}°C (real battery does not reset on a software restart).")
    else:
        soc_kwh = CAPACITY_KWH * 0.20  # справжній холодний старт — телеметрії ще не було
        soh = 100.0
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

            # 2026-08-26: знайдено користувачем — тепловий баланс раніше НЕ
            # масштабувався на `dt` (на відміну від SoC/деградації вище) і
            # коефіцієнт брав СИРІ кВт, а не частку від номінальної
            # потужності активу — при 125-250 кВт (як у первинній
            # перевірці п.40, 35 секунд спостереження) це виглядало
            # правдоподібно, але для реального продового активу 1 МВт
            # (VPS) давало приріст ~7.5°C НА СЕКУНДУ — за хвилину сталого
            # заряду температура "вигадано" залітала за 300°C. Тепер:
            # heating масштабовано часткою від номінальної потужності
            # активу (`MAX_POWER_KW`, різна для кожного деплою — 250 кВт
            # локально, 1 МВт на проді) так, щоб рівновага (heating=
            # cooling) при 100% сталої потужності виходила на реалістичні
            # +40°C над ambient, а не залежала від абсолютних кВт. Стала
            # часу охолодження ~20 хв (K_COOLING=3.0/год) — досить швидко,
            # щоб рух температури лишався видимим за кілька хвилин
            # спостереження, досить повільно, щоб не зімітувати миттєвий
            # перегрів.
            EQUILIBRIUM_RISE_AT_FULL_POWER_C = 40.0
            K_COOLING_PER_HOUR = 3.0
            K_HEATING_PER_HOUR = K_COOLING_PER_HOUR * EQUILIBRIUM_RISE_AT_FULL_POWER_C
            max_loss_kw = MAX_POWER_KW * (1.0 - EFFICIENCY)
            loss = abs(current_power) * (1.0 - EFFICIENCY)
            loss_fraction = (loss / max_loss_kw) if max_loss_kw > 0 else 0.0
            heating_rate = loss_fraction * K_HEATING_PER_HOUR
            cooling_rate = (temp - AMBIENT_TEMP) * K_COOLING_PER_HOUR
            temp += (heating_rate - cooling_rate) * dt

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
    # Внутрішня адреса нашого власного симулятора — завжди 127.0.0.1:5020
    # (не з Settings: 2026-08-26, реальні host/port тепер стосуються лише
    # РЕАЛЬНОЇ батареї, див. scada_service.py::load_bess_connection_settings).
    host, port = '127.0.0.1', 5020
    print(f"SCADA: Starting Modbus TCP Server on {host}:{port}...")
    await StartAsyncTcpServer(context=device, address=(host, port))

def run_simulator_process():
    t = threading.Thread(target=run_physical_simulation, daemon=True)
    t.start()
    asyncio.run(start_modbus_server())

if __name__ == "__main__":
    run_simulator_process()
