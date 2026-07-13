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
    - nuclear_pct/hydro_pct немає відповідної навченої ознаки (дані по типах
      відсутні), тож переводяться в МВт-дельту (через довідникові потужності
      нижче) і додаються до Grid_Net_Export_Lag_24/Mean_24h — це РЕАЛЬНА
      навчена ознака, яка відображає баланс генерація/споживання через
      транскордонні перетоки, тож дельта проходить крізь вже навчену
      залежність моделі, а не вигаданий коефіцієнт.
    """
    __tablename__ = "generation_adjustments"

    date = Column(DateTime, primary_key=True, nullable=False)
    nuclear_pct = Column(Float, nullable=False, default=100.0)
    hydro_pct = Column(Float, nullable=False, default=100.0)
    solar_pct = Column(Float, nullable=False, default=100.0)
    wind_pct = Column(Float, nullable=False, default=100.0)
    note = Column(String(500), nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

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
