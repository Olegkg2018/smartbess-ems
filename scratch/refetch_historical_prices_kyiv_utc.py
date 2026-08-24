"""
Форсований повторний живий фетч усіх історичних місяців DAM/IDM з
oree.com.ua через ВЖЕ ВИПРАВЛЕНИЙ fetch_oree_market_month (Kyiv->UTC
конвертація) — потрібно для CLAUDE.md п.26/27: закешовані місяці до
2026-08 (крім поточного, який завжди живий) писались СТАРИМ (наївним,
без конвертації) кодом. Замість математичного зсуву вже закешованих
значень (що конфліктувало б із ВЖЕ коректно UTC-мітимими погодою/ENTSO-E
в тому ж об'єднаному файлі — CLAUDE.md п.26 знахідка Explore-агента),
чесно перезапитуємо ті самі реальні дані з джерела (oree.com.ua),
тепер через правильний код. Не вигадуємо нічого — той самий детермінований
реальний ряд, просто правильно сконвертований.

Серпень 2026 (поточний місяць) НЕ чіпаємо — він і так завжди живий і вже
коректний.

Запуск: python3 scratch/refetch_historical_prices_kyiv_utc.py
"""
import os
import sys
import time

sys.path.insert(0, '.')

import src.modules.market_data_service.data_manager as dm


def all_months_since(start_year, start_month):
    """Явний діапазон 2021-01..поточний місяць — НЕ покладаємось на те, які
    файли вже закешовані (на VPS кеш виявився розрідженим, лише останні
    кілька місяців, на відміну від локального dev — historical_data_merged.csv
    там колись насіявся інакше, без повного помісячного кешу)."""
    now = __import__('datetime').datetime.now()
    out = []
    y, m = start_year, start_month
    while (y, m) <= (now.year, now.month):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def refetch_all(subdir, prefix, market, value_col):
    months = all_months_since(2021, 1)
    now = __import__('datetime').datetime.now()
    ok, failed = [], []
    for year, month in months:
        if year == now.year and month == now.month:
            print(f"skip current month {year}-{month:02d} (always live, already correct)")
            continue
        cache_path = os.path.join(dm.DATA_DIR, subdir, f"{prefix}{year}_{month:02d}.csv")
        bak_path = cache_path + '.premigration.bak'
        had_cache = os.path.exists(cache_path)
        if had_cache:
            os.rename(cache_path, bak_path)
        t0 = time.time()
        df = dm.fetch_oree_market_month(month, year, market=market, value_col=value_col, cache_subdir=subdir)
        dt = time.time() - t0
        if df.empty:
            print(f"FAILED {year}-{month:02d} ({dt:.2f}s) — empty result, restoring old cache")
            if had_cache:
                os.rename(bak_path, cache_path)
            failed.append((year, month))
        else:
            print(f"OK {year}-{month:02d} ({dt:.2f}s, {len(df)} rows) first={df['Datetime'].iloc[0]}")
            ok.append((year, month))
            if had_cache and os.path.exists(bak_path):
                os.remove(bak_path)
    return ok, failed


print("=== DAM ===")
dam_ok, dam_failed = refetch_all('prices_cache', 'dam_', 'DAM', 'Price')
print("=== IDM ===")
idm_ok, idm_failed = refetch_all('idm_cache', 'idm_', 'IDM', 'IDM_Price')

print("\n=== SUMMARY ===")
print(f"DAM: {len(dam_ok)} ok, {len(dam_failed)} failed: {dam_failed}")
print(f"IDM: {len(idm_ok)} ok, {len(idm_failed)} failed: {idm_failed}")
