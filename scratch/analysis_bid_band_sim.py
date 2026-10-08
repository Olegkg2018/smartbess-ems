"""Порівняння правил ціни заявки РДН на реальних заявках проду, включно з P10/P90
(купівля по верхній межі P90, продаж по нижній P10). Лише доби, де P10/P90 є для
всіх buy/sell заявок. Та сама модель SoC/КПД/зносу, що scratch/analysis_bid_margin_sim.py.
Запуск: python3 scratch/analysis_bid_band_sim.py scratch/market_bids_with_bands_20261008.csv"""
import sys
import numpy as np
import pandas as pd

d = pd.read_csv(sys.argv[1], parse_dates=['timestamp'])
d = d[d.bid_type.isin(['buy', 'sell'])].copy()
d['day'] = d.timestamp.dt.tz_localize('UTC').dt.tz_convert('Europe/Kyiv').dt.date
full = d.groupby('day').lower_bound_uah.apply(lambda s: s.notna().all())
d = d[d.day.isin(full[full].index)]
CAP, MIN, MAX, ETA, DEG = 4000., 400., 3600., 0.95, 700.


def sim(rule):
    soc = MIN; prof = 0; nb = ns = eb = es = 0; days = []; worst = []
    for day, g in d.groupby('day'):
        sells = g[g.bid_type == 'sell']; buys = g[g.bid_type == 'buy']
        ctx = dict(avg_sell=sells.forecast_price_uah.mean() if len(sells) else np.nan,
                   avg_buy=buys.forecast_price_uah.mean() if len(buys) else np.nan)
        p0 = prof
        for _, r in g.sort_values('timestamp').iterrows():
            lim = rule(r, ctx); act = r.actual_price_uah; v = r.volume_kw
            if r.bid_type == 'buy':
                nb += 1
                if lim >= act:
                    e = min(v * ETA, MAX - soc); eb += 1
                    soc += e; prof -= act * e / ETA / 1000
            else:
                ns += 1
                if lim <= act:
                    e = min(v, (soc - MIN) * ETA); es += 1
                    soc -= e / ETA; prof += act * e / 1000 - DEG * e / 1000
        days.append(prof - p0)
    days = np.array(days)
    return dict(profit=prof, buy_exec=eb / nb, sell_exec=es / ns, loss_days=int((days < 0).sum()),
                worst=days.min(), n=len(days))


def be_buy(c):
    return ETA ** 2 * c['avg_sell'] - DEG if not np.isnan(c['avg_sell']) else None


def be_sell(c):
    return (c['avg_buy'] if not np.isnan(c['avg_buy']) else 0.0) / ETA ** 2 + DEG


def breakeven(r, c):
    if r.bid_type == 'buy':
        b = be_buy(c)
        return b if b is not None else r.forecast_price_uah * 1.02
    return be_sell(c)


def band(r, c):
    return r.upper_bound_uah if r.bid_type == 'buy' else r.lower_bound_uah


def band_capped(r, c):
    # P90/P10, але не гірше точки беззбитковості
    if r.bid_type == 'buy':
        b = be_buy(c)
        return min(r.upper_bound_uah, b) if b is not None else r.upper_bound_uah
    return max(r.lower_bound_uah, be_sell(c))


rules = {
    'буфер 2% (було)': lambda r, c: r.forecast_price_uah * (1.02 if r.bid_type == 'buy' else 0.98),
    'буфер ±2000 грн': lambda r, c: r.forecast_price_uah + (2000 if r.bid_type == 'buy' else -2000),
    'P90 купівля / P10 продаж': band,
    'P90/P10, обмежено беззбитковістю': band_capped,
    'беззбиткова (зараз)': breakeven,
}
print(f"{'правило':34} {'прибуток,тис':>12} {'buy вик':>8} {'sell вик':>8} {'збит.днів':>9} {'гірша доба':>10}")
for k, f in rules.items():
    r = sim(f)
    print(f"{k:34} {r['profit']/1000:12.1f} {r['buy_exec']*100:7.0f}% {r['sell_exec']*100:7.0f}% {r['loss_days']:9d} {r['worst']:10.0f}")
print('діб:', r['n'])
