"""
Реальні транскордонні перетоки потужності України з ENTSO-E Transparency
Platform (потрібен безкоштовний security token, зареєстрований користувачем).

Розвідка (scratch/recon_entsoe.py) показала важливий факт: Україна повністю
припинила публікацію генерації по типах (АЕС/ГЕС/ТЕС) в ENTSO-E рівно
25.02.2022 (наступного дня після повномасштабного вторгнення) і не
відновлювала — це воєнне обмеження даних, а не збій. Тому AGGREGATED
GENERATION PER TYPE тут НЕ використовується як ознака: вона була б реальною
14 місяців, а потім назавжди відсутня для всього періоду, в якому реально
працює BESS, — саме та помилка (ознака, недоступна в момент прогнозу), якої
проєкт мав позбутися.

Натомість транскордонні перетоки (documentType=A11, Cross-Border Physical
Flow) звітуються СУСІДНЬОЮ стороною (Польща/Румунія/Словаччина/Угорщина/
Молдова) — і ці звіти не залежать від обмежень України. Підтверджено:
дані є безперервно з 2021 по сьогодні на всіх п'яти напрямках.
"""
import os
import time
import datetime
import requests
import pandas as pd
import xml.etree.ElementTree as ET

from src.core.config import settings

DATA_DIR = settings.DATA_DIR
CACHE_DIR = os.path.join(DATA_DIR, "external_cache", "entsoe_flow")
TOKEN = settings.ENTSOE_API_KEY
BASE_URL = "https://web-api.tp.entsoe.eu/api"

UA_DOMAIN = "10Y1001C--00003F"
BORDER_DOMAINS = {
    "PL": "10YPL-AREA-----S",
    "RO": "10YRO-TEL------P",
    "SK": "10YSK-SEPS-----K",
    "HU": "10YHU-MAVIR----U",
    "MD": "10Y1001A1001A990",
}

NS = {"ns": "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:0"}
_RESOLUTION_MINUTES = {"PT15M": 15, "PT30M": 30, "PT60M": 60}


def _parse_flow_xml(xml_text: str) -> pd.DataFrame:
    """Розгортає TimeSeries/Period/Point (curveType A03 — значення тримається
    до наступної точки) в почасовий ряд Datetime/MW."""
    records = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return pd.DataFrame(columns=["Datetime", "MW"])

    for ts in root.iter("{urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:0}TimeSeries"):
        for period in ts.iter("{urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:0}Period"):
            start_el = period.find(".//ns:timeInterval/ns:start", NS)
            res_el = period.find("ns:resolution", NS)
            if start_el is None or res_el is None:
                continue
            period_start = pd.to_datetime(start_el.text)
            step_minutes = _RESOLUTION_MINUTES.get(res_el.text, 60)

            points = []
            for point in period.findall("ns:Point", NS):
                pos = int(point.find("ns:position", NS).text)
                qty = float(point.find("ns:quantity", NS).text)
                points.append((pos, qty))
            points.sort()

            # curveType A03: значення тримається до наступної явної точки
            for i, (pos, qty) in enumerate(points):
                next_pos = points[i + 1][0] if i + 1 < len(points) else pos + 1
                for p in range(pos, next_pos):
                    dt = period_start + pd.Timedelta(minutes=step_minutes * (p - 1))
                    records.append({"Datetime": dt, "MW": qty})

    if not records:
        return pd.DataFrame(columns=["Datetime", "MW"])
    df = pd.DataFrame(records).sort_values("Datetime").reset_index(drop=True)
    return df


def fetch_flow_year(in_domain: str, out_domain: str, year: int, direction_label: str) -> pd.DataFrame:
    """Один рік перетоку в одному запиті (ENTSO-E дозволяє діапазон до року).
    Кешується на диск — рік у минулому більше ніколи не перезапитується."""
    cache_path = os.path.join(CACHE_DIR, f"{direction_label}_{year}.csv")
    os.makedirs(CACHE_DIR, exist_ok=True)

    now = datetime.datetime.now()
    is_current_year = (year == now.year)
    if os.path.exists(cache_path) and not is_current_year:
        df = pd.read_csv(cache_path)
        df["Datetime"] = pd.to_datetime(df["Datetime"])
        return df

    if not TOKEN:
        return pd.DataFrame(columns=["Datetime", "MW"])

    start = f"{year}01010000"
    end_year = year + 1
    end = f"{end_year}01010000"
    if is_current_year:
        end = (now + datetime.timedelta(days=1)).strftime("%Y%m%d0000")

    params = {
        "securityToken": TOKEN,
        "documentType": "A11",
        "in_Domain": in_domain,
        "out_Domain": out_domain,
        "periodStart": start,
        "periodEnd": end,
    }

    df = pd.DataFrame(columns=["Datetime", "MW"])
    for attempt in range(3):
        try:
            r = requests.get(BASE_URL, params=params, timeout=30)
            if r.status_code == 200:
                df = _parse_flow_xml(r.text)
                break
        except requests.RequestException:
            pass
        time.sleep(2.0 * (attempt + 1))

    if not (is_current_year and df.empty):
        df.to_csv(cache_path, index=False)
    return df


def fetch_net_export_series(start_year: int = 2021, end_year: int = None) -> pd.DataFrame:
    """
    Сумарний нетто-експорт України по всіх п'яти кордонах (експорт мінус
    імпорт), реальний почасовий ряд. Позитивне значення — Україна експортує
    (надлишок генерації), негативне — імпортує (дефіцит).
    """
    end_year = end_year or datetime.datetime.now().year
    per_series = []  # по одному DataFrame на (кордон, напрямок) — вже склеєний по роках

    for name, code in BORDER_DOMAINS.items():
        export_years = [fetch_flow_year(code, UA_DOMAIN, year, f"UA_to_{name}") for year in range(start_year, end_year + 1)]
        export_years = [d for d in export_years if not d.empty]
        if export_years:
            export_df = pd.concat(export_years).drop_duplicates(subset=["Datetime"]).sort_values("Datetime")
            per_series.append(export_df.rename(columns={"MW": f"exp_{name}"}))

        import_years = [fetch_flow_year(UA_DOMAIN, code, year, f"{name}_to_UA") for year in range(start_year, end_year + 1)]
        import_years = [d for d in import_years if not d.empty]
        if import_years:
            import_df = pd.concat(import_years).drop_duplicates(subset=["Datetime"]).sort_values("Datetime")
            per_series.append(import_df.rename(columns={"MW": f"imp_{name}"}))

    if not per_series:
        return pd.DataFrame(columns=["Datetime", "Grid_Net_Export_MW"])

    merged = None
    for df in per_series:
        merged = df if merged is None else pd.merge(merged, df, on="Datetime", how="outer")

    merged = merged.sort_values("Datetime").reset_index(drop=True)
    exp_cols = [c for c in merged.columns if c.startswith("exp_")]
    imp_cols = [c for c in merged.columns if c.startswith("imp_")]
    merged["Grid_Net_Export_MW"] = merged[exp_cols].sum(axis=1) - merged[imp_cols].sum(axis=1)

    # Погодинне усереднення (сирі точки можуть бути 15/30/60-хвилинні)
    merged = merged.set_index("Datetime")[["Grid_Net_Export_MW"]].resample("h").mean().reset_index()

    # ENTSO-E віддає UTC (tz-aware). Решта пайплайну (oree, Open-Meteo) працює
    # з naive часовими мітками — приводимо до naive UTC для merge. ПРИМІТКА:
    # цінові мітки oree історично трактувались як київський час без явної
    # конвертації — можливе зміщення на 2-3г між Price та рештою джерел існує
    # в проєкті ще з попередньої версії (до цієї сесії) і потребує окремої
    # перевірки, а не мовчазного припущення, що воно узгоджене.
    merged["Datetime"] = merged["Datetime"].dt.tz_localize(None)
    return merged


# --- День-наперед ціна сусідніх зон (documentType=A44) ---------------------
# Гіпотеза (перевіряється бектестом, не приймається на віру): Україна фізично
# з'єднана з європейським ринком через ті самі 5 кордонів, тож ціна DAM
# сусідньої зони — це реальний сигнал про альтернативну вартість експорту/
# імпорту саме в години профіциту/дефіциту, де точковий прогноз зараз
# найгірший. НЕ включається у продові FEATURES автоматично — лише як
# експериментальна ознака для walk_forward_backtest(extra_features=...).
DAM_PRICE_CACHE_DIR = os.path.join(os.path.dirname(CACHE_DIR), "entsoe_dam_price")
# Публікаційний документ A44 використовує інший namespace (7:3), ніж A11 (7:0).
NS_PRICE = {"ns": "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3"}


def _parse_price_xml(xml_text: str) -> pd.DataFrame:
    """Розгортає TimeSeries/Period/Point для A44 (день-наперед ціна) в
    почасовий ряд Datetime/EUR_MWh. Той самий hold-to-next-point підхід
    (curveType A03), що й для перетоків у _parse_flow_xml."""
    records = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return pd.DataFrame(columns=["Datetime", "EUR_MWh"])

    ns_uri = NS_PRICE["ns"]
    for ts in root.iter(f"{{{ns_uri}}}TimeSeries"):
        for period in ts.iter(f"{{{ns_uri}}}Period"):
            start_el = period.find(".//ns:timeInterval/ns:start", NS_PRICE)
            res_el = period.find("ns:resolution", NS_PRICE)
            if start_el is None or res_el is None:
                continue
            period_start = pd.to_datetime(start_el.text)
            step_minutes = _RESOLUTION_MINUTES.get(res_el.text, 60)

            points = []
            for point in period.findall("ns:Point", NS_PRICE):
                pos_el = point.find("ns:position", NS_PRICE)
                price_el = point.find("ns:price.amount", NS_PRICE)
                if pos_el is None or price_el is None:
                    continue
                points.append((int(pos_el.text), float(price_el.text)))
            points.sort()

            for i, (pos, price) in enumerate(points):
                next_pos = points[i + 1][0] if i + 1 < len(points) else pos + 1
                for p in range(pos, next_pos):
                    dt = period_start + pd.Timedelta(minutes=step_minutes * (p - 1))
                    records.append({"Datetime": dt, "EUR_MWh": price})

    if not records:
        return pd.DataFrame(columns=["Datetime", "EUR_MWh"])
    df = pd.DataFrame(records).sort_values("Datetime").reset_index(drop=True)
    return df


def fetch_dam_price_year(domain: str, year: int, label: str) -> pd.DataFrame:
    """Один рік день-наперед ціни однієї зони. Кешується так само, як
    fetch_flow_year — минулий рік більше ніколи не перезапитується, навіть
    якщо порожній (деякі зони можуть не публікувати A44 в ENTSO-E)."""
    cache_path = os.path.join(DAM_PRICE_CACHE_DIR, f"{label}_{year}.csv")
    os.makedirs(DAM_PRICE_CACHE_DIR, exist_ok=True)

    now = datetime.datetime.now()
    is_current_year = (year == now.year)
    if os.path.exists(cache_path) and not is_current_year:
        df = pd.read_csv(cache_path)
        df["Datetime"] = pd.to_datetime(df["Datetime"])
        return df

    if not TOKEN:
        return pd.DataFrame(columns=["Datetime", "EUR_MWh"])

    start = f"{year}01010000"
    end_year = year + 1
    end = f"{end_year}01010000"
    if is_current_year:
        end = (now + datetime.timedelta(days=1)).strftime("%Y%m%d0000")

    params = {
        "securityToken": TOKEN,
        "documentType": "A44",
        "in_Domain": domain,
        "out_Domain": domain,
        "periodStart": start,
        "periodEnd": end,
    }

    df = pd.DataFrame(columns=["Datetime", "EUR_MWh"])
    for attempt in range(3):
        try:
            r = requests.get(BASE_URL, params=params, timeout=30)
            if r.status_code == 200:
                df = _parse_price_xml(r.text)
                break
        except requests.RequestException:
            pass
        time.sleep(2.0 * (attempt + 1))

    if not (is_current_year and df.empty):
        df.to_csv(cache_path, index=False)
    return df


def fetch_eu_dam_price_series(start_year: int = 2021, end_year: int = None) -> pd.DataFrame:
    """
    Середня реальна день-наперед ціна (EUR/МВт·год) серед сусідніх зон, з
    якими є дані (PL/RO/SK/HU/MD — ті самі кордони, що й для перетоків).
    Усереднення по рядку ігнорує зони без даних на цей час — не вигадка,
    просто середнє з того, що реально опубліковано.
    """
    end_year = end_year or datetime.datetime.now().year
    per_zone = []

    for name, code in BORDER_DOMAINS.items():
        years = [fetch_dam_price_year(code, year, name) for year in range(start_year, end_year + 1)]
        years = [d for d in years if not d.empty]
        if years:
            zone_df = pd.concat(years).drop_duplicates(subset=["Datetime"]).sort_values("Datetime")
            per_zone.append(zone_df.rename(columns={"EUR_MWh": f"dam_price_{name}"}))

    if not per_zone:
        return pd.DataFrame(columns=["Datetime", "EU_DAM_Price_EUR_MWh"])

    merged = None
    for df in per_zone:
        merged = df if merged is None else pd.merge(merged, df, on="Datetime", how="outer")

    merged = merged.sort_values("Datetime").reset_index(drop=True)
    price_cols = [c for c in merged.columns if c.startswith("dam_price_")]
    merged["EU_DAM_Price_EUR_MWh"] = merged[price_cols].mean(axis=1, skipna=True)

    merged = merged.set_index("Datetime")[["EU_DAM_Price_EUR_MWh"]].resample("h").mean().reset_index()
    merged["Datetime"] = merged["Datetime"].dt.tz_localize(None)
    return merged
