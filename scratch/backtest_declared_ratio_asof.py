"""
Продовий as-of шлях: FEATURES vs FEATURES + DAM_Declared_Ratio_* (заявлений
продаж/купівля РДН з файлу OREE) — пряме порівняння на тих самих днях
(2026-09-28, CLAUDE.md п.59; після фіксу київської доби в бектесті).

Запуск: python3 scratch/backtest_declared_ratio_asof.py [test_days ...]
"""
import sys
import json
import math

sys.path.insert(0, '.')

import numpy as np

import src.modules.market_data_service.data_manager as dm
dm.sync_realtime_data = lambda force=False: True  # фіксований знімок даних
import src.modules.forecast_service.ml_pipeline as mt

CANDIDATE = ['DAM_Declared_Ratio_Lag_24', 'DAM_Declared_Ratio_Mean_24h']


def dm_test(a, b):
    d = np.asarray(a) - np.asarray(b)
    stat = d.mean() / (d.std(ddof=1) / math.sqrt(len(d)))
    return float(stat), float(math.erfc(abs(stat) / math.sqrt(2)))


results = {}
for test_days in [int(x) for x in sys.argv[1:]] or [90, 365]:
    base = mt.walk_forward_backtest(test_days=test_days, retrain_every_days=7, acknowledge_approximation=True)
    cand = mt.walk_forward_backtest(test_days=test_days, retrain_every_days=7, acknowledge_approximation=True,
                                    extra_features=CANDIDATE)
    bd = {d['date']: d for d in base['daily']}
    cd = {d['date']: d for d in cand['daily']}
    common = sorted(set(bd) & set(cd))
    bw = np.array([bd[d]['wape'] for d in common]); cw = np.array([cd[d]['wape'] for d in common])
    bm = np.array([bd[d]['mae'] for d in common]); cm = np.array([cd[d]['mae'] for d in common])
    stat, p = dm_test(cm, bm)
    r = {
        'days': len(common), 'period': [common[0], common[-1]],
        'methodology': [base['summary'].get('methodology_version'), cand['summary'].get('methodology_version')],
        'base_mean_wape': float(bw.mean()), 'cand_mean_wape': float(cw.mean()),
        'base_median_mape': base['summary'].get('median_mape'), 'cand_median_mape': cand['summary'].get('median_mape'),
        'base_mean_mae': float(bm.mean()), 'cand_mean_mae': float(cm.mean()),
        'cand_better_days': int((cw < bw).sum()),
        'dm_stat_mae_cand_minus_base': stat, 'dm_p_value': p,
    }
    results[test_days] = r
    print(json.dumps({test_days: r}, indent=2, default=str), flush=True)

with open('scratch/backtest_declared_ratio_asof_results.json', 'w') as f:
    json.dump(results, f, indent=2, default=str)
print("DM: stat<0 і p<0.05 — кандидат значуще кращий за прод.")
