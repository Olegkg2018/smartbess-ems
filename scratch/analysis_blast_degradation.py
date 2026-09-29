"""
Калібрування вартості деградації (Asset.deg_cost_per_mwh, зараз 1200 грн/МВт·год;
кусково-лінійна крива п.29) незалежною фізичною моделлю старіння NREL BLAST-Lite
(BSD-3), модель Lfp_Gr_250AhPrismatic — великоформатні промислові LFP-комірки,
дані старіння https://doi.org/10.1016/j.est.2023.109042 (2026-09-28, п.5 плану).

Що рахується:
  1. Річний погодинний профіль SoC з РЕАЛЬНИХ рішень MILP (optimize_battery_schedule)
     на реальних цінах РДН останніх 365 діб, продовий актив 4 МВт·год/1 МВт,
     для max_cycles_per_day = 1.0 / 1.5 (прод) / 2.0.
  2. Календарне старіння окремо: батарея стоїть на SoC 50% без циклування.
  3. BLAST-Lite повторює річний профіль HORIZON_YEARS років → ємність q(t).
  4. Гранична (від циклування) вартість зносу, грн за МВт·год розряду:
        (fade_з_циклуванням − fade_календарний) / 0.2 × вартість_заміни / МВт·год_розряду
     тобто яку частку ресурсу до EOL (80%) "з'їдає" саме торгівля — саме це й
     має бути в MILP (календарне старіння йде незалежно від рішень диспетчера).

Невизначеність (не вигадується одним числом — діапазон):
  - CAPEX 180-200 тис. EUR/МВт·год (ЛІГА.Блоги, 2026) — повна система; частка
    вартості саме батарейних модулів (що замінюються) — 40% / 100%.
  - Температура комірок — 25°C (контейнер з HVAC) і 35°C (погане охолодження).
  - EUR/UAH — живий курс НБУ на дату розрахунку.
  - Модель — лабораторні комірки, не конкретні Huawei LUNA2000.

Запуск: python3 scratch/analysis_blast_degradation.py
"""
import sys
import json

sys.path.insert(0, '.')

import numpy as np
import pandas as pd
import requests
from blast import models

import src.modules.market_data_service.data_manager as dm
from src.modules.optimization_service.milp_model import optimize_battery_schedule

CAPACITY_MWH, POWER_MW, EFF, MIN_SOC, MAX_SOC = 4.0, 1.0, 0.95, 0.10, 0.90
DEG_UAH_PER_KWH_PROD = 1.2
HORIZON_YEARS = 10
CAPEX_EUR_PER_MWH = (180_000, 200_000)
BATTERY_SHARE = (0.4, 1.0)
TEMPS_C = (25.0, 35.0)

try:
    r = requests.get('https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?valcode=EUR&json', timeout=20)
    EUR_UAH = float(r.json()[0]['rate'])
    EUR_SOURCE = f"НБУ {r.json()[0]['exchangedate']}"
except Exception as e:
    sys.exit(f"Не вдалося отримати курс НБУ ({e}) — без вигаданого курсу не рахуємо.")
print(f"EUR/UAH = {EUR_UAH} ({EUR_SOURCE})")

df = dm.load_merged_csv_cached(['Datetime', 'Price'])
kyiv = df['Datetime'].dt.tz_localize('UTC').dt.tz_convert('Europe/Kyiv')
df['Date'] = kyiv.dt.date
groups = {d: g.sort_values('Datetime')['Price'].tolist() for d, g in df.groupby('Date')}
days = [d for d, p in groups.items() if len(p) == 24 and not np.isnan(p).any()][-366:-1]
print(f"days {days[0]} .. {days[-1]} ({len(days)})")


def year_profile(max_cycles):
    soc, discharged_mwh = [], 0.0
    for d in days:
        res = optimize_battery_schedule(
            prices=groups[d], battery_capacity=CAPACITY_MWH * 1000, max_charge_power=POWER_MW * 1000,
            max_discharge_power=POWER_MW * 1000, charge_efficiency=EFF, discharge_efficiency=EFF,
            initial_soc=MIN_SOC, min_soc=MIN_SOC, max_soc=MAX_SOC, max_cycles_per_day=max_cycles,
            degradation_cost=DEG_UAH_PER_KWH_PROD, transmission_tariff=0.0, distribution_tariff=0.0,
            dispatch_tariff=0.0, supplier_margin=0.0,
        )
        s = res['soc'][1:] if res.get('status') == 'Optimal' else [MIN_SOC * CAPACITY_MWH * 1000] * 24
        soc.extend(np.array(s) / (CAPACITY_MWH * 1000))
        discharged_mwh += sum(res.get('discharge', [])) / 1000.0 if res.get('status') == 'Optimal' else 0.0
    return np.clip(np.array(soc), 0, 1), discharged_mwh


def simulate(soc, temp_c):
    t = np.arange(len(soc)) * 3600.0
    cell = models.Lfp_Gr_250AhPrismatic()
    cell.simulate_battery_life({'Time_s': t, 'SOC': soc, 'Temperature_C': np.full(len(soc), temp_c)},
                               threshold_time=HORIZON_YEARS)
    q = np.asarray(cell.outputs['q'])
    t_years = np.asarray(cell.stressors['t_days']) / 365.0
    return q, t_years


calendar_soc = np.full(len(days) * 24, 0.5)
results = {'eur_uah': EUR_UAH, 'eur_source': EUR_SOURCE, 'horizon_years': HORIZON_YEARS, 'scenarios': {}}
for temp in TEMPS_C:
    q_cal, _ = simulate(calendar_soc, temp)
    fade_cal = 1.0 - q_cal[-1]
    for mc in (1.0, 1.5, 2.0):
        soc, dis_mwh_year = year_profile(mc)
        q, ty = simulate(soc, temp)
        fade = 1.0 - q[-1]
        fade_cycling = max(fade - fade_cal, 0.0)
        years_to_80 = float(np.interp(0.8, q[::-1], ty[::-1])) if q.min() <= 0.8 else None
        dis_total = dis_mwh_year * HORIZON_YEARS
        costs = {}
        for capex in CAPEX_EUR_PER_MWH:
            for share in BATTERY_SHARE:
                replacement_uah = capex * CAPACITY_MWH * share * EUR_UAH
                costs[f'capex{capex // 1000}k_share{int(share * 100)}'] = round(
                    fade_cycling / 0.2 * replacement_uah / dis_total, 1)
        key = f'T{int(temp)}_cycles{mc}'
        results['scenarios'][key] = {
            'efc_per_year': round(dis_mwh_year / CAPACITY_MWH, 1),
            'discharged_mwh_per_year': round(dis_mwh_year, 1),
            f'capacity_after_{HORIZON_YEARS}y': round(float(q[-1]), 4),
            'calendar_only_capacity': round(float(q_cal[-1]), 4),
            'fade_from_cycling': round(fade_cycling, 4),
            'years_to_80pct': round(years_to_80, 1) if years_to_80 else f'>{HORIZON_YEARS}',
            'marginal_cycling_cost_uah_per_mwh_discharged': costs,
        }
        print(key, json.dumps(results['scenarios'][key], ensure_ascii=False), flush=True)

print(f"\nПоточне налаштування: {DEG_UAH_PER_KWH_PROD * 1000:.0f} грн/МВт·год розряду")
with open('scratch/analysis_blast_degradation_results.json', 'w') as f:
    json.dump(results, f, indent=2, ensure_ascii=False, default=str)
print("Saved: scratch/analysis_blast_degradation_results.json")
