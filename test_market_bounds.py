"""
Тести ринкових меж ціни й поведінки при від'ємних цінах (CLAUDE.md п.61):
src/core/market_bounds.py, bidding_service.compute_bid_price/clamp,
optimize_battery_schedule на добі з від'ємними цінами.

Запуск: pytest test_market_bounds.py
"""
import json

import pytest

import src.core.market_bounds as mb
import src.modules.bidding_service.services as bs
from src.modules.optimization_service.milp_model import optimize_battery_schedule


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    monkeypatch.setattr(mb.settings, 'DATA_DIR', str(tmp_path))
    mb._CACHE.clear()

    def write(data):
        (tmp_path / 'system_settings.json').write_text(json.dumps(data))
        mb._CACHE.clear()
    return write


def test_defaults_match_real_market(settings_file):
    settings_file({})
    assert mb.get_market_price_bounds() == (10.0, 15000.0)


def test_negative_floor_allowed_and_invalid_pair_ignored(settings_file):
    settings_file({'market_price_floor_uah': -5000, 'market_price_cap_uah': 50000})
    assert mb.get_market_price_bounds() == (-5000.0, 50000.0)
    settings_file({'market_price_floor_uah': 20000, 'market_price_cap_uah': 15000})
    assert mb.get_market_price_bounds() == (10.0, 15000.0)


def test_bid_clamp_uses_settings(settings_file):
    settings_file({})
    assert bs.clamp_bid_price_to_oree_bounds(16500.0) == (15000.0, True)
    assert bs.clamp_bid_price_to_oree_bounds(-300.0) == (10.0, True)
    settings_file({'market_price_floor_uah': -5000, 'market_price_cap_uah': 50000})
    assert bs.clamp_bid_price_to_oree_bounds(-300.0) == (-300.0, False)


def test_percent_margin_positive_prices_unchanged():
    assert bs.compute_bid_price(4000.0, 'buy', 2.0) == (4080.0, 2.0)
    assert bs.compute_bid_price(4000.0, 'sell', 2.0) == (3920.0, 2.0)


def test_percent_margin_negative_forecast_moves_in_right_direction():
    # Купівля має подаватись ВИЩЕ прогнозу (більше шансів виконатись),
    # продаж — НИЖЧЕ, незалежно від знака ціни.
    buy, _ = bs.compute_bid_price(-1000.0, 'buy', 10.0)
    sell, _ = bs.compute_bid_price(-1000.0, 'sell', 10.0)
    assert buy == -900.0 and sell == -1100.0


def test_absolute_margin_has_priority():
    assert bs.compute_bid_price(-500.0, 'buy', 2.0, margin_uah=1000.0) == (500.0, 200.0)


def test_milp_charges_during_negative_prices_and_earns():
    # Денний профіцит з від'ємною ціною, вечірній пік — батарея має заряджатись
    # саме в години від'ємної ціни (за заряд доплачують) і розряджатись увечері.
    prices = [3000.0] * 24
    for h in range(10, 15):
        prices[h] = -2000.0
    for h in range(18, 22):
        prices[h] = 8000.0
    res = optimize_battery_schedule(
        prices=prices, battery_capacity=4000.0, max_charge_power=1000.0, max_discharge_power=1000.0,
        charge_efficiency=0.95, discharge_efficiency=0.95, initial_soc=0.1, min_soc=0.1, max_soc=0.9,
        max_cycles_per_day=1.5, degradation_cost=0.7, transmission_tariff=0.0, distribution_tariff=0.0,
        dispatch_tariff=0.0, supplier_margin=0.0,
    )
    assert res['status'] == 'Optimal'
    charge = res['charge']
    assert sum(charge[h] for h in range(10, 15)) > 0.9 * sum(charge)
    assert sum(res['discharge'][h] for h in range(18, 22)) > 0
    # Дохід від розряду ввечері ≤ 3.2 МВт·год × 8000 = 25 600; прибуток вищий
    # за це лише тому, що за заряд у від'ємні години ще й доплатили.
    assert res['net_profit_uah'] > 25600
