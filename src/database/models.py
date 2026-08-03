import datetime
from sqlalchemy import Column, String, Float, DateTime, ForeignKey, Integer, Boolean
from sqlalchemy.dialects.postgresql import UUID
import uuid

from src.database.session import Base

class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    country = Column(String(50), default="UA")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Asset(Base):
    __tablename__ = "assets"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    capacity_mwh = Column(Float, nullable=False) # Номинальная емкость
    power_mw = Column(Float, nullable=False)    # Номинальная мощность
    efficiency_charge = Column(Float, default=0.95)
    efficiency_discharge = Column(Float, default=0.95)
    min_soc_pct = Column(Float, default=10.0)
    max_soc_pct = Column(Float, default=90.0)
    deg_cost_per_mwh = Column(Float, nullable=False) # Стоимость деградации
    # Скільки повних еквівалентних циклів заряд/розряд MILP дозволяєте
    # виконати за добу (cycle_limit у milp_model.py). 1.5 — типовий орієнтир
    # для одного пікового вечірнього розряду з невеликим запасом. Реальний
    # сенс піднімати вище — лише коли денний спред цін дозволяє окупити
    # додатковий цикл (тарифи + деградація + КПД в обидва боки) — див.
    # коментар над battery_params у scheduler.py.
    max_cycles_per_day = Column(Float, default=1.5, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class MarketPrice(Base):
    __tablename__ = "market_prices"

    timestamp = Column(DateTime, primary_key=True, nullable=False)
    price_eur = Column(Float, nullable=True)  # немає чесного відкритого курсу без окремої інтеграції — не вигадуємо
    price_uah = Column(Float, nullable=False)
    volume_mwh = Column(Float, nullable=True)
    area = Column(String(10), default="UA_IPS")

class PriceForecast(Base):
    __tablename__ = "price_forecasts"

    timestamp = Column(DateTime, primary_key=True, nullable=False)
    forecast_run_at = Column(DateTime, primary_key=True, nullable=False) # Время генерации прогноза
    model_version = Column(String(50), nullable=False)
    predicted_price_uah = Column(Float, nullable=False)
    lower_bound_uah = Column(Float, nullable=True)
    upper_bound_uah = Column(Float, nullable=True)

class BessTelemetry(Base):
    __tablename__ = "bess_telemetry"

    timestamp = Column(DateTime, primary_key=True, nullable=False)
    asset_id = Column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True, nullable=False)
    current_soc_mwh = Column(Float, nullable=False)
    current_power_mw = Column(Float, nullable=False) # Положительная - разряд, отрицательная - заряд
    battery_temp_c = Column(Float, nullable=True)
    soh_pct = Column(Float, nullable=True) # State of Health (износ)
    system_status = Column(String(50), nullable=True)

class ChargeDischargePlan(Base):
    __tablename__ = "charge_discharge_plans"

    timestamp = Column(DateTime, primary_key=True, nullable=False)
    asset_id = Column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True, nullable=False)
    optimized_run_at = Column(DateTime, primary_key=True, nullable=False)
    target_power_mw = Column(Float, nullable=False) # Заданная мощность заряда(-) или разряда(+)
    expected_soc_mwh = Column(Float, nullable=False)
    expected_profit_uah = Column(Float, nullable=False)

class ManualOverride(Base):
    __tablename__ = "manual_overrides"

    timestamp = Column(DateTime, primary_key=True, nullable=False)
    asset_id = Column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True, nullable=False)
    power_mw = Column(Float, nullable=False)  # Заданная мощность: разряд (+), заряд (-)
    price_uah = Column(Float, nullable=False)  # Цена UAH/MWh

class GenerationAdjustment(Base):
    """
    Ручна корекція диспетчера щодо доступності генерації на конкретну дату —
    ремонт/бойові пошкодження/погода, дані про які немає в жодному реальному
    відкритому джерелі (ENTSO-E не публікує генерацію України по типах з
    25.02.2022). *_pct = 100.0 означає "без відхилень від норми".
    Застосування (ml_pipeline.build_forecast_feature_matrix):
    - solar_pct/wind_pct масштабують РЕАЛЬНО НАВЧЕНІ ознаки Solar_Gen/Wind_Gen
      напряму — чесна дія моделі.
    - nuclear_pct/hydro_pct не мають відповідної навченої ознаки (дані по типах
      відсутні), тож переводяться в МВт-дельту (через довідникові потужності
      нижче), масштабуються редагованим baseload_passthrough_ratio (не весь
      дефіцит проявляється в транскордонному потоці — частина поглинається
      всередині країни) і жорстко обмежуються реальним 99-перцентилем
      Grid_Net_Export_MW за останні 180 днів, перш ніж додатись до
      Grid_Net_Export_Lag_24/Mean_24h — це РЕАЛЬНА навчена ознака, яка
      відображає баланс генерація/споживання через транскордонні перетоки,
      тож дельта проходить крізь вже навчену залежність моделі, а не
      вигаданий коефіцієнт, і не виштовхує ознаку за межі діапазону, на якому
      модель взагалі навчалась (без ratio+clip — насичення дерев LightGBM,
      знайдено 2026-08-03: корекція генерації не давала видимого ефекту на
      прогноз).
    """
    __tablename__ = "generation_adjustments"

    date = Column(DateTime, primary_key=True, nullable=False)
    nuclear_pct = Column(Float, nullable=False, default=100.0)
    hydro_pct = Column(Float, nullable=False, default=100.0)
    solar_pct = Column(Float, nullable=False, default=100.0)
    wind_pct = Column(Float, nullable=False, default=100.0)
    note = Column(String(500), nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class GridStressOverride(Base):
    """
    Ручна оцінка диспетчера обсягу примусових погодинних відключень (ГПВ,
    у "чергах") на дату — на випадок, коли автоматичний сигнал
    (parse_energy_status у telegram_public.py, витягує обсяг з постів
    Укренерго "СТАН ЕНЕРГОСИСТЕМИ") ще не опублікований або відсутній.
    Єдиного структурованого національного API по ГПВ немає (перевірено:
    офіційний сайт, alerts.energy, 20+ різних сайтів обленерго без спільного
    формату) — Telegram-сигнал точніший за keyword-скан, але покриває не
    кожен день, а диспетчер часто знає реальний обсяг раніше й точніше
    (зі свого регіону/оператора). Ручне значення заповнює лише прогалину
    (де автоматичного немає), не перекриває реальний автоматичний сигнал —
    див. merge-логіку в data_manager.add_real_market_factors.
    """
    __tablename__ = "grid_stress_overrides"

    date = Column(DateTime, primary_key=True, nullable=False)
    forced_restriction_queues = Column(Float, nullable=True)
    note = Column(String(500), nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class BidMarginOverride(Base):
    """
    Ручна маржа диспетчера на конкретну добу — наскільки зсунути ціну заявки
    РДН від прогнозної, щоб керувати ймовірністю виконання (див. MarketBid
    нижче). Той самий патерн "одне значення на добу", що InitialSocOverride —
    навмисно поруч у Settings/UI, за прямою вимогою користувача.
    margin_pct=0 означає "подавати заявку рівно за прогнозом" (найбільший
    ризик невиконання при помилці прогнозу, але і найбільший потенційний
    прибуток, якщо прогноз точний).
    """
    __tablename__ = "bid_margin_overrides"

    date = Column(DateTime, primary_key=True, nullable=False)
    asset_id = Column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True, nullable=False)
    margin_pct = Column(Float, nullable=False, default=2.0)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class MarketBid(Base):
    """
    Реальна механіка заявки РДН (аукціон єдиної ціни), а не спрощене
    припущення "виконується завжди за прогнозною ціною", яке малось на
    увазі скрізь раніше в проєкті (walk_forward_backtest, MILP-графік тощо).

    - Заявка на ПРОДАЖ (bid_type='sell', диспетчер хоче розрядити батарею):
      виконується, якщо bid_price_uah <= actual_price_uah (реальна ціна
      достатньо висока, щоб покрити нашу мінімальну прийнятну) — і тоді ми
      продаємо/розряджаємо САМЕ за actual_price_uah (аукціон єдиної ціни, не
      за своєю заявленою ціною).
    - Заявка на ПОКУПКУ (bid_type='buy', диспетчер хоче зарядити
      батарею): виконується, якщо bid_price_uah >= actual_price_uah — і тоді купуємо/
      заряджаємо за actual_price_uah.
    - Якщо НЕ виконалась — фізично зарядити/розрядити батарею в цю годину
      через РДН неможливо (realized_profit_uah=0 для РДН-частини); диспетчеру
      пропонується альтернатива на ВДР (idm_fallback_*).

    bid_price_uah = forecast_price_uah, зсунута на margin_pct у бік більшої
    ймовірності виконання: sell → forecast*(1-margin/100), buy →
    forecast*(1+margin/100) — саме той механізм, який просив користувач
      ("автоматично коригувати на необхідну маржу").
    """
    __tablename__ = "market_bids"

    timestamp = Column(DateTime, primary_key=True, nullable=False)
    asset_id = Column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True, nullable=False)
    bid_type = Column(String(10), nullable=False)  # 'sell' | 'buy' | 'standby'
    volume_kw = Column(Float, nullable=False)
    forecast_price_uah = Column(Float, nullable=False)
    margin_pct = Column(Float, nullable=False)
    bid_price_uah = Column(Float, nullable=False)
    actual_price_uah = Column(Float, nullable=True)
    executed = Column(Boolean, nullable=True)
    realized_profit_uah = Column(Float, nullable=True)
    idm_fallback_suggested = Column(Boolean, default=False)
    idm_fallback_price_uah = Column(Float, nullable=True)
    idm_fallback_profit_uah = Column(Float, nullable=True)
    bid_generated_at = Column(DateTime, default=datetime.datetime.utcnow)
    settled_at = Column(DateTime, nullable=True)

class InitialSocOverride(Base):
    """
    Ручне значення ємності батареї на 00:00 конкретної доби — на випадок,
    коли реального зв'язку зі SCADA/BESS немає (soc_state.py інакше бере
    останній запис BessTelemetry, а за його відсутності — фолбек 20%, який
    міг НЕ відповідати реальному стану батареї, якщо диспетчер знає його з
    інших джерел, напр. фізично зчитав з контролера). Якщо є запис на дату —
    має пріоритет НАД телеметрією (диспетчер напевно знає краще за фолбек).
    """
    __tablename__ = "initial_soc_overrides"

    date = Column(DateTime, primary_key=True, nullable=False)
    asset_id = Column(String(36), ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True, nullable=False)
    capacity_kwh = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
