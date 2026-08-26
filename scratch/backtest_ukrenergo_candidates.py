"""
Ad-hoc бектест кандидатів НЕК "Укренерго" (Imbalance_Price_Lag_24/Mean_24h,
Grid_Outage_Official_Mean_24h) — див. scratch/merge_ukrenergo_candidate_features.py
для мерджу сирих колонок і feature_pipeline.py для лаг-обчислення.

Обидва датасети НЕ живі (кінець реального покриття ~кінець лютого -
кінець березня 2026) — тестове вікно ОБОВ'ЯЗКОВО має закінчуватись ДО
цієї межі, інакше з trailing test_days (рахуються від "сьогодні",
серпень 2026) кандидат буде 100% NaN на всі тестові дні і порівняння
нічого не покаже. Тому: тимчасова УРІЗАНА копія historical_data_merged.csv
(до 2026-03-10, з запасом до реальної межі покриття) підміняється замість
продового файлу лише на час цього прогону (monkeypatch MERGED_DATA_PATH),
потім відновлюється — прод-файл не чіпається.
"""
import sys
sys.path.insert(0, '.')
import json
import time
import shutil
import pandas as pd

import src.modules.market_data_service.data_manager as dm
from src.modules.forecast_service.ml_pipeline import walk_forward_backtest

REAL_PATH = dm.MERGED_DATA_PATH
TRUNCATED_PATH = 'data/historical_data_merged.truncated_for_ukrenergo_backtest.csv'
CUTOFF = '2026-03-10'

df = pd.read_csv(REAL_PATH)
df['Datetime'] = pd.to_datetime(df['Datetime'])
df_trunc = df[df['Datetime'] < CUTOFF].copy()
df_trunc.to_csv(TRUNCATED_PATH, index=False)
print(f"Truncated snapshot: {len(df_trunc)} rows, {df_trunc['Datetime'].min()} .. {df_trunc['Datetime'].max()}", flush=True)

dm.MERGED_DATA_PATH = TRUNCATED_PATH

results = {}


def run(name, **kwargs):
    print(f"=== {name} ===", flush=True)
    t0 = time.time()
    report = walk_forward_backtest(acknowledge_approximation=True, **kwargs)
    elapsed = round(time.time() - t0, 1)
    summary = report['summary']
    summary['elapsed_sec'] = elapsed
    results[name] = summary
    print(json.dumps(summary, indent=2), flush=True)
    with open('scratch/backtest_ukrenergo_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    return report


try:
    baseline = run('baseline_90d', test_days=90, retrain_every_days=7, model_type='lightgbm')

    imbalance = run(
        'imbalance_price_90d', test_days=90, retrain_every_days=7, model_type='lightgbm',
        extra_features=['Imbalance_Price_Lag_24', 'Imbalance_Price_Mean_24h'],
    )

    outage = run(
        'official_outage_90d', test_days=90, retrain_every_days=7, model_type='lightgbm',
        extra_features=['Grid_Outage_Official_Mean_24h'],
    )

    combined = run(
        'combined_90d', test_days=90, retrain_every_days=7, model_type='lightgbm',
        extra_features=['Imbalance_Price_Lag_24', 'Imbalance_Price_Mean_24h', 'Grid_Outage_Official_Mean_24h'],
    )
finally:
    dm.MERGED_DATA_PATH = REAL_PATH

print("\n=== SUMMARY ===")
for name, s in results.items():
    print(f"{name}: mean_wape={s.get('mean_wape'):.3f}  last_7d_mape={s.get('last_7d_mean_mape')}  last_30d_mape={s.get('last_30d_mean_mape')}  test_days={s.get('test_days')}")
