"""
Оракульний бектест `optimize_battery_schedule_dam_idm` (milp_model.py) на
РЕАЛЬНИХ історичних парах РДН(Price)/ВДР(IDM_Price) з historical_data_merged.csv.

"Оракульний" — тут навмисно береться РЕАЛЬНА фактична ціна ВДР за той самий
день (не прогноз), щоб окремо виміряти теоретичну СТЕЛЮ вигоди від участі в
ВДР, перш ніж братися за прогнозну версію (оцінка ціни ВДР наперед — окреме,
складніше питання). Якщо навіть на оракульних цінах приросту немає/малий —
прогнозна версія точно не допоможе.

Запуск: python3 scratch/backtest_dam_idm.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from src.modules.optimization_service.milp_model import optimize_battery_schedule, optimize_battery_schedule_dam_idm

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "historical_data_merged.csv")

# Реальні параметри активу з локальної БД (assets, 'BESS Unit 1 (Primary)').
BESS_PARAMS = dict(
    battery_capacity=1000.0, max_charge_power=250.0, max_discharge_power=250.0,
    charge_efficiency=0.95, discharge_efficiency=0.95, initial_soc=0.20,
    min_soc=0.10, max_soc=0.90, max_cycles_per_day=1.5, degradation_cost=1.20,
    mode='arbitrage',
)


def load_full_days():
    df = pd.read_csv(DATA_PATH)
    df['Datetime'] = pd.to_datetime(df['Datetime'])
    df = df.dropna(subset=['Price', 'IDM_Price']).sort_values('Datetime')
    df['Date'] = df['Datetime'].dt.date
    days = []
    for date, g in df.groupby('Date'):
        g = g.sort_values('Datetime')
        if len(g) == 24 and g['Hour'].nunique() == 24:
            days.append((date, g['Price'].tolist(), g['IDM_Price'].tolist()))
    return days


def main():
    days = load_full_days()
    print(f"Повних діб з реальними Price+IDM_Price: {len(days)}")
    if not days:
        return

    # Рівномірна вибірка до 300 днів по всьому періоду 2021-2026 (не тільки
    # останні дні), щоб не переоцінити/недооцінити ефект через один сезон.
    if len(days) > 300:
        idx = np.linspace(0, len(days) - 1, 300).astype(int)
        days = [days[i] for i in idx]
    print(f"Днів у вибірці для бектесту: {len(days)}")

    for headroom in (0.0, 0.3, 1.0):
        uplifts = []
        dam_profits = []
        total_profits = []
        for date, dam_prices, idm_prices in days:
            dam_res = optimize_battery_schedule(dam_prices, **BESS_PARAMS)
            two_res = optimize_battery_schedule_dam_idm(dam_prices, idm_prices, idm_headroom_fraction=headroom, **BESS_PARAMS)
            if not dam_res or not two_res:
                continue
            dam_profits.append(dam_res['net_profit_uah'])
            total_profits.append(two_res['total_net_profit_uah'])
            uplifts.append(two_res['uplift_vs_dam_only_uah'])

        uplifts = np.array(uplifts)
        dam_profits = np.array(dam_profits)
        total_profits = np.array(total_profits)
        print(f"\n=== idm_headroom_fraction={headroom} ===")
        print(f"  Днів прораховано: {len(uplifts)}")
        print(f"  Середній прибуток DAM-only: {dam_profits.mean():.1f} грн/добу")
        print(f"  Середній прибуток DAM+IDM:  {total_profits.mean():.1f} грн/добу")
        print(f"  Середній приріст: {uplifts.mean():.1f} грн/добу ({100*uplifts.mean()/max(1e-9, abs(dam_profits.mean())):.1f}% від DAM-only)")
        print(f"  Медіана приросту: {np.median(uplifts):.1f} грн/добу")
        print(f"  Частка днів з приростом >0: {100*np.mean(uplifts > 0.01):.1f}%")
        print(f"  Частка днів з приростом <0 (мало бути неможливо!): {100*np.mean(uplifts < -0.01):.1f}%")
        print(f"  Мін/Макс приріст: {uplifts.min():.1f} / {uplifts.max():.1f} грн/добу")
        print(f"  Сума за весь період вибірки: {uplifts.sum():.0f} грн ({len(uplifts)} днів)")


if __name__ == "__main__":
    main()
