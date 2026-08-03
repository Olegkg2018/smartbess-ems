import os
import json
import pickle
import pandas as pd
import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor
from sklearn.neural_network import MLPRegressor
from lightgbm import LGBMRegressor, LGBMClassifier

from src.core.config import settings
import src.modules.market_data_service.data_manager as dm
from src.modules.tariff_service.services import TariffService
from src.database.session import SessionLocal
from src.database.models import GenerationAdjustment, PriceShiftOverride

DATA_DIR = settings.DATA_DIR
DEFAULT_NUCLEAR_REFERENCE_CAPACITY_MW = 7835.0
DEFAULT_HYDRO_REFERENCE_CAPACITY_MW = 3800.0
# Не весь дефіцит АЕС/ГЕС конвертується 1:1 у транскордонний нетто-експорт —
# більшість поглинається всередині країни (теплова/резервна генерація, ГПВ),
# тож пряма МВт-дельта від номінальної потужності системно переоцінює
# вплив на Grid_Net_Export. baseload_passthrough_ratio — явний редагований
# коефіцієнт (як BidMarginOverride.margin_pct), а не вигадані дані: масштабує
# вже реальну навчену залежність, не додає нову.
DEFAULT_BASELOAD_PASSTHROUGH_RATIO = 0.3
# Дельта додатково жорстко обмежується реальним 99-перцентилем
# Grid_Net_Export_MW за останні BASELOAD_DELTA_CLIP_LOOKBACK_DAYS днів — без
# цього довідникові потужності (7835/3800 МВт) на порядок перевищують
# історичний розкид фічі (std≈392 МВт), LightGBM не екстраполює за межі
# навчених порогів і прогноз "насичується" вже при 20-30% відхилення
# (знайдено 2026-08-03 при розслідуванні скарги диспетчера — поправка на
# генерацію не давала жодного видимого ефекту на прогноз).
BASELOAD_DELTA_CLIP_QUANTILE = 0.99
BASELOAD_DELTA_CLIP_LOOKBACK_DAYS = 180
PRICE_FLOOR = TariffService.PRICE_FLOOR_UAH_MWH
PRICE_CAP = 16000.0
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

# Тільки реальні джерела (oree.com.ua, Open-Meteo) + фізично обґрунтовані
# оцінки Solar_Gen/Wind_Gen з реальної погоди. Раніше тут були Gas_Price,
# Nuclear_Outage, Solar_Strike, Market_Coeff, VDR_Volume, Grid_Import_Export —
# усі фейкові (np.random). Прибрані повністю, а не замінені вигадкою.
#
# Gas_Price_EUR_MWh / Grid_Stress_High / Grid_Stress_Medium вже збираються
# реально (src/modules/external_data_service/), але поки що мають занадто
# коротку історію (перші дні/тижні роботи), щоб модель могла на них чомусь
# навчитись — намеренно НЕ включені в FEATURES. Додати їх сюди, коли
# накопичиться достатньо днів (перевіряти через dm.verify_data_completeness()
# та частку не-NaN значень у historical_data_merged.csv).
FEATURES = [
    'Hour', 'Month', 'DayOfWeek', 'Is_Weekend', 'Is_Holiday', 'Is_Weekend_Or_Holiday',
    'Temperature', 'Cloud_Cover', 'Wind_Speed', 'Shortwave_Radiation',
    'Solar_Gen', 'Wind_Gen',
    'Hour_Sin', 'Hour_Cos', 'Month_Sin', 'Month_Cos',
    'DayOfYear_Sin', 'DayOfYear_Cos',
    'Is_Night', 'Is_Morning_Peak', 'Is_Daytime', 'Is_Evening_Peak',
    'Price_Lag_24', 'Price_Lag_48', 'Price_Lag_168', 'Price_Mean_24h',
    'Temp_Lag_3', 'Temp_Lag_6',
    'Cloud_Lag_3', 'Cloud_Lag_6',
    'Radiation_Lag_3', 'Radiation_Lag_6',
    # Спред ВДР/РДН лагований на 24г — ВДР торгується ПІСЛЯ публікації РДН,
    # тож спред за той самий час, що прогнозується, використовувати не можна
    # (витік даних з майбутнього). Лаг на 24г — це вже відомий на момент
    # прогнозу реальний ринковий сигнал про волатильність/розбіжність ринків.
    #
    # ДІАГНОСТИКА (реальна, підтверджена на живих даних 21-22.07.2026): ВДР на
    # oree.com.ua публікується із затримкою ~1 доба, тож у live-інференсі
    # (build_forecast_feature_matrix) свіжі 24г IDM_Price майже завжди NaN і
    # limit_direction='both' на хвості БЕЗ якоря вперед вироджується у пласку
    # константу — IDM_Price_Lag_24 (найвпливовіша ознака моделі, ~55% gain)
    # системно приходить на inference "зіпсованим" (пласким), хоча під час
    # НАВЧАННЯ (prepare_features рахує на всій історії одразу, якір є з обох
    # боків) той самий стовпець зазвичай МАВ реальну форму — справжній
    # train/serve skew.
    #
    # СПРОБА ВИПРАВЛЕННЯ (Lag_48 замість Lag_24, щоб надійно потрапляти в
    # останню добу з реальними даними) — ПЕРЕВІРЕНА walk_forward_backtest
    # (test_days=60, retrain_every_days=7, ті самі дані 2021-2026,
    # baseline vs Lag_48 на ідентичному знімку): mean_wape практично не
    # змінився (25.24%→25.16%, шум), АЛЕ last_7d_mean_mape (237.7→250.1) і
    # last_30d_mean_mape (399.0→456.2) — САМЕ ті метрики, які мали
    # покращитись — стали ГІРШЕ. Причина: walk_forward_backtest симулює
    # кожен тестовий день через prepare_features на всій історії одразу
    # (якір є завжди), тобто НЕ відтворює живий "хвіст без якоря" — Lag_24 у
    # бектесті майже завжди "здоровий" і лишається кращим сигналом, ніж
    # застарілий на добу Lag_48. Тобто діагноз реальний, а глобальна заміна
    # лагу — НЕ те виправлення (жертвуємо загалом кращим сигналом заради
    # рідкісного edge-case на inference). Lag_24 ЗАЛИШЕНО. Правильний
    # наступний крок — не міняти лаг, а розумніше заповнювати саме
    # inference-хвост (напр. формою РДН-ціни через нещодавнє реальне
    # співвідношення ВДР/РДН, а не пласкою константою) — НЕ зроблено.
    'IDM_Price_Lag_24', 'DAM_IDM_Spread_Lag_24', 'Spread_Mean_24h',
    # Реальний транскордонний нетто-експорт (ENTSO-E, звітується сусідами
    # PL/RO/SK/HU/MD — не залежить від воєнних обмежень публікації України).
    # Лаговано на 24г з тієї ж причини, що й ВДР-спред: ENTSO-E публікує з
    # затримкою, "сьогоднішнє" значення на момент прогнозу ще не відоме.
    'Grid_Net_Export_Lag_24', 'Grid_Net_Export_Mean_24h',
]

def is_ukrainian_holiday(dt):
    fixed_holidays = [
        (1, 1), (1, 7), (3, 8), (5, 1), (5, 9), (6, 28), (8, 24), (10, 14), (12, 25)
    ]
    if (dt.month, dt.day) in fixed_holidays:
        return 1
    return 0

def prepare_features(df):
    df = df.copy()
    df['Datetime'] = pd.to_datetime(df['Datetime'])
    df = df.sort_values('Datetime').set_index('Datetime')

    full_range = pd.date_range(start=df.index.min(), end=df.index.max(), freq='h')
    df = df.reindex(full_range)
    df.index.name = 'Datetime'

    df['Price'] = df['Price'].interpolate(method='linear').bfill().ffill()
    df['Temperature'] = df['Temperature'].interpolate(method='linear').bfill().ffill()
    df['Cloud_Cover'] = df['Cloud_Cover'].interpolate(method='linear').fillna(50.0)
    df['Wind_Speed'] = df['Wind_Speed'].interpolate(method='linear').fillna(15.0)
    df['Shortwave_Radiation'] = df['Shortwave_Radiation'].interpolate(method='linear').fillna(0.0)

    df['Solar_Gen'] = df['Solar_Gen'].interpolate(method='linear').fillna(0.0)
    df['Wind_Gen'] = df['Wind_Gen'].interpolate(method='linear').fillna(0.0)

    # IDM/спред — реальні дані з невеликими прогалинами (вихідні/збої джерела);
    # інтерполюємо лінійно, як і ціну РДН, а не підставляємо вигадані числа.
    if 'IDM_Price' not in df.columns:
        df['IDM_Price'] = np.nan
    if 'DAM_IDM_Spread' not in df.columns:
        df['DAM_IDM_Spread'] = np.nan
    df['IDM_Price'] = df['IDM_Price'].interpolate(method='linear').bfill().ffill()
    df['DAM_IDM_Spread'] = df['DAM_IDM_Spread'].interpolate(method='linear').fillna(0.0)

    # Реальний ENTSO-E нетто-експорт — доступний з 2021, крім останніх кількох
    # годин (публікаційна затримка). Лінійна інтерполяція заповнює цю затримку
    # й вихідні прогалини, не вигадуючи режим (на відміну від старого
    # add_generation_and_market_factors).
    if 'Grid_Net_Export_MW' not in df.columns:
        df['Grid_Net_Export_MW'] = np.nan
    df['Grid_Net_Export_MW'] = df['Grid_Net_Export_MW'].interpolate(method='linear').bfill().ffill()

    # Експериментальна ознака (EU_DAM_Price_Lag_24, EU_DAM_Price_Mean_24h нижче)
    # — реальна ENTSO-E день-наперед ціна сусідніх зон (PL/RO/SK/HU), поки НЕ
    # в продових FEATURES, перевіряється walk_forward_backtest(extra_features=...).
    #
    # РЕЗУЛЬТАТ ПЕРЕВІРКИ (walk_forward_backtest, test_days=60,
    # retrain_every_days=7, дані 2021-2026): агрегований WAPE практично не
    # змінився (22.522% без ознаки → 22.486% з нею — шум, як і з ціновими
    # стелями). Але last_7d_mean_mape (466.6→487.8) і last_30d_mean_mape
    # (352.9→380.0) — тобто саме ті останні/волатильні дні, які й треба було
    # покращити — стали ГІРШЕ, не краще. Гіпотеза (єдиний європейський ринок
    # через перетоки має тягнути ціну) не підтвердилась на реальних даних у
    # цій формі (простий mean по зонах, лаг 24г). Ознака НЕ додана в FEATURES.
    # Дані лишаються зібраними в historical_data_merged.csv (реальні, шкоди
    # немає) — можна спробувати інше кодування (напр. зважене по напрямку
    # потоку, різниця EU-UA замість рівня) з новим бектестом, а не повторювати
    # цей самий варіант.
    if 'EU_DAM_Price_EUR_MWh' not in df.columns:
        df['EU_DAM_Price_EUR_MWh'] = np.nan
    df['EU_DAM_Price_EUR_MWh'] = df['EU_DAM_Price_EUR_MWh'].interpolate(method='linear').bfill().ffill()

    # Експериментальна ознака: реальна погода сусідніх зон PL/RO (Open-Meteo,
    # neighbor_weather.py), гіпотеза — leading-сигнал власної відновлюваної
    # генерації сусіда, точніший за EU_DAM_Price вище. Поки НЕ в продових
    # FEATURES.
    #
    # РЕЗУЛЬТАТ ПЕРЕВІРКИ (walk_forward_backtest, test_days=60,
    # retrain_every_days=7, дані 2021-2026): baseline mean_wape=23.54%,
    # last_7d_mean_mape=579.96, last_30d_mean_mape=341.65. З PL+RO погодою
    # (Temperature/Cloud_Cover/Wind_Speed/Shortwave_Radiation обох зон):
    # mean_wape=23.80% (гірше), last_7d_mean_mape=701.0 (+21%, гірше),
    # last_30d_mean_mape=320.0 (краще). Перевірено й окремо: PL-only,
    # RO-only, тільки Shortwave_Radiation обох зон — у ВСІХ варіантах
    # last_7d_mean_mape гірше за baseline (615-704 проти 579.96), той самий
    # патерн, що й з EU_DAM_Price (див. вище) і recency weighting (див.
    # walk_forward_backtest докстрінг): саме останні/волатильні дні, які
    # треба покращити, стають гіршими. Ознака НЕ додана в FEATURES. Дані
    # лишаються зібраними (реальні, шкоди немає) — можливо, варте спробувати
    # з більшим лагом (сьогоднішня погода сусіда ще не встигає вплинути на
    # український перетік) або комбінацію з EU_DAM_Price, а не повторювати
    # цей самий варіант.
    for zone in ('PL', 'RO'):
        for col in ('Temperature', 'Cloud_Cover', 'Wind_Speed', 'Shortwave_Radiation'):
            full_col = f'{zone}_{col}'
            if full_col not in df.columns:
                df[full_col] = np.nan
            df[full_col] = df[full_col].interpolate(method='linear').bfill().ffill()

    df = df.reset_index()

    df['Hour'] = df['Datetime'].dt.hour
    df['Month'] = df['Datetime'].dt.month
    df['DayOfWeek'] = df['Datetime'].dt.dayofweek
    df['Is_Weekend'] = df['DayOfWeek'].isin([5, 6]).astype(int)
    df['Is_Holiday'] = df['Datetime'].apply(is_ukrainian_holiday)
    df['Is_Weekend_Or_Holiday'] = ((df['Is_Weekend'] == 1) | (df['Is_Holiday'] == 1)).astype(int)

    df['Hour_Sin'] = np.sin(2 * np.pi * df['Hour'] / 24.0)
    df['Hour_Cos'] = np.cos(2 * np.pi * df['Hour'] / 24.0)
    df['Month_Sin'] = np.sin(2 * np.pi * df['Month'] / 12.0)
    df['Month_Cos'] = np.cos(2 * np.pi * df['Month'] / 12.0)

    df['DayOfYear'] = df['Datetime'].dt.dayofyear
    df['DayOfYear_Sin'] = np.sin(2 * np.pi * df['DayOfYear'] / 365.25)
    df['DayOfYear_Cos'] = np.cos(2 * np.pi * df['DayOfYear'] / 365.25)

    df['Is_Night'] = df['Hour'].isin([0, 1, 2, 3, 4, 5, 6, 23]).astype(int)
    df['Is_Morning_Peak'] = df['Hour'].isin([7, 8, 9, 10]).astype(int)
    df['Is_Daytime'] = df['Hour'].isin([11, 12, 13, 14, 15, 16]).astype(int)
    df['Is_Evening_Peak'] = df['Hour'].isin([17, 18, 19, 20, 21, 22]).astype(int)

    df['Price_Lag_24'] = df['Price'].shift(24)
    df['Price_Lag_48'] = df['Price'].shift(48)
    df['Price_Lag_168'] = df['Price'].shift(168)
    df['Price_Mean_24h'] = df['Price'].shift(24).rolling(window=24).mean()

    for lag in [3, 6]:
        df[f'Temp_Lag_{lag}'] = df['Temperature'].shift(lag)
        df[f'Cloud_Lag_{lag}'] = df['Cloud_Cover'].shift(lag)
        df[f'Radiation_Lag_{lag}'] = df['Shortwave_Radiation'].shift(lag)

    df['IDM_Price_Lag_24'] = df['IDM_Price'].shift(24)
    df['DAM_IDM_Spread_Lag_24'] = df['DAM_IDM_Spread'].shift(24)
    df['Spread_Mean_24h'] = df['DAM_IDM_Spread'].shift(24).rolling(window=24).mean()

    df['Grid_Net_Export_Lag_24'] = df['Grid_Net_Export_MW'].shift(24)
    df['Grid_Net_Export_Mean_24h'] = df['Grid_Net_Export_MW'].shift(24).rolling(window=24).mean()

    df['EU_DAM_Price_Lag_24'] = df['EU_DAM_Price_EUR_MWh'].shift(24)
    df['EU_DAM_Price_Mean_24h'] = df['EU_DAM_Price_EUR_MWh'].shift(24).rolling(window=24).mean()

    df = df.dropna(subset=FEATURES + ['Price']).reset_index(drop=True)
    return df

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
        'mlp': {'mae': float(mae_mlp), 'rmse': float(rmse_mlp), 'r2': float(r2_mlp), 'mape': mape_mlp, 'wape': wape_mlp}
    }

    metrics_path = os.path.join(DATA_DIR, "metrics_report.json")
    with open(metrics_path, 'w') as f:
        json.dump(metrics, f, indent=4)

    lgb_final = _make_lgbm()
    lgb_final.fit(X, y)

    xgb_final = _make_xgb()
    xgb_final.fit(X, y)

    scaler_final = StandardScaler()
    X_scaled = scaler_final.fit_transform(X)

    mlp_final = _make_mlp()
    mlp_final.fit(X_scaled, y)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LGBM_MODEL_PATH, 'wb') as f:
        pickle.dump(lgb_final, f)
    with open(XGB_MODEL_PATH, 'wb') as f:
        pickle.dump(xgb_final, f)
    with open(MLP_MODEL_PATH, 'wb') as f:
        pickle.dump(mlp_final, f)
    with open(SCALER_PATH, 'wb') as f:
        pickle.dump(scaler_final, f)

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

    # Фінальні продові моделі — перенавчені на всіх даних (як і в train_models),
    # conformal-поправка лишається зафіксованою з чесного held-out калібрування.
    X_all, y_all = df[FEATURES], df['Price']
    lower_final = _make_quantile_lgbm(QUANTILE_LOWER)
    lower_final.fit(X_all, y_all)
    upper_final = _make_quantile_lgbm(QUANTILE_UPPER)
    upper_final.fit(X_all, y_all)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(Q_LOWER_MODEL_PATH, 'wb') as f:
        pickle.dump(lower_final, f)
    with open(Q_UPPER_MODEL_PATH, 'wb') as f:
        pickle.dump(upper_final, f)

    calibration = {
        'quantile_lower': QUANTILE_LOWER,
        'quantile_upper': QUANTILE_UPPER,
        'target_coverage': target_coverage,
        'conformal_correction_uah': correction,
        'calibration_hours': int(len(df_calib)),
        'coverage_raw_quantile_regression': coverage_raw,
        'coverage_after_conformal_correction': coverage_conformal,
    }
    with open(CONFORMAL_CALIBRATION_PATH, 'w') as f:
        json.dump(calibration, f, indent=2)

    return calibration

QUANTILE_COVERAGE_REPORT_PATH = os.path.join(DATA_DIR, "quantile_coverage_report.json")

def quantile_coverage_backtest(test_days=60, retrain_every_days=14, calib_days=14):
    """
    Чесна walk-forward перевірка калібрування P10/P90 інтервалу на реальних
    історичних даних — не одне статичне вікно, а день у день по всьому
    test_days: train -> невеликий conformal calib -> тест, вікно рухається
    вперед, моделі й conformal-поправка перераховуються раз на
    retrain_every_days (як і в проді). Перевіряємо ПОКРИТТЯ (чи потрапляє
    факт у [P10,P90]), а не точність точки.
    """
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
    }

    report = {'daily': daily_results, 'summary': summary}
    with open(QUANTILE_COVERAGE_REPORT_PATH, 'w') as f:
        json.dump(report, f, indent=2)

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

def walk_forward_backtest(test_days=90, retrain_every_days=7, model_type='lightgbm', use_surplus_classifier=False, extra_features=None):
    """
    Чесна оцінка точності день-наперед прогнозу: розширюване вікно навчання,
    прогноз на наступну добу (24г), крок вперед. Модель перенавчається раз на
    retrain_every_days (як і в проді — раз на тиждень), а не одноразовий
    85/15 holdout, який не показує, як точність змінюється у часі.

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

    РЕЗУЛЬТАТ ПЕРЕВІРКИ (walk_forward_backtest, test_days=60,
    retrain_every_days=7, дані 2021-2026-07, scratch/backtest_ensemble.py):
    baseline (соло LightGBM) mean_wape=26.224%, last_7d_mean_mape=1009.72,
    last_30d_mean_mape=566.38. ensemble_average: mean_wape=25.935% (краще),
    last_7d_mean_mape=985.59 (краще), last_30d_mean_mape=591.61 (ГІРШЕ).
    ensemble_weighted: mean_wape=25.765% (краще), last_7d_mean_mape=1043.09
    (ГІРШЕ), last_30d_mean_mape=533.83 (краще). Обидва варіанти покращують
    mean_wape, але РІЗНОНАПРАВЛЕНО псують одну з двох recency-метрик (та й у
    протилежні боки одна відносно одної) — той самий патерн "загальне
    покращення / останні дні гірше", що й з EU_DAM_Price і погодою PL/RO
    (prepare_features вище), тільки тут ще й сам напрямок псування нестійкий
    між двома схемами зважування. На 60 тестових днях last_7d — це лише 7
    точок, замало для довіри. Ансамбль НЕ підключено до проду (scheduler.py
    лишається на соло LightGBM). Код лишається доступним
    (ENSEMBLE_MODEL_TYPES/_train_ensemble_members/_compute_inverse_error_weights)
    для повторної перевірки — напр. на test_days=90+ (менше шуму в last_7d)
    або з іншою схемою зважування, а не для повторення цього самого прогону.

    extra_features — список додаткових колонок (напр. EU_DAM_Price_Lag_24),
    які додаються поверх продових FEATURES ЛИШЕ для цього прогону A/B-тесту,
    без зміни глобального FEATURES.

    Повертає щоденний MAPE/WAPE + підсумкову статистику, зберігає у
    data/backtest_report.json.
    """
    df_raw = dm.get_combined_historical_data()
    df = prepare_features(df_raw)
    df = df.sort_values('Datetime').reset_index(drop=True)

    features = FEATURES + list(extra_features) if extra_features else FEATURES
    is_ensemble = model_type in ENSEMBLE_MODEL_TYPES

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

    while day <= last_date:
        train_mask = df['Datetime'] < day
        test_mask = (df['Datetime'] >= day) & (df['Datetime'] < day + pd.Timedelta(days=1))

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
            'n_hours': int(len(df_test)),
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
    }

    report = {'daily': daily_results, 'summary': summary}
    with open(BACKTEST_REPORT_PATH, 'w') as f:
        json.dump(report, f, indent=2)

    return report

def _get_generation_adjustment(forecast_date):
    """Ручна корекція диспетчера на цю дату (GenerationAdjustment), або
    нейтральні 100%/без нотатки, якщо нічого не збережено."""
    db = SessionLocal()
    try:
        target_dt = pd.to_datetime(forecast_date).to_pydatetime()
        row = db.query(GenerationAdjustment).filter(GenerationAdjustment.date == target_dt).first()
        if not row:
            return {'nuclear_pct': 100.0, 'hydro_pct': 100.0, 'solar_pct': 100.0, 'wind_pct': 100.0, 'note': None}
        return {
            'nuclear_pct': row.nuclear_pct, 'hydro_pct': row.hydro_pct,
            'solar_pct': row.solar_pct, 'wind_pct': row.wind_pct, 'note': row.note,
        }
    except Exception:
        return {'nuclear_pct': 100.0, 'hydro_pct': 100.0, 'solar_pct': 100.0, 'wind_pct': 100.0, 'note': None}
    finally:
        db.close()

def _get_reference_capacities_mw():
    """Довідкові потужності АЕС/ГЕС (наближені, редаговані в Settings) для
    переведення % у МВт-дельту — див. коментар при DEFAULT_* констант вище."""
    path = os.path.join(DATA_DIR, "system_settings.json")
    nuclear = DEFAULT_NUCLEAR_REFERENCE_CAPACITY_MW
    hydro = DEFAULT_HYDRO_REFERENCE_CAPACITY_MW
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                saved = json.load(f)
                nuclear = float(saved.get("nuclear_reference_capacity_mw", nuclear))
                hydro = float(saved.get("hydro_reference_capacity_mw", hydro))
        except Exception:
            pass
    return nuclear, hydro

def _get_baseload_passthrough_ratio():
    """Частка дефіциту АЕС/ГЕС, що реально проявляється в Grid_Net_Export
    (редагована в Settings) — див. коментар при DEFAULT_BASELOAD_PASSTHROUGH_RATIO."""
    path = os.path.join(DATA_DIR, "system_settings.json")
    ratio = DEFAULT_BASELOAD_PASSTHROUGH_RATIO
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                saved = json.load(f)
                ratio = float(saved.get("baseload_passthrough_ratio", ratio))
        except Exception:
            pass
    return max(0.0, min(1.0, ratio))

def _get_price_shift_pct(forecast_date):
    """Ручний відсотковий зсув прогнозу (PriceShiftOverride) на цю дату, або
    0.0 (нейтрально), якщо нічого не збережено."""
    db = SessionLocal()
    try:
        target_dt = pd.to_datetime(forecast_date).to_pydatetime()
        row = db.query(PriceShiftOverride).filter(PriceShiftOverride.date == target_dt).first()
        return row.shift_pct if row else 0.0
    except Exception:
        return 0.0
    finally:
        db.close()

def build_forecast_feature_matrix(forecast_date, forecast_weather, last_prices):
    """
    Будує матрицю ознак (FEATURES) для прогнозу на 24 години наперед. Спільна
    для точкового прогнозу (predict_next_day) і квантильного інтервалу
    невизначеності (predict_price_band) — та сама логіка лагів/фічей, щоб
    обидва прогнози завжди узгоджувались між собою.

    Якщо на forecast_date збережена ручна корекція генерації
    (GenerationAdjustment — диспетчер відзначив ремонт/пошкодження/погану
    погоду на АЕС/ГЕС/СЕС/ВЕС), вона застосовується тут:
    - solar_pct/wind_pct масштабують Solar_Gen/Wind_Gen напряму (реальні
      навчені ознаки — чесний вплив на прогноз через саму модель).
    - nuclear_pct/hydro_pct не мають навченої ознаки (даних по типах немає з
      2022 року) — переводяться в МВт-дельту через довідникові потужності,
      масштабуються baseload_passthrough_ratio (не весь дефіцит проявляється
      в транскордонному потоці — частина поглинається всередині країни) і
      жорстко обмежуються реальним 99-перцентилем Grid_Net_Export_MW за
      останні BASELOAD_DELTA_CLIP_LOOKBACK_DAYS днів, перш ніж додатись до
      Grid_Net_Export_Lag_24/Mean_24h (реальна навчена ознака балансу
      генерація/споживання) — це приблизна, але НЕ вигадана оцінка: дельта
      проходить крізь вже навчену моделлю залежність ціни від нетто-експорту,
      а не через довільний множник, і не виштовхує ознаку за межі діапазону,
      на якому модель взагалі навчалась (без цього — насичення, знайдено
      2026-08-03: корекція генерації не давала видимого ефекту на прогноз).
    """
    adjustment = _get_generation_adjustment(forecast_date)
    nuclear_ref_mw, hydro_ref_mw = _get_reference_capacities_mw()
    passthrough_ratio = _get_baseload_passthrough_ratio()
    baseload_delta_raw = (
        nuclear_ref_mw * (adjustment['nuclear_pct'] / 100.0 - 1.0)
        + hydro_ref_mw * (adjustment['hydro_pct'] / 100.0 - 1.0)
    ) * passthrough_ratio

    df_hist = pd.read_csv(dm.MERGED_DATA_PATH)
    df_hist['Datetime'] = pd.to_datetime(df_hist['Datetime'])
    df_hist = df_hist.sort_values('Datetime')

    forecast_dt_start = pd.to_datetime(forecast_date)

    lookback_start = forecast_dt_start - pd.Timedelta(days=BASELOAD_DELTA_CLIP_LOOKBACK_DAYS)
    recent_export = df_hist.loc[df_hist['Datetime'] >= lookback_start, 'Grid_Net_Export_MW'].dropna()
    export_source = recent_export if len(recent_export) >= 200 else df_hist['Grid_Net_Export_MW'].dropna()
    clip_bound = (
        float(export_source.abs().quantile(BASELOAD_DELTA_CLIP_QUANTILE))
        if len(export_source) > 30 else 1800.0
    )
    baseload_delta_mw = float(np.clip(baseload_delta_raw, -clip_bound, clip_bound))
    hist_before_target = df_hist[df_hist['Datetime'] < forecast_dt_start].sort_values('Datetime')

    def _last_n(col, n, fallback):
        if col in hist_before_target.columns and len(hist_before_target) >= n:
            return hist_before_target[col].iloc[-n:].tolist()
        return [fallback] * n

    last_temps = _last_n('Temperature', 24, 15.0)
    last_clouds = _last_n('Cloud_Cover', 24, 40.0)
    last_rads = _last_n('Shortwave_Radiation', 24, 0.0)

    if len(last_prices) < 168:
        mean_p = np.mean(last_prices) if len(last_prices) > 0 else 4000.0
        last_prices = [mean_p] * (168 - len(last_prices)) + list(last_prices)

    # ВДР (IDM) публікується із затримкою ~1 доба — свіжий хвост (типово
    # останні ~24г) на момент прогнозу завжди NaN. РАНІШЕ тут просто
    # "протягувався" останній відомий IDM_Price пласкою константою
    # (interpolate(limit_direction='both') на хвості без якоря вперед) — це
    # знищувало денну форму IDM_Price_Lag_24, найвпливовішої ознаки моделі
    # (~55% gain), саме в момент прогнозу (train/serve skew, підтверджено на
    # 21-22.07.2026: прогноз на 22.07 майже не показав денний провал ціни,
    # хоча реальний РДН 21.07 провалився вдесятеро). Проста заміна лагу на
    # Lag_48 НЕ допомогла (ПЕРЕВІРЕНО backtest — last_7d/30d MAPE стали
    # ГІРШЕ, див. коментар над FEATURES) — Lag_24 лишено як є.
    #
    # Замість цього відновлюємо ФОРМУ пропущеного хвоста через уже відому
    # реальну форму РДН-ціни (last_prices) + медіанну РЕАЛЬНУ різницю
    # ВДР-РДН за останній тиждень перекриття. Адитивна різниця, а не
    # співвідношення — на низьких цінах (~10-100 ₴, сонячний профіцит)
    # IDM/DAM "вибухає" до 0.45-4.6x (перевірено на реальних даних
    # 18-20.07.2026), тоді як різниця лишається обмеженою й стабільною.
    last_prices_168 = last_prices[-168:]
    last_idm_raw = _last_n('IDM_Price', 168, np.nan)
    overlap_diffs = [i - p for p, i in zip(last_prices_168, last_idm_raw) if pd.notna(i)]

    if len(overlap_diffs) >= 24:
        median_diff = float(np.median(overlap_diffs))
        last_idm = [float(i) if pd.notna(i) else p + median_diff for p, i in zip(last_prices_168, last_idm_raw)]
    else:
        # Замало реального перекриття (холодний старт/довга прогалина
        # джерела) — той самий плаский фолбек, що й раніше: чесніше за
        # медіану з майже нуля реальних точок.
        last_idm = pd.Series(last_idm_raw).interpolate(limit_direction='both').fillna(np.mean(last_prices)).tolist()

    # DAM_IDM_Spread визначається як IDM_Price - Price (intraday_market.py) —
    # рахуємо з тих самих last_idm/last_prices, щоб ознаки лишались
    # внутрішньо узгодженими (а не два незалежно заповнені ряди, які можуть
    # розійтись).
    last_spreads = [i - p for p, i in zip(last_prices_168, last_idm)]
    # ENTSO-E теж публікується із затримкою (за 5 кордонами PL/RO/SK/HU/MD) —
    # той самий інтерполяційний підхід, що і для IDM вище.
    last_flows = pd.Series(_last_n('Grid_Net_Export_MW', 168, np.nan)).interpolate(limit_direction='both').fillna(0.0).tolist()

    records = []
    for h in range(24):
        dt = pd.to_datetime(forecast_date) + pd.to_timedelta(h, unit='h')
        weather_row = forecast_weather.iloc[h] if h < len(forecast_weather) else forecast_weather.iloc[-1]

        lag_24 = last_prices[-24 + h]
        lag_48 = last_prices[-48 + h]
        lag_168 = last_prices[-168 + h]
        mean_24h = np.mean(last_prices[121 + h: 145 + h])

        idm_lag_24 = last_idm[-24 + h]
        spread_lag_24 = last_spreads[-24 + h]
        spread_mean_24h = np.mean(last_spreads[121 + h: 145 + h])

        flow_lag_24 = last_flows[-24 + h]
        flow_mean_24h = np.mean(last_flows[121 + h: 145 + h])

        rad = float(weather_row.get('Shortwave_Radiation', 0.0))
        clouds = float(weather_row.get('Cloud_Cover', 40.0))
        temp = float(weather_row.get('Temperature', 15.0))
        ws = float(weather_row.get('Wind_Speed', 12.0))

        temp_lag_3 = float(forecast_weather.iloc[h - 3]['Temperature'] if h >= 3 else last_temps[-3 + h])
        temp_lag_6 = float(forecast_weather.iloc[h - 6]['Temperature'] if h >= 6 else last_temps[-6 + h])

        cloud_lag_3 = float(forecast_weather.iloc[h - 3]['Cloud_Cover'] if h >= 3 else last_clouds[-3 + h])
        cloud_lag_6 = float(forecast_weather.iloc[h - 6]['Cloud_Cover'] if h >= 6 else last_clouds[-6 + h])

        rad_lag_3 = float(forecast_weather.iloc[h - 3]['Shortwave_Radiation'] if h >= 3 else last_rads[-3 + h])
        rad_lag_6 = float(forecast_weather.iloc[h - 6]['Shortwave_Radiation'] if h >= 6 else last_rads[-6 + h])

        solar_gen = np.clip(6500.0 * (rad / 1000.0) * (1.0 - 0.003 * (temp - 25.0)), 0.0, 5500.0)
        if ws < 8.0 or ws > 80.0:
            wind_gen = 0.0
        elif ws > 45.0:
            wind_gen = 1800.0
        else:
            wind_gen = 1800.0 * ((ws - 8.0) / (45.0 - 8.0)) ** 3

        # Ручна корекція диспетчера (див. докстрінг функції вище)
        solar_gen = solar_gen * (adjustment['solar_pct'] / 100.0)
        wind_gen = wind_gen * (adjustment['wind_pct'] / 100.0)
        flow_lag_24 = flow_lag_24 + baseload_delta_mw
        flow_mean_24h = flow_mean_24h + baseload_delta_mw

        hour_sin = np.sin(2 * np.pi * h / 24.0)
        hour_cos = np.cos(2 * np.pi * h / 24.0)
        month_sin = np.sin(2 * np.pi * dt.month / 12.0)
        month_cos = np.cos(2 * np.pi * dt.month / 12.0)

        day_of_year = dt.dayofyear
        day_of_year_sin = np.sin(2 * np.pi * day_of_year / 365.25)
        day_of_year_cos = np.cos(2 * np.pi * day_of_year / 365.25)

        is_night = int(h in [0, 1, 2, 3, 4, 5, 6, 23])
        is_morning_peak = int(h in [7, 8, 9, 10])
        is_daytime = int(h in [11, 12, 13, 14, 15, 16])
        is_evening_peak = int(h in [17, 18, 19, 20, 21, 22])

        is_we = int(dt.dayofweek in [5, 6])
        is_hol = is_ukrainian_holiday(dt)
        is_we_or_hol = int(is_we == 1 or is_hol == 1)

        records.append({
            'Hour': h, 'Month': dt.month, 'DayOfWeek': dt.dayofweek, 'Is_Weekend': is_we, 'Is_Holiday': is_hol,
            'Is_Weekend_Or_Holiday': is_we_or_hol, 'Temperature': temp, 'Cloud_Cover': clouds, 'Wind_Speed': ws,
            'Shortwave_Radiation': rad, 'Solar_Gen': float(solar_gen), 'Wind_Gen': float(wind_gen),
            'Hour_Sin': hour_sin, 'Hour_Cos': hour_cos, 'Month_Sin': month_sin, 'Month_Cos': month_cos,
            'DayOfYear_Sin': day_of_year_sin, 'DayOfYear_Cos': day_of_year_cos, 'Is_Night': is_night,
            'Is_Morning_Peak': is_morning_peak, 'Is_Daytime': is_daytime, 'Is_Evening_Peak': is_evening_peak,
            'Price_Lag_24': float(lag_24), 'Price_Lag_48': float(lag_48), 'Price_Lag_168': float(lag_168),
            'Price_Mean_24h': float(mean_24h), 'Temp_Lag_3': temp_lag_3, 'Temp_Lag_6': temp_lag_6,
            'Cloud_Lag_3': cloud_lag_3, 'Cloud_Lag_6': cloud_lag_6, 'Radiation_Lag_3': rad_lag_3, 'Radiation_Lag_6': rad_lag_6,
            'IDM_Price_Lag_24': float(idm_lag_24), 'DAM_IDM_Spread_Lag_24': float(spread_lag_24),
            'Spread_Mean_24h': float(spread_mean_24h),
            'Grid_Net_Export_Lag_24': float(flow_lag_24), 'Grid_Net_Export_Mean_24h': float(flow_mean_24h),
        })

    X_forecast = pd.DataFrame(records)[FEATURES]
    return X_forecast, records, adjustment

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
    ринкової аномалії, яку модель не могла передбачити і не варто вчити з
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
    shift_mult = 1.0 + shift_pct / 100.0

    def _clip_and_shift(preds):
        return [float(np.clip(np.clip(p, PRICE_FLOOR, PRICE_CAP) * shift_mult, PRICE_FLOOR, PRICE_CAP)) for p in preds]

    final_lgb = _clip_and_shift(pred_lgb)
    final_xgb = _clip_and_shift(pred_xgb)
    final_mlp = _clip_and_shift(pred_mlp)

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
    shift_mult = 1.0 + shift_pct / 100.0

    lower_clipped = [float(np.clip(np.clip(p, PRICE_FLOOR, PRICE_CAP) * shift_mult, PRICE_FLOOR, PRICE_CAP)) for p in pred_lower]
    upper_clipped = [float(np.clip(np.clip(p, PRICE_FLOOR, PRICE_CAP) * shift_mult, PRICE_FLOOR, PRICE_CAP)) for p in pred_upper]
    # Після clip/conformal-поправки полоса теоретично може "перевернутись" —
    # підстраховуємось, щоб lower завжди <= upper.
    lower_final = [min(lo, up) for lo, up in zip(lower_clipped, upper_clipped)]
    upper_final = [max(lo, up) for lo, up in zip(lower_clipped, upper_clipped)]

    return {'hours': list(range(24)), 'lower_uah': lower_final, 'upper_uah': upper_final}

def estimate_idm_price_for_hour(actual_dam_price_uah, as_of_date=None):
    """
    Оцінка ціни ВДР на КОНКРЕТНУ годину, для якої вже відома РЕАЛЬНА ціна РДН
    (напр. заявка РДН щойно не зіграла, і диспетчеру пропонується альтернатива
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
