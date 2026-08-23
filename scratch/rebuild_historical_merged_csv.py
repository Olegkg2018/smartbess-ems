"""
Перебудова historical_data_merged.csv після повторного живого фетчу всіх
історичних DAM/IDM місяців через виправлений fetch_oree_market_month
(CLAUDE.md п.26/27, scratch/refetch_historical_prices_kyiv_utc.py).

Ціна (Price/IDM_Price/DAM_IDM_Spread) тепер справжній UTC для ВСІЄЇ історії.
Погода/ENTSO-E/gas/Telegram/ГПВ-колонки в ІСНУЮЧОМУ файлі вже й так були
справжнім UTC (не залежали від oree Kyiv-конвертації) — беремо їх як є,
перемерджуємо з новою ціновою серією по Datetime.

Запуск: python3 scratch/rebuild_historical_merged_csv.py [--apply]
"""
import sys
import os
import glob

sys.path.insert(0, '.')

import pandas as pd
import src.modules.market_data_service.data_manager as dm
import src.modules.external_data_service.intraday_market as ext_idm


def main():
    apply = '--apply' in sys.argv

    # 1. Зібрати всю виправлену DAM-цінову історію з уже перезафетчених кешів.
    dam_files = sorted(glob.glob(os.path.join(dm.DATA_DIR, 'prices_cache', 'dam_*.csv')))
    frames = []
    for f in dam_files:
        d = pd.read_csv(f)
        d['Datetime'] = pd.to_datetime(d['Datetime'])
        frames.append(d)
    df_prices = pd.concat(frames).drop_duplicates(subset=['Datetime']).sort_values('Datetime').reset_index(drop=True)
    print(f"Price rows collected: {len(df_prices)}, range {df_prices['Datetime'].min()} .. {df_prices['Datetime'].max()}")

    # 2. Домерджити IDM_Price/DAM_IDM_Spread (той самий шлях, що продовий sync_realtime_data).
    df_prices = ext_idm.merge_idm_into_prices(df_prices)
    print(f"After IDM merge: {len(df_prices)} rows, IDM coverage: {df_prices['IDM_Price'].notna().sum()}")

    # 3. Узяти решту колонок (погода/ENTSO-E/gas/telegram/ГПВ) з ІСНУЮЧОГО файлу як є.
    old = pd.read_csv(dm.MERGED_DATA_PATH)
    old['Datetime'] = pd.to_datetime(old['Datetime'])
    other_cols = [c for c in old.columns if c not in ('Price', 'IDM_Price', 'DAM_IDM_Spread', 'Month', 'Hour')]
    old_other = old[other_cols]
    print(f"Old file: {len(old)} rows, keeping non-price columns: {[c for c in other_cols if c != 'Datetime']}")

    # 4. Перемердж — outer, щоб чесно зберегти обидві сторони (не викидати реальні дані).
    merged = df_prices.merge(old_other, on='Datetime', how='outer').sort_values('Datetime').reset_index(drop=True)
    print(f"Final merged: {len(merged)} rows, range {merged['Datetime'].min()} .. {merged['Datetime'].max()}")
    print(f"Price NaN: {merged['Price'].isna().sum()}, Temperature NaN: {merged['Temperature'].isna().sum() if 'Temperature' in merged.columns else 'N/A'}")

    if not apply:
        print("\nDRY RUN — not written. Re-run with --apply to save.")
        print(merged.head(3))
        print(merged.tail(3))
        return

    backup_path = dm.MERGED_DATA_PATH + '.premigration.bak'
    if not os.path.exists(backup_path):
        old.to_csv(backup_path, index=False)
        print(f"Backed up old file to {backup_path}")

    merged.to_csv(dm.MERGED_DATA_PATH, index=False)
    print(f"Wrote {len(merged)} rows to {dm.MERGED_DATA_PATH}")


if __name__ == '__main__':
    main()
