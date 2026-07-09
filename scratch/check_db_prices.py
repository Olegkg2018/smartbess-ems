import datetime
from src.database.session import SessionLocal
from src.database.models import MarketPrice

db = SessionLocal()
try:
    prices = db.query(MarketPrice).filter(
        MarketPrice.timestamp >= datetime.datetime(2026, 7, 9, 0, 0),
        MarketPrice.timestamp <= datetime.datetime(2026, 7, 9, 23, 59)
    ).order_by(MarketPrice.timestamp).all()
    
    print(f"PostgreSQL Market prices count for 2026-07-09: {len(prices)}")
    for p in prices:
        print(f"{p.timestamp.strftime('%H:%M')} -> {p.price_uah_mwh:.2f}")
finally:
    db.close()
