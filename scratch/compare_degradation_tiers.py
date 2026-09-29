"""
Кусково-лінійна (tier2 = 2× tier1, п.29) vs пласка вартість деградації — на
реальних цінах РДН останніх 365 діб (2026-09-29, п.61).

BLAST-Lite (п.59) показав: граничний знос LFP майже лінійний за throughput
(1.0→2.0 циклу/добу: 1180→1197 грн/МВт·год). Тому "істинна" вартість зносу
тут — лінійна DEG грн/МВт·год розряду (поточне налаштування проду = 700), і
обидва плани оцінюються нею: чистий результат = енергія (продаж − купівля) −
DEG × розряджені МВт·год. Кращий той, хто більше заробляє на "істинних"
умовах; пласка схема за побудовою оптимальна при лінійній вартості, питання —
наскільки опукла недовикористовує батарею.

Запуск: python3 scratch/compare_degradation_tiers.py [days]
"""
import sys
import json

sys.path.insert(0, '.')

import numpy as np

import src.modules.market_data_service.data_manager as dm
import src.modules.optimization_service.milp_model as milp

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 365
CAPACITY_KWH, POWER_KW, EFF, MIN_SOC, MAX_SOC, MAX_CYCLES = 4000.0, 1000.0, 0.95, 0.10, 0.90, 1.5
DEG_UAH_PER_KWH = 0.7

df = dm.load_merged_csv_cached(['Datetime', 'Price'])
kyiv = df['Datetime'].dt.tz_localize('UTC').dt.tz_convert('Europe/Kyiv')
df['Date'] = kyiv.dt.date
groups = {d: g.sort_values('Datetime')['Price'].tolist() for d, g in df.groupby('Date')}
days = [d for d, p in groups.items() if len(p) == 24 and not np.isnan(p).any()][-DAYS - 1:-1]
print(f"days {days[0]} .. {days[-1]} ({len(days)}), DEG={DEG_UAH_PER_KWH * 1000:.0f} грн/МВт·год")

_orig = milp.tiered_degradation_rates


def run(multiplier):
    milp.tiered_degradation_rates = lambda *a, **k: _orig(*a, **{**k, 'tier2_multiplier': multiplier})
    energy, discharged, cycles = [], [], []
    for d in days:
        p = groups[d]
        r = milp.optimize_battery_schedule(
            prices=p, battery_capacity=CAPACITY_KWH, max_charge_power=POWER_KW, max_discharge_power=POWER_KW,
            charge_efficiency=EFF, discharge_efficiency=EFF, initial_soc=MIN_SOC, min_soc=MIN_SOC, max_soc=MAX_SOC,
            max_cycles_per_day=MAX_CYCLES, degradation_cost=DEG_UAH_PER_KWH, transmission_tariff=0.0,
            distribution_tariff=0.0, dispatch_tariff=0.0, supplier_margin=0.0)
        ch, dis = np.array(r['charge']), np.array(r['discharge'])
        energy.append(float(np.sum((dis - ch) * np.array(p)) / 1000.0))
        discharged.append(float(dis.sum() / 1000.0))
        cycles.append(float(dis.sum() / CAPACITY_KWH))
    milp.tiered_degradation_rates = _orig
    energy, discharged = np.array(energy), np.array(discharged)
    net_true = energy - DEG_UAH_PER_KWH * 1000.0 * discharged
    return {'energy_uah_per_day': float(energy.mean()), 'discharged_mwh_per_day': float(discharged.mean()),
            'cycles_per_day': float(np.mean(cycles)), 'days_over_1_cycle': int(np.sum(np.array(cycles) > 1.0 + 1e-6)),
            'net_true_uah_per_day': float(net_true.mean()), '_net': net_true}


tiered = run(2.0)
flat = run(1.0)
diff = flat['_net'] - tiered['_net']
res = {k: {kk: vv for kk, vv in v.items() if kk != '_net'} for k, v in (('tiered_x2', tiered), ('flat', flat))}
res['flat_minus_tiered_net_uah_per_day'] = float(diff.mean())
res['flat_minus_tiered_net_uah_per_year'] = float(diff.mean() * 365)
res['days_flat_better'] = int(np.sum(diff > 1e-6))
res['days_tiered_better'] = int(np.sum(diff < -1e-6))
print(json.dumps(res, indent=2, ensure_ascii=False))
with open('scratch/compare_degradation_tiers_results.json', 'w') as f:
    json.dump(res, f, indent=2, ensure_ascii=False)
