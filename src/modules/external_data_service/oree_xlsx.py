"""
Погодинні ОБСЯГИ РДН і діапазон цін ВДР з oree.com.ua (2026-09-28).

`fetch_oree_market_month` (data_manager.py) бере з `pricectr/data_view` лише
ЦІНУ. Той самий Оператор ринку віддає добовий файл
`PXS/downloadxlsx/{ДД.ММ.РРРР}/{DAM|IDM}/2` (знайдено в open-source проєктах
LexGlu/energy-data-api, SergejKolesnik/entsoe-ua-prices), де на кожну годину є:

  РДН: ціна, обсяг продажу/купівлі, ЗАЯВЛЕНИЙ обсяг продажу/купівлі
  ВДР: середньозважена/мінімальна/максимальна/остання ціна, обсяги, заявлені обсяги

Заявлені обсяги РДН — пряма міра профіциту/дефіциту пропозиції (напр.
27.09.2026 01:00: заявлено продаж 4192 МВт·год проти купівлі 2632) — кандидат
в ознаки прогнозу (лише з лагом ≥24г: файл на добу D публікується ~13:00 D-1,
тож для прогнозу D+1, що рахується о 06:00 D, доступні дані доби D).
Мін/макс/остання ціна ВДР — реальний діапазон угод для ВДР-фолбеку замість
оцінки "РДН + медіана різниці".

Файл — формально .xls (OLE2), але зі зламаною таблицею розміщення секторів:
xlrd без `ignore_workbook_corruption=True` падає з "Workbook corruption"
(той самий "malformed legacy OLE .xls", що описаний в entsoe-ua-prices).
"""
import os
import time
import datetime

import numpy as np
import pandas as pd
import requests

from src.core.config import settings
from src.core.time_utils import assert_naive_utc

URL_TEMPLATE = "https://www.oree.com.ua/index.php/PXS/downloadxlsx/{date}/{market}/2"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
CACHE_DIR = os.path.join(settings.DATA_DIR, "oree_xlsx_cache")

# Українські заголовки файлу -> наші назви колонок. Ціна зберігається теж —
# для звірки з уже наявним `Price`/`IDM_Price` (той самий Оператор ринку,
# мусить збігатися; розбіжність = ознака помилки парсингу/часу).
COLUMN_MAP = {
    'DAM': {
        'Ціна, грн/МВт.год': 'DAM_Price_Xlsx',
        'Обсяг продажу, МВт.год': 'DAM_Sell_Volume_MWh',
        'Обсяг купівлі, МВт.год': 'DAM_Buy_Volume_MWh',
        'Заявлений обсяг продажу, МВт.год': 'DAM_Sell_Declared_MWh',
        'Заявлений обсяг купівлі, МВт.год': 'DAM_Buy_Declared_MWh',
    },
    'IDM': {
        'Ціна, грн/МВт.год': 'IDM_Price_Xlsx',
        'Мінімальна ціна, грн/МВт.год': 'IDM_Min_Price',
        'Максимальна ціна, грн/МВт.год': 'IDM_Max_Price',
        'Остання ціна, грн/МВт.год': 'IDM_Last_Price',
        'Обсяг продажу, МВт.год': 'IDM_Sell_Volume_MWh',
        'Обсяг купівлі, МВт.год': 'IDM_Buy_Volume_MWh',
        'Заявлений обсяг продажу, МВт.год': 'IDM_Sell_Declared_MWh',
        'Заявлений обсяг купівлі, МВт.год': 'IDM_Buy_Declared_MWh',
    },
}


def _parse_number(v):
    """"5 600,00" / "2 581,2" (пробіли-роздільники, кома) / число / порожньо."""
    if v is None:
        return np.nan
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace('\xa0', '').replace(' ', '').replace(',', '.').strip()
    if s in ('', '-', '—'):
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def _read_xls_bytes(content):
    import xlrd
    with open(os.devnull, 'w') as devnull:
        book = xlrd.open_workbook(file_contents=content, ignore_workbook_corruption=True, logfile=devnull)
    return pd.read_excel(book, engine='xlrd', header=0, dtype=object)


def _hour_labels_to_utc(date_str, labels):
    """
    Години файлу — "01:00".."24:00" (година, що ЗАКІНЧУЄТЬСЯ о N, київський
    час), тобто позиція i = київська година i. Добу з переходом на літній час
    (23 рядки) і зимовий (25 рядків) обробляємо за ПОРЯДКОМ рядків, а не за
    текстом мітки: беремо реальну київську північ доби і додаємо i годин
    ФІЗИЧНОГО часу в UTC — так 23/25-годинні доби лягають на правильні UTC-
    мітки без вгадування формату повторної години.
    """
    kyiv_midnight = pd.Timestamp(date_str).tz_localize('Europe/Kyiv')
    start_utc = kyiv_midnight.tz_convert('UTC').tz_localize(None)
    return [start_utc + pd.Timedelta(hours=i) for i in range(len(labels))]


def parse_oree_day_file(content, date_str, market):
    """Байти добового файлу -> DataFrame [Datetime (наївний UTC), колонки COLUMN_MAP[market]]."""
    raw = _read_xls_bytes(content)
    if raw.empty or raw.shape[1] < 2:
        return pd.DataFrame()
    first_col = raw.columns[0]
    raw = raw[raw[first_col].astype(str).str.match(r'^\s*\d{1,2}:\d{2}')].reset_index(drop=True)
    if raw.empty:
        return pd.DataFrame()

    mapping = COLUMN_MAP[market]
    out = pd.DataFrame({'Datetime': _hour_labels_to_utc(date_str, raw[first_col].tolist())})
    for ua_name, our_name in mapping.items():
        col = next((c for c in raw.columns if str(c).strip() == ua_name), None)
        out[our_name] = raw[col].map(_parse_number) if col is not None else np.nan
    assert_naive_utc(out, source=f'oree_xlsx({market})')
    return out


def fetch_oree_day(date_str, market, use_cache=True, timeout=30, max_attempts=3):
    """
    Одна доба (`date_str` = 'YYYY-MM-DD', київська дата) одного ринку.
    Минулі доби кешуються на диску назавжди (опубліковані дані OREE не
    змінюються); сьогодні/майбутні — ніколи (можуть бути ще неповні).
    Повертає порожній DataFrame, якщо файл без годин (доба ще не торгувалась).
    """
    cache_path = os.path.join(CACHE_DIR, market.lower(), f"{date_str}.csv")
    today_kyiv = pd.Timestamp.now(tz='Europe/Kyiv').date()
    is_past = pd.Timestamp(date_str).date() < today_kyiv

    if use_cache and is_past and os.path.exists(cache_path):
        df = pd.read_csv(cache_path)
        df['Datetime'] = pd.to_datetime(df['Datetime'])
        return df

    d = pd.Timestamp(date_str)
    url = URL_TEMPLATE.format(date=d.strftime('%d.%m.%Y'), market=market)
    last_error = None
    for attempt in range(max_attempts):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            # WAF інколи віддає HTML замість файлу (див. п.54 CLAUDE.md) —
            # справжній .xls починається з OLE2-сигнатури.
            if r.status_code == 200 and r.content[:4] == b'\xd0\xcf\x11\xe0':
                df = parse_oree_day_file(r.content, date_str, market)
                if is_past and not df.empty:
                    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                    tmp = cache_path + '.tmp'
                    df.to_csv(tmp, index=False)
                    os.replace(tmp, cache_path)
                return df
            last_error = f"HTTP {r.status_code}, {len(r.content)}B, not OLE2"
        except Exception as e:
            last_error = str(e)
        if attempt < max_attempts - 1:
            time.sleep(2 * (attempt + 1))
    print(f"oree_xlsx: failed {market} {date_str}: {last_error}")
    return pd.DataFrame()


def merge_into(df, start_date, end_date, overwrite_prices=True):
    """
    Додає до `df` (колонки Datetime, Price, IDM_Price, ...) колонки з добових
    файлів OREE за [start_date, end_date] — для щоденного sync_realtime_data
    (поточний місяць). Минулі доби з кешу, сьогодні/завтра — живий запит.

    overwrite_prices=True: Price/IDM_Price беруться з файлу (+ перерахунок
    DAM_IDM_Spread) — `data_view`-шлях на добах переходу годинника розкладає
    24 колонки за настінним часом і зсуває години після переходу на 1г
    (CLAUDE.md п.59); на звичайних добах обидва джерела збігаються до копійки.
    Якщо файл недоступний (WAF/мережа) — df повертається як є, без падіння.
    """
    frames = {}
    for market in ('DAM', 'IDM'):
        try:
            frames[market] = fetch_oree_range(start_date, end_date, market, pause_seconds=0.2, progress_every=0)
        except Exception as e:
            print(f"oree_xlsx.merge_into: {market} failed: {e}")
            frames[market] = pd.DataFrame()

    out = df.copy()
    out['Datetime'] = pd.to_datetime(out['Datetime'])
    for market, extra in frames.items():
        if extra.empty:
            continue
        price_col = 'DAM_Price_Xlsx' if market == 'DAM' else 'IDM_Price_Xlsx'
        cols = [c for c in extra.columns if c != 'Datetime']
        out = out.drop(columns=[c for c in cols if c in out.columns]).merge(extra, on='Datetime', how='left')
        if overwrite_prices:
            target = 'Price' if market == 'DAM' else 'IDM_Price'
            if target in out.columns:
                has = out[price_col].notna()
                out.loc[has, target] = out.loc[has, price_col]
        out = out.drop(columns=[price_col])
    if overwrite_prices and {'Price', 'IDM_Price'} <= set(out.columns):
        out['DAM_IDM_Spread'] = out['IDM_Price'] - out['Price']
    return out


def fetch_oree_range(start_date, end_date, market, pause_seconds=0.3, progress_every=100):
    """
    Діапазон діб [start_date, end_date] включно. Для первинного бекфілу
    історії — ввічлива пауза між живими запитами (кешовані доби без паузи).
    """
    frames = []
    days = pd.date_range(start_date, end_date, freq='D')
    for i, d in enumerate(days):
        date_str = d.strftime('%Y-%m-%d')
        cache_path = os.path.join(CACHE_DIR, market.lower(), f"{date_str}.csv")
        was_cached = os.path.exists(cache_path)
        df = fetch_oree_day(date_str, market)
        if not df.empty:
            frames.append(df)
        if not was_cached:
            time.sleep(pause_seconds)
        if progress_every and (i + 1) % progress_every == 0:
            print(f"oree_xlsx {market}: {i + 1}/{len(days)} days ({date_str})")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames).drop_duplicates(subset=['Datetime']).sort_values('Datetime').reset_index(drop=True)
