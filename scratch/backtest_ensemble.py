"""
Ad-hoc backtest comparison: solo LightGBM (production baseline) vs
ensemble_average vs ensemble_weighted (LightGBM+XGBoost+MLP blend).
Not part of the app — one-off script per project convention (see
scratch/backtest_dam_idm.py precedent). Prints summary JSON for each run.
"""
import sys
sys.path.insert(0, '.')
import json
from src.modules.forecast_service.ml_pipeline import walk_forward_backtest

TEST_DAYS = 60
RETRAIN_EVERY_DAYS = 7

results = {}
for model_type in ('lightgbm', 'ensemble_average', 'ensemble_weighted'):
    print(f"=== running {model_type} ===", flush=True)
    report = walk_forward_backtest(test_days=TEST_DAYS, retrain_every_days=RETRAIN_EVERY_DAYS, model_type=model_type)
    results[model_type] = report['summary']
    print(json.dumps(report['summary'], indent=2), flush=True)

with open('scratch/backtest_ensemble_results.json', 'w') as f:
    json.dump(results, f, indent=2)

print("=== DONE ===", flush=True)
