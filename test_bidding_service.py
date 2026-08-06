import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import datetime

from src.modules.bidding_service.services import (
    clamp_bid_price_to_oree_bounds,
    OREE_BID_PRICE_MIN_UAH,
    OREE_BID_PRICE_MAX_UAH,
    build_daily_action_summary,
)
from src.database.session import SessionLocal
from src.database.models import Asset, MarketBid


def test_clamp_below_floor():
    print("=== Testing clamp_bid_price_to_oree_bounds: below floor ===")
    price, clamped = clamp_bid_price_to_oree_bounds(8.0)
    assert price == OREE_BID_PRICE_MIN_UAH
    assert clamped is True
    print("test_clamp_below_floor: PASSED\n")


def test_clamp_above_ceiling():
    print("=== Testing clamp_bid_price_to_oree_bounds: above ceiling ===")
    price, clamped = clamp_bid_price_to_oree_bounds(60000.0)
    assert price == OREE_BID_PRICE_MAX_UAH
    assert clamped is True
    print("test_clamp_above_ceiling: PASSED\n")


def test_clamp_within_bounds_noop():
    print("=== Testing clamp_bid_price_to_oree_bounds: within bounds (no-op) ===")
    price, clamped = clamp_bid_price_to_oree_bounds(3500.0)
    assert price == 3500.0
    assert clamped is False
    print("test_clamp_within_bounds_noop: PASSED\n")


def test_action_summary_no_bids():
    print("=== Testing build_daily_action_summary: no bids ===")
    db = SessionLocal()
    try:
        asset = db.query(Asset).first()
        assert asset is not None, "потрібен хоча б один Asset у тестовій БД"
        target = datetime.datetime(2099, 1, 1)
        db.query(MarketBid).filter(
            MarketBid.asset_id == asset.id,
            MarketBid.timestamp >= target,
            MarketBid.timestamp < target + datetime.timedelta(days=1),
        ).delete()
        db.commit()

        summary = build_daily_action_summary(db, asset, target)
        assert summary['has_bids'] is False
        assert any('сформовано' in a['text'] for a in summary['actions'])
        print("test_action_summary_no_bids: PASSED\n")
    finally:
        db.close()


def test_action_summary_needs_idm():
    print("=== Testing build_daily_action_summary: needs IDM action ===")
    db = SessionLocal()
    try:
        asset = db.query(Asset).first()
        assert asset is not None, "потрібен хоча б один Asset у тестовій БД"
        target = datetime.datetime(2099, 1, 2)
        db.query(MarketBid).filter(
            MarketBid.asset_id == asset.id,
            MarketBid.timestamp >= target,
            MarketBid.timestamp < target + datetime.timedelta(days=1),
        ).delete()
        db.commit()

        row = MarketBid(
            asset_id=asset.id, timestamp=target + datetime.timedelta(hours=10),
            bid_type='sell', volume_kw=200.0, forecast_price_uah=3000.0, margin_pct=2.0,
            bid_price_uah=2940.0, actual_price_uah=2000.0, executed=False, realized_profit_uah=0.0,
            idm_fallback_suggested=True, idm_fallback_price_uah=2100.0, idm_fallback_profit_uah=150.0,
        )
        db.add(row)
        db.commit()

        summary = build_daily_action_summary(db, asset, target)
        assert summary['n_needs_idm_action'] == 1
        assert any('ВДР' in a['text'] and a['hour'] == 10 for a in summary['actions'])

        db.query(MarketBid).filter(MarketBid.asset_id == asset.id, MarketBid.timestamp == row.timestamp).delete()
        db.commit()
        print("test_action_summary_needs_idm: PASSED\n")
    finally:
        db.close()


if __name__ == "__main__":
    test_clamp_below_floor()
    test_clamp_above_ceiling()
    test_clamp_within_bounds_noop()
    test_action_summary_no_bids()
    test_action_summary_needs_idm()
