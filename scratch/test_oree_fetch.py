import sys
import os
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.modules.market_data_service.data_manager import fetch_oree_prices_for_month

print("Fetching prices for July 2026 from oree.com.ua...")
df = fetch_oree_prices_for_month(7, 2026)

if df.empty:
    print("Failed to fetch or parse prices!")
else:
    print(f"Successfully fetched {len(df)} records.")
    df['Datetime'] = pd.to_datetime(df['Datetime'])
    target_date = pd.to_datetime('2026-07-09').date()
    df_day = df[df['Datetime'].dt.date == target_date]
    if df_day.empty:
        print("No prices found for 2026-07-09 in parsed data.")
    else:
        print("Prices for 2026-07-09:")
        for idx, row in df_day.iterrows():
            print(f"{row['Datetime'].strftime('%H:%M')} -> {row['Price']:.2f}")
