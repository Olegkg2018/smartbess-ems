"""Recon: перевіряє, чи Open-Meteo archive API віддає ті самі поля
(temperature_2m, cloud_cover, wind_speed_10m, shortwave_radiation) для точок
у Польщі та Румунії так само надійно, як для України (LAT/LON у settings).
Точки — умовні національні центри (Варшава, Бухарест), як і UA-точка в
проєкті (49.53, 30.40 — не прив'язана до конкретного об'єкта, а орієнтовний
центр). Мета: перевірити глибину історії й повноту (без NaN-прогалин), перш
ніж будувати повноцінний fetch-модуль.
Запуск: python scratch/recon_neighbor_weather.py
"""
import requests
import pandas as pd

POINTS = {
    "PL": (52.23, 21.01),   # Варшава
    "RO": (44.43, 26.10),   # Бухарест
}

for label, (lat, lon) in POINTS.items():
    url = (
        f"https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}"
        f"&start_date=2021-01-01&end_date=2021-01-07"
        f"&hourly=temperature_2m,cloud_cover,wind_speed_10m,shortwave_radiation"
    )
    r = requests.get(url, timeout=30)
    print(label, "STATUS", r.status_code)
    if r.status_code != 200:
        print(r.text[:300])
        continue
    data = r.json()
    hourly = data.get("hourly", {})
    df = pd.DataFrame(hourly)
    print(label, "rows:", len(df), "nan counts:\n", df.isna().sum().to_dict())
    print(df.head(3))
    print("---")

# Перевірка найсвіжіших даних (чи є затримка публікації архіву як для UA)
for label, (lat, lon) in POINTS.items():
    yesterday = (pd.Timestamp.now() - pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    url = (
        f"https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}"
        f"&start_date={yesterday}&end_date={today}"
        f"&hourly=temperature_2m,cloud_cover,wind_speed_10m,shortwave_radiation"
    )
    r = requests.get(url, timeout=30)
    print(label, "recency STATUS", r.status_code)
    if r.status_code == 200:
        hourly = r.json().get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        print(label, "last non-null temp at:", next((t for t, v in zip(reversed(times), reversed(temps)) if v is not None), None))

# Прогнозна точка (forecast API), як для UA fetch_weather_forecast fallback
for label, (lat, lon) in POINTS.items():
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly=temperature_2m,cloud_cover,wind_speed_10m,shortwave_radiation"
    )
    r = requests.get(url, timeout=15)
    print(label, "forecast STATUS", r.status_code, "len:", len(r.json().get("hourly", {}).get("time", [])) if r.status_code == 200 else None)
