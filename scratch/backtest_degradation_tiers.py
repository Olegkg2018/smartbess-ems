"""
Бектест кусочно-лінійної (tiered) деградації проти старої плоскої моделі
на РЕАЛЬНИХ історичних цінах РДН (market_prices, UA_IPS, локальна sqlite).

Питання: чи МІНЯЄ новий кусочно-лінійний degradation cost реальну диспетчерську
поведінку (не лише перерахунок ціни на тому самому графіку), і наскільки —
на реальному активі 250кВт/1МВт·год, max_cycles_per_day=1.5 (поточний
конфіг з БД). Ad-hoc скрипт, за зразком scratch/backtest_dam_idm.py.
"""
import sys
sys.path.insert(0, '.')
import sqlite3
import json
import random

import src.modules.optimization_service.milp_model as m

ASSET = dict(
    battery_capacity=1000.0, max_charge_power=250.0, max_discharge_power=250.0,
    charge_efficiency=0.95, discharge_efficiency=0.95,
    min_soc=0.10, max_soc=0.90, max_cycles_per_day=1.5, degradation_cost=1.20,
)

conn = sqlite3.connect('data/smartbess.db')
cur = conn.cursor()
cur.execute("""
    SELECT date(timestamp) as d, timestamp, price_uah FROM market_prices
    WHERE area = 'UA_IPS' ORDER BY timestamp
""")
rows = cur.fetchall()

by_day = {}
for d, ts, price in rows:
    by_day.setdefault(d, {})[ts[11:13]] = price

full_days = [d for d, hours in by_day.items() if len(hours) == 24]
random.seed(42)
sample_days = random.sample(full_days, min(300, len(full_days)))

def run_flat(prices):
    orig = m.tiered_degradation_rates
    m.tiered_degradation_rates = lambda *a, **kw: orig(*a, **{**kw, 'tier2_multiplier': 1.0})
    try:
        return m.optimize_battery_schedule(prices, **ASSET)
    finally:
        m.tiered_degradation_rates = orig

def run_tiered(prices):
    return m.optimize_battery_schedule(prices, **ASSET)

results = []
for d in sample_days:
    hours = by_day[d]
    prices = [hours[f"{h:02d}"] for h in range(24)]
    flat = run_flat(prices)
    tiered = run_tiered(prices)
    if not flat or not tiered:
        continue
    results.append({
        'date': d,
        'flat_cycles': flat['cycles_used'],
        'tiered_cycles': tiered['cycles_used'],
        'flat_profit': flat['net_profit_uah'],
        'tiered_profit': tiered['net_profit_uah'],
        'flat_degradation': flat['degradation_cost_uah'],
        'tiered_degradation': tiered['degradation_cost_uah'],
    })

n = len(results)
avg_flat_cycles = sum(r['flat_cycles'] for r in results) / n
avg_tiered_cycles = sum(r['tiered_cycles'] for r in results) / n
avg_flat_profit = sum(r['flat_profit'] for r in results) / n
avg_tiered_profit = sum(r['tiered_profit'] for r in results) / n
avg_flat_degr = sum(r['flat_degradation'] for r in results) / n
avg_tiered_degr = sum(r['tiered_degradation'] for r in results) / n

days_cycles_reduced = sum(1 for r in results if r['tiered_cycles'] < r['flat_cycles'] - 1e-6)
days_over_1_cycle_flat = sum(1 for r in results if r['flat_cycles'] > 1.0 + 1e-6)
days_over_1_cycle_tiered = sum(1 for r in results if r['tiered_cycles'] > 1.0 + 1e-6)

summary = {
    'n_days': n,
    'avg_flat_cycles': round(avg_flat_cycles, 4),
    'avg_tiered_cycles': round(avg_tiered_cycles, 4),
    'avg_flat_net_profit_uah': round(avg_flat_profit, 2),
    'avg_tiered_net_profit_uah': round(avg_tiered_profit, 2),
    'avg_flat_degradation_uah': round(avg_flat_degr, 2),
    'avg_tiered_degradation_uah': round(avg_tiered_degr, 2),
    'days_where_tiered_cycles_lower': days_cycles_reduced,
    'days_over_1_cycle_flat': days_over_1_cycle_flat,
    'days_over_1_cycle_tiered': days_over_1_cycle_tiered,
    'pct_annual_profit_delta': round(100 * (avg_tiered_profit - avg_flat_profit) / avg_flat_profit, 3) if avg_flat_profit else None,
}

print(json.dumps(summary, indent=2, ensure_ascii=False))
with open('scratch/backtest_degradation_tiers_results.json', 'w') as f:
    json.dump({'summary': summary, 'days': results}, f, indent=2, ensure_ascii=False)
