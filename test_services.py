import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.modules.tariff_service.services import TariffService
from src.modules.reporting_service.services import ReportingService

def test_tariff_service():
    print("=== Testing TariffService ===")
    
    t_c1 = TariffService.calculate_total_tariff_kwh("DTEK_Kyiv_Grids", 1, supplier_margin_uah_mwh=100.0)
    t_c2 = TariffService.calculate_total_tariff_kwh("DTEK_Kyiv_Grids", 2, supplier_margin_uah_mwh=100.0)
    
    # Transmission (528.57) + Dispatch (104.57) + Distribution (180 for C1 / 780 for C2) + Margin (100)
    # C1: 913.14 UAH/MWh = 0.91314 UAH/kWh
    # C2: 1513.14 UAH/MWh = 1.51314 UAH/kWh
    print(f"DTEK Kyiv Grids Class 1 Tariff: {t_c1:.5f} UAH/kWh (Expected: ~0.91314)")
    print(f"DTEK Kyiv Grids Class 2 Tariff: {t_c2:.5f} UAH/kWh (Expected: ~1.51314)")
    
    assert abs(t_c1 - 0.91314) < 1e-4
    assert abs(t_c2 - 1.51314) < 1e-4
    print("TariffService Test: PASSED\n")

def test_project_economics():
    print("=== Testing ReportingService (Economics) ===")
    # Capex: 10,000,000 UAH
    # Yearly Revenue: 3,500,000 UAH
    # Yearly Opex: 500,000 UAH
    # Net Yearly Cash Flow: 3,000,000 UAH
    # Discount Rate: 12%, Lifetime: 5 years
    economics = ReportingService.calculate_project_economics(
        capex_uah=10000000.0,
        yearly_revenue_uah=3500000.0,
        yearly_opex_uah=500000.0,
        discount_rate=0.12,
        lifetime_years=5
    )
    
    print(f"NPV: {economics['npv_uah']:,.2f} UAH")
    print(f"IRR: {economics['irr_pct']:.2f}%")
    print(f"Simple Payback: {economics['simple_payback_years']:.2f} years")
    print(f"Discounted Payback: {economics['discounted_payback_years']:.2f} years")
    print(f"ROI: {economics['roi_pct']:.2f}%")
    
    assert economics['simple_payback_years'] == 3.3333333333333335
    assert economics['npv_uah'] > 0
    print("ReportingService (Economics) Test: PASSED\n")

def test_value_at_risk():
    print("=== Testing ReportingService (Value at Risk) ===")
    # Dummy base price forecast (24 hours) in UAH/MWh
    base_prices = [
        3000.0, 2800.0, 2000.0, 1500.0, 1000.0, 800.0,  # Night/morning dip
        2000.0, 3500.0, 4500.0, 4000.0, 3200.0, 2500.0,  # Midday peak/dip
        2000.0, 1800.0, 1200.0, 1000.0, 1500.0, 2200.0,  # Afternoon solar dip
        4500.0, 6000.0, 7500.0, 8500.0, 6500.0, 4500.0   # Evening peak
    ]
    
    # Run VaR analysis using 20 simulations for speed
    var_results = ReportingService.calculate_value_at_risk(
        base_prices=base_prices,
        battery_capacity=1000.0,
        max_power=250.0,
        initial_soc=0.20,
        min_soc=0.10,
        max_soc=0.90,
        degradation_cost=1.20,
        distribution_tariff=780.0, # DTEK Kyiv Grids Class 2
        confidence_level=0.95,
        num_simulations=20
    )
    
    print(f"Base Expected Profit: {var_results['base_expected_profit_uah']:.2f} UAH")
    print(f"Worst Case Profit (5th percentile): {var_results['worst_case_profit_uah']:.2f} UAH")
    print(f"Value at Risk (VaR 95%): {var_results['value_at_risk_uah']:.2f} UAH")
    print(f"Mean Simulated Profit: {var_results['mean_simulated_profit_uah']:.2f} UAH")
    
    assert var_results['value_at_risk_uah'] >= 0
    print("ReportingService (Value at Risk) Test: PASSED\n")

def test_oree_participation_fee():
    print("=== Testing TariffService (OREE participation fee) ===")
    fee = TariffService.calculate_oree_participation_fee_uah(traded_volume_mwh=1000.0, months=12.0)
    expected = 4669.71 * 12 + 6.88 * 1000.0
    print(f"OREE participation fee: {fee:.2f} UAH (Expected: {expected:.2f})")
    assert abs(fee - expected) < 1e-6
    print("TariffService (OREE fee) Test: PASSED\n")

def test_project_economics_with_oree_fee():
    print("=== Testing ReportingService (Economics + OREE fee) ===")
    econ_no_fee = ReportingService.calculate_project_economics(
        capex_uah=10000000.0, yearly_revenue_uah=3500000.0, yearly_opex_uah=500000.0,
        oree_participation_fee_uah=0.0, discount_rate=0.12, lifetime_years=5,
    )
    fee = TariffService.calculate_oree_participation_fee_uah(traded_volume_mwh=500.0)
    econ_with_fee = ReportingService.calculate_project_economics(
        capex_uah=10000000.0, yearly_revenue_uah=3500000.0, yearly_opex_uah=500000.0,
        oree_participation_fee_uah=fee, discount_rate=0.12, lifetime_years=5,
    )
    print(f"NPV without fee: {econ_no_fee['npv_uah']:,.2f} UAH; with fee: {econ_with_fee['npv_uah']:,.2f} UAH")
    assert econ_with_fee['oree_participation_fee_uah'] == fee
    assert econ_with_fee['npv_uah'] < econ_no_fee['npv_uah']
    assert econ_with_fee['simple_payback_years'] > econ_no_fee['simple_payback_years']
    print("ReportingService (OREE fee) Test: PASSED\n")

if __name__ == "__main__":
    test_tariff_service()
    test_project_economics()
    test_value_at_risk()
    test_oree_participation_fee()
    test_project_economics_with_oree_fee()
