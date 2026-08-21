import os
import json
import pickle
import datetime
import pandas as pd
import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor
from sklearn.neural_network import MLPRegressor
from lightgbm import LGBMRegressor, LGBMClassifier

from src.core.config import settings
import src.modules.market_data_service.data_manager as dm
from src.database.session import SessionLocal
from src.database.models import WeatherForecastArchive
# Фаза B (2026-08-21): FEATURES/побудова ознак/пост-обробка винесені в
# feature_pipeline.py — єдине джерело і для навчання/бектеста, і для живого
# прогнозу (раніше prepare_features/build_forecast_feature_matrix були
# двома незалежними реалізаціями, що ніколи фізично не перетиналися; див.
# docs/review_ml_forecast_pipeline_2026-08-21.md).
from src.modules.forecast_service import feature_pipeline
from src.modules.forecast_service.feature_pipeline import (
    FEATURES, PRICE_FLOOR, PRICE_CAP, clip_and_shift, _get_price_shift_pct,
)

DATA_DIR = settings.DATA_DIR
LGBM_MODEL_PATH = os.path.join(DATA_DIR, "model_lightgbm.pkl")
XGB_MODEL_PATH = os.path.join(DATA_DIR, "model_xgboost.pkl")
MLP_MODEL_PATH = os.path.join(DATA_DIR, "model_mlp.pkl")
SCALER_PATH = os.path.join(DATA_DIR, "scaler.pkl")
BACKTEST_REPORT_PATH = os.path.join(DATA_DIR, "backtest_report.json")
Q_LOWER_MODEL_PATH = os.path.join(DATA_DIR, "model_lightgbm_q_lower.pkl")
Q_UPPER_MODEL_PATH = os.path.join(DATA_DIR, "model_lightgbm_q_upper.pkl")
CONFORMAL_CALIBRATION_PATH = os.path.join(DATA_DIR, "conformal_calibration.json")
QUANTILE_LOWER = 0.1
QUANTILE_UPPER = 0.9

def _atomic_pickle_dump(obj, path):
    """
    Пише у tmp-файл поруч і атомарно (os.replace, POSIX rename) підміняє
    боевий шлях — якщо процес уб'ють (OOM, рестарт) саме під час запису,
    predict_next_day/predict_price_band все одно побачать або повністю
    старий, або повністю новий файл, ніколи не битий pickle посередині.
    Критично при автоматичному нічному перенавчанні без нагляду.
    """
    tmp_path = path + ".tmp"
    with open(tmp_path, 'wb') as f:
        pickle.dump(obj, f)
    os.replace(tmp_path, path)

def _atomic_json_dump(obj, path, **kwargs):
    tmp_path = path + ".tmp"
    with open(tmp_path, 'w') as f:
        json.dump(obj, f, **kwargs)
    os.replace(tmp_path, path)

# FEATURES/is_ukrainian_holiday — див. feature_pipeline.py (Фаза B, 2026-08-21).

def prepare_features(df):
    """Сумісна тонка обгортка над feature_pipeline.build_training_table()
    (Фаза B) — стара назва лишена на випадок зовнішніх викликів."""
    return feature_pipeline.build_training_table(df)

def calculate_mape_wape(y_true, y_pred):
    y_true = np.array(y_true)
    y_pred = np.array(y_pred)
    non_zero = y_true != 0
    mape = np.mean(np.abs((y_true[non_zero] - y_pred[non_zero]) / y_true[non_zero])) * 100 if np.any(non_zero) else 0.0
    wape = (np.sum(np.abs(y_true - y_pred)) / np.sum(np.abs(y_true))) * 100 if np.sum(np.abs(y_true)) != 0 else 0.0
    return float(mape), float(wape)

def _make_lgbm():
    return LGBMRegressor(
        n_estimators=300, max_depth=7, learning_rate=0.06, subsample=0.85, colsample_bytree=0.85,
        random_state=42, n_jobs=-1, verbose=-1
    )

def _make_xgb():
    return XGBRegressor(
        n_estimators=300, max_depth=7, learning_rate=0.06, subsample=0.85, colsample_bytree=0.85,
        min_child_weight=3, random_state=42, n_jobs=-1
    )

def _make_mlp():
    return MLPRegressor(
        hidden_layer_sizes=(128, 64, 32), activation='relu', solver='adam', max_iter=500,
        random_state=42, early_stopping=True, validation_fraction=0.1
    )

# ПРИМІТКА: частка годин з ціною на підлозі (<=15 грн, профіцит СЕС) реально
# зросла з ~0.2-0.5% у 2021-2025 до ~4% (і ~20% влітку опівдні) у 2026 —
# звідси гірший WAPE саме по суботах (найбільше сонця + найменший попит).
# Пробували exponential recency weighting (half-life ~180 днів) як фікс —
# емпірично на walk_forward_backtest ПОГІРШИЛО і суботи, і останні 14 днів
# (перевірено, не здогадка) — відкочено. Не переробляти без нового бектесту.
def train_models():
    df_raw = dm.get_combined_historical_data()
    df = prepare_features(df_raw)

    X = df[FEATURES]
    y = df['Price']

    split_idx = int(len(df) * 0.85)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    scaler_eval = StandardScaler()
    X_train_scaled = scaler_eval.fit_transform(X_train)
    X_test_scaled = scaler_eval.transform(X_test)

    lgb_eval = _make_lgbm()
    lgb_eval.fit(X_train, y_train)
    y_pred_lgb = lgb_eval.predict(X_test)
    mae_lgb = mean_absolute_error(y_test, y_pred_lgb)
    rmse_lgb = np.sqrt(mean_squared_error(y_test, y_pred_lgb))
    r2_lgb = r2_score(y_test, y_pred_lgb)
    mape_lgb, wape_lgb = calculate_mape_wape(y_test, y_pred_lgb)

    xgb_eval = _make_xgb()
    xgb_eval.fit(X_train, y_train)
    y_pred_xgb = xgb_eval.predict(X_test)
    mae_xgb = mean_absolute_error(y_test, y_pred_xgb)
    rmse_xgb = np.sqrt(mean_squared_error(y_test, y_pred_xgb))
    r2_xgb = r2_score(y_test, y_pred_xgb)
    mape_xgb, wape_xgb = calculate_mape_wape(y_test, y_pred_xgb)

    mlp_eval = _make_mlp()
    mlp_eval.fit(X_train_scaled, y_train)
    y_pred_mlp = mlp_eval.predict(X_test_scaled)
    mae_mlp = mean_absolute_error(y_test, y_pred_mlp)
    rmse_mlp = np.sqrt(mean_squared_error(y_test, y_pred_mlp))
    r2_mlp = r2_score(y_test, y_pred_mlp)
    mape_mlp, wape_mlp = calculate_mape_wape(y_test, y_pred_mlp)

    metrics = {
        'lightgbm': {'mae': float(mae_lgb), 'rmse': float(rmse_lgb), 'r2': float(r2_lgb), 'mape': mape_lgb, 'wape': wape_lgb},
        'xgboost': {'mae': float(mae_xgb), 'rmse': float(rmse_xgb), 'r2': float(r2_xgb), 'mape': mape_xgb, 'wape': wape_xgb},
        'mlp': {'mae': float(mae_mlp), 'rmse': float(rmse_mlp), 'r2': float(r2_mlp), 'mape': mape_mlp, 'wape': wape_mlp},
        'trained_at': datetime.datetime.utcnow().isoformat() + 'Z',
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    metrics_path = os.path.join(DATA_DIR, "metrics_report.json")
    _atomic_json_dump(metrics, metrics_path, indent=4)

    lgb_final = _make_lgbm()
    lgb_final.fit(X, y)

    xgb_final = _make_xgb()
    xgb_final.fit(X, y)

    scaler_final = StandardScaler()
    X_scaled = scaler_final.fit_transform(X)

    mlp_final = _make_mlp()
    mlp_final.fit(X_scaled, y)

    _atomic_pickle_dump(lgb_final, LGBM_MODEL_PATH)
    _atomic_pickle_dump(xgb_final, XGB_MODEL_PATH)
    _atomic_pickle_dump(mlp_final, MLP_MODEL_PATH)
    _atomic_pickle_dump(scaler_final, SCALER_PATH)

    return metrics

def _make_quantile_lgbm(alpha):
    return LGBMRegressor(
        objective='quantile', alpha=alpha,
        n_estimators=300, max_depth=7, learning_rate=0.06, subsample=0.85, colsample_bytree=0.85,
        random_state=42, n_jobs=-1, verbose=-1
    )

def train_quantile_models(calibration_days=60):
    """
    Навчає LightGBM quantile regressors для P10/P90 (тими самими FEATURES, що
    й точковий прогноз) і калібрує їх split-conformal поправкою на реальному
    held-out вікні (останні calibration_days) — щоб інтервал [P10,P90] мав
    приблизно номінальне ~80% покриття на РЕАЛЬНИХ даних, а не просто
    теоретичне покриття квантильної регресії (яке на практиці часто гірше
    заявленого через зсув моделі).

    Фаза B (2026-08-21): продові моделі — САМЕ lower_model/upper_model,
    навчені лише на df_train (без df_calib). До Фази B тут стояв додатковий
    крок "перенавчити на всіх даних (X_all,y_all)" і саме ЦІ перенавчені
    моделі йшли в .pkl — але conformal-поправка порахована на df_calib, яку
    перенавчена модель уже бачила під час власного фіту, тобто формальна
    split-conformal гарантія покриття не виконувалась для реально
    розгорнутої моделі (docs/review_ml_forecast_pipeline_2026-08-21.md).
    Ціна — ~calibration_days днів меншої свіжості квантильних моделей
    порівняно з точковою; прийнятно, бо нічний retrain (02:00) щодня
    зсуває це вікно вперед, а сама поправка й так рахується на
    найсвіжіших calibration_days.
    """
    df_raw = dm.get_combined_historical_data()
    df = prepare_features(df_raw)
    df = df.sort_values('Datetime').reset_index(drop=True)

    n = len(df)
    calib_hours = calibration_days * 24
    if n < calib_hours * 3:
        calib_hours = max(24 * 14, n // 5)

    split_idx = n - calib_hours
    df_train = df.iloc[:split_idx]
    df_calib = df.iloc[split_idx:]

    X_train, y_train = df_train[FEATURES], df_train['Price']
    lower_model = _make_quantile_lgbm(QUANTILE_LOWER)
    lower_model.fit(X_train, y_train)
    upper_model = _make_quantile_lgbm(QUANTILE_UPPER)
    upper_model.fit(X_train, y_train)

    # Split conformal calibration (CQR-style): наскільки сирий інтервал
    # промахується повз реальні дані на held-out вікні, яке модель не бачила.
    X_calib, y_calib = df_calib[FEATURES], df_calib['Price'].values
    pred_lower_calib = lower_model.predict(X_calib)
    pred_upper_calib = upper_model.predict(X_calib)

    scores = np.maximum(pred_lower_calib - y_calib, y_calib - pred_upper_calib)
    target_coverage = QUANTILE_UPPER - QUANTILE_LOWER  # 0.8
    conformal_alpha = 1.0 - target_coverage
    q_level = float(np.clip(np.ceil((len(scores) + 1) * (1 - conformal_alpha)) / len(scores), 0.0, 1.0))
    correction = max(0.0, float(np.quantile(scores, q_level)))

    coverage_raw = float(np.mean((y_calib >= pred_lower_calib) & (y_calib <= pred_upper_calib)))
    coverage_conformal = float(np.mean((y_calib >= pred_lower_calib - correction) & (y_calib <= pred_upper_calib + correction)))

    # Продові моделі — це САМЕ lower_model/upper_model вище (навчені лише на
    # df_train, чесно виключаючи df_calib) — жодного повторного фіту на всіх
    # даних, інакше conformal-поправка формально не покриває розгорнуту
    # модель (див. докстрінг функції).
    os.makedirs(DATA_DIR, exist_ok=True)
    _atomic_pickle_dump(lower_model, Q_LOWER_MODEL_PATH)
    _atomic_pickle_dump(upper_model, Q_UPPER_MODEL_PATH)

    calibration = {
        'quantile_lower': QUANTILE_LOWER,
        'quantile_upper': QUANTILE_UPPER,
        'target_coverage': target_coverage,
        'conformal_correction_uah': correction,
        'calibration_hours': int(len(df_calib)),
        'coverage_raw_quantile_regression': coverage_raw,
        'coverage_after_conformal_correction': coverage_conformal,
        'trained_at': datetime.datetime.utcnow().isoformat() + 'Z',
    }
    _atomic_json_dump(calibration, CONFORMAL_CALIBRATION_PATH, indent=2)

    return calibration

QUANTILE_COVERAGE_REPORT_PATH = os.path.join(DATA_DIR, "quantile_coverage_report.json")

def quantile_coverage_backtest(test_days=60, retrain_every_days=14, calib_days=14, acknowledge_approximation=False):
    """
    Чесна walk-forward перевірка калібрування P10/P90 інтервалу на реальних
    історичних даних — не одне статичне вікно, а день у день по всьому
    test_days: train -> невеликий conformal calib -> тест, вікно рухається
    вперед, моделі й conformal-поправка перераховуються раз на
    retrain_every_days (як і в проді). Перевіряємо ПОКРИТТЯ (чи потрапляє
    факт у [P10,P90]), а не точність точки.

    Ця функція НЕ має жодного виклику ніде в застосунку (перевірено
    2026-08-21) — залишена для ручного запуску. Фаза B торкнулась лише
    build_training_table() (усунення утечки, спільне з walk_forward_backtest)
    і gate нижче; повна as-of-уніфікація з build_asof_feature_matrix() для
    цієї функції НЕ зроблена (нема жодного продового споживача, який
    виправляти) — див. docs/review_ml_forecast_pipeline_2026-08-21.md.
    """
    if not acknowledge_approximation:
        raise ValueError(
            "quantile_coverage_backtest оцінюється на архівній (не прогнозній) погоді, "
            "вже запеченій у historical_data_merged.csv — це оптимістичне наближення "
            "реальної точності. Потрібне явне acknowledge_approximation=True. Див. "
            "docs/review_ml_forecast_pipeline_2026-08-21.md."
        )
    df_raw = dm.get_combined_historical_data()
    df = prepare_features(df_raw)
    df = df.sort_values('Datetime').reset_index(drop=True)

    if len(df) < 24 * (test_days + calib_days + 60):
        test_days = max(14, len(df) // 24 - calib_days - 60)

    last_date = df['Datetime'].max().normalize()
    first_test_day = last_date - pd.Timedelta(days=test_days - 1)

    daily_results = []
    lower_model = upper_model = None
    correction = 0.0
    days_since_retrain = 0
    target_coverage = QUANTILE_UPPER - QUANTILE_LOWER

    day = first_test_day
    while day <= last_date:
        train_mask = df['Datetime'] < (day - pd.Timedelta(days=calib_days))
        calib_mask = (df['Datetime'] >= (day - pd.Timedelta(days=calib_days))) & (df['Datetime'] < day)
        test_mask = (df['Datetime'] >= day) & (df['Datetime'] < day + pd.Timedelta(days=1))

        df_train = df[train_mask]
        df_calib = df[calib_mask]
        df_test = df[test_mask]

        if len(df_train) < 24 * 60 or len(df_calib) < 24 * 7 or df_test.empty:
            day += pd.Timedelta(days=1)
            continue

        if lower_model is None or days_since_retrain >= retrain_every_days:
            lower_model = _make_quantile_lgbm(QUANTILE_LOWER)
            lower_model.fit(df_train[FEATURES], df_train['Price'])
            upper_model = _make_quantile_lgbm(QUANTILE_UPPER)
            upper_model.fit(df_train[FEATURES], df_train['Price'])

            pred_lower_calib = lower_model.predict(df_calib[FEATURES])
            pred_upper_calib = upper_model.predict(df_calib[FEATURES])
            y_calib = df_calib['Price'].values
            scores = np.maximum(pred_lower_calib - y_calib, y_calib - pred_upper_calib)
            conformal_alpha = 1.0 - target_coverage
            q_level = float(np.clip(np.ceil((len(scores) + 1) * (1 - conformal_alpha)) / len(scores), 0.0, 1.0))
            correction = max(0.0, float(np.quantile(scores, q_level)))
            days_since_retrain = 0

        y_pred_lower = lower_model.predict(df_test[FEATURES]) - correction
        y_pred_upper = upper_model.predict(df_test[FEATURES]) + correction
        y_true = df_test['Price'].values

        covered = (y_true >= y_pred_lower) & (y_true <= y_pred_upper)
        daily_results.append({
            'date': day.strftime('%Y-%m-%d'),
            'coverage': float(np.mean(covered)),
            'mean_band_width_uah': float(np.mean(y_pred_upper - y_pred_lower)),
            'n_hours': int(len(df_test)),
        })

        days_since_retrain += 1
        day += pd.Timedelta(days=1)

    if not daily_results:
        return {'daily': [], 'summary': {}}

    coverages = [d['coverage'] for d in daily_results]
    summary = {
        'test_days': len(daily_results),
        'retrain_every_days': retrain_every_days,
        'target_coverage': target_coverage,
        'mean_coverage': float(np.mean(coverages)),
        'median_coverage': float(np.median(coverages)),
        'pct_days_within_10pp_of_target': float(np.mean([abs(c - target_coverage) <= 0.10 for c in coverages])),
        'mean_band_width_uah': float(np.mean([d['mean_band_width_uah'] for d in daily_results])),
        'methodology_version': 'phase_b_leakage_fix_only_2026',
        'weather_mode': 'archived_actual_approx',
    }

    report = {'daily': daily_results, 'summary': summary}
    _atomic_json_dump(report, QUANTILE_COVERAGE_REPORT_PATH, indent=2)

    return report

SURPLUS_PRICE_THRESHOLD = 20.0  # UAH/MWh — поріг "режиму профіциту" (ціна біля підлоги)
SURPLUS_FLOOR_ESTIMATE = 12.0   # UAH/MWh — типове значення всередині профіцитного кластеру

def _make_surplus_classifier():
    return LGBMClassifier(
        n_estimators=300, max_depth=6, learning_rate=0.06, subsample=0.85, colsample_bytree=0.85,
        random_state=42, n_jobs=-1, verbose=-1, class_weight='balanced'
    )

def _blend_with_surplus_proba(point_pred, surplus_proba, floor_estimate=SURPLUS_FLOOR_ESTIMATE):
    """
    point_pred — точковий прогноз регресії; surplus_proba — P(ціна впаде до
    профіцитної підлоги) з окремого класифікатора на тих самих ознаках.
    Лінійна суміш замість жорсткого порогу — уникає різкого "перемикання".
    """
    return surplus_proba * floor_estimate + (1.0 - surplus_proba) * point_pred

ENSEMBLE_MODEL_TYPES = ('ensemble_average', 'ensemble_weighted')

def _train_ensemble_members(df_train, features):
    lgbm = _make_lgbm()
    lgbm.fit(df_train[features], df_train['Price'])
    xgb = _make_xgb()
    xgb.fit(df_train[features], df_train['Price'])
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(df_train[features])
    mlp = _make_mlp()
    mlp.fit(X_scaled, df_train['Price'])
    return lgbm, xgb, mlp, scaler

def _predict_ensemble_members(members, df_slice, features):
    lgbm, xgb, mlp, scaler = members
    return {
        'lightgbm': lgbm.predict(df_slice[features]),
        'xgboost': xgb.predict(df_slice[features]),
        'mlp': mlp.predict(scaler.transform(df_slice[features])),
    }

def _compute_inverse_error_weights(df_train, features, val_days=14):
    """
    Ваги ансамблю = обернена помилка (1/MAE) кожної моделі, порахована на
    ЧЕСНОМУ held-out хвості df_train (останні val_days) — а НЕ на тестовому
    вікні бектеста, інакше вибір ваг був би підглядуванням у майбутнє. Якщо
    даних замало (початок бектеста) — фолбек на рівні ваги (просте середнє).
    """
    cutoff = df_train['Datetime'].max() - pd.Timedelta(days=val_days)
    fit_part = df_train[df_train['Datetime'] < cutoff]
    val_part = df_train[df_train['Datetime'] >= cutoff]
    equal = {'lightgbm': 1 / 3, 'xgboost': 1 / 3, 'mlp': 1 / 3}
    if len(fit_part) < 24 * 30 or val_part.empty:
        return equal

    members = _train_ensemble_members(fit_part, features)
    preds = _predict_ensemble_members(members, val_part, features)
    y_val = val_part['Price'].values
    maes = {k: mean_absolute_error(y_val, v) for k, v in preds.items()}
    inv = {k: 1.0 / max(m, 1e-6) for k, m in maes.items()}
    total = sum(inv.values())
    return {k: v / total for k, v in inv.items()}

def _weather_slice_archived_actual(df_raw, day, day_end):
    """weather_mode='archived_actual_approx' — архівна (фактична, не
    прогнозна) погода за добу з historical_data_merged.csv. Оптимістичне
    наближення (модель бачить погоду точнішу за реальний day-ahead прогноз,
    який отримує прод) — саме тому виклик заблокований без явного
    acknowledge_approximation=True, див. докстрінг walk_forward_backtest."""
    cols = ['Datetime', 'Temperature', 'Cloud_Cover', 'Wind_Speed', 'Shortwave_Radiation']
    sl = df_raw[(df_raw['Datetime'] >= day) & (df_raw['Datetime'] < day_end)][cols]
    return sl.sort_values('Datetime').reset_index(drop=True)


def _weather_slice_archived_forecast(day, day_end):
    """weather_mode='archived_forecast' — реально виданий прогноз погоди з
    WeatherForecastArchive (Фаза A, збирається з 2026-08-21) замість
    архівної факт-погоди. Чесніше, але архів фізично щойно почав
    накопичуватись — для більшості історичних днів покриття немає, і день
    чесно пропускається (не підміняється мовчки)."""
    db = SessionLocal()
    try:
        rows = db.query(WeatherForecastArchive).filter(
            WeatherForecastArchive.target_datetime >= day,
            WeatherForecastArchive.target_datetime < day_end,
        ).order_by(WeatherForecastArchive.issued_at_utc.desc()).all()
    finally:
        db.close()
    by_hour = {}
    for r in rows:
        by_hour.setdefault(r.target_datetime, r)  # desc order => перший = найсвіжіший issued_at
    if len(by_hour) < 24:
        return None
    records = [
        {'Datetime': dt, 'Temperature': r.temperature, 'Cloud_Cover': r.cloud_cover,
         'Wind_Speed': r.wind_speed, 'Shortwave_Radiation': r.shortwave_radiation}
        for dt, r in sorted(by_hour.items())
    ]
    return pd.DataFrame(records)


def walk_forward_backtest(test_days=90, retrain_every_days=7, model_type='lightgbm',
                           weather_mode='archived_actual_approx', acknowledge_approximation=False,
                           use_surplus_classifier=False, extra_features=None, idm_lag_hours=24):
    """
    Чесна оцінка точності день-наперед прогнозу: розширюване вікно навчання,
    прогноз на наступну добу (24г), крок вперед. Модель перенавчається раз на
    retrain_every_days (як і в проді — раз на тиждень), а не одноразовий
    85/15 holdout, який не показує, як точність змінюється у часі.

    idm_lag_hours=24 — Фаза C (2026-08-21): повторна перевірка старого
    відхиленого експерименту "Lag_48 замість Lag_24" на новому as-of-
    бектесті (застосовна лише коли asof_eligible, див. нижче — інакше
    ігнорується, бо legacy-шлях бере лаг з df_train[features], уже
    порахований build_training_table з тим самим idm_lag_hours).

    Фаза B (2026-08-21): для конфігурації, що реально відповідає проду
    (model_type='lightgbm', без use_surplus_classifier/extra_features) —
    предикт на тестову добу тепер іде через build_forecast_feature_matrix()
    (та сама функція, що й predict_next_day/predict_price_band у
    scheduler.py/forecast.py, з симульованим as_of=day), а не через зріз
    df_test[features] з глобально побудованого датафрейму. До Фази B
    walk_forward_backtest/quantile_coverage_backtest не викликались НІДЕ в
    застосунку (лише вручну) і йшли повз реальний прод-шлях зовсім — див.
    docs/review_ml_forecast_pipeline_2026-08-21.md. Для інших конфігурацій
    (ensemble/surplus_classifier/extra_features) немає прод-еквіваленту, з
    яким уніфікувати — вони лишаються на старому зрізовому шляху, лише з
    виправленою утечкою (build_training_table замість prepare_features).
    summary['methodology_version'] відрізняє обидва випадки, щоб не
    сплутати зі старими (leaky) звітами.

    weather_mode='archived_actual_approx' (типово) оцінюється на
    архівній/фактичній погоді — оптимістичне наближення реального
    day-ahead прогнозу, який бачить прод. Потребує явного
    acknowledge_approximation=True (рішення користувача, 2026-08-21) — інакше
    ValueError, а не тихий запуск. weather_mode='archived_forecast' бере
    реально виданий прогноз з WeatherForecastArchive (Фаза A) — чесніше, але
    для більшості історичних днів покриття ще немає (архів лише почав
    накопичуватись), такі дні пропускаються (report['summary']['skipped_days_no_weather_archive']).

    use_surplus_classifier=True — експериментальний двоступеневий режим:
    окремий LightGBM-класифікатор "ця година потрапить в профіцитну підлогу"
    (клас Price<=20, class_weight='balanced' через рідкість класу), змішаний
    з точковою регресією. Перевіряється ТУТ, у бектесті, на реальних даних,
    ДО того як потрапити у прод (як і з recency weighting — не віримо
    гіпотезі на слово).

    model_type='ensemble_average'/'ensemble_weighted' — блендинг LightGBM+
    XGBoost+MLP (усі три вже навчаються паралельно в train_models(), але у
    проді scheduler.py бере лише LightGBM). 'ensemble_average' — просте
    середнє трьох прогнозів; 'ensemble_weighted' — обернена помилка (MAE) на
    чесному held-out хвості кожного тренувального вікна (_compute_inverse_error_weights),
    без підглядування в тестові дні. use_surplus_classifier ігнорується для
    ансамблю (комбінація не реалізована — окремий експеримент).

    РЕЗУЛЬТАТ ПЕРЕВІРКИ ДО ФАЗИ B (walk_forward_backtest, test_days=60,
    retrain_every_days=7, дані 2021-2026-07, scratch/backtest_ensemble.py):
    baseline (соло LightGBM) mean_wape=26.224%, last_7d_mean_mape=1009.72,
    last_30d_mean_mape=566.38. ensemble_average: mean_wape=25.935% (краще),
    last_7d_mean_mape=985.59 (краще), last_30d_mean_mape=591.61 (ГІРШЕ).
    ensemble_weighted: mean_wape=25.765% (краще), last_7d_mean_mape=1043.09
    (ГІРШЕ), last_30d_mean_mape=533.83 (краще). Обидва варіанти покращують
    mean_wape, але РІЗНОНАПРАВЛЕНО псують одну з двох recency-метрик. Той
    самий патерн "загальне покращення / останні дні гірше" повторювався у
    щонайменше 4 незалежних експериментах (Lag_48, EU_DAM_Price, PL/RO
    погода, ensemble/recency) — ВСІ вони міряні на старому leaky бектесті
    (до Фази B) і варті повторної перевірки на новому as-of бектесті, перш
    ніж вважати їх остаточними (Фаза C, окрема сесія). Ансамбль НЕ
    підключено до проду (scheduler.py лишається на соло LightGBM). Код
    лишається доступним для повторної перевірки, а не для повторення цього
    самого (застарілого методологічно) прогону.

    extra_features — список додаткових колонок (напр. EU_DAM_Price_Lag_24),
    які додаються поверх продових FEATURES ЛИШЕ для цього прогону A/B-тесту,
    без зміни глобального FEATURES. Вимикає as-of-уніфікацію (див. вище).

    Повертає щоденний MAPE/WAPE + підсумкову статистику, зберігає у
    data/backtest_report.json.
    """
    if weather_mode not in ('archived_actual_approx', 'archived_forecast'):
        raise ValueError(f"Unknown weather_mode: {weather_mode!r}")
    if weather_mode == 'archived_actual_approx' and not acknowledge_approximation:
        raise ValueError(
            "walk_forward_backtest на архівній (не прогнозній) погоді дає оптимістичну оцінку "
            "точності — потрібне явне acknowledge_approximation=True. Див. "
            "docs/review_ml_forecast_pipeline_2026-08-21.md."
        )

    df_raw = dm.get_combined_historical_data()
    df = feature_pipeline.build_training_table(df_raw, idm_lag_hours=idm_lag_hours)
    df = df.sort_values('Datetime').reset_index(drop=True)
    df_raw = df_raw.copy()
    df_raw['Datetime'] = pd.to_datetime(df_raw['Datetime'])
    df_raw = df_raw.sort_values('Datetime').reset_index(drop=True)

    features = FEATURES + list(extra_features) if extra_features else FEATURES
    is_ensemble = model_type in ENSEMBLE_MODEL_TYPES
    # As-of-уніфікація (виклик того самого build_forecast_feature_matrix, що
    # й прод) застосовна лише для конфігурації, яка реально відповідає
    # проду — інші режими не мають прод-еквіваленту, з яким їх зіставляти.
    asof_eligible = (model_type == 'lightgbm' and not use_surplus_classifier and not extra_features)

    if len(df) < 24 * (test_days + 30):
        test_days = max(7, len(df) // 24 - 30)

    last_date = df['Datetime'].max().normalize()
    first_test_day = last_date - pd.Timedelta(days=test_days - 1)

    model_builders = {'lightgbm': _make_lgbm, 'xgboost': _make_xgb}
    build_model = model_builders.get(model_type, _make_lgbm)

    daily_results = []
    model = None
    surplus_clf = None
    ensemble_members = None
    ensemble_weights = None
    day = first_test_day
    days_since_retrain = 0
    skipped_days_no_weather = 0

    while day <= last_date:
        day_end = day + pd.Timedelta(days=1)
        train_mask = df['Datetime'] < day
        test_mask = (df['Datetime'] >= day) & (df['Datetime'] < day_end)

        df_train = df[train_mask]
        df_test = df[test_mask]

        if len(df_train) < 24 * 30 or df_test.empty:
            day += pd.Timedelta(days=1)
            continue

        if is_ensemble:
            if ensemble_members is None or days_since_retrain >= retrain_every_days:
                ensemble_members = _train_ensemble_members(df_train, features)
                ensemble_weights = (
                    _compute_inverse_error_weights(df_train, features)
                    if model_type == 'ensemble_weighted'
                    else {'lightgbm': 1 / 3, 'xgboost': 1 / 3, 'mlp': 1 / 3}
                )
                days_since_retrain = 0

            member_preds = _predict_ensemble_members(ensemble_members, df_test, features)
            y_pred = sum(ensemble_weights[k] * member_preds[k] for k in member_preds)
            y_true = df_test['Price'].values
        elif asof_eligible:
            if model is None or days_since_retrain >= retrain_every_days:
                model = build_model()
                model.fit(df_train[features], df_train['Price'])
                days_since_retrain = 0

            if weather_mode == 'archived_forecast':
                weather_for_day = _weather_slice_archived_forecast(day, day_end)
            else:
                weather_for_day = _weather_slice_archived_actual(df_raw, day, day_end)
            if weather_for_day is None or len(weather_for_day) < 24:
                skipped_days_no_weather += 1
                day += pd.Timedelta(days=1)
                continue

            last_prices_for_day = df_raw[df_raw['Datetime'] < day]['Price'].iloc[-168:].tolist()
            if len(last_prices_for_day) < 168:
                day += pd.Timedelta(days=1)
                continue

            X_test, _, _ = build_forecast_feature_matrix(
                day.strftime('%Y-%m-%d'), weather_for_day, last_prices_for_day, as_of=day,
                idm_lag_hours=idm_lag_hours,
            )
            y_pred_raw = model.predict(X_test)
            y_pred_all = clip_and_shift(y_pred_raw, shift_pct=0.0)

            day_actual = df_raw[(df_raw['Datetime'] >= day) & (df_raw['Datetime'] < day_end)].copy()
            day_actual['hour'] = day_actual['Datetime'].dt.hour
            actual_by_hour = day_actual.set_index('hour')['Price']
            common_hours = [h for h in range(24) if h in actual_by_hour.index and pd.notna(actual_by_hour.loc[h])]
            if not common_hours:
                day += pd.Timedelta(days=1)
                continue
            y_true = actual_by_hour.loc[common_hours].values
            y_pred = np.array([y_pred_all[h] for h in common_hours])
        else:
            if model is None or days_since_retrain >= retrain_every_days:
                model = build_model()
                model.fit(df_train[features], df_train['Price'])
                if use_surplus_classifier:
                    y_surplus = (df_train['Price'] <= SURPLUS_PRICE_THRESHOLD).astype(int)
                    if y_surplus.nunique() > 1:
                        surplus_clf = _make_surplus_classifier()
                        surplus_clf.fit(df_train[features], y_surplus)
                    else:
                        surplus_clf = None
                days_since_retrain = 0

            y_pred = model.predict(df_test[features])
            if use_surplus_classifier and surplus_clf is not None:
                surplus_proba = surplus_clf.predict_proba(df_test[features])[:, 1]
                y_pred = _blend_with_surplus_proba(y_pred, surplus_proba)
            y_true = df_test['Price'].values

        mape, wape = calculate_mape_wape(y_true, y_pred)
        mae = float(mean_absolute_error(y_true, y_pred))

        daily_results.append({
            'date': day.strftime('%Y-%m-%d'),
            'mape': mape,
            'wape': wape,
            'mae': mae,
            'n_hours': int(len(y_true)),
        })

        days_since_retrain += 1
        day += pd.Timedelta(days=1)

    if not daily_results:
        return {'daily': [], 'summary': {}}

    mape_series = [d['mape'] for d in daily_results]
    wape_series = [d['wape'] for d in daily_results]
    summary = {
        'model_type': model_type,
        'test_days': len(daily_results),
        'retrain_every_days': retrain_every_days,
        'use_surplus_classifier': use_surplus_classifier,
        'mean_mape': float(np.mean(mape_series)),
        'median_mape': float(np.median(mape_series)),
        'p90_mape': float(np.percentile(mape_series, 90)),
        'mean_wape': float(np.mean(wape_series)),
        'last_7d_mean_mape': float(np.mean(mape_series[-7:])),
        'last_30d_mean_mape': float(np.mean(mape_series[-30:])) if len(mape_series) >= 30 else None,
        'methodology_version': 'phase_b_asof_unified_2026' if asof_eligible else 'phase_b_leakage_fix_only_2026',
        'weather_mode': weather_mode,
        'skipped_days_no_weather_archive': skipped_days_no_weather,
        'idm_lag_hours': idm_lag_hours,
    }

    report = {'daily': daily_results, 'summary': summary}
    _atomic_json_dump(report, BACKTEST_REPORT_PATH, indent=2)

    return report

# _get_generation_adjustment/_get_reference_capacities_mw/
# _get_baseload_passthrough_ratio/_get_price_shift_pct — див. feature_pipeline.py
# (Фаза B, 2026-08-21; _get_price_shift_pct лишається імпортованим вище для
# predict_next_day/predict_price_band, які застосовують зсув ПІСЛЯ моделі).

def build_forecast_feature_matrix(forecast_date, forecast_weather, last_prices, as_of=None, idm_lag_hours=24):
    """Сумісна тонка обгортка над feature_pipeline.build_asof_feature_matrix()
    (Фаза B) — та сама функція тепер обслуговує і живий прогноз
    (predict_next_day/predict_price_band, as_of=None → зараз, idm_lag_hours=24
    завжди), і walk_forward_backtest (as_of=симульований історичний момент,
    idm_lag_hours=48 лише для Фази C повторної перевірки Lag_48). Стара
    назва й сигнатура (без нових параметрів) лишені сумісними для двох
    існуючих продових викликів нижче."""
    return feature_pipeline.build_asof_feature_matrix(
        forecast_date, forecast_weather, last_prices, as_of=as_of, apply_manual_overrides=True,
        idm_lag_hours=idm_lag_hours,
    )

def predict_next_day(forecast_date, forecast_weather, last_prices, factors=None):
    """
    factors лишається опціональним параметром заради зворотної сумісності з
    існуючими викликами (scheduler.py, forecast endpoint) — АЛЕ більше не
    застосовує довільні хардкод-поправки (gas_adj/nuke_adj/solar_strike тощо),
    як було раніше: ці "фактори" були фейковими вхідними даними, яких
    диспетчер мав вручну вгадувати щодня. Реальний вплив ринкових умов тепер
    навчається моделлю напряму з реальних Solar_Gen/Wind_Gen/IDM-спреду.

    Ручний зсув прогнозу (PriceShiftOverride) застосовується тут, ПІСЛЯ
    передбачення моделі, а не як ознака — на випадок реальної тимчасової
    ринкової аномалії, яку модель не могла передбачити і не варто вчитися з
    одного епізоду (див. докстрінг PriceShiftOverride у models.py).
    """
    if not os.path.exists(LGBM_MODEL_PATH):
        train_models()

    with open(LGBM_MODEL_PATH, 'rb') as f:
        lgbm_model = pickle.load(f)
    with open(XGB_MODEL_PATH, 'rb') as f:
        xgb_model = pickle.load(f)
    with open(MLP_MODEL_PATH, 'rb') as f:
        mlp_model = pickle.load(f)
    with open(SCALER_PATH, 'rb') as f:
        scaler = pickle.load(f)

    X_forecast, records, adjustment = build_forecast_feature_matrix(forecast_date, forecast_weather, last_prices)

    pred_lgb = lgbm_model.predict(X_forecast)
    pred_xgb = xgb_model.predict(X_forecast)
    X_forecast_scaled = scaler.transform(X_forecast)
    pred_mlp = mlp_model.predict(X_forecast_scaled)

    shift_pct = _get_price_shift_pct(forecast_date)

    final_lgb = clip_and_shift(pred_lgb, shift_pct)
    final_xgb = clip_and_shift(pred_xgb, shift_pct)
    final_mlp = clip_and_shift(pred_mlp, shift_pct)

    return {
        'hours': list(range(24)),
        'lightgbm': final_lgb,
        'xgboost': final_xgb,
        'mlp': final_mlp,
        'features': records,
        'generation_adjustment': adjustment,
        'price_shift_pct': shift_pct,
    }

def predict_price_band(forecast_date, forecast_weather, last_prices):
    """
    P10/P90 conformal-калібрований інтервал невизначеності для тих самих 24
    годин прогнозу (побудований на тій же матриці ознак, що й точковий
    прогноз). Використовується MILP-оптимізатором для Pessimistic/Aggressive
    сценаріїв замість вигаданого ±1.64σ log-normal припущення про волатильність.

    Ручний зсув прогнозу (PriceShiftOverride), якщо збережений, зсуває обидві
    межі на той самий відсоток, що й точковий прогноз (predict_next_day) —
    щоб інтервал лишався узгодженим з точкою після ручної поправки.
    """
    if not os.path.exists(Q_LOWER_MODEL_PATH) or not os.path.exists(Q_UPPER_MODEL_PATH):
        train_quantile_models()

    with open(Q_LOWER_MODEL_PATH, 'rb') as f:
        lower_model = pickle.load(f)
    with open(Q_UPPER_MODEL_PATH, 'rb') as f:
        upper_model = pickle.load(f)

    correction = 0.0
    if os.path.exists(CONFORMAL_CALIBRATION_PATH):
        with open(CONFORMAL_CALIBRATION_PATH) as f:
            calibration = json.load(f)
        correction = calibration.get('conformal_correction_uah', 0.0)

    X_forecast, _, _ = build_forecast_feature_matrix(forecast_date, forecast_weather, last_prices)

    pred_lower = lower_model.predict(X_forecast) - correction
    pred_upper = upper_model.predict(X_forecast) + correction

    shift_pct = _get_price_shift_pct(forecast_date)

    lower_clipped = clip_and_shift(pred_lower, shift_pct)
    upper_clipped = clip_and_shift(pred_upper, shift_pct)
    # Після clip/conformal-поправки полоса теоретично може "перевернутись" —
    # підстраховуємось, щоб lower завжди <= upper.
    lower_final = [min(lo, up) for lo, up in zip(lower_clipped, upper_clipped)]
    upper_final = [max(lo, up) for lo, up in zip(lower_clipped, upper_clipped)]

    return {'hours': list(range(24)), 'lower_uah': lower_final, 'upper_uah': upper_final}

def estimate_idm_price_for_hour(actual_dam_price_uah, as_of_date=None):
    """
    Оцінка ціни ВДР на КОНКРЕТНУ годину, для якої вже відома РЕАЛЬНА
    ціна РДН (напр. заявка РДН щойно не зіграла, і диспетчеру пропонується альтернатива
    на ВДР — bidding_service.py). ВДР на цю саму годину ще НЕ відбувся (він
    ближче до реального часу постачання, ніж момент, коли ми дізнались
    результат РДН-аукціону) — тому реальної ціни ВДР для неї ще нема,
    лише оцінка через той самий метод, що вже підтверджений у
    build_forecast_feature_matrix для заповнення хвоста IDM_Price_Lag_24:
    реальна медіанна різниця (ВДР-РДН) за останній тиждень перекриття,
    додана до ВЖЕ ВІДОМОЇ реальної ціни РДН (а не до прогнозу — тут прогноз
    вже не потрібен, бо РДН факт відомий).
    """
    df_hist = pd.read_csv(dm.MERGED_DATA_PATH)
    df_hist['Datetime'] = pd.to_datetime(df_hist['Datetime'])
    df_hist = df_hist.sort_values('Datetime')
    if as_of_date is not None:
        df_hist = df_hist[df_hist['Datetime'] < pd.to_datetime(as_of_date)]

    recent = df_hist.dropna(subset=['Price', 'IDM_Price']).tail(168)
    if len(recent) < 24:
        return float(np.clip(actual_dam_price_uah, PRICE_FLOOR, PRICE_CAP))

    median_diff = float((recent['IDM_Price'] - recent['Price']).median())
    estimate = actual_dam_price_uah + median_diff
    return float(np.clip(estimate, PRICE_FLOOR, PRICE_CAP))
