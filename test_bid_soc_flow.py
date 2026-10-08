"""
Беззбиткова ціна заявки, SoC на початок доби з урахуванням невиконаних
заявок, SCADA-команда лише за виконаними заявками (CLAUDE.md п.62).

Запуск: pytest test_bid_soc_flow.py
"""
import datetime

import pytest

import src.modules.bidding_service.services as bs
import src.modules.scada_service.soc_state as ss
from src.modules.scada_service.scada_service import _bid_not_executed
from src.database.session import SessionLocal
from src.database.models import (Asset, MarketBid, MarketBidSocFeasibility, ChargeDischargePlan,
                                 InitialSocOverride, PriceForecast)
from src.core.time_utils import kyiv_to_utc, kyiv_day_bounds

DAY_PREV, DAY = '2099-03-10', '2099-03-11'


def test_breakeven_limits_formula():
    bids = [('buy', 1000, 500.0), ('buy', 1000, 700.0), ('sell', 1000, 9000.0), ('sell', 1000, 11000.0), ('standby', 0, 4000.0)]
    eta = 0.95 * 0.95
    buy_max, sell_min = bs.compute_breakeven_limits(bids, eta, 700.0)
    assert buy_max == pytest.approx(eta * (10000.0 - 700.0))
    assert sell_min == pytest.approx(600.0 / eta + 700.0)
    # Тариф на доставку зменшує граничну ціну купівлі й піднімає мінімум продажу.
    b2, s2 = bs.compute_breakeven_limits(bids, eta, 700.0, tariff_uah_per_mwh=1000.0)
    assert b2 == pytest.approx(buy_max - 1000.0) and s2 == pytest.approx(sell_min + 1000.0 / eta)


def test_breakeven_one_sided_day():
    # Лише продаж (енергія вже в батареї) — мінімум продажу = знос.
    _, sell_min = bs.compute_breakeven_limits([('sell', 1000, 9000.0)], 0.9, 700.0)
    assert sell_min == 700.0
    # Лише купівля (заряд на наступну добу) — граничної ціни нема, діє буфер.
    buy_max, _ = bs.compute_breakeven_limits([('buy', 1000, 500.0)], 0.9, 700.0)
    assert buy_max is None


@pytest.fixture
def db_asset():
    db = SessionLocal()
    asset = db.query(Asset).first()
    assert asset is not None
    days = [DAY_PREV, DAY]
    def clean():
        for d in days:
            s, e = kyiv_day_bounds(d)
            for model in (MarketBid, MarketBidSocFeasibility, ChargeDischargePlan):
                db.query(model).filter(model.asset_id == asset.id, model.timestamp >= s, model.timestamp < e).delete()
            db.query(InitialSocOverride).filter(InitialSocOverride.asset_id == asset.id,
                                                InitialSocOverride.date == kyiv_to_utc(d, 0)).delete()
            db.query(PriceForecast).filter(PriceForecast.forecast_run_at == kyiv_to_utc(d, 0)).delete()
        db.commit()
    clean()
    yield db, asset
    clean()
    db.close()


def _plan_day(db, asset, day, powers, end_soc_mwh):
    t0 = kyiv_to_utc(day, 0)
    for h, p in enumerate(powers):
        db.add(ChargeDischargePlan(timestamp=t0 + datetime.timedelta(hours=h), asset_id=asset.id, optimized_run_at=t0,
                                   target_power_mw=p, expected_soc_mwh=end_soc_mwh, expected_profit_uah=0.0))


def test_start_of_day_uses_realized_end_not_plan(db_asset):
    db, asset = db_asset
    _plan_day(db, asset, DAY_PREV, [0.0] * 24, asset.capacity_mwh * asset.min_soc_pct / 100.0)
    db.commit()
    frac, src = ss.get_start_of_day_soc(db, asset, DAY)
    assert src == 'calculated_previous_day'
    # Звірка попередньої доби: вечірній продаж не виконався — батарея лишилась на 3.6 МВт·год.
    s, e = kyiv_day_bounds(DAY_PREV)
    t = s
    while t < e:
        db.add(MarketBidSocFeasibility(timestamp=t, asset_id=asset.id, soc_feasible=True,
                                       soc_before_mwh=3.6, soc_after_mwh=3.6))
        t += datetime.timedelta(hours=1)
    db.commit()
    frac, src = ss.get_start_of_day_soc(db, asset, DAY)
    assert src == 'realized_previous_day' and frac == pytest.approx(min(3.6 / asset.capacity_mwh, asset.max_soc_pct / 100.0))
    # Ручне значення диспетчера має пріоритет.
    db.add(InitialSocOverride(date=kyiv_to_utc(DAY, 0), asset_id=asset.id, capacity_kwh=1000.0))
    db.commit()
    frac, src = ss.get_start_of_day_soc(db, asset, DAY)
    assert src == 'manual'


def test_partial_realized_day_is_ignored(db_asset):
    db, asset = db_asset
    s, _ = kyiv_day_bounds(DAY_PREV)
    db.add(MarketBidSocFeasibility(timestamp=s, asset_id=asset.id, soc_feasible=True, soc_before_mwh=1.0, soc_after_mwh=2.0))
    db.commit()
    assert ss.previous_day_realized_fraction(db, asset, kyiv_to_utc(DAY, 0)) is None


def test_generate_bids_breakeven(db_asset, monkeypatch):
    db, asset = db_asset
    monkeypatch.setattr(bs, 'get_bid_price_mode', lambda: 'breakeven')
    monkeypatch.setattr(bs, 'get_delivery_tariff_uah_per_mwh', lambda: 0.0)
    powers = [0.0] * 24
    for h in (12, 13):
        powers[h] = -1.0
    for h in (19, 20):
        powers[h] = 1.0
    _plan_day(db, asset, DAY, powers, 0.4)
    t0 = kyiv_to_utc(DAY, 0)
    prices = [3000.0] * 24
    prices[12], prices[13], prices[19], prices[20] = 400.0, 600.0, 9000.0, 11000.0
    for h, p in enumerate(prices):
        db.add(PriceForecast(timestamp=t0 + datetime.timedelta(hours=h), forecast_run_at=t0, model_version='test', predicted_price_uah=p))
    db.commit()
    res = bs.generate_bids_for_date(db, asset, t0, force_full_day=True)
    assert res['status'] == 'ok' and res['bid_price_mode'] == 'breakeven'
    eta = asset.efficiency_charge * asset.efficiency_discharge
    deg = asset.deg_cost_per_mwh
    rows = {b.timestamp: b for b in db.query(MarketBid).filter(MarketBid.asset_id == asset.id, MarketBid.timestamp >= t0,
                                                               MarketBid.timestamp < t0 + datetime.timedelta(hours=24))}
    buy = rows[t0 + datetime.timedelta(hours=12)]
    sell = rows[t0 + datetime.timedelta(hours=19)]
    standby = rows[t0 + datetime.timedelta(hours=5)]
    assert buy.bid_price_mode == 'breakeven' and buy.bid_price_uah == pytest.approx(eta * (10000.0 - deg))
    assert sell.bid_price_mode == 'breakeven' and sell.bid_price_uah == pytest.approx(500.0 / eta + deg)
    assert standby.bid_type == 'standby'


def test_scada_skips_unexecuted_bid(db_asset):
    db, asset = db_asset
    t = kyiv_to_utc(DAY, 19)
    db.add(MarketBid(timestamp=t, asset_id=asset.id, bid_type='sell', volume_kw=1000.0, forecast_price_uah=9000.0,
                     margin_pct=2.0, bid_price_uah=8800.0, executed=False))
    t2 = kyiv_to_utc(DAY, 20)
    db.add(MarketBid(timestamp=t2, asset_id=asset.id, bid_type='sell', volume_kw=1000.0, forecast_price_uah=9000.0,
                     margin_pct=2.0, bid_price_uah=8800.0, executed=True))
    db.commit()
    assert _bid_not_executed(db, asset.id, t) is True
    assert _bid_not_executed(db, asset.id, t2) is False
    assert _bid_not_executed(db, asset.id, kyiv_to_utc(DAY, 3)) is False


def test_soc_replay_tolerates_rounded_volumes(db_asset, monkeypatch):
    # Реальний випадок 2026-10-08: план довів SoC рівно до мінімуму, а через
    # округлений volume_kw реплей промахувався на ~3e-9 МВт·год і позначав
    # виконаний продаж "фізично неможливим", ламаючи й наступні купівлі.
    db, asset = db_asset
    t0 = kyiv_to_utc(DAY, 0)
    min_soc = asset.min_soc_pct / 100.0 * asset.capacity_mwh
    start = min_soc + 1.0 / asset.efficiency_discharge - 3.5e-9
    monkeypatch.setattr(bs, 'get_current_soc_fraction', lambda *a, **k: start / asset.capacity_mwh)
    bids = [MarketBid(timestamp=t0 + datetime.timedelta(hours=8), asset_id=asset.id, bid_type='sell', volume_kw=1000.0,
                      forecast_price_uah=7000.0, margin_pct=0.0, bid_price_uah=2000.0, executed=True),
            MarketBid(timestamp=t0 + datetime.timedelta(hours=9), asset_id=asset.id, bid_type='sell', volume_kw=1000.0,
                      forecast_price_uah=7000.0, margin_pct=0.0, bid_price_uah=2000.0, executed=True)]
    soc_map = bs._replay_soc_feasibility(db, asset, t0, bids)
    assert soc_map[bids[0].timestamp] is True
    # Справжня нестача заряду (батарея вже на мінімумі) як і раніше ловиться.
    assert soc_map[bids[1].timestamp] is False
    db.commit()


def test_band_bid_price_capped_by_breakeven():
    # Купівля за P90, але не вище беззбиткової межі; продаж за P10, але не нижче.
    assert bs.compute_band_bid_price('buy', 200.0, 3000.0, buy_max=9000.0) == 3000.0
    assert bs.compute_band_bid_price('buy', 200.0, 3000.0, buy_max=2500.0) == 2500.0
    assert bs.compute_band_bid_price('sell', 7000.0, 12000.0, sell_min=2000.0) == 7000.0
    assert bs.compute_band_bid_price('sell', 1500.0, 12000.0, sell_min=2000.0) == 2000.0
    # Без P10/P90 — None (діє буфер), standby — завжди None.
    assert bs.compute_band_bid_price('buy', None, None, buy_max=9000.0) is None
    assert bs.compute_band_bid_price('standby', 1.0, 2.0) is None


def test_generate_bids_band(db_asset, monkeypatch):
    db, asset = db_asset
    monkeypatch.setattr(bs, 'get_bid_price_mode', lambda: 'band')
    monkeypatch.setattr(bs, 'get_delivery_tariff_uah_per_mwh', lambda: 0.0)
    powers = [0.0] * 24
    powers[12], powers[19] = -1.0, 1.0
    _plan_day(db, asset, DAY, powers, 0.4)
    t0 = kyiv_to_utc(DAY, 0)
    for h in range(24):
        p = {12: 400.0, 19: 9000.0}.get(h, 3000.0)
        db.add(PriceForecast(timestamp=t0 + datetime.timedelta(hours=h), forecast_run_at=t0, model_version='test',
                             predicted_price_uah=p, lower_bound_uah=p - 1000.0, upper_bound_uah=p + 1500.0))
    db.commit()
    res = bs.generate_bids_for_date(db, asset, t0, force_full_day=True)
    assert res['status'] == 'ok' and res['bid_price_mode'] == 'band'
    rows = {b.timestamp: b for b in db.query(MarketBid).filter(MarketBid.asset_id == asset.id, MarketBid.timestamp >= t0,
                                                               MarketBid.timestamp < t0 + datetime.timedelta(hours=24))}
    buy, sell = rows[t0 + datetime.timedelta(hours=12)], rows[t0 + datetime.timedelta(hours=19)]
    assert buy.bid_price_mode == 'band' and buy.bid_price_uah == pytest.approx(1900.0)
    assert sell.bid_price_mode == 'band' and sell.bid_price_uah == pytest.approx(8000.0)
