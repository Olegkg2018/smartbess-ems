"""
Ad-hoc: мерджить 2 реальні датасети НЕК "Укренерго" (energy-map.info,
завантажено користувачем вручну 2026-08-26 у /home/oleg/claude/dani/) у
historical_data_merged.csv як НОВІ сирі колонки-кандидати для бектесту:

- Imbalance_Price_UAH — "Ціна небалансу електричної енергії" (Фактичні
  ціни небалансів), погодинно, 01.10.2019-28.02.2026. Фільтр на головну
  континентальну зону (обидва історичні лейбли — "ОЕС України" до
  24.02.2022 і "ОЕС України (синхронізована з ENTSO-E)" після, той самий
  день аварійної синхронізації — підтверджено прямим групуванням по
  датах), без окремого Бурштинського острова.
- Grid_Outage_Official_Active — "Запровадження відключень електроенергії"
  (так/ні) з офіційного ГПВ-датасету Укренерго, 01.01.2023-31.03.2026 —
  3+ роки реальної історії, значно глибше за поточний Telegram-парсинг
  (Strike_Oblasts_Affected, лише ~2 місяці).

НЕ чіпає FEATURES/build_forecast_feature_matrix — лише сирі колонки для
подальшого лаг-експерименту через build_training_table+extra_features
(як EU_DAM_Price_Lag_24 раніше). Не завершено датасети (ще 2: граничні
ціни балансуючого ринку, маржинальні ціни активованої балансуючої
енергії) — НЕ включені в цей перший раунд, свідомо звужено до
найінформативніших двох кандидатів.

Запуск: python3 scratch/merge_ukrenergo_candidate_features.py [--apply]
Без --apply — dry-run (показує статистику покриття, нічого не пише).
"""
import sys
sys.path.insert(0, '.')
import shutil
import datetime
import pandas as pd

from src.core.time_utils import kyiv_to_utc

DANI_DIR = '/home/oleg/claude/dani'
CSV_PATH = 'data/historical_data_merged.csv'

MAIN_ZONE_LABELS = {'ОЕС України', 'ОЕС України (синхронізована з ENTSO-E)'}


def parse_hour_range_start(s: str) -> int:
    """'00:00-01:00' -> 0. 'Операційний період'/'Година' (string range)."""
    return int(str(s).split(':')[0])


def build_imbalance_price():
    df = pd.read_excel(f'{DANI_DIR}/2026_04_01_faktychni_tciny_nebalansiv.xlsx', sheet_name='Дані')
    df = df[df['Торгова зона'].isin(MAIN_ZONE_LABELS)].copy()
    df['hour'] = df['Операційний період'].apply(parse_hour_range_start)
    df['Datetime'] = df.apply(lambda r: kyiv_to_utc(r['Дата'].strftime('%Y-%m-%d'), r['hour']), axis=1)
    df = df[['Datetime', 'Ціна небалансу електричної енергії, грн/МВт*год']].rename(
        columns={'Ціна небалансу електричної енергії, грн/МВт*год': 'Imbalance_Price_UAH'})
    dupes = df['Datetime'].duplicated().sum()
    if dupes:
        print(f"WARNING: {dupes} duplicate Datetime rows in imbalance price after zone filter — keeping first.")
        df = df.drop_duplicates(subset='Datetime', keep='first')
    return df


def build_outage_flag():
    df = pd.read_excel(f'{DANI_DIR}/2026_04_01_electricity_outages.xlsx', sheet_name='Дані')
    df['hour'] = df['Година'].apply(parse_hour_range_start)
    df['Datetime'] = df.apply(lambda r: kyiv_to_utc(r['Дата'].strftime('%Y-%m-%d'), r['hour']), axis=1)
    df['Grid_Outage_Official_Active'] = (df['Запровадження відключень електроенергії'] == 'так').astype(int)
    df = df[['Datetime', 'Grid_Outage_Official_Active']]
    dupes = df['Datetime'].duplicated().sum()
    if dupes:
        print(f"WARNING: {dupes} duplicate Datetime rows in outage flag — keeping first.")
        df = df.drop_duplicates(subset='Datetime', keep='first')
    return df


def main():
    apply = '--apply' in sys.argv

    base = pd.read_csv(CSV_PATH)
    base['Datetime'] = pd.to_datetime(base['Datetime'])
    print(f"Base CSV: {len(base)} rows, {base['Datetime'].min()} .. {base['Datetime'].max()}")

    imb = build_imbalance_price()
    print(f"Imbalance_Price_UAH: {len(imb)} real hourly rows, {imb['Datetime'].min()} .. {imb['Datetime'].max()}")

    out = build_outage_flag()
    print(f"Grid_Outage_Official_Active: {len(out)} real hourly rows, {out['Datetime'].min()} .. {out['Datetime'].max()}, "
          f"{out['Grid_Outage_Official_Active'].sum()} active hours ({out['Grid_Outage_Official_Active'].mean()*100:.1f}%)")

    merged = base.merge(imb, on='Datetime', how='left').merge(out, on='Datetime', how='left')

    n_imb_matched = merged['Imbalance_Price_UAH'].notna().sum()
    n_out_matched = merged['Grid_Outage_Official_Active'].notna().sum()
    print(f"\nAfter merge onto {len(base)} base rows:")
    print(f"  Imbalance_Price_UAH matched: {n_imb_matched} ({n_imb_matched/len(base)*100:.1f}%)")
    print(f"  Grid_Outage_Official_Active matched: {n_out_matched} ({n_out_matched/len(base)*100:.1f}%)")

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to write.")
        return

    backup_path = f"{CSV_PATH}.before_ukrenergo_merge_{datetime.date.today().isoformat()}"
    shutil.copy(CSV_PATH, backup_path)
    print(f"Backup: {backup_path}")

    merged.to_csv(CSV_PATH, index=False)
    print(f"Written: {CSV_PATH} ({len(merged)} rows, {len(merged.columns)} columns)")


if __name__ == '__main__':
    main()
