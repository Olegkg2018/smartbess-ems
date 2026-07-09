import os
import sys
import time
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import app
from src.database.models import Asset

client = TestClient(app)

def test_payback_endpoint():
    print("=== Testing POST /api/v1/scenarios/payback ===")
    payload = {
        "capex_uah": 12000000.0,
        "yearly_revenue_base_uah": 4000000.0,
        "yearly_opex_uah": 240000.0,
        "discount_rate": 0.12,
        "lifetime_years": 8,
        "pessimistic_risk_factor": 0.15
    }
    
    r = client.post("/api/v1/scenarios/payback", json=payload)
    print(f"Status Code: {r.status_code}")
    assert r.status_code == 200
    
    data = r.json()
    metrics = data.get("metrics", {})
    print(f"Lifetime: {metrics.get('lifetime_years')} years")
    print(f"Base NPV: {metrics.get('base', {}).get('npv_uah'):,.2f} UAH")
    print(f"Base IRR: {metrics.get('base', {}).get('irr_pct'):.2f}%")
    print(f"Pessimistic NPV: {metrics.get('pessimistic', {}).get('npv_uah'):,.2f} UAH")
    print(f"Pessimistic IRR: {metrics.get('pessimistic', {}).get('irr_pct'):.2f}%")
    print("Payback Endpoint Test: PASSED\n")

def test_forecast_async_job():
    print("=== Testing POST /api/v1/forecast/run ===")
    payload = {
        "target_date": "2026-07-10",
        "selected_model": "lightgbm",
        "gas_price_eur_mwh": 40.0,
        "nuclear_outage_pct": 0.15
    }
    
    r = client.post("/api/v1/forecast/run", json=payload)
    print(f"Status Code: {r.status_code}")
    assert r.status_code == 200 # returns 200 since we return job status json
    
    job_data = r.json()
    job_id = job_data.get("job_id")
    print(f"Job ID: {job_id} | Status: {job_data.get('status')}")
    
    # Poll job status
    print("Polling job status...")
    for _ in range(5):
        time.sleep(1.0)
        status_res = client.get(f"/api/v1/jobs/{job_id}")
        status_data = status_res.json()
        print(f"Job Status: {status_data.get('status')} | Progress: {status_data.get('progress_pct')}%")
        if status_data.get("status") in ["completed", "failed"]:
            break
            
    assert status_data.get("status") == "completed"
    print(f"Forecast Result Hours Count: {len(status_data.get('result'))}")
    print("Forecast Async Job Test: PASSED\n")

def test_executive_summary_report():
    print("=== Testing GET /api/v1/reports/executive-summary ===")
    # Get first asset id
    from src.database.session import SessionLocal
    db = SessionLocal()
    asset = db.query(Asset).first()
    db.close()
    
    if not asset:
        print("Warning: No BESS asset found in DB. Seeding default...")
        from src.database.init_db import init_db
        from src.database.models import Organization
        init_db()
        db = SessionLocal()
        org = db.query(Organization).first() or Organization(name="Demo Org")
        db.add(org)
        db.commit()
        asset = Asset(organization_id=org.id, name="Test Asset", capacity_mwh=1.0, power_mw=0.25, efficiency_charge=0.95, efficiency_discharge=0.95, min_soc_pct=10.0, max_soc_pct=90.0, deg_cost_per_mwh=1200.0)
        db.add(asset)
        db.commit()
        db.refresh(asset)
        db.close()
        
    r = client.get(f"/api/v1/reports/executive-summary?asset_id={asset.id}&period=month")
    print(f"Status Code: {r.status_code}")
    assert r.status_code == 200
    
    report = r.json()
    print(f"Asset ID: {report['report_metadata']['asset_id']}")
    print(f"Period: {report['report_metadata']['period']}")
    print(f"Total Revenue: {report['financials']['total_revenue_uah']:,.2f} UAH")
    print(f"Net Profit: {report['financials']['net_profit_uah']:,.2f} UAH")
    print(f"Cycles Executed: {report['operations']['cycles_executed']:.2f}")
    print(f"Value at Risk (VaR 95%): {report['risk_assessment']['var_95_daily_average_uah']:,.2f} UAH")
    print("Executive Summary Endpoint Test: PASSED\n")

if __name__ == "__main__":
    test_payback_endpoint()
    test_forecast_async_job()
    test_executive_summary_report()
