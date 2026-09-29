"""
Unit-тести профілів обладнання BESS (src/modules/scada_service/device_profiles.py).
Фейковий Modbus-клієнт: регістри заповнено так, як їх віддав би пристрій за
офіційною специфікацією виробника; перевіряється декодування телеметрії і
ЩО саме пишеться на команду. Транспорт і реальне обладнання НЕ перевіряються.

Запуск: pytest test_device_profiles.py
"""
import pytest

from src.modules.scada_service.device_profiles import (
    GenericSmartBessProfile, HuaweiSun2000Luna2000Profile, SungrowShHybridProfile,
    ProfileError, get_profile, list_profiles, DEFAULT_PROFILE,
)


class _Res:
    def __init__(self, registers=None, error=False):
        self.registers = registers or []
        self._error = error

    def isError(self):
        return self._error


class FakeClient:
    def __init__(self, holding=None, inputs=None):
        self.holding = dict(holding or {})
        self.inputs = dict(inputs or {})
        self.writes = []  # (address, [values])

    def _read(self, table, address, count):
        if any(address + i not in table for i in range(count)):
            return _Res(error=True)
        return _Res([table[address + i] for i in range(count)])

    def read_holding_registers(self, address, count=1, device_id=1):
        return self._read(self.holding, address, count)

    def read_input_registers(self, address, count=1, device_id=1):
        return self._read(self.inputs, address, count)

    def write_register(self, address, value, device_id=1):
        assert 0 <= value <= 0xFFFF
        self.writes.append((address, [value]))
        self.holding[address] = value
        return _Res()

    def write_registers(self, address, values, device_id=1):
        self.writes.append((address, list(values)))
        for i, v in enumerate(values):
            self.holding[address + i] = v
        return _Res()


def _u32(v):
    v &= 0xFFFFFFFF
    return [(v >> 16) & 0xFFFF, v & 0xFFFF]


# ---------------- generic (симулятор) ----------------

def test_generic_read_and_write_matches_legacy_map():
    c = FakeClient(holding={0: 2, 1: 553, 2: 65536 - 250, 3: 312, 4: 998, 5: 0})
    t = GenericSmartBessProfile().read(c, 1)
    assert (t.soc_pct, t.power_kw, t.temp_c, t.soh_pct, t.status) == (55.3, -250.0, 31.2, 99.8, 'DISCHARGING')
    GenericSmartBessProfile().write_power(c, 1, -125)
    assert c.writes == [(5, [65536 - 125])]


# ---------------- Huawei ----------------

def _huawei_regs(soc_x10, status, power_w, temp_x10):
    regs = {37760: soc_x10, 37762: status, 37022: temp_x10 & 0xFFFF}
    hi, lo = _u32(power_w)
    regs[37765], regs[37766] = hi, lo
    return regs


def test_huawei_read_charging_sign_convention():
    # Huawei: 37765 > 0 — заряд. Наша конвенція: < 0 — заряд.
    t = HuaweiSun2000Luna2000Profile().read(FakeClient(holding=_huawei_regs(456, 2, 250_000, 285)), 1)
    assert t.soc_pct == 45.6 and t.power_kw == -250.0 and t.temp_c == 28.5
    assert t.status == 'CHARGING' and t.soh_pct is None


def test_huawei_read_discharging_negative_temp_and_fault():
    t = HuaweiSun2000Luna2000Profile().read(FakeClient(holding=_huawei_regs(900, 2, -1_000_000, -35)), 1)
    assert t.power_kw == 1000.0 and t.status == 'DISCHARGING' and t.temp_c == -3.5
    t = HuaweiSun2000Luna2000Profile().read(FakeClient(holding=_huawei_regs(500, 3, 0, 250)), 1)
    assert t.status == 'FAULT'


def test_huawei_discharge_command_sequence():
    c = FakeClient()
    HuaweiSun2000Luna2000Profile().write_power(c, 1, 800)
    assert c.writes == [
        (47246, [0]),                   # режим: тривалість
        (47083, [5]),                   # період 5 хв (страховка, якщо EMS зависне)
        (47249, _u32(800_000)),         # потужність розряду, Вт (U32, gain 1000 від кВт)
        (47100, [2]),                   # розряд
    ]


def test_huawei_charge_and_stop_and_refresh_throttle():
    p = HuaweiSun2000Luna2000Profile()
    c = FakeClient()
    p.write_power(c, 1, -500)
    assert (47247, _u32(500_000)) in c.writes and c.writes[-1] == (47100, [1])
    n = len(c.writes)
    p.write_power(c, 1, -500)          # та сама команда в межах REFRESH_S — не переподаємо
    assert len(c.writes) == n
    p.write_power(c, 1, 0)
    assert c.writes[-1] == (47100, [0])


# ---------------- Sungrow ----------------

def _sungrow_inputs(flow_bits, power_w, soc_x10, soh_x10, temp_x10, state=0x4000):
    doc = {13000: state, 13001: flow_bits, 13022: power_w, 13023: soc_x10, 13024: soh_x10,
           13025: temp_x10 & 0xFFFF}
    regs = {n - 1: 0 for n in range(13000, 13026)}
    regs.update({n - 1: v for n, v in doc.items()})  # адреса протоколу = номер − 1
    return regs


def test_sungrow_read_uses_input_registers_with_offset():
    t = SungrowShHybridProfile().read(FakeClient(inputs=_sungrow_inputs(0b100, 4200, 612, 981, 254)), 1)
    assert (t.soc_pct, t.power_kw, t.soh_pct, t.temp_c, t.status) == (61.2, 4.2, 98.1, 25.4, 'DISCHARGING')
    t = SungrowShHybridProfile().read(FakeClient(inputs=_sungrow_inputs(0b10, 3000, 400, 990, -50)), 1)
    assert t.power_kw == -3.0 and t.status == 'CHARGING' and t.temp_c == -5.0
    t = SungrowShHybridProfile().read(FakeClient(inputs=_sungrow_inputs(0, 0, 400, 990, 200)), 1)
    assert t.power_kw == 0.0 and t.status == 'STANDBY'


def test_sungrow_read_fails_on_holding_only_device():
    # Якщо по помилці читати holding — пристрій поверне помилку; профіль має
    # явно кинути ProfileError, а не вигадати нулі.
    with pytest.raises(ProfileError):
        SungrowShHybridProfile().read(FakeClient(holding={}), 1)


def test_sungrow_commands():
    c = FakeClient()
    SungrowShHybridProfile().write_power(c, 1, 5)
    assert c.writes == [(13049, [2]), (13050, [0xBB]), (13051, [5000])]
    c = FakeClient()
    SungrowShHybridProfile().write_power(c, 1, -3)
    assert c.writes == [(13049, [2]), (13050, [0xAA]), (13051, [3000])]
    c = FakeClient()
    SungrowShHybridProfile().write_power(c, 1, 0)
    assert c.writes == [(13049, [2]), (13050, [0xCC])]


def test_sungrow_refuses_power_above_u16_watts():
    with pytest.raises(ProfileError):
        SungrowShHybridProfile().write_power(FakeClient(), 1, 1000)


# ---------------- реєстр ----------------

def test_registry_and_fallback():
    keys = {p['key'] for p in list_profiles()}
    assert keys == {'generic_smartbess', 'huawei_sun2000_luna2000', 'sungrow_sh_hybrid'}
    assert get_profile('does_not_exist').key == DEFAULT_PROFILE
