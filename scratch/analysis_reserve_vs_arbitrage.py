"""
Арбітраж РДН vs арбітраж + резерв для ринку допоміжних послуг (FCR/aFRR),
на реальних цінах РДН останніх N днів (2026-09-28, пункт 3 плану розвитку).

Модель (свідомо проста, всі припущення — параметри, не вигадані факти):
  - Резерв r МВт "з'їдає" r МВт потужності арбітражу в відповідному напрямку
    (FCR — симетрично: і заряд, і розряд обмежені P-r).
  - Резерв вимагає запасу енергії: щоб видати r МВт протягом D годин, SoC має
    бути не нижче min + r·D (вгору/розряд) і не вище max − r·D (вниз/заряд).
    D — вимога TSO до тривалості активації; точне значення для УЗЕ в
    Україні НЕ підтверджене — дається діапазоном (чутливість).
  - Дохід за резерв = r × ціна_потужності × 24 × днів. Ціни — реальні
    середньозважені результати 4-го спецаукціону Укренерго (2025):
    786.95 грн/МВт·год (симетричний резерв), 951.06 грн/МВт·год
    (завантаження) — EXPRO/ua-energy. Це 5-річні контракти для НОВОЇ
    потужності; доступність для вже збудованого активу — окреме питання.
  - Енергія активації резерву/знос від неї НЕ моделюються (для FCR нетто-
    енергія близька до нуля; для aFRR — занижує витрати, чесне обмеження).
  - Тариф на доставку = 0 (поточне налаштування), деградація — як у проді.

Запуск: python3 scratch/analysis_reserve_vs_arbitrage.py [days] [capture]
"""
import sys
import json

sys.path.insert(0, '.')

import numpy as np
import pandas as pd

import src.modules.market_data_service.data_manager as dm
from src.modules.optimization_service.milp_model import optimize_battery_schedule

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 365

# Реальні параметри продового активу (VPS, таблиця assets, 2026-09-28).
CAPACITY_MWH = 4.0
POWER_MW = 1.0
EFF = 0.95
MIN_SOC, MAX_SOC = 0.10, 0.90
DEG_UAH_PER_KWH = 1.2
MAX_CYCLES = 1.5

# Арбітраж тут рахується з ідеальним знанням цін (perfect foresight) —
# верхня межа; дохід за резерв натомість гарантований. Реальна частка
# захопленого арбітражного прибутку на проді ~0.9 (get_profit_capture_ratio,
# CLAUDE.md п.8-9) — множимо арбітраж на CAPTURE, щоб порівняння було чесним.
CAPTURE = float(sys.argv[2]) if len(sys.argv) > 2 else 0.9

PRICE_SYMMETRIC = 786.95   # грн/МВт·год, симетричний резерв (4-й спецаукціон)
PRICE_UPWARD = 951.06      # грн/МВт·год, "завантаження"

# (назва, r МВт, D год, напрям, ціна потужності)
SCENARIOS = [('arbitrage_only', 0.0, 0.0, None, 0.0)]
for r in (0.25, 0.5, 0.75, 1.0):
    SCENARIOS.append((f'fcr_sym_r{r}_D0.5', r, 0.5, 'sym', PRICE_SYMMETRIC))
for d in (0.25, 1.0):
    SCENARIOS.append((f'fcr_sym_r0.5_D{d}', 0.5, d, 'sym', PRICE_SYMMETRIC))
for r in (0.5, 1.0):
    SCENARIOS.append((f'afrr_up_r{r}_D1', r, 1.0, 'up', PRICE_UPWARD))
# 2026-09-29, умови участі (CLAUDE.md п.60): аРВЧ за правилами тримається до
# 60 хв, але огляд Міненерго (серп. 2025) фіксує реальні односторонні
# активації 2+ год зі штрафами за невиконання — сценарій з запасом на 2 год.
SCENARIOS.append(('afrr_up_r0.5_D2', 0.5, 2.0, 'up', PRICE_UPWARD))
# Граничні ціни річного аукціону 2026 (аРВЧ завантаження 973.39, симетричний
# 1339.82 грн/МВт·год) — верхня межа, pay-as-bid, реальна ціна нижча.
SCENARIOS.append(('afrr_up_r0.5_D1_capprice', 0.5, 1.0, 'up', 973.39))
SCENARIOS.append(('fcr_sym_r0.5_D0.25_capprice', 0.5, 0.25, 'sym', 1339.82))

df = dm.load_merged_csv_cached(['Datetime', 'Price'])
kyiv = df['Datetime'].dt.tz_localize('UTC').dt.tz_convert('Europe/Kyiv')
df['Date'] = kyiv.dt.date
days = [d for d, g in df.groupby('Date') if len(g) == 24 and g['Price'].notna().all()]
days = days[-DAYS - 1:-1]  # без сьогоднішньої (може бути неповна)
print(f"days: {len(days)} ({days[0]} .. {days[-1]}), capture={CAPTURE}")
by_day = {d: g.sort_values('Datetime')['Price'].tolist() for d, g in df.groupby('Date') if d in set(days)}

results = {}
for name, r, dur, direction, cap_price in SCENARIOS:
    charge_p = POWER_MW - (r if direction == 'sym' else 0.0)
    discharge_p = POWER_MW - r
    min_soc = MIN_SOC + (r * dur / CAPACITY_MWH)
    max_soc = MAX_SOC - (r * dur / CAPACITY_MWH if direction == 'sym' else 0.0)
    if min_soc >= max_soc or discharge_p < 0:
        results[name] = {'feasible': False}
        print(f"{name}: infeasible (SoC window {min_soc:.2f}-{max_soc:.2f})")
        continue
    arb = []
    for d in days:
        res = optimize_battery_schedule(
            prices=by_day[d], battery_capacity=CAPACITY_MWH * 1000, max_charge_power=charge_p * 1000,
            max_discharge_power=discharge_p * 1000, charge_efficiency=EFF, discharge_efficiency=EFF,
            initial_soc=min_soc, min_soc=min_soc, max_soc=max_soc, max_cycles_per_day=MAX_CYCLES,
            degradation_cost=DEG_UAH_PER_KWH, transmission_tariff=0.0, distribution_tariff=0.0,
            dispatch_tariff=0.0, supplier_margin=0.0,
        )
        arb.append(float(res.get('net_profit_uah', 0.0)) if res.get('status') == 'Optimal' else np.nan)
    arb = np.array(arb)
    reserve_rev_per_day = r * cap_price * 24
    arb = arb * CAPTURE
    per_day = arb + reserve_rev_per_day
    results[name] = {
        'feasible': True, 'r_mw': r, 'duration_h': dur, 'direction': direction,
        'soc_window': [round(min_soc, 3), round(max_soc, 3)],
        'arbitrage_uah_per_day': float(np.nanmean(arb)),
        'reserve_uah_per_day': reserve_rev_per_day,
        'total_uah_per_day': float(np.nanmean(per_day)),
        'total_uah_per_year': float(np.nanmean(per_day) * 365),
        'days_reserve_better_than_arbitrage_only': None,
        'n_days': int(np.isfinite(arb).sum()),
        '_daily_total': per_day.tolist(),
    }
    print(f"{name}: arb {np.nanmean(arb):,.0f} + reserve {reserve_rev_per_day:,.0f} = "
          f"{np.nanmean(per_day):,.0f} грн/добу", flush=True)

base = np.array(results['arbitrage_only']['_daily_total'])
print("\n=== Порівняння з чистим арбітражем ===")
for name, v in results.items():
    if not v.get('feasible') or name == 'arbitrage_only':
        continue
    tot = np.array(v['_daily_total'])
    v['days_reserve_better_than_arbitrage_only'] = int(np.nansum(tot > base))
    delta = v['total_uah_per_day'] - results['arbitrage_only']['total_uah_per_day']
    print(f"{name}: {delta:+,.0f} грн/добу ({delta * 365 / 1e6:+.2f} млн/рік), "
          f"кращий у {v['days_reserve_better_than_arbitrage_only']}/{v['n_days']} днях")

# Точка беззбитковості: при якій ціні потужності резерв r=0.5 (FCR, D=0.5)
# зрівнюється з чистим арбітражем.
v = results.get('fcr_sym_r0.5_D0.5')
if v and v.get('feasible'):
    lost = results['arbitrage_only']['arbitrage_uah_per_day'] - v['arbitrage_uah_per_day']
    print(f"\nFCR r=0.5 D=0.5: втрата арбітражу {lost:,.0f} грн/добу → беззбиткова ціна "
          f"резерву {lost / (0.5 * 24):,.0f} грн/МВт·год (аукціон: {PRICE_SYMMETRIC})")

for v in results.values():
    v.pop('_daily_total', None)
with open(f'scratch/analysis_reserve_vs_arbitrage_{DAYS}d.json', 'w') as f:
    json.dump({'days': [str(days[0]), str(days[-1])], 'capture': CAPTURE, 'results': results}, f, indent=2, default=str)
print(f"\nSaved: scratch/analysis_reserve_vs_arbitrage_{DAYS}d.json")
