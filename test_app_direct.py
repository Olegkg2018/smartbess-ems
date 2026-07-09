import sys
import os
import asyncio
from pydantic import BaseModel

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import forecast, ForecastRequest
from src.database.init_db import init_db

async def run_test():
    print("Initializing database...")
    init_db()

    # Seed default Organization and Asset
    from src.database.session import SessionLocal
    from src.database.models import Organization, Asset
    db = SessionLocal()
    try:
        org = db.query(Organization).first()
        if not org:
            org = Organization(name="SmartBESS Demo Organization", country="UA")
            db.add(org)
            db.commit()
            db.refresh(org)
            print("Seeded default organization.")
        asset = db.query(Asset).first()
        if not asset:
            asset = Asset(
                organization_id=org.id,
                name="BESS Unit 1 (Primary)",
                capacity_mwh=1.0,
                power_mw=0.25,
                efficiency_charge=0.95,
                efficiency_discharge=0.95,
                min_soc_pct=10.0,
                max_soc_pct=90.0,
                deg_cost_per_mwh=1200.0
            )
            db.add(asset)
            db.commit()
            db.refresh(asset)
            print("Seeded default BESS asset.")
    finally:
        db.close()

    # Define a test request for a historical date that exists in our dataset
    req = ForecastRequest(
        date="2025-06-15",
        selected_model="lightgbm",
        battery_capacity=1000.0,
        max_charge_power=250.0,
        max_discharge_power=250.0,
        charge_efficiency=95.0,
        discharge_efficiency=95.0,
        initial_soc=20.0,
        min_soc=10.0,
        max_soc=90.0,
        mode="arbitrage"
    )

    print("Calling forecast endpoint handler directly...")
    try:
        # Since forecast is an async function in app.py, we await it
        result = await forecast(req)
        print("Success! Forecast results returned:")
        print(f"Date: {result['date']}")
        print(f"Is historical: {result['is_historical']}")
        print(f"Weather hours count: {len(result['weather']['hours'])}")
        print(f"Forecast prices (first 5 hours): {result['forecast']['lightgbm'][:5]}")
        print(f"Optimization status: {result['optimization']['status']}")
        print(f"Expected BESS profit: {result['optimization']['net_profit_uah']:.2f} UAH")
        print(f"Actual cycles used: {result['optimization']['cycles_used']:.2f}")
        
        sa = result.get('scenarios_analysis', {})
        summary = sa.get('summary', {})
        print("\n--- Scenarios and Risk Analysis ---")
        print(f"Base Expected Profit: {summary.get('base_expected_profit_uah'):.2f} UAH")
        print(f"Worst Case Profit (5th percentile): {summary.get('worst_case_profit_uah'):.2f} UAH")
        print(f"Value at Risk (VaR 95%): {summary.get('value_at_risk_uah'):.2f} UAH")
        print(f"Pessimistic Scenario Profit: {sa.get('scenarios', {}).get('pessimistic', {}).get('net_profit_uah'):.2f} UAH")
        print(f"Aggressive Scenario Profit: {sa.get('scenarios', {}).get('aggressive', {}).get('net_profit_uah'):.2f} UAH")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("Test failed with error:", e)

if __name__ == "__main__":
    asyncio.run(run_test())
