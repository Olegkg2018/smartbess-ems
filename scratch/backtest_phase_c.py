"""
Фаза C (docs/review_ml_forecast_pipeline_2026-08-21.md) — повторная проверка
отклонённых feature-экспериментов на новом as-of-честном бектесте (Фаза B).
Ad-hoc скрипт, не часть застосунку — за зразком scratch/backtest_ensemble.py.

Запускає:
1. baseline (test_days=90, поточна прод-конфігурація) — новий офіційний
   заголовний WAPE, порівнянний із задокументованим діапазоном 22-26%.
2. Lag_48 замість Lag_24 (test_days=60, той самий test_days, що й
   оригінальний відхилений експеримент — для прямого порівняння).
3. EU_DAM_Price_Lag_24 як extra_feature (test_days=60, legacy-шлях —
   лише leakage fix, без as-of-уніфікації: нема прод-еквіваленту).
4. PL/RO погода як extra_features (test_days=60, legacy-шлях).
5-6. ensemble_average / ensemble_weighted (test_days=60, legacy-шлях).
"""
import sys
sys.path.insert(0, '.')
import json
import time
from src.modules.forecast_service.ml_pipeline import walk_forward_backtest

results = {}


def run(name, **kwargs):
    print(f"=== {name} ===", flush=True)
    t0 = time.time()
    report = walk_forward_backtest(acknowledge_approximation=True, **kwargs)
    elapsed = round(time.time() - t0, 1)
    summary = report['summary']
    summary['elapsed_sec'] = elapsed
    results[name] = report
    print(json.dumps(summary, indent=2), flush=True)
    with open('scratch/backtest_phase_c_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    return report


baseline90 = run(
    'baseline_90d',
    test_days=90, retrain_every_days=7, model_type='lightgbm',
)

# Той самий baseline, останні 60 днів — для прямого порівняння з
# оригінальними експериментами нижче (усі колись перевірялись test_days=60).
last60 = baseline90['daily'][-60:]
mape60 = [d['mape'] for d in last60]
wape60 = [d['wape'] for d in last60]
baseline_last60_summary = {
    'test_days': len(last60),
    'mean_mape': sum(mape60) / len(mape60),
    'mean_wape': sum(wape60) / len(wape60),
    'last_7d_mean_mape': sum(m['mape'] for m in last60[-7:]) / min(7, len(last60)),
    'last_30d_mean_mape': sum(m['mape'] for m in last60[-30:]) / min(30, len(last60)) if len(last60) >= 30 else None,
}
print("=== baseline_last_60d (subset of 90d run, for comparison) ===", flush=True)
print(json.dumps(baseline_last60_summary, indent=2), flush=True)
results['baseline_last_60d_subset'] = baseline_last60_summary
with open('scratch/backtest_phase_c_results.json', 'w') as f:
    json.dump(results, f, indent=2)

run(
    'lag48_60d',
    test_days=60, retrain_every_days=7, model_type='lightgbm', idm_lag_hours=48,
)

run(
    'eu_dam_price_60d',
    test_days=60, retrain_every_days=7, model_type='lightgbm', extra_features=['EU_DAM_Price_Lag_24'],
)

run(
    'pl_ro_weather_60d',
    test_days=60, retrain_every_days=7, model_type='lightgbm',
    extra_features=[f'{zone}_{col}' for zone in ('PL', 'RO') for col in ('Temperature', 'Cloud_Cover', 'Wind_Speed', 'Shortwave_Radiation')],
)

run(
    'ensemble_average_60d',
    test_days=60, retrain_every_days=7, model_type='ensemble_average',
)

run(
    'ensemble_weighted_60d',
    test_days=60, retrain_every_days=7, model_type='ensemble_weighted',
)

print("=== ALL DONE ===", flush=True)
