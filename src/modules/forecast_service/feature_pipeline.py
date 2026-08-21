"""
Єдиний шар побудови ознак — до Фази B (2026-08-21) backtest (prepare_features
у ml_pipeline.py) і production inference (build_forecast_feature_matrix) були
двома незалежними реалізаціями, що ніколи фізично не перетиналися:
walk_forward_backtest/quantile_coverage_backtest не викликаються ніде в
застосунку (лише вручну, scratch/backtest_ensemble.py), тоді як
predict_next_day/predict_price_band (з scheduler.py/forecast.py) йшли через
окрему build_forecast_feature_matrix. Історичні рішення по FEATURES
(Lag_48, EU_DAM_Price, PL/RO погода, ensemble, recency weighting) міряні на
алгоритмі, що не зовсім збігався з тим, що реально працює в проді — і з
витоком: prepare_features робив bfill()/interpolate() по всій історії
одразу, включно з самим target Price, до будь-якого розбиття train/test.
Див. docs/review_ml_forecast_pipeline_2026-08-21.md.

build_training_table() (наступник prepare_features) і
build_asof_feature_matrix() (наступник build_forecast_feature_matrix) —
тепер єдині функції, якими користуються і навчання/бектест, і живий прогноз.
"""
import datetime
import numpy as np
import pandas as pd

from src.core.config import settings
from src.modules.tariff_service.services import TariffService
from src.database.session import SessionLocal
from src.database.models import GenerationAdjustment, PriceShiftOverride
import src.modules.market_data_service.data_manager as dm

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

# Скільки годин ffill() дозволено "тягнути" ознаку вперед з останнього
# реального значення (Фаза B, 2026-08-21) — заміна старого
# .interpolate(method='linear').bfill().ffill(), яке (навіть без явного
# .bfill()) для ВНУТРІШНІХ дірок все одно дивилось на точку ПІСЛЯ дірки
# (лінійна інтерполяція завжди двостороння) — справжня утечка майбутнього,
# включно з самим target Price. ffill() дивиться лише в минуле; дірка
# довша за ліміт лишається NaN і рядок відсіється фінальним dropna() у
# build_training_table(), як і раніше — чесно, без вигаданого значення.
MAX_CAUSAL_FFILL_GAP_HOURS = 6

# Реальна публікаційна затримка джерел — використовується лише в
# build_asof_feature_matrix() для симуляції "хвоста без якоря" при бектесті
# історичних дат (у живому проді ця дірка вже природньо існує в CSV на
# момент запиту, затирання нічого не міняє). IDM — емпірично підтверджено
# 2026-07-21/22 (див. коментар біля FEATURES нижче). ENTSO-E — оцінка
# автора зовнішнього ревью (docs/review_ml_forecast_pipeline_2026-08-21.md),
# потребує окремої емпіричної перевірки, не блокує Фазу B.
IDM_PUBLICATION_DELAY_HOURS = 24
ENTSOE_PUBLICATION_DELAY_HOURS = 6

# Колонки-джерела ознак (не target), які отримують ffill(limit=...) у
# build_training_table(). EU_DAM_Price_EUR_MWh — експериментальна, не в
# FEATURES, але заповнюється тим самим чесним способом.
FEATURE_SOURCE_COLUMNS = [
    'Temperature', 'Cloud_Cover', 'Wind_Speed', 'Shortwave_Radiation',
    'Solar_Gen', 'Wind_Gen', 'IDM_Price', 'DAM_IDM_Spread',
    'Grid_Net_Export_MW', 'EU_DAM_Price_EUR_MWh',
]

# Тільки реальні джерела (oree.com.ua, Open-Meteo) + фізично обґрунтовані
# оцінки Solar_Gen/Wind_Gen з реальної погоди. Раніше тут були Gas_Price,
# Nuclear_Outage, Solar_Strike, Market_Coeff, VDR_Volume, Grid_Import_Export —
# усі фейкові (np.random). Прибрані повністю, а не замінені вигадкою.
#
# Gas_Price_EUR_MWh / Grid_Stress_High / Grid_Stress_Medium вже збираються
# реально (src/modules/external_data_service/), але поки що мають занадто
# коротку історію (перші дні/тижні роботи), щоб модель могла на
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
    # (build_asof_feature_matrix) свіжі 24г IDM_Price майже завжди NaN і
    # limit_direction='both' на хвості БЕЗ якоря вперед вироджується у пласку
    # константу — IDM_Price_Lag_24 (найвпливовіша ознака моделі, ~55% gain)
    # системно приходить на inference "зіпсованим" (пласким), хоча під час
    # НАВЧАННЯ (build_training_table рахує на всій історії одразу) той самий
    # стовпець зазвичай МАВ реальну форму — справжній train/serve skew.
    #
    # СПРОБА ВИПРАВЛЕННЯ (Lag_48 замість Lag_24, щоб надійно потрапляти в
    # останню добу з реальними даними) — ПЕРЕВІРЕНА walk_forward_backtest
    # (test_days=60, retrain_every_days=7, ті самі дані 2021-2026,
    # baseline vs Lag_48 на ідентичному знімку): mean_wape практично не
    # змінився (25.24%→25.16%, шум), АЛЕ last_7d_mean_mape (237.7→250.1) і
    # last_30d_mean_mape (399.0→456.2) — САМЕ ті метрики, які мали
    # покращитись — стали ГІРШЕ. Причина (Фаза A/B, 2026-08-21): старий
    # walk_forward_backtest симулював кожен тестовий день через
    # prepare_features на всій історії одразу (якір є завжди), тобто НЕ
    # відтворював живий "хвост без якоря" — цей конкретний експеримент варто
    # повторити після Фази B на новому as-of бектесті, а не вважати
    # остаточним. Lag_24 ЗАЛИШЕНО. Правильний наступний крок був —
    # розумніше заповнювати саме inference-хвост (формою РДН-ціни), що й
    # зроблено в build_asof_feature_matrix нижче.
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


def build_training_table(df_raw, idm_lag_hours=24):
    """
    Наступник prepare_features() (до Фази B, 2026-08-21). Той самий reindex
    на повну почасову сітку — потрібен для коректної семантики shift()/
    rolling() незалежно від дірок, це НЕ джерело утечки, лишається без змін.
    Змінюється лише стратегія заповнення ДО того, як рахуються lag/rolling:

    - Price (target) — НІКОЛИ не заповнюється. Дірки лишаються NaN, такі
      рядки відсіються фінальним dropna() нижче — чесно, без вигаданого
      значення в тому, що модель має навчитись передбачати. Раніше тут був
      .interpolate().bfill().ffill() — пряма утечка майбутнього в сам target.
    - Ознаки (FEATURE_SOURCE_COLUMNS) — ffill(limit=MAX_CAUSAL_FFILL_GAP_HOURS)
      замість .interpolate()+bfill()+ffill(): лише минулим значенням, дірка
      довша за ліміт лишається NaN і теж уйде через dropna.

    idm_lag_hours=24 — за замовчуванням продове значення. Параметр існує
    ЛИШЕ для Фази C (docs/review_ml_forecast_pipeline_2026-08-21.md) —
    чесна повторна перевірка старого відхиленого експерименту "Lag_48
    замість Lag_24" на новому as-of-бектесті (до Фази B цей експеримент був
    перевірений на leaky бектесті, що не відтворював живий "хвост без
    якоря"). Назва колонки лишається 'IDM_Price_Lag_24' незалежно від
    реального лагу — щоб не міняти список FEATURES заради A/B-тесту.
    """
    df = df_raw.copy()
    df['Datetime'] = pd.to_datetime(df['Datetime'])
    df = df.sort_values('Datetime').set_index('Datetime')

    full_range = pd.date_range(start=df.index.min(), end=df.index.max(), freq='h')
    df = df.reindex(full_range)
    df.index.name = 'Datetime'

    for col in FEATURE_SOURCE_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = df[col].ffill(limit=MAX_CAUSAL_FFILL_GAP_HOURS)

    # Експериментальна погода сусідніх зон PL/RO (neighbor_weather.py) — той
    # самий причинний ffill, не в FEATURES, не блокує прод-шлях.
    for zone in ('PL', 'RO'):
        for base_col in ('Temperature', 'Cloud_Cover', 'Wind_Speed', 'Shortwave_Radiation'):
            full_col = f'{zone}_{base_col}'
            if full_col not in df.columns:
                df[full_col] = np.nan
            df[full_col] = df[full_col].ffill(limit=MAX_CAUSAL_FFILL_GAP_HOURS)

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

    df['IDM_Price_Lag_24'] = df['IDM_Price'].shift(idm_lag_hours)
    df['DAM_IDM_Spread_Lag_24'] = df['DAM_IDM_Spread'].shift(idm_lag_hours)
    df['Spread_Mean_24h'] = df['DAM_IDM_Spread'].shift(idm_lag_hours).rolling(window=24).mean()

    df['Grid_Net_Export_Lag_24'] = df['Grid_Net_Export_MW'].shift(24)
    df['Grid_Net_Export_Mean_24h'] = df['Grid_Net_Export_MW'].shift(24).rolling(window=24).mean()

    df['EU_DAM_Price_Lag_24'] = df['EU_DAM_Price_EUR_MWh'].shift(24)
    df['EU_DAM_Price_Mean_24h'] = df['EU_DAM_Price_EUR_MWh'].shift(24).rolling(window=24).mean()

    df = df.dropna(subset=FEATURES + ['Price']).reset_index(drop=True)
    return df


def clip_and_shift(preds, shift_pct, floor=PRICE_FLOOR, cap=PRICE_CAP):
    """Спільна постобробка прогнозу — раніше окреме замикання _clip_and_shift
    всередині predict_next_day (проду) і взагалі відсутня в backtest
    (walk_forward_backtest оцінював сирі предикти без clip/shift)."""
    shift_mult = 1.0 + shift_pct / 100.0
    return [float(np.clip(np.clip(p, floor, cap) * shift_mult, floor, cap)) for p in preds]


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
    import os
    import json
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
    import os
    import json
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


def build_asof_feature_matrix(target_date, forecast_weather, last_prices, as_of=None, apply_manual_overrides=True, idm_lag_hours=24):
    """
    Наступник build_forecast_feature_matrix() (до Фази B). Будує матрицю
    ознак (FEATURES) для прогнозу на 24 години наперед — спільна для
    точкового прогнозу (predict_next_day), квантильного інтервалу
    (predict_price_band) І ТЕПЕР ТАКОЖ walk_forward_backtest — та сама
    логіка лагів/фічей скрізь, замість двох незалежних реалізацій
    (docs/review_ml_forecast_pipeline_2026-08-21.md).

    idm_lag_hours=24 — див. build_training_table(). Лише для Фази C
    повторної перевірки Lag_48; продовий шлях (predict_next_day/
    predict_price_band) завжди лишає замовчування 24.

    as_of — коли "нібито" рахується прогноз (UTC). За замовчуванням —
    зараз (жива робота проду, поведінка не змінюється). walk_forward_backtest
    передає симульований історичний момент: CSV сьогодні вже "знає" все
    майбутнє відносно тієї історичної дати (публікації давно відбулись), тож
    без цього параметра бектест бачив би куди повнішу картину, ніж
    реально бачив прод у той момент. Публікаційна затримка IDM/ENTSO-E
    (IDM_PUBLICATION_DELAY_HOURS/ENTSOE_PUBLICATION_DELAY_HOURS) — хвіст
    hist_before_target ближче за ці межі до as_of затирається в NaN ПЕРЕД
    реконструкцією нижче. У живому проді (as_of=зараз) це no-op — дірка там
    і так природньо NaN, дані ще не опубліковані.

    apply_manual_overrides=False (потрібно для бектесту — історичні дати
    фізично не могли мати ручних диспетчерських поправок) пропускає
    GenerationAdjustment/PriceShiftOverride повністю замість запиту в БД.

    Якщо на target_date збережена ручна корекція генерації
    (GenerationAdjustment — диспетчер відзначив ремонт/пошкодження/погану
    погоду на АЕС/ГЕС/СЕС/ВЕС), вона застосовується тут:
    - solar_pct/wind_pct масштабують Solar_Gen/Wind_Gen напряму (реальні
      навчені ознаки — чесний вплив на прогноз через саму модель).
    - nuclear_pct/hydro_pct не мають навченої ознаки (даних по типах немає з
      2022 року) — переводяться в МВт-дельту через довідникові потужності,
      масштабуються baseload_passthrough_ratio і жорстко обмежуються
      реальним 99-перцентилем Grid_Net_Export_MW за останні
      BASELOAD_DELTA_CLIP_LOOKBACK_DAYS днів, перш ніж додатись до
      Grid_Net_Export_Lag_24/Mean_24h.
    """
    if as_of is None:
        as_of = datetime.datetime.utcnow()
    as_of = pd.to_datetime(as_of)

    if apply_manual_overrides:
        adjustment = _get_generation_adjustment(target_date)
    else:
        adjustment = {'nuclear_pct': 100.0, 'hydro_pct': 100.0, 'solar_pct': 100.0, 'wind_pct': 100.0, 'note': None}

    nuclear_ref_mw, hydro_ref_mw = _get_reference_capacities_mw()
    passthrough_ratio = _get_baseload_passthrough_ratio()
    baseload_delta_raw = (
        nuclear_ref_mw * (adjustment['nuclear_pct'] / 100.0 - 1.0)
        + hydro_ref_mw * (adjustment['hydro_pct'] / 100.0 - 1.0)
    ) * passthrough_ratio

    df_hist = pd.read_csv(dm.MERGED_DATA_PATH)
    df_hist['Datetime'] = pd.to_datetime(df_hist['Datetime'])
    df_hist = df_hist.sort_values('Datetime')

    forecast_dt_start = pd.to_datetime(target_date)

    lookback_start = forecast_dt_start - pd.Timedelta(days=BASELOAD_DELTA_CLIP_LOOKBACK_DAYS)
    recent_export = df_hist.loc[df_hist['Datetime'] >= lookback_start, 'Grid_Net_Export_MW'].dropna()
    export_source = recent_export if len(recent_export) >= 200 else df_hist['Grid_Net_Export_MW'].dropna()
    clip_bound = (
        float(export_source.abs().quantile(BASELOAD_DELTA_CLIP_QUANTILE))
        if len(export_source) > 30 else 1800.0
    )
    baseload_delta_mw = float(np.clip(baseload_delta_raw, -clip_bound, clip_bound))
    hist_before_target = df_hist[df_hist['Datetime'] < forecast_dt_start].sort_values('Datetime').copy()

    # Симуляція публікаційної затримки відносно as_of (див. докстрінг вище).
    idm_cutoff = as_of - pd.Timedelta(hours=IDM_PUBLICATION_DELAY_HOURS)
    entsoe_cutoff = as_of - pd.Timedelta(hours=ENTSOE_PUBLICATION_DELAY_HOURS)
    if 'IDM_Price' in hist_before_target.columns:
        hist_before_target.loc[hist_before_target['Datetime'] > idm_cutoff, 'IDM_Price'] = np.nan
    if 'Grid_Net_Export_MW' in hist_before_target.columns:
        hist_before_target.loc[hist_before_target['Datetime'] > entsoe_cutoff, 'Grid_Net_Export_MW'] = np.nan

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
    # останні ~24г) на момент прогнозу завжди NaN. Відновлюємо ФОРМУ
    # пропущеного хвоста через уже відому реальну форму РДН-ціни
    # (last_prices) + медіанну РЕАЛЬНУ різницю ВДР-РДН за останній тиждень
    # перекриття. Адитивна різниця, а не співвідношення — на низьких цінах
    # (~10-100 ₴, сонячний профіцит) IDM/DAM "вибухає" до 0.45-4.6x
    # (перевірено на реальних даних 18-20.07.2026), тоді як різниця
    # лишається обмеженою й стабільною.
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
        dt = pd.to_datetime(target_date) + pd.to_timedelta(h, unit='h')
        weather_row = forecast_weather.iloc[h] if h < len(forecast_weather) else forecast_weather.iloc[-1]

        lag_24 = last_prices[-24 + h]
        lag_48 = last_prices[-48 + h]
        lag_168 = last_prices[-168 + h]
        mean_24h = np.mean(last_prices[121 + h: 145 + h])

        idm_lag_24 = last_idm[-idm_lag_hours + h]
        spread_lag_24 = last_spreads[-idm_lag_hours + h]
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
