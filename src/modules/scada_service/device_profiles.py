"""
Профілі пристроїв BESS для SCADA-клієнта (2026-09-29, CLAUDE.md п.60).

Кожен профіль знає, ЯК прочитати телеметрію й ЯК віддати команду потужності
конкретному типу обладнання по Modbus. Єдина конвенція назовні:
  power_kw > 0 — РОЗРЯД (віддача в мережу), < 0 — ЗАРЯД
(та сама, що в ChargeDischargePlan.target_power_mw / BessTelemetry).

Адреси взято ЛИШЕ з офіційних документів виробників (не з пам'яті й не з
AGPL-бібліотеки huawei-solar-lib — див. MEMORY.md §9):
  - Huawei: "Solar Inverter Modbus Interface Definitions", Issue 05, 2023-02-16
    (SUN2000 + LUNA2000). Адреси в документі = адреси протоколу (0-based).
  - Sungrow: "Communication Protocol of Residential Hybrid Inverter" V1.1.9,
    2025-06-20 (SH-серія). У документі номер регістра = адреса + 1 (приклад
    з самого документа: регістр 13000 → запит 0x32C7 = 12999), телеметрія —
    input registers (функція 04), керування — holding (06/16).

ВАЖЛИВО: профілі перевірені лише на рівні кодування/декодування (unit-тести
з фейковим клієнтом, test_device_profiles.py), НЕ на реальному обладнанні.
Промислові системи (Huawei LUNA2000-200 через SmartLogger, Sungrow
PowerTitan/ST) мають інші, здебільшого непублічні протоколи — для них
потрібен окремий профіль за документацією, отриманою від постачальника.
"""
from dataclasses import dataclass
import time


@dataclass
class Telemetry:
    soc_pct: float
    power_kw: float           # > 0 розряд, < 0 заряд
    temp_c: float | None
    soh_pct: float | None
    status: str               # STANDBY / CHARGING / DISCHARGING / FAULT / OFFLINE / UNKNOWN


class ProfileError(Exception):
    pass


def _check(res, what):
    if res is None or res.isError():
        raise ProfileError(f"{what}: {res}")
    return res


def _u32(hi, lo):
    return (hi << 16) | lo


def _i32(hi, lo):
    v = _u32(hi, lo)
    return v - (1 << 32) if v & 0x80000000 else v


def _i16(v):
    return v - 65536 if v > 32767 else v


def _u32_words(v):
    v = int(v) & 0xFFFFFFFF
    return [(v >> 16) & 0xFFFF, v & 0xFFFF]


class DeviceProfile:
    key = ''
    label = ''
    source = ''
    notes = ''

    def read(self, client, unit_id) -> Telemetry:
        raise NotImplementedError

    def write_power(self, client, unit_id, target_kw):
        raise NotImplementedError

    def describe(self):
        return {'key': self.key, 'label': self.label, 'source': self.source, 'notes': self.notes}


class GenericSmartBessProfile(DeviceProfile):
    """Власна 6-регістрова карта симулятора (bess_simulator.py) — поведінка
    до 2026-09-29 без змін: holding 0..5 = [стан, SoC×10, потужність кВт (i16),
    темп.×10, SoH×10, команда кВт (i16, запис у регістр 5)]."""
    key = 'generic_smartbess'
    label = 'SmartBESS (симулятор / власна 6-регістрова карта)'
    source = 'bess_simulator.py'
    notes = 'Дефолт. Для реального обладнання потрібен контролер, що реалізує цю карту.'
    STATES = {0: 'STANDBY', 1: 'CHARGING', 2: 'DISCHARGING', 3: 'FAULT'}

    def read(self, client, unit_id):
        r = _check(client.read_holding_registers(0, count=6, device_id=unit_id), 'generic read').registers
        return Telemetry(soc_pct=r[1] / 10.0, power_kw=float(_i16(r[2])), temp_c=r[3] / 10.0,
                         soh_pct=r[4] / 10.0, status=self.STATES.get(r[0], 'UNKNOWN'))

    def write_power(self, client, unit_id, target_kw):
        v = int(round(target_kw))
        _check(client.write_register(5, v + 65536 if v < 0 else v, device_id=unit_id), 'generic write')


class HuaweiSun2000Luna2000Profile(DeviceProfile):
    """Huawei SUN2000 + LUNA2000 (Solar Inverter Modbus Interface Definitions,
    Issue 05, 2023-02-16):
      37760 [ESS] SOC                    RO U16  %   gain 10
      37762 [ESS] Running status         RO U16  0 offline/1 standby/2 running/3 fault/4 sleep
      37765 [ESS] Charge/Discharge power RO I32  W   gain 1   (>0 заряд, <0 розряд)
      37022 [ESS unit 1] Battery temp.   RO I16  °C  gain 10
      47100 [ESS] Forcible charge/disch. RW U16  0 stop/1 charge/2 discharge
      47246 [ESS] Forcible setting mode  RW U16  0 duration
      47083 [ESS] Forcible period        RW U16  хв [0,1440], "value is not stored"
      47247 [ESS] Forcible charge power  RW U32  kW gain 1000 (тобто сире значення = Вт)
      47249 [ESS] Forcible disch. power  RW U32  kW gain 1000
    SoH у цьому документі для LUNA2000 немає — повертаємо None (не вигадуємо).
    """
    key = 'huawei_sun2000_luna2000'
    label = 'Huawei SUN2000 + LUNA2000 (Modbus TCP/RTU)'
    source = 'Huawei Solar Inverter Modbus Interface Definitions, Issue 05 (2023-02-16)'
    notes = ('Потрібно: Modbus TCP увімкнено на інверторі/SDongle, доступ на запис (на частині прошивок — '
             'installer-логін), режим роботи батареї, що дозволяє примусовий заряд/розряд. Не для '
             'LUNA2000-200 через SmartLogger. Не перевірено на реальному обладнанні.')
    STATUS = {0: 'OFFLINE', 1: 'STANDBY', 2: 'RUNNING', 3: 'FAULT', 4: 'STANDBY'}
    # Команда живе FORCIBLE_PERIOD_MIN хв і переподається не рідше ніж раз на
    # REFRESH_S — якщо EMS зависне, батарея сама зупиниться після періоду.
    FORCIBLE_PERIOD_MIN = 5
    REFRESH_S = 60

    def __init__(self):
        self._last_cmd = None
        self._last_sent = 0.0

    def read(self, client, unit_id):
        soc = _check(client.read_holding_registers(37760, count=1, device_id=unit_id), 'huawei soc').registers[0] / 10.0
        st = _check(client.read_holding_registers(37762, count=1, device_id=unit_id), 'huawei status').registers[0]
        p = _check(client.read_holding_registers(37765, count=2, device_id=unit_id), 'huawei power').registers
        t = _check(client.read_holding_registers(37022, count=1, device_id=unit_id), 'huawei temp').registers[0]
        power_w = _i32(p[0], p[1])
        power_kw = -power_w / 1000.0  # Huawei: >0 заряд → наша конвенція: >0 розряд
        status = self.STATUS.get(st, 'UNKNOWN')
        if status == 'RUNNING':
            status = 'CHARGING' if power_kw < 0 else 'DISCHARGING' if power_kw > 0 else 'STANDBY'
        return Telemetry(soc_pct=soc, power_kw=power_kw, temp_c=_i16(t) / 10.0, soh_pct=None, status=status)

    def write_power(self, client, unit_id, target_kw):
        cmd = int(round(target_kw))
        if cmd == self._last_cmd and time.time() - self._last_sent < self.REFRESH_S:
            return
        if cmd == 0:
            _check(client.write_register(47100, 0, device_id=unit_id), 'huawei stop')
        else:
            watts = abs(cmd) * 1000
            _check(client.write_register(47246, 0, device_id=unit_id), 'huawei mode')
            _check(client.write_register(47083, self.FORCIBLE_PERIOD_MIN, device_id=unit_id), 'huawei period')
            power_reg = 47249 if cmd > 0 else 47247
            _check(client.write_registers(power_reg, _u32_words(watts), device_id=unit_id), 'huawei power')
            _check(client.write_register(47100, 2 if cmd > 0 else 1, device_id=unit_id), 'huawei command')
        self._last_cmd = cmd
        self._last_sent = time.time()


class SungrowShHybridProfile(DeviceProfile):
    """Sungrow SH-серія (Communication Protocol of Residential Hybrid Inverter
    V1.1.9, 2025-06-20). Номери з документа; адреса протоколу = номер − 1.
    Телеметрія — input registers (fc 04):
      13000 Running state      U16 (0x4000 = External EMS mode)
      13001 Power flow status  U16 bit1 = заряд батареї, bit2 = розряд
      13022 Battery power      U16 W (модуль; напрям — з 13001)
      13023 Battery SOC        U16 0.1%
      13024 Battery SOH        U16 0.1%
      13025 Battery temp.      S16 0.1°C
    Керування — holding (fc 06):
      13050 EMS mode selection        2 = Compulsory (Forced) mode
      13051 Charge/discharge command  0xAA заряд / 0xBB розряд / 0xCC стоп
      13052 Charge/discharge power    U16 W, 0..100% номінальної потужності BDC
    """
    key = 'sungrow_sh_hybrid'
    label = 'Sungrow SH-серія (гібридний інвертор, Modbus TCP/RTU)'
    source = 'Sungrow Communication Protocol of Residential Hybrid Inverter V1.1.9 (2025-06-20)'
    notes = ('Потужність команди обмежена номінальною потужністю BDC (регістр 5628) і SOC-межами '
             '13058/13059 самого інвертора. Не для PowerTitan/ST (промисловий протокол непублічний). '
             'Не перевірено на реальному обладнанні.')
    CHARGE, DISCHARGE, STOP = 0xAA, 0xBB, 0xCC

    @staticmethod
    def _addr(register_no):
        return register_no - 1

    def read(self, client, unit_id):
        r = _check(client.read_input_registers(self._addr(13000), count=26, device_id=unit_id), 'sungrow read').registers
        reg = lambda n: r[n - 13000]
        flow = reg(13001)
        magnitude_kw = reg(13022) / 1000.0
        if flow & 0b100:
            power_kw, status = magnitude_kw, 'DISCHARGING'
        elif flow & 0b10:
            power_kw, status = -magnitude_kw, 'CHARGING'
        else:
            power_kw, status = 0.0, 'STANDBY'
        return Telemetry(soc_pct=reg(13023) / 10.0, power_kw=power_kw, temp_c=_i16(reg(13025)) / 10.0,
                         soh_pct=reg(13024) / 10.0, status=status)

    def write_power(self, client, unit_id, target_kw):
        cmd = int(round(target_kw))
        _check(client.write_register(self._addr(13050), 2, device_id=unit_id), 'sungrow ems mode')
        if cmd == 0:
            _check(client.write_register(self._addr(13051), self.STOP, device_id=unit_id), 'sungrow stop')
            return
        _check(client.write_register(self._addr(13051), self.DISCHARGE if cmd > 0 else self.CHARGE,
                                     device_id=unit_id), 'sungrow command')
        watts = abs(cmd) * 1000
        if watts > 0xFFFF:
            # U16 у Вт — максимум 65.5 кВт. Мовчки обрізати команду не можна
            # (батарея робила б не те, що в плані) — явна помилка.
            raise ProfileError(f"sungrow: {abs(cmd)} кВт не вміщується в U16 Вт (макс. 65 кВт) — "
                               f"цей профіль для SH-серії, не для промислових систем")
        _check(client.write_register(self._addr(13052), watts, device_id=unit_id), 'sungrow power')


PROFILES = {p.key: p for p in (GenericSmartBessProfile, HuaweiSun2000Luna2000Profile, SungrowShHybridProfile)}
DEFAULT_PROFILE = GenericSmartBessProfile.key


def get_profile(key):
    return PROFILES.get(key, PROFILES[DEFAULT_PROFILE])()


def list_profiles():
    return [cls().describe() for cls in PROFILES.values()]
