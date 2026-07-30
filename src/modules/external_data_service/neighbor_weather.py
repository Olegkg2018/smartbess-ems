"""
Реальна погода сусідніх цінових зон (Open-Meteo, той самий безкоштовний
архів без ключа, що вже використовується для UA — settings.LAT/LON).

Гіпотеза (перевіряється бектестом, не приймається на віру): сонце/вітер у
сусідній зоні — leading-фактор її власної відновлюваної генерації, тож і
власної ціни, тож і напряму/об'єму транскордонного перетоку в години
профіциту/дефіциту УКраїни — потенційно точніший сигнал, ніж вже перевірений
і НЕ включений EU_DAM_Price_Lag_24 (агрегована середня ціна, дала гірший
last_7d/last_30d MAPE, див. коментар prepare_features у ml_pipeline.py).

НЕ конвертуємо тут погоду в Solar_Gen/Wind_Gen (МВт), як для UA: та
конвертація використовує довідникові константи встановленої потужності УКраїни
(6500 МВт СЕС, 1800 МВт ВЕС) — для PL/RO ці цифри невідомі й довільними
константами вигадувати саме встановлену потужність було б порушенням
принципу проєкту "не вигадувати". Тому в FEATURES/бектест йдуть сирі погодні
змінні (як і для UA Temperature/Cloud_Cover/Wind_Speed/Shortwave_Radiation) —
модель сама навчиться вазі сигналу.

Координати — умовні національні центри (Варшава/Бухарест), як і UA-точка в
settings.LAT/LON (не прив'язана до конкретного об'єкта).
"""
import os
import datetime
import requests
import pandas as pd

from src.core.config import settings

DATA_DIR = settings.DATA_DIR
CACHE_DIR = os.path.join(DATA_DIR, "external_cache", "neighbor_weather")

NEIGHBOR_POINTS = {
    "PL": (52.23, 21.01),   # Варшава
    "RO": (44.43, 26.10),   # Бухарест
}

_FIELDS = ["temperature_2m", "cloud_cover", "wind_speed_10m", "shortwave_radiation"]
_FIELD_TO_COL = {
    "temperature_2m": "Temperature",
    "cloud_cover": "Cloud_Cover",
    "wind_speed_10m": "Wind_Speed",
    "shortwave_radiation": "Shortwave_Radiation",
}


def fetch_zone_weather_archive(zone: str, lat: float, lon: float, start_year: int = 2021) -> pd.DataFrame:
    """Історична погода однієї зони, кешована на диск (той самий підхід, що
    fetch_weather_archive_full у data_manager.py, але параметризований по
    зоні). Повертає Datetime + {zone}_Temperature/{zone}_Cloud_Cover/... ."""
    now = datetime.datetime.now()
    yesterday = now - datetime.timedelta(days=2)
    end_date_str = yesterday.strftime("%Y-%m-%d")
    start_date_str = f"{start_year}-01-01"

    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{zone}_{start_year}_{now.year}.csv")

    if os.path.exists(cache_path):
        try:
            df = pd.read_csv(cache_path)
            df["Datetime"] = pd.to_datetime(df["Datetime"])
            last_date = df["Datetime"].max()
            if last_date.date() >= yesterday.date():
                return df
        except Exception as e:
            print(f"Error reading neighbor weather cache ({zone}): {e}")

    url = (
        f"https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}&start_date={start_date_str}&end_date={end_date_str}"
        f"&hourly={','.join(_FIELDS)}"
    )
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            data = r.json()
            hourly = data.get("hourly", {})
            times = hourly.get("time", [])
            if not times:
                raise ValueError("empty hourly payload")
            records = {"Datetime": [pd.to_datetime(t) for t in times]}
            for field in _FIELDS:
                records[f"{zone}_{_FIELD_TO_COL[field]}"] = hourly.get(field, [])
            df = pd.DataFrame(records).sort_values("Datetime").reset_index(drop=True)
            df.to_csv(cache_path, index=False)
            return df
    except Exception as e:
        print(f"Error fetching neighbor weather archive ({zone}): {e}")

    if os.path.exists(cache_path):
        df = pd.read_csv(cache_path)
        df["Datetime"] = pd.to_datetime(df["Datetime"])
        return df
    cols = ["Datetime"] + [f"{zone}_{_FIELD_TO_COL[f]}" for f in _FIELDS]
    return pd.DataFrame(columns=cols)


def fetch_all_neighbor_weather(start_year: int = 2021, points: dict = None) -> pd.DataFrame:
    """Об'єднує погоду всіх сусідніх зон (outer join по Datetime) в один
    DataFrame з префіксованими колонками PL_*/RO_* — прогалини (зона тимчасово
    недоступна) лишаються NaN, не підмінюються."""
    points = points or NEIGHBOR_POINTS
    merged = None
    for zone, (lat, lon) in points.items():
        df = fetch_zone_weather_archive(zone, lat, lon, start_year=start_year)
        if df.empty:
            continue
        merged = df if merged is None else pd.merge(merged, df, on="Datetime", how="outer")

    if merged is None:
        cols = ["Datetime"]
        for zone in points:
            cols += [f"{zone}_{_FIELD_TO_COL[f]}" for f in _FIELDS]
        return pd.DataFrame(columns=cols)

    return merged.sort_values("Datetime").reset_index(drop=True)
