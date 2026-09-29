"""
Мердж погодинних обсягів РДН / діапазону цін ВДР з добових файлів OREE
(src/modules/external_data_service/oree_xlsx.py, кеш data/oree_xlsx_cache/)
у data/historical_data_merged.csv — нові сирі колонки для бектесту
кандидатів (feature_pipeline.FEATURE_SOURCE_COLUMNS). Той самий шаблон, що
scratch/merge_ukrenergo_candidate_features.py (CLAUDE.md п.47).

Спершу заповнити кеш (фоновий бекфіл):
    python3 -c "from src.modules.external_data_service import oree_xlsx as ox; ox.fetch_oree_range('2021-01-01', '<завтра>', 'DAM')"
    (те саме для 'IDM')

Запуск:
    python3 scratch/merge_oree_xlsx_volumes.py            # dry-run: покриття + звірка цін
    python3 scratch/merge_oree_xlsx_volumes.py --apply    # бекап CSV + запис
    python3 scratch/merge_oree_xlsx_volumes.py --apply --fix-prices   # + виправити зсунуті ціни
"""
import os
import sys
import glob
import shutil
import datetime

sys.path.insert(0, '.')

import pandas as pd

import src.modules.market_data_service.data_manager as dm
from src.modules.external_data_service import oree_xlsx as ox

APPLY = '--apply' in sys.argv
# --fix-prices: перезаписати Price/IDM_Price значеннями з добового файлу OREE
# (і перерахувати DAM_IDM_Spread). Знайдено 2026-09-28: весь жовтень
# 2021-2024 (РДН) і 2021-2025 (ВДР) у CSV лежить у НЕконвертованому київському
# часі як UTC — зсув рівно +3г (CSV[t] == файл[t-3h] у ~95% розбіжних годин,
# решта — години після переходу на зимовий час, де зсув 2г). Ймовірна
# причина — міграція Kyiv->UTC (CLAUDE.md п.27): жовтень падав на
# ambiguous='infer', і в історію потрапив старий неконвертований місячний
# кеш. Плюс доби переходу годинника (старий шлях розкладав 24 колонки за
# настінним часом; файл нумерує фізичні години по порядку). На всіх
# звичайних добах файл і CSV збігаються до копійки — файл вважаємо еталоном.
FIX_PRICES = '--fix-prices' in sys.argv
NEW_COLUMNS = [c for m in ('DAM', 'IDM') for c in ox.COLUMN_MAP[m].values()
               if c not in ('DAM_Price_Xlsx', 'IDM_Price_Xlsx')]


def load_cache(market):
    files = sorted(glob.glob(os.path.join(ox.CACHE_DIR, market.lower(), '*.csv')))
    if not files:
        return pd.DataFrame()
    df = pd.concat((pd.read_csv(f) for f in files), ignore_index=True)
    df['Datetime'] = pd.to_datetime(df['Datetime'])
    return df.drop_duplicates(subset=['Datetime']).sort_values('Datetime')


dam = load_cache('DAM')
idm = load_cache('IDM')
print(f"cache: DAM {len(dam)} rows ({dam.Datetime.min()} .. {dam.Datetime.max()}), "
      f"IDM {len(idm)} rows ({idm.Datetime.min()} .. {idm.Datetime.max()})")

hist = pd.read_csv(dm.MERGED_DATA_PATH)
hist['Datetime'] = pd.to_datetime(hist['Datetime'])
print(f"historical CSV: {len(hist)} rows, {hist.Datetime.min()} .. {hist.Datetime.max()}")
# Дублі міток (клас багу "межа місяця", CLAUDE.md п.51 — код виправлено
# 2026-09-08, але локальний CSV зберіг дублі 31.08 21:00-23:00 з синку до
# фіксу). Рядки-дублі ідентичні — лишаємо останній.
n_dup = int(hist['Datetime'].duplicated().sum())
if n_dup:
    hist = hist.drop_duplicates(subset=['Datetime'], keep='last').reset_index(drop=True)
    print(f"dropped {n_dup} duplicate Datetime rows")

merged = hist.drop(columns=[c for c in NEW_COLUMNS if c in hist.columns])
merged = merged.merge(dam, on='Datetime', how='left').merge(idm, on='Datetime', how='left')
assert len(merged) == len(hist), "merge змінив кількість рядків — дублі в кеші?"

# Звірка: ціна з файлу мусить збігатися з уже наявною (той самий Оператор
# ринку). Розбіжності очікувані ЛИШЕ на добах переходу годинника — там
# старий data_view-шлях розкладав 24 колонки за настінним часом (CLAUDE.md
# п.59), а файл нумерує фізичні години по порядку.
for ours, theirs in (('Price', 'DAM_Price_Xlsx'), ('IDM_Price', 'IDM_Price_Xlsx')):
    both = merged[[ours, theirs, 'Datetime']].dropna()
    bad = both[(both[ours] - both[theirs]).abs() > 0.01]
    bad_days = sorted({(d + pd.Timedelta(hours=3)).date() for d in bad['Datetime']})
    print(f"{ours} vs {theirs}: compared {len(both)}, mismatched {len(bad)} hours on {len(bad_days)} days: "
          f"{[str(d) for d in bad_days[:20]]}{' ...' if len(bad_days) > 20 else ''}")

if FIX_PRICES:
    for ours, theirs in (('Price', 'DAM_Price_Xlsx'), ('IDM_Price', 'IDM_Price_Xlsx')):
        has = merged[theirs].notna()
        changed = has & (((merged[ours] - merged[theirs]).abs() > 0.01) | merged[ours].isna())
        merged.loc[has, ours] = merged.loc[has, theirs]
        print(f"--fix-prices: {ours} overwritten in {int(changed.sum())} hours")
    merged['DAM_IDM_Spread'] = merged['IDM_Price'] - merged['Price']

merged = merged.drop(columns=['DAM_Price_Xlsx', 'IDM_Price_Xlsx'])
for c in NEW_COLUMNS:
    print(f"  {c}: {merged[c].notna().mean() * 100:.1f}% coverage")

if not APPLY:
    print("\nDry-run — нічого не записано. Запустити з --apply для запису.")
    sys.exit(0)

backup = f"{dm.MERGED_DATA_PATH}.before_oree_xlsx_merge_{datetime.date.today():%Y%m%d}"
shutil.copy2(dm.MERGED_DATA_PATH, backup)
tmp = dm.MERGED_DATA_PATH + '.tmp'
merged.to_csv(tmp, index=False)
os.replace(tmp, dm.MERGED_DATA_PATH)
print(f"\nWritten {dm.MERGED_DATA_PATH} (backup: {backup})")
