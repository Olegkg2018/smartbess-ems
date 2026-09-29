"""
Порівняння as-of бектесту (продовий шлях walk_forward_backtest) ДО і ПІСЛЯ
фіксу київської доби (CLAUDE.md п.59) на тому самому знімку даних і вікні.
"До" — копія ml_pipeline.py з HEAD (scratch/_ml_pipeline_before_kyiv_backtest_fix.py).

Запуск: python3 scratch/compare_backtest_kyiv_fix.py [test_days]
"""
import sys
import json
import importlib.util

sys.path.insert(0, '.')

import src.modules.market_data_service.data_manager as dm
dm.sync_realtime_data = lambda force=False: True  # фіксований знімок даних

import src.modules.forecast_service.ml_pipeline as mt_new

spec = importlib.util.spec_from_file_location('ml_old', 'scratch/_ml_pipeline_before_kyiv_backtest_fix.py')
mt_old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mt_old)

TEST_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 90
out = {}
for name, mod in (('before_fix', mt_old), ('after_fix', mt_new)):
    res = mod.walk_forward_backtest(test_days=TEST_DAYS, retrain_every_days=7, acknowledge_approximation=True)
    s = res['summary']
    out[name] = {k: s.get(k) for k in ('test_days', 'mean_wape', 'median_mape', 'last_7d_mean_mape', 'last_30d_mean_mape', 'methodology_version')}
    print(name, json.dumps(out[name], default=str), flush=True)

with open(f'scratch/compare_backtest_kyiv_fix_{TEST_DAYS}d.json', 'w') as f:
    json.dump(out, f, indent=2, default=str)
