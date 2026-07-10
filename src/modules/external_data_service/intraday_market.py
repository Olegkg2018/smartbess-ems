"""
Реальні дані внутрішньодобового ринку (ВДР / IDM) з oree.com.ua — та сама
адреса, що вже використовується для РДН, підтверджено розвідкою
(scratch/recon_idm.py): market='IDM' повертає ідентичну за структурою
таблицю погодинних середньозважених цін.

Раніше "VDR_Volume" був випадковим шумом (np.random). Замінюємо його чесним
ринковим сигналом: ціна ВДР та її спред відносно РДН (наскільки внутрішньодобовий
ринок переоцінює завтрашній день) — це реальний і економічно змістовний фактор.
"""
import time
import pandas as pd

import src.modules.market_data_service.data_manager as dm


def fetch_idm_prices_for_month(month: int, year: int) -> pd.DataFrame:
    return dm.fetch_oree_market_month(month, year, market='IDM', value_col='IDM_Price', cache_subdir='idm_cache')


def fetch_idm_prices_range(start_date, end_date) -> pd.DataFrame:
    """Помісячно тягне IDM_Price за діапазон дат і склеює в один ряд."""
    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date)
    months = pd.period_range(start_date, end_date, freq='M')

    frames = []
    for p in months:
        df_month = fetch_idm_prices_for_month(p.month, p.year)
        if not df_month.empty:
            frames.append(df_month)
        time.sleep(0.3)

    if not frames:
        return pd.DataFrame(columns=['Datetime', 'IDM_Price'])

    combined = pd.concat(frames).drop_duplicates(subset=['Datetime']).sort_values('Datetime').reset_index(drop=True)
    mask = (combined['Datetime'] >= start_date) & (combined['Datetime'] <= end_date)
    return combined[mask].reset_index(drop=True)


def merge_idm_into_prices(df_prices: pd.DataFrame) -> pd.DataFrame:
    """
    df_prices — DataFrame з колонками Datetime, Price (РДН). Додає IDM_Price та
    DAM_IDM_Spread (наскільки внутрішньодобовий ринок відхилився від РДН-прогнозу
    на ту саму годину). Дні без IDM (напр. дуже старі, коли ринку ще не було)
    залишаються NaN — не підмінюємо вигаданими значеннями.
    """
    if df_prices.empty:
        return df_prices

    start_date = df_prices['Datetime'].min()
    end_date = df_prices['Datetime'].max()
    idm = fetch_idm_prices_range(start_date, end_date)

    merged = df_prices.merge(idm, on='Datetime', how='left')
    merged['DAM_IDM_Spread'] = merged['IDM_Price'] - merged['Price']
    return merged
