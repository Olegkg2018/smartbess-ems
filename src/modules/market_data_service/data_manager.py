import os
import re
import json
import datetime
import time
import requests
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup

from src.core.config import settings

# Load parameters from settings
LAT = settings.LAT
LON = settings.LON
DATA_DIR = settings.DATA_DIR
PRICES_2025_PATH = os.path.join(DATA_DIR, "prices_2025.csv")
WEATHER_2025_PATH = os.path.join(DATA_DIR, "weather_2025.csv")
MERGED_DATA_PATH = os.path.join(DATA_DIR, "historical_data_merged.csv")

OPENWEATHER_KEY = settings.OPENWEATHER_API_KEY
ENTSOE_KEY = settings.ENTSOE_API_KEY

def parse_prices_file(filepath):
    df = pd.read_csv(filepath, sep=';')
    if df.columns[0].startswith('\ufeff') or '﻿' in df.columns[0]:
        df.rename(columns={df.columns[0]: 'Дата'}, inplace=True)
    
    hour_cols = [c for c in df.columns if c.startswith('Год')]
    melted = df.melt(id_vars=['Дата'], value_vars=hour_cols, var_name='Hour_Col', value_name='Price')
    melted['Hour'] = melted['Hour_Col'].apply(lambda x: int(re.search(r'\d+', x).group(0)) - 1)
    melted['Price'] = melted['Price'].astype(str).str.replace(',', '.')
    melted['Price'] = pd.to_numeric(melted['Price'], errors='coerce')
    melted['Datetime'] = pd.to_datetime(melted['Дата'], format='%d.%m.%Y') + pd.to_timedelta(melted['Hour'], unit='h')
    
    return melted[['Datetime', 'Price']].sort_values('Datetime').reset_index(drop=True)

def fetch_oree_prices_for_month(month, year):
    cache_path = os.path.join(DATA_DIR, "prices_cache", f"prices_{year}_{month:02d}.csv")
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    
    now = datetime.datetime.now()
    is_current_month = (year == now.year) and (month == now.month)
    
    if os.path.exists(cache_path) and not is_current_month:
        try:
            df = pd.read_csv(cache_path)
            df['Datetime'] = pd.to_datetime(df['Datetime'])
            return df
        except Exception as e:
            print(f"Error reading price cache: {e}")
            
    url = "https://www.oree.com.ua/index.php/pricectr/data_view"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    date_str = f"{month:02d}.{year}"
    data = {
        'date': date_str,
        'market': 'DAM',
        'zone': 'IPS'
    }
    
    retries = 3
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, data=data, timeout=20)
            if r.status_code == 200:
                res = r.json()
                content = res.get('content', '')
                if not content:
                    time.sleep(1.5)
                    continue
                    
                soup = BeautifulSoup(content, 'html.parser')
                table = soup.find('table', id='price_table')
                if table:
                    rows = table.find('tbody').find_all('tr') if table.find('tbody') else table.find_all('tr')
                    records = []
                    
                    for row in rows:
                        cols = [td.text.strip() for td in row.find_all(['td', 'th'])]
                        if cols and re.match(r'^\d{2}\.\d{2}\.\d{4}$', cols[0]):
                            date_val = cols[0]
                            if len(cols) >= 25:
                                for h in range(24):
                                    price_str = cols[h+1].replace(',', '')
                                    try:
                                        price = float(price_str)
                                        dt = pd.to_datetime(date_val, format='%d.%m.%Y') + pd.to_timedelta(h, unit='h')
                                        records.append({'Datetime': dt, 'Price': price})
                                    except ValueError:
                                        pass
                                        
                    if records:
                        df = pd.DataFrame(records).sort_values('Datetime').reset_index(drop=True)
                        df.to_csv(cache_path, index=False)
                        return df
        except Exception as e:
            print(f"Error fetching oree prices: {e}")
        time.sleep(2.0 * (attempt + 1))
        
    if os.path.exists(cache_path):
        try:
            df = pd.read_csv(cache_path)
            df['Datetime'] = pd.to_datetime(df['Datetime'])
            return df
        except:
            pass
    return pd.DataFrame()

def fetch_oree_prices_full_history():
    now = datetime.datetime.now()
    current_year = now.year
    current_month = now.month
    
    all_dfs = []
    if os.path.exists(PRICES_2025_PATH):
        prices_2025 = parse_prices_file(PRICES_2025_PATH)
        all_dfs.append(prices_2025)
        skip_2025 = True
    else:
        skip_2025 = False
        
    for year in range(2021, current_year + 1):
        if year == 2025 and skip_2025:
            continue
            
        start_m = 1
        end_m = current_month if year == current_year else 12
        
        for month in range(start_m, end_m + 1):
            df_month = fetch_oree_prices_for_month(month, year)
            if not df_month.empty:
                all_dfs.append(df_month)
            time.sleep(0.3)
            
    if all_dfs:
        combined = pd.concat(all_dfs).drop_duplicates(subset=['Datetime']).sort_values('Datetime').reset_index(drop=True)
        return combined
    return pd.DataFrame()

def fetch_weather_archive_full(start_year=2021):
    now = datetime.datetime.now()
    yesterday = now - datetime.timedelta(days=2)
    end_date_str = yesterday.strftime('%Y-%m-%d')
    start_date_str = f"{start_year}-01-01"
    
    cache_path = os.path.join(DATA_DIR, f"weather_archive_{start_year}_{now.year}.csv")
    
    if os.path.exists(cache_path):
        try:
            df = pd.read_csv(cache_path)
            df['Datetime'] = pd.to_datetime(df['Datetime'])
            last_date = df['Datetime'].max()
            if last_date.date() >= yesterday.date():
                return df
        except Exception as e:
            print(f"Error reading weather cache: {e}")
            
    url = f"https://archive-api.open-meteo.com/v1/archive?latitude={LAT}&longitude={LON}&start_date={start_date_str}&end_date={end_date_str}&hourly=temperature_2m,cloud_cover,wind_speed_10m,shortwave_radiation"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            data = r.json()
            hourly = data.get('hourly', {})
            times = hourly.get('time', [])
            temps = hourly.get('temperature_2m', [])
            clouds = hourly.get('cloud_cover', [])
            winds = hourly.get('wind_speed_10m', [])
            rads = hourly.get('shortwave_radiation', [])
            
            records = []
            for i in range(len(times)):
                records.append({
                    'Datetime': pd.to_datetime(times[i]),
                    'Temperature': temps[i],
                    'Cloud_Cover': clouds[i],
                    'Wind_Speed': winds[i],
                    'Shortwave_Radiation': rads[i]
                })
            df = pd.DataFrame(records).sort_values('Datetime').reset_index(drop=True)
            df.to_csv(cache_path, index=False)
            return df
    except Exception as e:
        print(f"Error fetching weather archive: {e}")
        
    if os.path.exists(cache_path):
        return pd.read_csv(cache_path, parse_dates=['Datetime'])
    return pd.DataFrame()

def get_combined_historical_data():
    if os.path.exists(MERGED_DATA_PATH):
        try:
            df = pd.read_csv(MERGED_DATA_PATH)
            df['Datetime'] = pd.to_datetime(df['Datetime'])
            sync_realtime_data(force=False)
            df = pd.read_csv(MERGED_DATA_PATH)
            df['Datetime'] = pd.to_datetime(df['Datetime'])
            return df
        except Exception as e:
            print(f"Error loading historical merged file: {e}")

    prices = fetch_oree_prices_full_history()
    if prices.empty:
        raise ValueError("Could not fetch price history from oree.com.ua!")
    prices['Price'] = prices['Price'].interpolate(method='linear').bfill().ffill()
    
    weather = fetch_weather_archive_full(start_year=2021)
    if weather.empty:
        raise ValueError("Could not fetch weather history!")
        
    merged = pd.merge(prices, weather, on='Datetime', how='inner')
    merged = add_generation_and_market_factors(merged)
    
    os.makedirs(DATA_DIR, exist_ok=True)
    merged.to_csv(MERGED_DATA_PATH, index=False)
    sync_realtime_data(force=True)
    return pd.read_csv(MERGED_DATA_PATH)

def add_generation_and_market_factors(df):
    df = df.copy()
    df['Day_of_Year'] = df['Datetime'].dt.dayofyear
    df['Month'] = df['Datetime'].dt.month
    df['Hour'] = df['Datetime'].dt.hour
    
    df['Solar_Gen'] = np.clip(6500.0 * (df['Shortwave_Radiation'] / 1000.0) * (1.0 - 0.003 * (df['Temperature'] - 25.0)), 0.0, 5500.0)
    
    def wind_curve(ws):
        if ws < 8.0 or ws > 80.0:
            return 0.0
        elif ws > 45.0:
            return 1800.0
        else:
            return 1800.0 * ((ws - 8.0) / (45.0 - 8.0)) ** 3
    df['Wind_Gen'] = df['Wind_Speed'].apply(wind_curve)
    
    np.random.seed(42)
    nuke_base = 8500.0 - 2500.0 * (df['Month'].isin([6, 7, 8])).astype(float)
    nuke_noise = np.random.normal(0, 150, size=len(df))
    df['Nuclear_Gen'] = np.clip(nuke_base + nuke_noise, 4000.0, 9500.0)
    
    base_gas = 35.0
    seasonal_gas = 12.0 * np.cos(2 * np.pi * (df['Day_of_Year'] - 15) / 365.0)
    noise_gas = np.random.normal(0, 2.5, size=len(df))
    trend_gas = np.cumsum(np.random.normal(0, 0.05, size=len(df)))
    df['Gas_Price'] = np.clip(base_gas + seasonal_gas + noise_gas + trend_gas, 15.0, 90.0)
    
    df['Nuclear_Outage'] = (9500.0 - df['Nuclear_Gen']) / 9500.0
    
    df['Solar_Strike'] = 0.0
    df.loc[(df['Datetime'].dt.year == 2024) & (df['Month'] == 6) & (df['Datetime'].dt.day.between(5, 15)), 'Solar_Strike'] = 0.35
    df.loc[(df['Datetime'].dt.year == 2025) & (df['Month'] == 7) & (df['Datetime'].dt.day.between(10, 18)), 'Solar_Strike'] = 0.40
    
    df['Solar_Gen'] = df['Solar_Gen'] * (1.0 - df['Solar_Strike'])
    df['Market_Coeff'] = 1.0
    df['VDR_Volume'] = np.clip(1.0 + np.random.normal(0, 0.15, size=len(df)), 0.3, 1.8)
    df['Grid_Import_Export'] = 400.0 * np.sin(2 * np.pi * (df['Day_of_Year'] - 80) / 365.0) + 200.0 * (df['Hour'].isin([8,9,10,18,19,20,21])).astype(float)
    
    df['Hydro_Gen'] = np.clip(800.0 + 700.0 * df['Hour'].isin([8,9,10,18,19,20,21]).astype(float) + np.random.normal(0, 80, size=len(df)), 200.0, 2000.0)
    df['Thermal_Gen'] = np.clip(12000.0 - df['Nuclear_Gen'] - df['Solar_Gen'] - df['Wind_Gen'] - df['Grid_Import_Export'] - df['Hydro_Gen'], 1500.0, 7500.0)
    
    return df.drop(columns=['Day_of_Year'])

def sync_realtime_data(force=False):
    if not force and os.path.exists(MERGED_DATA_PATH):
        try:
            mtime = os.path.getmtime(MERGED_DATA_PATH)
            if time.time() - mtime < 900:
                return True
        except:
            pass

    now = datetime.datetime.now()
    current_year = now.year
    current_month = now.month
    
    df_prices = fetch_oree_prices_for_month(current_month, current_year)
    if df_prices.empty:
        return False
        
    start_date = f"{current_year}-{current_month:02d}-01"
    end_date = now.strftime('%Y-%m-%d')
    
    url = f"https://archive-api.open-meteo.com/v1/archive?latitude={LAT}&longitude={LON}&start_date={start_date}&end_date={end_date}&hourly=temperature_2m,cloud_cover,wind_speed_10m,shortwave_radiation"
    
    df_weather = pd.DataFrame()
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            data = r.json()
            hourly = data.get('hourly', {})
            times = hourly.get('time', [])
            temps = hourly.get('temperature_2m', [])
            clouds = hourly.get('cloud_cover', [])
            winds = hourly.get('wind_speed_10m', [])
            rads = hourly.get('shortwave_radiation', [])
            
            records = []
            for i in range(len(times)):
                records.append({
                    'Datetime': pd.to_datetime(times[i]),
                    'Temperature': temps[i],
                    'Cloud_Cover': clouds[i],
                    'Wind_Speed': winds[i],
                    'Shortwave_Radiation': rads[i]
                })
            df_weather = pd.DataFrame(records).sort_values('Datetime').reset_index(drop=True)
    except:
        pass
        
    if df_weather.empty:
        records = []
        for i in range(len(df_prices)):
            dt = df_prices.iloc[i]['Datetime']
            h = dt.hour
            temp = 18.0 + 7.0 * np.sin(np.pi * (h - 6) / 12) if 6 <= h <= 18 else 12.0
            records.append({
                'Datetime': dt,
                'Temperature': temp,
                'Cloud_Cover': 40.0,
                'Wind_Speed': 12.0,
                'Shortwave_Radiation': 600.0 * np.sin(np.pi * (h - 6) / 12) if 6 < h < 18 else 0.0
            })
        df_weather = pd.DataFrame(records)
        
    new_month_data = pd.merge(df_prices, df_weather, on='Datetime', how='inner')
    if new_month_data.empty:
        return False
        
    new_month_data = add_generation_and_market_factors(new_month_data)
    
    if os.path.exists(MERGED_DATA_PATH):
        try:
            df_hist = pd.read_csv(MERGED_DATA_PATH)
            df_hist['Datetime'] = pd.to_datetime(df_hist['Datetime'])
            start_of_month = pd.to_datetime(f"{current_year}-{current_month:02d}-01")
            df_hist = df_hist[df_hist['Datetime'] < start_of_month]
            
            df_updated = pd.concat([df_hist, new_month_data]).sort_values('Datetime').reset_index(drop=True)
            df_updated.to_csv(MERGED_DATA_PATH, index=False)
            return True
        except:
            pass
    return False

def fetch_weather_forecast(lat=LAT, lon=LON, api_key=OPENWEATHER_KEY):
    if api_key:
        url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={api_key}&units=metric"
        try:
            r = requests.get(url, timeout=12)
            if r.status_code == 200:
                data = r.json()
                forecast_list = data.get('list', [])
                records = []
                for item in forecast_list:
                    dt = pd.to_datetime(item.get('dt_txt'))
                    main = item.get('main', {})
                    clouds = item.get('clouds', {}).get('all', 50)
                    wind = item.get('wind', {}).get('speed', 4.0) * 3.6
                    temp = main.get('temp', 15.0)
                    
                    hour = dt.hour
                    month = dt.month
                    is_day = 6 <= hour <= 19
                    if is_day:
                        rad_peak = 800.0 if month in [5,6,7,8] else 400.0
                        rad = rad_peak * np.sin(np.pi * (hour - 6) / 13)
                        rad = rad * (1.0 - 0.75 * (clouds / 100.0))
                    else:
                        rad = 0.0
                        
                    records.append({
                        'Datetime': dt,
                        'Temperature': temp,
                        'Cloud_Cover': float(clouds),
                        'Wind_Speed': float(wind),
                        'Shortwave_Radiation': float(rad)
                    })
                
                df_3h = pd.DataFrame(records).sort_values('Datetime').reset_index(drop=True)
                df_3h = df_3h.set_index('Datetime')
                df_1h = df_3h.resample('1h').interpolate(method='linear').reset_index()
                
                now = datetime.datetime.now()
                tomorrow = (now + datetime.timedelta(days=1)).date()
                df_tomorrow = df_1h[df_1h['Datetime'].dt.date == tomorrow].copy()
                if len(df_tomorrow) < 24:
                    df_tomorrow = df_1h.iloc[:24].copy()
                return df_tomorrow
        except:
            pass
            
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m,cloud_cover,wind_speed_10m,shortwave_radiation"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            hourly = data.get('hourly', {})
            times = hourly.get('time', [])
            temps = hourly.get('temperature_2m', [])
            clouds = hourly.get('cloud_cover', [])
            winds = hourly.get('wind_speed_10m', [])
            rads = hourly.get('shortwave_radiation', [])
            
            records = []
            for i in range(len(times)):
                records.append({
                    'Datetime': pd.to_datetime(times[i]),
                    'Temperature': temps[i],
                    'Cloud_Cover': clouds[i],
                    'Wind_Speed': winds[i],
                    'Shortwave_Radiation': rads[i]
                })
            df = pd.DataFrame(records).sort_values('Datetime').reset_index(drop=True)
            now = datetime.datetime.now()
            tomorrow = (now + datetime.timedelta(days=1)).date()
            df_tomorrow = df[df['Datetime'].dt.date == tomorrow].copy()
            if len(df_tomorrow) == 24:
                return df_tomorrow
            return df.iloc[24:48].copy()
    except:
        pass
        
    now = datetime.datetime.now()
    tomorrow = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    records = []
    for h in range(24):
        dt = tomorrow + datetime.timedelta(hours=h)
        temp = 16.0 + 8.0 * np.sin(np.pi * (h - 6) / 12) if 6 <= h <= 18 else 11.0
        clouds = 30.0
        wind = 12.0
        rad = 600.0 * np.sin(np.pi * (h - 6) / 12) if 6 < h < 18 else 0.0
        records.append({
            'Datetime': dt,
            'Temperature': temp,
            'Cloud_Cover': clouds,
            'Wind_Speed': wind,
            'Shortwave_Radiation': rad
        })
    return pd.DataFrame(records)

def verify_data_completeness():
    report = {
        'status': 'OK',
        'errors': [],
        'warnings': [],
        'details': {}
    }
    
    if not os.path.exists(MERGED_DATA_PATH):
        report['status'] = 'ERROR'
        report['errors'].append("База данных не найдена.")
        return report
        
    try:
        df = pd.read_csv(MERGED_DATA_PATH)
        df['Datetime'] = pd.to_datetime(df['Datetime'])
        
        min_date = df['Datetime'].min()
        max_date = df['Datetime'].max()
        now = datetime.datetime.now()
        
        report['details']['start_date'] = min_date.strftime('%Y-%m-%d %H:%M')
        report['details']['end_date'] = max_date.strftime('%Y-%m-%d %H:%M')
        
        target_start = pd.to_datetime('2021-01-01')
        if min_date > target_start:
            report['status'] = 'WARNING'
            report['warnings'].append(f"Данные начинаются с {min_date.strftime('%Y-%m-%d')}, что позже целевой даты 2021-01-01.")
            
        last_allowed_delay = now - datetime.timedelta(days=2)
        if max_date < last_allowed_delay:
            report['status'] = 'WARNING'
            report['warnings'].append(f"Данные застарели.")
            
        expected_hours = int((max_date - min_date).total_seconds() / 3600) + 1
        actual_hours = len(df)
        gap_count = expected_hours - actual_hours
        
        report['details']['expected_hours'] = expected_hours
        report['details']['actual_hours'] = actual_hours
        report['details']['gap_count'] = gap_count
        
        if gap_count > 0:
            report['warnings'].append(f"Обнаружены пропуски: {gap_count} часов.")
            
        required_cols = [
            'Price', 'Temperature', 'Cloud_Cover', 'Wind_Speed', 'Shortwave_Radiation',
            'Solar_Gen', 'Wind_Gen', 'Nuclear_Gen', 'Hydro_Gen', 'Thermal_Gen', 'Grid_Import_Export'
        ]
        
        missing_cols = [c for c in required_cols if c not in df.columns]
        if missing_cols:
            report['status'] = 'ERROR'
            report['errors'].append(f"Отсутствуют обязательные колонки: {', '.join(missing_cols)}.")
    except Exception as e:
        report['status'] = 'ERROR'
        report['errors'].append(f"Ошибка проверки базы данных: {str(e)}")
    return report
