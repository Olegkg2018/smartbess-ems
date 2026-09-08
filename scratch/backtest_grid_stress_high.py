"""
Чесна переоцінка Grid_Stress_High (2026-09-08, "3 місяці накопичення даних") —
той самий шаблон, що scratch/backtest_ukrenergo_candidates.py (CLAUDE.md п.47):
baseline (прод FEATURES, as-of шлях) vs candidate (extra_features=['Grid_Stress_High'],
legacy-шлях — extra_features вимикає as-of-уніфікацію, та сама вже задокументована
асиметрія методології). test_days=60 — вміщується в реальне ~74-денне вікно
нетривіального сигналу (перший реальний Grid_Stress_High==1 — 2026-06-26).

Запуск: python3 scratch/backtest_grid_stress_high.py
"""
import sys
import json

sys.path.insert(0, '.')

import src.modules.forecast_service.ml_pipeline as mt

TEST_DAYS = 60
RETRAIN_EVERY = 7

print("=== Baseline (продові FEATURES, as-of шлях) ===")
baseline = mt.walk_forward_backtest(
    test_days=TEST_DAYS, retrain_every_days=RETRAIN_EVERY,
    acknowledge_approximation=True,
)
print(json.dumps({k: v for k, v in baseline.items() if k != 'daily'}, indent=2, default=str))

print("\n=== Кандидат: + Grid_Stress_High (extra_features, legacy-шлях) ===")
candidate = mt.walk_forward_backtest(
    test_days=TEST_DAYS, retrain_every_days=RETRAIN_EVERY,
    acknowledge_approximation=True,
    extra_features=['Grid_Stress_High'],
)
print(json.dumps({k: v for k, v in candidate.items() if k != 'daily'}, indent=2, default=str))

print("\n=== Порівняння ===")
for key in ('mean_wape', 'last_7d_mean_mape', 'last_30d_mean_mape'):
    b = baseline.get(key)
    c = candidate.get(key)
    if b is not None and c is not None:
        delta_pct = (c - b) / b * 100.0
        print(f"{key}: baseline={b:.3f}  candidate={c:.3f}  delta={delta_pct:+.2f}%")
    else:
        print(f"{key}: baseline={b}  candidate={c}")

with open('scratch/backtest_grid_stress_high_results.json', 'w') as f:
    json.dump({
        'baseline': {k: v for k, v in baseline.items() if k != 'daily'},
        'candidate': {k: v for k, v in candidate.items() if k != 'daily'},
    }, f, indent=2, default=str)
print("\nSaved: scratch/backtest_grid_stress_high_results.json")
