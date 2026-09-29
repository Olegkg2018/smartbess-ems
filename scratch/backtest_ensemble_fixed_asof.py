"""
Повторна перевірка ансамблю LightGBM+XGBoost+MLP (CLAUDE.md п.36) на ВИПРАВЛЕНОМУ
as-of бектесті (київська доба + as_of 06:00 Kyiv напередодні, п.59). Рішення п.36
"не підключати" приймалось на зіпсованій оцінці, де обидві моделі мали зсунуту
на 2-3г "годину доби" — пряме порівняння соло vs ансамбль на тих самих днях +
тест Diebold-Mariano (2026-09-29, п.61).

Запуск: python3 scratch/backtest_ensemble_fixed_asof.py [test_days]
"""
import sys
import json
import math
import time

sys.path.insert(0, '.')

import numpy as np

import src.modules.market_data_service.data_manager as dm
dm.sync_realtime_data = lambda force=False: True  # фіксований знімок даних
import src.modules.forecast_service.ml_pipeline as mt

TEST_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 180


def dm_test(a, b):
    d = np.asarray(a) - np.asarray(b)
    stat = d.mean() / (d.std(ddof=1) / math.sqrt(len(d)))
    return float(stat), float(math.erfc(abs(stat) / math.sqrt(2)))


runs = {}
for model_type in ('lightgbm', 'ensemble_average', 'ensemble_weighted'):
    t0 = time.time()
    res = mt.walk_forward_backtest(test_days=TEST_DAYS, retrain_every_days=7, model_type=model_type,
                                   acknowledge_approximation=True)
    runs[model_type] = {d['date']: d for d in res['daily']}
    s = res['summary']
    print(f"{model_type}: days={s.get('test_days')} mean_wape={s.get('mean_wape'):.3f} "
          f"median_mape={s.get('median_mape'):.2f} method={s.get('methodology_version')} "
          f"({time.time() - t0:.0f}s)", flush=True)

base = runs['lightgbm']
out = {}
for name in ('ensemble_average', 'ensemble_weighted'):
    common = sorted(set(base) & set(runs[name]))
    bw = np.array([base[d]['wape'] for d in common]); ew = np.array([runs[name][d]['wape'] for d in common])
    bm = np.array([base[d]['mae'] for d in common]); em = np.array([runs[name][d]['mae'] for d in common])
    stat, p = dm_test(em, bm)
    out[name] = {'days': len(common), 'lgbm_mean_wape': float(bw.mean()), 'ens_mean_wape': float(ew.mean()),
                 'lgbm_mean_mae': float(bm.mean()), 'ens_mean_mae': float(em.mean()),
                 'ens_better_days': int((ew < bw).sum()), 'dm_stat_mae_ens_minus_lgbm': stat, 'dm_p_value': p}
    print(name, json.dumps(out[name]), flush=True)

with open(f'scratch/backtest_ensemble_fixed_asof_{TEST_DAYS}d.json', 'w') as f:
    json.dump(out, f, indent=2)
print("DM: stat<0 і p<0.05 — ансамбль значуще кращий.")
