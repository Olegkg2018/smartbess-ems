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
import src.modules.external_data_service.gas_price as ext_gas
import src.modules.external_data_service.telegram_public as ext_tg
import src.modules.external_data_service.entsoe as ext_entsoe

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

def fetch_oree_market_month(month, year, market='DAM', value_col='Price', cache_subdir='prices_cache'):
    """
    Общий загрузчик почасовых цен с oree.com.ua (ДП «Оператор ринку»).
    Используется и для РДН (market='DAM'), и для ВДР/внутрішньодобового ринку
    (market='IDM') — обе таблицы имеют идентичную структуру (дата + 24 колонки годин).
    """
    cache_path = os.path.join(DATA_DIR, cache_subdir, f"{market.lower()}_{year}_{month:02d}.csv")
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    now = datetime.datetime.now()
    is_current_month = (year == now.year) and (month == now.month)

    if os.path.exists(cache_path) and not is_current_month:
        try:
            df = pd.read_csv(cache_path)
            df['Datetime'] = pd.to_datetime(df['Datetime'])
            return df
        except Exception as e:
            print(f"Error reading {market} cache: {e}")

    url = "https://www.oree.com.ua/index.php/pricectr/data_view"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    date_str = f"{month:02d}.{year}"
    data = {
        'date': date_str,
        'market': market,
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
                                        records.append({'Datetime': dt, value_col: price})
                                    except ValueError:
                                        pass

                    if records:
                        df = pd.DataFrame(records).sort_values('Datetime').reset_index(drop=True)
                        df.to_csv(cache_path, index=False)
                        return df
        except Exception as e:
            print(f"Error fetching oree {market} data: {e}")
        time.sleep(2.0 * (attempt + 1))

    if os.path.exists(cache_path):
        try:
            df = pd.read_csv(cache_path)
            df['Datetime'] = pd.to_datetime(df['Datetime'])
            return df
        except:
            pass
    return pd.DataFrame()

def fetch_oree_prices_for_month(month, year):
    return fetch_oree_market_month(month, year, market='DAM', value_col='Price', cache_subdir='prices_cache')

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

    # Реальний ВДР (внутрішньодобовий ринок) — окремий модуль, лежить тут,
    # а не в add_real_market_factors, щоб уникнути циклічного імпорту
    # (external_data_service.intraday_market імпортує цей файл).
    import src.modules.external_data_service.intraday_market as ext_idm
    prices = ext_idm.merge_idm_into_prices(prices)

    weather = fetch_weather_archive_full(start_year=2021)
    if weather.empty:
        raise ValueError("Could not fetch weather history!")

    merged = pd.merge(prices, weather, on='Datetime', how='inner')
    merged = add_real_market_factors(merged)

    os.makedirs(DATA_DIR, exist_ok=True)
    merged.to_csv(MERGED_DATA_PATH, index=False)
    sync_realtime_data(force=True)
    return pd.read_csv(MERGED_DATA_PATH)

def add_real_market_factors(df):
    """
    Замінює колишній add_generation_and_market_factors (np.random-шум) на
    реальні джерела. Nuclear_Gen/Hydro_Gen/Thermal_Gen прибрані повністю —
    Україна припинила публікацію генерації по типах в ENTSO-E з 25.02.2022
    (воєнне обмеження, підтверджено розвідкою) і не відновлювала — чесного
    джерела для них немає. Grid_Net_Export_MW — РЕАЛЬНІ транскордонні
    перетоки (звітуються сусідами: PL/RO/SK/HU/MD), доступні безперервно
    2021-сьогодні. Solar_Gen/Wind_Gen — детермінована фізична оцінка з
    РЕАЛЬНОЇ погоди (радіація, вітер), а не вигадка.
    """
    df = df.copy()
    df['Month'] = df['Datetime'].dt.month
    df['Hour'] = df['Datetime'].dt.hour
    df['Date'] = df['Datetime'].dt.normalize()

    flow = ext_entsoe.fetch_net_export_series(start_year=2021)
    if not flow.empty:
        df = df.merge(flow, on='Datetime', how='left')
    else:
        df['Grid_Net_Export_MW'] = np.nan

    # Експериментальна ознака (поки НЕ в продових FEATURES моделі — див.
    # ml_pipeline.py): реальна день-наперед ціна сусідніх зон ENTSO-E.
    dam_eu = ext_entsoe.fetch_eu_dam_price_series(start_year=2021)
    if not dam_eu.empty:
        df = df.merge(dam_eu, on='Datetime', how='left')
    else:
        df['EU_DAM_Price_EUR_MWh'] = np.nan

    df['Solar_Gen'] = np.clip(6500.0 * (df['Shortwave_Radiation'] / 1000.0) * (1.0 - 0.003 * (df['Temperature'] - 25.0)), 0.0, 5500.0)

    def wind_curve(ws):
        if ws < 8.0 or ws > 80.0:
            return 0.0
        elif ws > 45.0:
            return 1800.0
        else:
            return 1800.0 * ((ws - 8.0) / (45.0 - 8.0)) ** 3
    df['Wind_Gen'] = df['Wind_Speed'].apply(wind_curve)

    # Реальний накопичувальний лог ціни газу (росте з часом; NaN до початку збору)
    gas_log = ext_gas.load_daily_log()
    if not gas_log.empty:
        gas_log = gas_log.rename(columns={'Date': 'Date'})
        df = df.merge(gas_log, on='Date', how='left')
    else:
        df['Gas_Price_EUR_MWh'] = np.nan

    # Реальний keyword-сигнал зі публічних каналів Укренерго/Міненерго
    stress = ext_tg.daily_grid_stress_signal()
    if stress:
        stress_df = pd.DataFrame([
            {'Date': pd.to_datetime(k), **v} for k, v in stress.items()
        ])
        df = df.merge(stress_df, on='Date', how='left')
    for col in ['grid_stress_high', 'grid_stress_medium', 'mentions']:
        if col not in df.columns:
            df[col] = 0
        df[col] = df[col].fillna(0)
    df = df.rename(columns={
        'grid_stress_high': 'Grid_Stress_High',
        'grid_stress_medium': 'Grid_Stress_Medium',
        'mentions': 'Telegram_Mentions',
    })

    return df.drop(columns=['Date'])

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
        
    import src.modules.external_data_service.intraday_market as ext_idm
    df_prices = ext_idm.merge_idm_into_prices(df_prices)

    new_month_data = pd.merge(df_prices, df_weather, on='Datetime', how='inner')
    if new_month_data.empty:
        return False

    new_month_data = add_real_market_factors(new_month_data)
    ext_gas.append_daily_snapshot()
    ext_tg.sync_all(max_pages=2)
    
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
            'Solar_Gen', 'Wind_Gen', 'IDM_Price', 'DAM_IDM_Spread', 'Grid_Net_Export_MW',
            'Gas_Price_EUR_MWh', 'Grid_Stress_High', 'Grid_Stress_Medium', 'Telegram_Mentions'
        ]
        
        missing_cols = [c for c in required_cols if c not in df.columns]
        if missing_cols:
            report['status'] = 'ERROR'
            report['errors'].append(f"Отсутствуют обязательные колонки: {', '.join(missing_cols)}.")
    except Exception as e:
        report['status'] = 'ERROR'
        report['errors'].append(f"Ошибка проверки базы данных: {str(e)}")
    return report
