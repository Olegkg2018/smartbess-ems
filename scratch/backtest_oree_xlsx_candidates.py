"""
Бектест кандидатів з добового файлу OREE (2026-09-28): заявлені/виконані
обсяги РДН і діапазон цін ВДР (feature_pipeline.build_training_table).

ПРАВИЛО (MEMORY.md §4, п.51 CLAUDE.md): extra_features-шлях вимикає as-of-
уніфікацію і сам по собі дає ІНШІ числа, ніж продовий as-of бейзлайн,
незалежно від сигналу. Тому кожен кандидат порівнюється з КОНТРОЛЕМ —
завідомо беззмістовною константною колонкою, прогнаною тим самим
extra_features-шляхом на тому самому вікні. Висновок — лише "кандидат
мінус контроль", не "кандидат мінус бейзлайн".

Запуск: python3 scratch/backtest_oree_xlsx_candidates.py [test_days]
"""
import sys
import json
import time

sys.path.insert(0, '.')

import src.modules.forecast_service.ml_pipeline as mt
import src.modules.forecast_service.feature_pipeline as fp

TEST_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 90
RETRAIN_EVERY = 7

_original_build = fp.build_training_table


def _build_with_control(df_raw, **kwargs):
    df = _original_build(df_raw, **kwargs)
    df['Control_Zero'] = 0.0
    return df


fp.build_training_table = _build_with_control

# get_combined_historical_data() сам викликає sync_realtime_data(), якщо CSV
# старший за 15 хв — синк перебудовує поточний місяць БЕЗ колонок з файлу
# OREE, і різні прогони бачили б різні дані. Для бектесту — фіксований знімок.
import src.modules.market_data_service.data_manager as dm
dm.sync_realtime_data = lambda force=False: True

RUNS = {
    'control': ['Control_Zero'],
    'declared_ratio': ['DAM_Declared_Ratio_Lag_24', 'DAM_Declared_Ratio_Mean_24h'],
    'declared_levels': ['DAM_Buy_Declared_Lag_24', 'DAM_Sell_Declared_Lag_24', 'DAM_Buy_Volume_Lag_24'],
    'idm_range': ['IDM_Range_Lag_48'],
    'all': ['DAM_Declared_Ratio_Lag_24', 'DAM_Declared_Ratio_Mean_24h',
            'DAM_Buy_Declared_Lag_24', 'DAM_Sell_Declared_Lag_24', 'DAM_Buy_Volume_Lag_24',
            'IDM_Range_Lag_48'],
}

results = {}
for name, extra in RUNS.items():
    t0 = time.time()
    res = mt.walk_forward_backtest(
        test_days=TEST_DAYS, retrain_every_days=RETRAIN_EVERY,
        acknowledge_approximation=True, extra_features=extra,
    )
    s = res['summary']
    results[name] = {'extra_features': extra, 'summary': s, 'daily': res['daily'],
                     'elapsed_s': round(time.time() - t0, 1)}
    print(f"{name}: mean_wape={s.get('mean_wape'):.3f} last_7d_mape={s.get('last_7d_mean_mape'):.2f} "
          f"last_30d_mape={s.get('last_30d_mean_mape')} ({results[name]['elapsed_s']}s)", flush=True)

print("\n=== Кандидат мінус КОНТРОЛЬ (від'ємне = краще) ===")
ctrl = results['control']['summary']
for name in RUNS:
    if name == 'control':
        continue
    s = results[name]['summary']
    parts = []
    for key in ('mean_wape', 'median_mape', 'last_7d_mean_mape', 'last_30d_mean_mape'):
        c, v = ctrl.get(key), s.get(key)
        if c is not None and v is not None:
            parts.append(f"{key} {v:.2f} vs {c:.2f} ({(v - c) / c * 100:+.1f}%)")
    print(f"{name}: " + "; ".join(parts))

# Скільки днів кандидат кращий за контроль (стійкість, а не лише середнє).
ctrl_daily = {d['date']: d['wape'] for d in results['control']['daily']}
for name in RUNS:
    if name == 'control':
        continue
    pairs = [(d['wape'], ctrl_daily[d['date']]) for d in results[name]['daily'] if d['date'] in ctrl_daily]
    better = sum(1 for v, c in pairs if v < c)
    print(f"{name}: кращий за контроль у {better}/{len(pairs)} днях")

out = f'scratch/backtest_oree_xlsx_results_{TEST_DAYS}d.json'
with open(out, 'w') as f:
    json.dump(results, f, indent=2, default=str)
print(f"\nSaved: {out}")
