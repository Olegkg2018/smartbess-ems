import pandas as pd, numpy as np, sys
d=pd.read_csv(sys.argv[1],parse_dates=['timestamp'])
d=d[d.actual_price_uah.notna()&d.bid_type.isin(['buy','sell','standby'])].copy()
d['day']=d.timestamp.dt.tz_localize('UTC').dt.tz_convert('Europe/Kyiv').dt.date
d=d[d.day>=pd.Timestamp('2026-08-01').date()]
CAP, MIN, MAX, ETA, DEG = 4000., 400., 3600., 0.95, 700.
def sim(rule, carry=True):
    soc=MIN; prof=0; nb=ns=eb=es=0; disch=0; days=[]
    for day,g in d.groupby('day'):
        if not carry: soc=MIN
        sells=g[g.bid_type=='sell']; buys=g[g.bid_type=='buy']
        ctx=dict(avg_sell=sells.forecast_price_uah.mean() if len(sells) else np.nan,
                 avg_buy=buys.forecast_price_uah.mean() if len(buys) else np.nan)
        p0=prof
        for _,r in g.sort_values('timestamp').iterrows():
            if r.bid_type=='standby': continue
            lim=rule(r,ctx); act=r.actual_price_uah; v=r.volume_kw
            if r.bid_type=='buy':
                nb+=1
                if lim>=act:
                    e=min(v*ETA, MAX-soc); eb+=1
                    soc+=e; prof-=act*e/ETA/1000
            else:
                ns+=1
                if lim<=act:
                    e=min(v, (soc-MIN)*ETA); es+=1
                    soc-=e/ETA; prof+=act*e/1000 - DEG*e/1000; disch+=e
        days.append(prof-p0)
    days=np.array(days)
    return dict(profit=prof, per_day=prof/len(days), buy_exec=eb/nb, sell_exec=es/ns, mwh=disch/1000, end_soc=soc, loss_days=(days<0).sum(), n=len(days))
rules={}
for m in [0,2,5,10,15,20,30]:
    rules[f'pct {m}%']=lambda r,c,m=m: r.forecast_price_uah*(1+m/100) if r.bid_type=='buy' else r.forecast_price_uah*(1-m/100)
for m in [500,1000,1500,2000,3000]:
    rules[f'abs {m}']=lambda r,c,m=m: r.forecast_price_uah+m if r.bid_type=='buy' else r.forecast_price_uah-m
for m in [1000,1500,2000]:
    rules[f'buy+{m}/sell-15%']=lambda r,c,m=m: r.forecast_price_uah+m if r.bid_type=='buy' else r.forecast_price_uah*0.85
def be(r,c,k=1.0):
    if r.bid_type=='buy':
        return (ETA**2*c['avg_sell']-DEG)*k if not np.isnan(c['avg_sell']) else r.forecast_price_uah
    base=c['avg_buy'] if not np.isnan(c['avg_buy']) else 0.0
    return base/ETA**2+DEG
rules['breakeven (беззбиткова ціна)']=be
rules['breakeven buy×0.8']=lambda r,c: be(r,c,0.8)
rules['ринкове (все виконується)']=lambda r,c: 1e9 if r.bid_type=='buy' else -1e9
for carry in (True,):
    print(f"{'стратегія':32} {'прибуток,тис':>12} {'грн/добу':>9} {'buy вик':>8} {'sell вик':>8} {'МВт·год':>8} {'збит.днів':>9} {'SoC кін':>8}")
    for k,f in rules.items():
        r=sim(f,carry)
        print(f"{k:32} {r['profit']/1000:12.1f} {r['per_day']:9.0f} {r['buy_exec']*100:7.0f}% {r['sell_exec']*100:7.0f}% {r['mwh']:8.0f} {r['loss_days']:9d} {r['end_soc']:8.0f}")
    print("днів:", r['n'])
