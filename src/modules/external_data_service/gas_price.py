"""
Реальна поточна ціна газу (EUR/MWh, аналог TTF Day-Ahead) з index.minfin.com.ua.
Публічне джерело, без ключа/реєстрації.

ВАЖЛИВЕ ОБМЕЖЕННЯ, виявлене при перевірці (scratch/recon_gas_price.py):
сторінка https://index.minfin.com.ua/ua/markets/gas/{дата}/ ЗАВЖДИ повертає
поточний знімок ринку незалежно від дати в URL (немає справжнього історичного
архіву по датах через цей шлях, хоча HTTP 200 повертається завжди — початкова
розвідка це неправильно прийняла за робочу історію, тому виправлено тут).

Тому цей модуль чесно НЕ вигадує історію назад. Замість цього — щоденний
накопичувальний лог: кожен запуск (з планувальника) дописує сьогоднішнє
реальне значення в persistent CSV. Через кілька місяців роботи це дає
реальний історичний ряд, що росте, замість фейкового np.random-шуму, який
був у add_generation_and_market_factors раніше. Дати до початку збору —
чесно відсутні (NaN), а не підмінені.
"""
import os
import re
import json
import time
import datetime
import requests
import pandas as pd

from src.core.config import settings

DATA_DIR = settings.DATA_DIR
CACHE_DIR = os.path.join(DATA_DIR, "external_cache", "gas_price")
DAILY_LOG_PATH = os.path.join(DATA_DIR, "external_cache", "gas_price_daily_log.csv")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

_TABLE_RE = re.compile(r"<table border=0 cellspacing=0 cellpadding=3 class='line'>.*?</table>", re.S)
_VALUE_RE = re.compile(r"([\d,]+)\s*(EUR\s*/\s*МВт|GB\s*p\s*/\s*thm|USD\s*/\s*MMBtu)")


def _parse_gas_tables(html: str) -> dict:
    result = {}
    for table_html in _TABLE_RE.findall(html):
        text = re.sub(r"<[^>]+>", " ", table_html)
        text = text.replace("&nbsp;", " ").replace("&middot;", ".")
        text = re.sub(r"\s+", " ", text).strip()
        m = _VALUE_RE.search(text)
        if not m:
            continue
        value = float(m.group(1).replace(",", "."))
        unit = m.group(2)
        if "EUR" in unit:
            result["eur_mwh"] = value
        elif "GB" in unit:
            result["gbp_thm"] = value
        elif "USD" in unit:
            result["usd_mmbtu"] = value
    return result


def fetch_gas_price_now() -> dict:
    """Реальна поточна ринкова ціна газу. Не прив'язана до конкретної історичної дати."""
    url = "https://index.minfin.com.ua/ua/markets/gas/"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return _parse_gas_tables(r.text)
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
    return {}


def append_daily_snapshot() -> dict:
    """
    Викликається раз на день (з планувальника). Дописує сьогоднішнє реальне
    значення у постійний лог, якщо за сьогодні ще не записано.
    """
    parsed = fetch_gas_price_now()
    if not parsed:
        return {}

    today_str = datetime.date.today().isoformat()
    os.makedirs(os.path.dirname(DAILY_LOG_PATH), exist_ok=True)

    if os.path.exists(DAILY_LOG_PATH):
        df = pd.read_csv(DAILY_LOG_PATH)
    else:
        df = pd.DataFrame(columns=["Date", "Gas_Price_EUR_MWh"])

    if today_str in set(df["Date"].astype(str)):
        return parsed

    new_row = pd.DataFrame([{"Date": today_str, "Gas_Price_EUR_MWh": parsed.get("eur_mwh")}])
    df = pd.concat([df, new_row], ignore_index=True)
    df.to_csv(DAILY_LOG_PATH, index=False)
    return parsed


def load_daily_log() -> pd.DataFrame:
    """Реальний накопичений ряд (росте з кожним днем роботи системи). Може бути коротким на старті."""
    if not os.path.exists(DAILY_LOG_PATH):
        return pd.DataFrame(columns=["Date", "Gas_Price_EUR_MWh"])
    df = pd.read_csv(DAILY_LOG_PATH)
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)
