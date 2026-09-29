"""
LEAR (Lasso Estimated AutoRegressive, Lago et al. 2021, epftoolbox) — стандартний
академічний бенчмарк прогнозу цін РДН — проти продового LightGBM на тих самих
днях (2026-09-28, пункт 4 плану розвитку, CLAUDE.md п.59).

Реалізація без пакета epftoolbox (тягне TensorFlow — зайве для VPS 2GB), за
його описом (epftoolbox/models/_lear.py):
  - окрема LassoLarsIC(criterion='aic') регресія на кожну годину доби;
  - 247 ознак: ціни доби D-1, D-2, D-3, D-7 (4×24), дві екзогенні змінні
    на D, D-1, D-7 (2×3×24), dummy дня тижня (7);
  - variance-stabilizing: asinh від ціни, нормалізованої медіаною/MAD
    вікна калібрування;
  - ансамбль (середнє) з кількох вікон калібрування.
Екзогенні змінні: Shortwave_Radiation і Temperature (в Україні немає
публічного прогнозу навантаження/ВДЕ; обидві — архівна фактична погода, те
саме наближення, що й acknowledge_approximation=True у walk_forward_backtest).

Чесність часу: прогноз на D рахується о 06:00 D-1, коли ціна доби D-1 вже
опублікована (~13:00 D-2) — P(D-1) доступна, як і Price_Lag_24 у LightGBM.

Порівняння — з as-of бейзлайном LightGBM (walk_forward_backtest, продовий шлях)
на СПІЛЬНИХ днях: середній WAPE, скільки днів кращий, тест Diebold-Mariano на
денних середніх абсолютних похибках.

Запуск: python3 scratch/backtest_lear.py [test_days]
"""
import sys
import json
import math
import time
import datetime

sys.path.insert(0, '.')

import numpy as np
import pandas as pd
from sklearn.linear_model import LassoLarsIC

import src.modules.market_data_service.data_manager as dm

TEST_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 90
# epftoolbox: 56/84/1092/1456 днів. Короткі вікна (< 247 ознак) у сучасному
# sklearn падають — LassoLarsIC не може оцінити дисперсію шуму при
# n_samples <= n_features (epftoolbox писався під старший sklearn). Тому лише
# довгі вікна — відхилення від статті, чесно зафіксоване.
WINDOWS = [364, 728, 1092]
EXOG = ['Shortwave_Radiation', 'Temperature']


def daily_matrix(df, col):
    """Kyiv-доби × 24 години; лише повні 24-годинні доби (доби переходу
    годинника — 23/25 годин — пропускаються, як і в epftoolbox)."""
    kyiv = df['Datetime'].dt.tz_localize('UTC').dt.tz_convert('Europe/Kyiv')
    t = pd.DataFrame({'date': kyiv.dt.date, 'hour': kyiv.dt.hour, 'v': df[col].values})
    counts = t.groupby('date').size()
    full = counts[counts == 24].index
    t = t[t['date'].isin(full)]
    return t.pivot_table(index='date', columns='hour', values='v', aggfunc='first').sort_index()


def build_xy(prices, exog, dates):
    """Рядок на кожну дату з повною історією лагів. Повертає X, Y, список дат."""
    idx = {d: i for i, d in enumerate(prices.index)}
    X, Y, out_dates = [], [], []
    for d in dates:
        lag = [d - datetime.timedelta(days=k) for k in (1, 2, 3, 7)]
        ex_lag = [d, d - datetime.timedelta(days=1), d - datetime.timedelta(days=7)]
        if d not in idx or any(x not in idx for x in lag) or any(x not in e.index for e in exog for x in ex_lag):
            continue
        row = []
        for x in lag:
            row.extend(prices.loc[x].values)
        for e in exog:
            for x in ex_lag:
                row.extend(e.loc[x].values)
        dow = np.zeros(7)
        dow[pd.Timestamp(d).dayofweek] = 1
        row.extend(dow)
        row = np.array(row, dtype=float)
        if np.isnan(row).any() or np.isnan(prices.loc[d].values).any():
            continue
        X.append(row)
        Y.append(prices.loc[d].values)
        out_dates.append(d)
    return np.array(X), np.array(Y), out_dates


N_PRICE_COLS = 4 * 24


def fit_lear(X_all, Y_all, date_pos, target_date, window):
    """Калібрування на вікні [target-window, target) — лише минуле. Повертає
    (моделі 24 години, параметри трансформації) або None."""
    rows = [date_pos[d] for d in (target_date - datetime.timedelta(days=k) for k in range(window, 0, -1))
            if d in date_pos]
    if len(rows) < 30:
        return None
    X_tr, Y_tr = X_all[rows].copy(), Y_all[rows]
    # Нормалізація + asinh (variance-stabilizing), параметри — лише з вікна.
    med = np.median(Y_tr)
    mad = np.median(np.abs(Y_tr - med)) * 1.4826 or 1.0
    X_tr[:, :N_PRICE_COLS] = np.arcsinh((X_tr[:, :N_PRICE_COLS] - med) / mad)
    ex_mu = X_tr[:, N_PRICE_COLS:-7].mean(axis=0)
    ex_sd = X_tr[:, N_PRICE_COLS:-7].std(axis=0)
    ex_sd[ex_sd == 0] = 1.0
    X_tr[:, N_PRICE_COLS:-7] = (X_tr[:, N_PRICE_COLS:-7] - ex_mu) / ex_sd
    hour_models = []
    for h in range(24):
        m = LassoLarsIC(criterion='aic', max_iter=2500)
        m.fit(X_tr, np.arcsinh((Y_tr[:, h] - med) / mad))
        hour_models.append(m)
    return hour_models, (med, mad, ex_mu, ex_sd)


def predict_lear(fitted, x_row):
    hour_models, (med, mad, ex_mu, ex_sd) = fitted
    x = x_row.copy()[None, :]
    x[:, :N_PRICE_COLS] = np.arcsinh((x[:, :N_PRICE_COLS] - med) / mad)
    x[:, N_PRICE_COLS:-7] = (x[:, N_PRICE_COLS:-7] - ex_mu) / ex_sd
    return np.array([np.sinh(m.predict(x)[0]) * mad + med for m in hour_models])


def wape(y, p):
    return float(np.sum(np.abs(y - p)) / max(np.sum(np.abs(y)), 1e-9) * 100.0)


def diebold_mariano(loss_a, loss_b):
    """DM-тест (h=1) на рядах денних втрат; від'ємна статистика = A краща.
    p-value — двобічне, нормальне наближення."""
    d = np.asarray(loss_a) - np.asarray(loss_b)
    n = len(d)
    stat = d.mean() / (d.std(ddof=1) / math.sqrt(n))
    p = math.erfc(abs(stat) / math.sqrt(2))
    return float(stat), float(p)


df = dm.load_merged_csv_cached(['Datetime', 'Price'] + EXOG)
prices = daily_matrix(df, 'Price')
exog = [daily_matrix(df, c) for c in EXOG]
all_dates = list(prices.index)
test_dates = all_dates[-TEST_DAYS - 1:-1]  # без сьогодні (може бути неповна)
print(f"LEAR: {len(test_dates)} test days {test_dates[0]} .. {test_dates[-1]}, windows {WINDOWS}", flush=True)

# Матриця ознак — один раз для всіх дат (рядок дати D використовує лише D-1..D-7
# ціни і погоду D/D-1/D-7 — без майбутнього).
X_all, Y_all, dates_ok = build_xy(prices, exog, all_dates)
date_pos = {d: i for i, d in enumerate(dates_ok)}
print(f"feature matrix {X_all.shape}", flush=True)

# Перекалібрування раз на RETRAIN_EVERY днів — так само, як LightGBM у
# walk_forward_backtest(retrain_every_days=7), щоб порівняння було рівним.
RETRAIN_EVERY = 7
t0 = time.time()
lear_daily = {}
fitted = {}
days_since = RETRAIN_EVERY
for i, d in enumerate(test_dates):
    if d not in date_pos:
        continue
    if days_since >= RETRAIN_EVERY:
        fitted = {w: f for w in WINDOWS if (f := fit_lear(X_all, Y_all, date_pos, d, w)) is not None}
        days_since = 0
    days_since += 1
    if not fitted:
        continue
    p = np.mean([predict_lear(f, X_all[date_pos[d]]) for f in fitted.values()], axis=0)
    y = Y_all[date_pos[d]]
    lear_daily[str(d)] = {'wape': wape(y, p), 'mae': float(np.mean(np.abs(y - p)))}
    if (i + 1) % 20 == 0:
        print(f"  {i + 1}/{len(test_dates)} ({time.time() - t0:.0f}s)", flush=True)

# Бейзлайн — продовий LightGBM, as-of шлях, те саме вікно. Синк вимкнено,
# щоб знімок даних не змінився посеред розрахунку.
import src.modules.forecast_service.ml_pipeline as mt
dm.sync_realtime_data = lambda force=False: True
base = mt.walk_forward_backtest(test_days=TEST_DAYS, retrain_every_days=7, acknowledge_approximation=True)
base_daily = {d['date']: {'wape': d['wape'], 'mae': d['mae']} for d in base['daily']}

common = sorted(set(lear_daily) & set(base_daily))
lw = np.array([lear_daily[d]['wape'] for d in common])
bw = np.array([base_daily[d]['wape'] for d in common])
lm = np.array([lear_daily[d]['mae'] for d in common])
bm = np.array([base_daily[d]['mae'] for d in common])
dm_stat, dm_p = diebold_mariano(lm, bm)

summary = {
    'test_days_common': len(common),
    'period': [common[0], common[-1]] if common else None,
    'lear_mean_wape': float(lw.mean()), 'lightgbm_mean_wape': float(bw.mean()),
    'lear_median_wape': float(np.median(lw)), 'lightgbm_median_wape': float(np.median(bw)),
    'lear_mean_mae': float(lm.mean()), 'lightgbm_mean_mae': float(bm.mean()),
    'lear_better_days': int((lw < bw).sum()),
    'dm_stat_mae_lear_minus_lgbm': dm_stat, 'dm_p_value': dm_p,
}
print(json.dumps(summary, indent=2, ensure_ascii=False))
print("Тлумачення DM: stat<0 і p<0.05 — LEAR значуще краща; stat>0 і p<0.05 — LightGBM значуще краща.")

with open(f'scratch/backtest_lear_results_{TEST_DAYS}d.json', 'w') as f:
    json.dump({'summary': summary, 'lear_daily': lear_daily, 'lightgbm_daily': base_daily}, f, indent=2, default=str)
print(f"Saved: scratch/backtest_lear_results_{TEST_DAYS}d.json")
