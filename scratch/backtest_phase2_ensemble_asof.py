"""
Фаза 2 плана "Ensemble-модель прогноза: честная переперепроверка" (2026-08-25).
Честная переперевірка ансамблю (ensemble_average/ensemble_weighted) ПІСЛЯ
Фази 1 — as-of-уніфікації ансамблевої гілки walk_forward_backtest (той самий
build_forecast_feature_matrix(..., as_of=day, ...) + clip_and_shift, яким
реально рахує прод predict_next_day). Попередні цифри Фази C (Фаза 5-6 у
scratch/backtest_phase_c.py) міряли ансамбль на старому, не as-of шляху —
нечесне порівняння із соло-lightgbm, який Фаза C вже тестувала as-of.

record_hourly=True на всіх трьох прогонах — потрібно для запрошеного
користувачем артефакту: погодинне порівняння прогноз/факт по кількох
показових днях (не лише агрегований відсоток).
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
    report = walk_forward_backtest(acknowledge_approximation=True, record_hourly=True, **kwargs)
    elapsed = round(time.time() - t0, 1)
    summary = report['summary']
    summary['elapsed_sec'] = elapsed
    results[name] = report
    print(json.dumps(summary, indent=2), flush=True)
    with open('scratch/backtest_phase2_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    return report


baseline90 = run(
    'baseline_90d',
    test_days=90, retrain_every_days=7, model_type='lightgbm',
)

# Той самий відрізок, що і 60-денні ансамблеві прогони нижче — для прямого
# порівняння "яблуко до яблука" (як і в Фазі C).
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
with open('scratch/backtest_phase2_results.json', 'w') as f:
    json.dump(results, f, indent=2)

run(
    'ensemble_average_60d',
    test_days=60, retrain_every_days=7, model_type='ensemble_average',
)

run(
    'ensemble_weighted_60d',
    test_days=60, retrain_every_days=7, model_type='ensemble_weighted',
)

print("=== ALL DONE ===", flush=True)
