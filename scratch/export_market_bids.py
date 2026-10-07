import csv, sys
from src.database.session import SessionLocal
from src.database.models import MarketBid, Asset
db = SessionLocal(); a = db.query(Asset).first()
w = csv.writer(sys.stdout)
cols = ['timestamp','bid_type','volume_kw','forecast_price_uah','margin_pct','margin_uah','bid_price_uah','actual_price_uah','executed','realized_profit_uah','idm_fallback_price_uah','idm_fallback_price_is_actual','idm_fallback_acknowledged','idm_submitted_at','settled_at','bid_generated_at']
w.writerow(cols)
for b in db.query(MarketBid).filter(MarketBid.asset_id == a.id).order_by(MarketBid.timestamp):
    w.writerow([getattr(b, c) for c in cols])
