import datetime
import uuid
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel
from typing import Optional, List

from src.core.config import settings
from src.database.session import SessionLocal
from src.database.models import Asset, ChargeDischargePlan, PriceForecast, ForecastRun, ManualOverride, InitialSocOverride, BessTelemetry
import src.modules.optimization_service.milp_model as opt
from src.modules.scada_service.soc_state import get_current_soc_fraction, previous_day_calculated_fraction
from src.core.redis import set_job_status, get_job_status
from src.core.security import RoleChecker
from src.core.time_utils import kyiv_to_utc, kyiv_day_bounds, utc_to_kyiv
from src.tasks.scheduler import DISPATCHER_ACTIONS, reschedule_virtual_dispatcher_jobs

router = APIRouter()

class RunOptimizationRequest(BaseModel):
    asset_id: str
    target_date: str
    # None -> бекенд бере РЕАЛЬНИЙ поточний SoC з SCADA-телеметрії
    # (get_current_soc_fraction), а не вигадану константу. Явне число
    # лишається можливим для навмисного what-if сценарію.
    initial_soc_pct: Optional[float] = None
    mode: Optional[str] = "arbitrage"
    simulations_count: Optional[int] = 50
    # 2026-08-26: явний, свідомий вихід із заморозки минулих годин (див.
    # коментар нижче) — для випадку, коли диспетчер хоче ПОВНІСТЮ
    # перезапустити день "як новий" (напр. попередні розрахунки на цю дату
    # були зроблені тестово/помилково і не відображають жодної реальної
    # диспетчеризації, яку варто зберігати як факт). За замовчуванням
    # False — звичайний виклик НЕ чіпає минуле.
    force_full_day: Optional[bool] = False

def run_optimization_background_job(
    job_id: str,
    asset_id_str: str,
    target_date_str: str,
    initial_soc_pct: Optional[float],
    mode_str: str,
    simulations_count: int,
    force_full_day: bool = False,
):
    job = get_job_status(job_id) or {}
    job["status"] = "running"
    set_job_status(job_id, job)
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == asset_id_str).first()
        if not asset:
            asset = db.query(Asset).first() # Fallback to first asset

        if not asset:
            raise ValueError("No asset found in database")

        target_dt_start = kyiv_to_utc(target_date_str, 0)

        # 2026-08-26: якщо target_date — СЬОГОДНІ (чи будь-яка дата, де частина
        # години вже минула), не можна рахувати MILP на всі 24 години "з нуля"
        # доби — це давало internally coherent, але ВІД'ЄДНАНИЙ ВІД РЕАЛЬНОСТІ
        # прогноз: `resolved_initial_soc` вище бере РЕАЛЬНИЙ SoC із SCADA
        # ЗАРАЗ (get_current_soc_fraction), але якщо цей SoC підставити як
        # "стан на північ доби" і порахувати вперед усі 24 години, MILP сам
        # вирішує, як "вигідно" зарядити/розрядити ще й ті години, що вже
        # ФІЗИЧНО минули — а write-guard нижче ці минулі години просто не
        # записує. Результат: майбутня частина графіка (яку MILP порахував як
        # продовження свого власного, відкинутого рішення для минулих годин)
        # описує SoC-траєкторію, що ніколи фізично не була і не буде
        # досяжною з РЕАЛЬНОГО поточного SoC. Знайдено користувачем: реальний
        # SoC 824 кВт·год, а графік на вечір показував розряд з 3600 кВт·год,
        # яких батарея фізично ніде не набрала. Правильно — рахувати MILP
        # ЛИШЕ на ще майбутній "хвіст" доби (горизонт `T = len(prices)` вже й
        # так підтримується `optimize_battery_schedule` для довільної
        # довжини, docstring явно каже "arbitrary horizon"), з
        # `resolved_initial_soc` як стан РІВНО "зараз" (а не "опівночі") —
        # це коректно, бо `T`-годинний хвіст починається саме зараз.
        # `start_t` — перша година доби, чий реальний UTC-момент ще НЕ настав
        # (той самий критерій `<=`, що й у write-guard нижче — узгоджено,
        # інакше межі розійдуться). Для звичайного випадку (щоденна 06:00-
        # джоба рахує ЗАВТРАШНІЙ день) усі 24 години в майбутньому, start_t=0
        # — поведінка НЕ змінюється.
        now_utc = datetime.datetime.utcnow()
        if force_full_day:
            start_t = 0
        else:
            start_t = 24
            for t in range(24):
                if kyiv_to_utc(target_date_str, t) > now_utc:
                    start_t = t
                    break
            if start_t == 24:
                raise RuntimeError(
                    f"Усі 24 години {target_date_str} вже минули — перерахунок на цю дату більше не має сенсу."
                )
        horizon = 24 - start_t

        # `include_midnight_override=False` для частткового (start_t>0)
        # перерахунку — InitialSocOverride означає "SoC на 00:00", не "SoC
        # прямо зараз"; якщо все одно спрацює, солвер прийме застаріле
        # опівнічне число за поточний стан замість живої телеметрії (див.
        # докстрінг get_current_soc_fraction).
        resolved_initial_soc = (
            initial_soc_pct / 100.0
            if initial_soc_pct is not None
            else get_current_soc_fraction(db, asset, target_date=target_date_str, include_midnight_override=(start_t == 0))
        )

        # Lineage (CODE_REVIEW.md п.7-20): який ForecastRun реально стоїть за
        # PriceForecast нижче — найновіший на цю target_date (persist_forecast_run
        # пишеться в ТІЙ САМІЙ транзакції, що й поточний PriceForecast, тож
        # найновіший ForecastRun.generated_at_utc відповідає тому, що зараз у
        # PriceForecast). None, якщо прогнозу взагалі не було/він старший за
        # 2026-08-21 (до появи ForecastRun) — чесно, не вигадуємо.
        latest_run = db.query(ForecastRun).filter(
            ForecastRun.target_date == target_dt_start
        ).order_by(ForecastRun.generated_at_utc.desc()).first()
        forecast_run_id = latest_run.id if latest_run else None

        # Load forecast prices from DB or generate mock if empty
        forecasts = db.query(PriceForecast).filter(
            PriceForecast.forecast_run_at == target_dt_start
        ).order_by(PriceForecast.timestamp).all()
        
        price_lower = None
        price_upper = None
        if len(forecasts) == 24:
            prices = [f.predicted_price_uah for f in forecasts]
            # P10/P90 conformal-калібрований інтервал (Фаза 3), якщо forecast/run
            # його порахував — реальні квантилі замість вигаданого ±1.64σ.
            if all(f.lower_bound_uah is not None and f.upper_bound_uah is not None for f in forecasts):
                price_lower = [f.lower_bound_uah for f in forecasts]
                price_upper = [f.upper_bound_uah for f in forecasts]
        else:
            # Generate mock prices if DB is empty
            prices = [
                3000.0, 2800.0, 2000.0, 1500.0, 1000.0, 800.0,
                2000.0, 3500.0, 4500.0, 4000.0, 3200.0, 2500.0,
                2000.0, 1800.0, 1200.0, 1000.0, 1500.0, 2200.0,
                4500.0, 6000.0, 7500.0, 8500.0, 6500.0, 4500.0
            ]

        # Різати на "хвіст" від start_t (див. коментар вище) — лише реально
        # майбутні години йдуть у солвер.
        prices = prices[start_t:]
        if price_lower is not None:
            price_lower = price_lower[start_t:]
        if price_upper is not None:
            price_upper = price_upper[start_t:]

        battery_params = {
            'battery_capacity': asset.capacity_mwh * 1000.0,
            'max_charge_power': asset.power_mw * 1000.0,
            'max_discharge_power': asset.power_mw * 1000.0,
            'charge_efficiency': asset.efficiency_charge,
            'discharge_efficiency': asset.efficiency_discharge,
            'initial_soc': resolved_initial_soc,
            'min_soc': asset.min_soc_pct / 100.0,
            'max_soc': asset.max_soc_pct / 100.0,
            'max_cycles_per_day': asset.max_cycles_per_day,
            'degradation_cost': asset.deg_cost_per_mwh / 1000.0,
            'transmission_tariff': 528.57,
            'distribution_tariff': 1500.0,
            'dispatch_tariff': 104.57,
            'supplier_margin': 100.0,
            'mode': mode_str
        }
        
        # Run scenarios and VaR
        scenarios_results = opt.optimize_with_scenarios_and_risks(
            prices=prices,
            price_lower=price_lower,
            price_upper=price_upper,
            num_simulations=simulations_count,
            **battery_params
        )
        
        # CODE_REVIEW.md п.7-20 (MILP-статус солвера ігнорується, 2026-08-25):
        # optimize_battery_schedule вже рахує pulp.LpStatus (напр. 'Infeasible',
        # якщо реальний SoC із SCADA-телеметрії опинився поза min_soc/max_soc
        # межами активу — не гіпотетичний випадок), але цей статус ніде не
        # перевірявся — при провалі солвера LpVariable.varValue стає None,
        # код мовчки підставляв 0.0 і писав ChargeDischargePlan з "нульовим"
        # графіком, нерозрізнюваним від реального "сьогодні вигідніше STANDBY".
        # raise тут ловиться вже існуючим except нижче (db.rollback +
        # job["status"]="failed") — той самий шлях, яким і так проходять інші
        # помилки цієї джоби; фронтенд (api/client.ts::pollJob) вже кидає
        # Error(status.error) на "failed", AppContext вже показує це як
        # addLog('API', ..., 'error') — нового UI-коду не потрібно.
        base_status = scenarios_results['scenarios']['base']['status']
        if base_status != 'Optimal':
            raise RuntimeError(
                f"MILP солвер не знайшов оптимальне рішення (status={base_status}) — "
                f"план НЕ збережено. Перевірте вхідний SoC/ліміти активу/ціни на {target_date_str}."
            )

        # Save optimal schedule to database — лише реально майбутні години
        # (t від start_t, обчисленого вище) записуються; уже минулі години
        # НЕ переписуються, лишається те, що там вже є (факт попереднього
        # запуску, або взагалі нічого — чесно, не вигадуємо заднім числом).
        # Для звичайного випадку (щоденна 06:00-джоба рахує ЗАВТРАШНІЙ день)
        # start_t=0 — записуються всі 24 години, поведінка НЕ змінюється.
        base_sched = scenarios_results['scenarios']['base']
        n_skipped_past = start_t
        for t in range(start_t, 24):
            forecast_time = kyiv_to_utc(target_date_str, t)

            db.query(ChargeDischargePlan).filter(
                ChargeDischargePlan.timestamp == forecast_time,
                ChargeDischargePlan.asset_id == asset.id,
                ChargeDischargePlan.optimized_run_at == target_dt_start
            ).delete()

            sched_item = base_sched['schedule'][t - start_t]
            plan_entry = ChargeDischargePlan(
                timestamp=forecast_time,
                asset_id=asset.id,
                optimized_run_at=target_dt_start,
                target_power_mw=sched_item['power_kw'] / 1000.0,
                expected_soc_mwh=sched_item['soc_kwh'] / 1000.0,
                expected_profit_uah=sched_item['hourly_p_l_uah'],
                forecast_run_id=forecast_run_id,
            )
            db.add(plan_entry)

        db.commit()
        scenarios_results['n_past_hours_frozen'] = n_skipped_past
        # schedule[i] тепер відповідає РЕАЛЬНІЙ годині (schedule_start_hour + i),
        # не завжди hour i доби — фронтенд (RiskScenarios.tsx) враховує зсув
        # при підписі осі, інакше графік сценаріїв показав би ціни не на тих
        # годинах для будь-якого перерахунку посеред дня.
        scenarios_results['schedule_start_hour'] = start_t
        
        job["status"] = "completed"
        job["progress"] = 100
        job["result"] = scenarios_results
        set_job_status(job_id, job)
    except Exception as e:
        db.rollback()
        job["status"] = "failed"
        job["error"] = str(e)
        set_job_status(job_id, job)
    finally:
        db.close()

@router.post("/run", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def run_optimization(req: RunOptimizationRequest, background_tasks: BackgroundTasks):
    job_id = f"job_opt_{uuid.uuid4().hex[:8]}"
    created_at = datetime.datetime.utcnow().isoformat() + "Z"
    job = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "created_at": created_at
    }
    set_job_status(job_id, job)
    
    background_tasks.add_task(
        run_optimization_background_job,
        job_id,
        req.asset_id,
        req.target_date,
        req.initial_soc_pct,
        req.mode,
        req.simulations_count,
        req.force_full_day or False,
    )
    
    return {
        "job_id": job_id,
        "status": "pending",
        "created_at": created_at,
        "message": "Задача оптимизации BESS добавлена в очередь.",
        "links": {
            "status_url": f"/api/v1/jobs/{job_id}"
        }
    }

class InitialSocOverrideModel(BaseModel):
    asset_id: str
    date: str
    capacity_kwh: float

@router.get("/initial-soc", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_initial_soc(asset_id: str, date: str):
    """
    Показує, що РЕАЛЬНО буде використано як SoC на 00:00 target_date — ручне
    значення (якщо збережене), інакше останнє з SCADA-телеметрії, інакше
    розрахункова ємність на кінець попередньої доби (з учорашнього MILP-
    плану), інакше фолбек 20%. source дозволяє диспетчеру бачити, звідки
    взялось число, і наскільки йому варто довіряти.
    """
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == asset_id).first()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        target_dt = kyiv_to_utc(date, 0)

        override = db.query(InitialSocOverride).filter(
            InitialSocOverride.asset_id == asset_id,
            InitialSocOverride.date == target_dt
        ).first()

        tel = db.query(BessTelemetry).filter(
            BessTelemetry.asset_id == asset_id
        ).order_by(BessTelemetry.timestamp.desc()).first()

        prev_fraction = None
        if asset.capacity_mwh > 0:
            prev_fraction = previous_day_calculated_fraction(db, asset, target_dt)

        if override is not None:
            source = "manual"
            capacity_kwh = override.capacity_kwh
        elif tel is not None:
            source = "scada_telemetry"
            capacity_kwh = tel.current_soc_mwh * 1000.0
        elif prev_fraction is not None:
            source = "calculated_previous_day"
            capacity_kwh = prev_fraction * asset.capacity_mwh * 1000.0
        else:
            source = "fallback_default"
            capacity_kwh = asset.capacity_mwh * 1000.0 * 0.20

        return {
            "asset_id": asset_id,
            "date": date,
            "capacity_kwh": capacity_kwh,
            "capacity_pct": (capacity_kwh / (asset.capacity_mwh * 1000.0) * 100.0) if asset.capacity_mwh > 0 else 0.0,
            "source": source,
            "has_manual_override": override is not None,
            "telemetry_available": tel is not None,
            "previous_day_calculated_available": prev_fraction is not None,
        }
    finally:
        db.close()

@router.post("/initial-soc", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def save_initial_soc(req: InitialSocOverrideModel):
    db = SessionLocal()
    try:
        target_dt = kyiv_to_utc(req.date, 0)
        row = db.query(InitialSocOverride).filter(
            InitialSocOverride.asset_id == req.asset_id,
            InitialSocOverride.date == target_dt
        ).first()
        if not row:
            row = InitialSocOverride(asset_id=req.asset_id, date=target_dt)
            db.add(row)
        row.capacity_kwh = req.capacity_kwh
        db.commit()
        return {"status": "success", "message": f"Ручний SoC на 00:00 {req.date} збережено ({req.capacity_kwh:.0f} кВт·год)."}
    finally:
        db.close()

@router.delete("/initial-soc", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def clear_initial_soc(asset_id: str, date: str):
    """Прибирає ручне значення — повертає розрахунок до автоматичного (SCADA-телеметрія / кінець попередньої доби / фолбек)."""
    db = SessionLocal()
    try:
        target_dt = kyiv_to_utc(date, 0)
        db.query(InitialSocOverride).filter(
            InitialSocOverride.asset_id == asset_id,
            InitialSocOverride.date == target_dt
        ).delete()
        db.commit()
        return {"status": "success", "message": f"Ручне значення SoC на {date} прибрано, розрахунок знову автоматичний."}
    finally:
        db.close()

@router.get("/plans", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_plans(asset_id: str, date: str):
    db = SessionLocal()
    try:
        target_dt = kyiv_to_utc(date, 0)
        plans = db.query(ChargeDischargePlan).filter(
            ChargeDischargePlan.asset_id == asset_id,
            ChargeDischargePlan.optimized_run_at == target_dt
        ).order_by(ChargeDischargePlan.timestamp).all()
        
        if not plans:
            raise HTTPException(status_code=404, detail="No optimization plans found for selected asset and date")
            
        return {
            "asset_id": asset_id,
            "date": date,
            "schedule": [
                {
                    "timestamp": p.timestamp.isoformat() + "Z",
                    "target_power_mw": p.target_power_mw,
                    "expected_soc_mwh": p.expected_soc_mwh,
                    "expected_profit_uah": p.expected_profit_uah,
                    "forecast_run_id": p.forecast_run_id,
                }
                for p in plans
            ]
        }
    finally:
        db.close()

class HourlyOverrideItem(BaseModel):
    hour: int
    power_mw: float
    price_uah: float

class SaveOverridesRequest(BaseModel):
    asset_id: str
    date: str
    overrides: List[HourlyOverrideItem]

@router.get("/manual-overrides", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_manual_overrides(asset_id: str, date: str):
    db = SessionLocal()
    try:
        # Реальна київська доба (CLAUDE.md п.26/27) — dt_start збігається з
        # forecast_run_at/optimized_run_at (kyiv_to_utc(date,0)); day_start/
        # day_end — для зрізу ManualOverride/CSV.
        dt_start = kyiv_to_utc(date, 0)
        day_start, day_end = kyiv_day_bounds(date)

        # Load overrides
        overrides = db.query(ManualOverride).filter(
            ManualOverride.asset_id == asset_id,
            ManualOverride.timestamp >= day_start,
            ManualOverride.timestamp < day_end
        ).order_by(ManualOverride.timestamp).all()

        # Also query active optimization plans for pre-filling
        plans = db.query(ChargeDischargePlan).filter(
            ChargeDischargePlan.asset_id == asset_id,
            ChargeDischargePlan.optimized_run_at == dt_start
        ).order_by(ChargeDischargePlan.timestamp).all()

        # Create map of REAL Kyiv hour -> override/plan
        override_map = {}
        for o in overrides:
            override_map[utc_to_kyiv(o.timestamp).hour] = o

        plan_map = {}
        for p in plans:
            plan_map[utc_to_kyiv(p.timestamp).hour] = p

        # Get base market prices: реальна ціна за факт (якщо доба вже минула),
        # інакше — реальний збережений прогноз (PriceForecast), і лише як
        # останній fallback — умовна константа (немає ні факту, ні прогнозу).
        import pandas as pd
        import os
        csv_path = os.path.join(settings.DATA_DIR, "historical_data_merged.csv")
        if not os.path.exists(csv_path):
            csv_path = "/home/oleg/agy_energo/data/historical_data_merged.csv"

        day_prices = None
        try:
            if os.path.exists(csv_path):
                df = pd.read_csv(csv_path)
                df['Datetime'] = pd.to_datetime(df['Datetime'])
                df_day = df[(df['Datetime'] >= day_start) & (df['Datetime'] < day_end)].sort_values('Datetime')
                if len(df_day) >= 24:
                    day_prices = df_day['Price'].tolist()
        except Exception:
            pass

        if day_prices is None:
            forecasts = db.query(PriceForecast).filter(
                PriceForecast.forecast_run_at == dt_start
            ).order_by(PriceForecast.timestamp).all()
            if len(forecasts) == 24:
                day_prices = [f.predicted_price_uah for f in forecasts]

        if day_prices is None:
            day_prices = [3000.0] * 24

        schedule = []
        for hour in range(24):
            dt_hour = kyiv_to_utc(date, hour)
            o = override_map.get(hour)
            p = plan_map.get(hour)
            
            # Default values
            default_power = p.target_power_mw if p else 0.0
            default_price = day_prices[hour]
            
            schedule.append({
                "hour": hour,
                "timestamp": dt_hour.isoformat() + "Z",
                "power_mw": o.power_mw if o else default_power,
                "price_uah": o.price_uah if o else default_price,
                "is_overridden": o is not None
            })
            
        return {
            "asset_id": asset_id,
            "date": date,
            "overrides": schedule
        }
    finally:
        db.close()

@router.post("/manual-overrides", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def save_manual_overrides(req: SaveOverridesRequest):
    db = SessionLocal()
    try:
        day_start, day_end = kyiv_day_bounds(req.date)

        # 1. Delete existing overrides for this day (реальна київська доба)
        db.query(ManualOverride).filter(
            ManualOverride.asset_id == req.asset_id,
            ManualOverride.timestamp >= day_start,
            ManualOverride.timestamp < day_end
        ).delete()

        # 2. Insert new overrides — item.hour є реальною київською годиною
        # (те саме, що повертає GET /manual-overrides).
        for item in req.overrides:
            timestamp = kyiv_to_utc(req.date, item.hour)
            override = ManualOverride(
                timestamp=timestamp,
                asset_id=req.asset_id,
                power_mw=item.power_mw,
                price_uah=item.price_uah
            )
            db.add(override)
            
        db.commit()
        
        # 3. Clear/Invalidate C-level cache file for this asset so it recalculates instantly!
        import os
        cache_path = os.path.join(settings.DATA_DIR, f"executive_cache_{req.asset_id}.json")
        if os.path.exists(cache_path):
            try:
                import json
                with open(cache_path, "r") as f:
                    cached_days = json.load(f)
                
                # Delete this date's cached profit
                date_key = req.date
                if date_key in cached_days:
                    del cached_days[date_key]
                    
                with open(cache_path, "w") as f:
                    json.dump(cached_days, f)
            except Exception:
                try:
                    os.remove(cache_path)
                except:
                    pass
                    
        return {
            "status": "success",
            "message": f"Manual overrides saved successfully for date {req.date}."
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error saving overrides: {str(e)}")
    finally:
        db.close()

class SystemSettingsModel(BaseModel):
    launch_date: str
    osr: str
    voltage_class: int
    margin: float
    capacity_kw: float
    power_kw: float
    efficiency_pct: float
    nuclear_reference_capacity_mw: Optional[float] = None
    hydro_reference_capacity_mw: Optional[float] = None
    baseload_passthrough_ratio: Optional[float] = None
    max_cycles_per_day: Optional[float] = None
    bid_reminder_telegram_enabled: Optional[bool] = None
    auto_dispatch_enabled: Optional[bool] = None
    excise_duty_pct: Optional[float] = None
    transformer_loss_pct: Optional[float] = None
    # Підключення реальної батареї (2026-08-26) — "simulator"|"tcp"|"serial"|
    # "disabled". У "simulator" tcp_host/tcp_port ігноруються (завжди
    # внутрішній 127.0.0.1:5020) — поля лишаються заповненими лише як
    # готовий дефолт, коли власник перемкне на "tcp".
    bess_connection_type: Optional[str] = None
    bess_tcp_host: Optional[str] = None
    bess_tcp_port: Optional[int] = None
    bess_serial_port: Optional[str] = None
    bess_serial_baudrate: Optional[int] = None
    bess_serial_parity: Optional[str] = None
    bess_serial_stopbits: Optional[int] = None
    bess_serial_bytesize: Optional[int] = None
    bess_modbus_unit_id: Optional[int] = None

# Довідкові потужності для перетворення "% робочих АЕС/ГЕС" у МВт-дельту
# (generation_adjustments.py). Це НЕ вигадка — реальні опубліковані дані:
# АЕС: 13835 МВт номінал 4 станцій (Рівненська/Хмельницька/Пд.-Українська/
#   Запорізька) мінус Запорізька (6 блоків ВВЕР-1000, під окупацією й
#   зупинена з 2022) ≈ 7835 МВт реально доступних. Джерела: World Nuclear
#   Association, IAEA PRIS (станом на 2025).
# ГЕС: сумарно ~6229 МВт номінал (включно ГАЕС), але після руйнування
#   Каховської ГЕС (335 МВт) і бойових пошкоджень значна частина каскаду
#   недоступна — беремо ~3800 МВт як орієнтовну робочу оцінку.
# ЦІ ЦИФРИ НАБЛИЗНІ й змінюються з часом (ремонти, відбудова, нові удари) —
# тому редаговані в Settings, а не жорстко зашиті як факт.
DEFAULT_NUCLEAR_REFERENCE_CAPACITY_MW = 7835.0
DEFAULT_HYDRO_REFERENCE_CAPACITY_MW = 3800.0
# Частка дефіциту АЕС/ГЕС, що реально проявляється в транскордонному
# нетто-експорті (решта поглинається всередині країни) — редагований
# коефіцієнт, дублює DEFAULT_BASELOAD_PASSTHROUGH_RATIO з ml_pipeline.py
# (той самий патерн дублювання, що вже є для nuclear/hydro-констант вище).
DEFAULT_BASELOAD_PASSTHROUGH_RATIO = 0.3

# Прапорець Telegram-нагадувань про дії з заявками РДН/ВДР (bidding_service/
# services.py::build_daily_action_summary, telegram_bot.py::check_and_send_bid_reminder)
# — за замовчуванням True (нагадування вимкнене лише якщо диспетчер сам вимкнув).
DEFAULT_BID_REMINDER_TELEGRAM_ENABLED = True

# "Віртуальний диспетчер" (2026-08-26, CLAUDE.md п.41) — за замовчуванням
# ВИМКНЕНО: власник батареї свідомо вмикає повну автоматизацію подачі
# заявок (scheduler.py::run_daily_forecast_and_optimization п.7). Реальний
# фінансовий/ринковий ризик — явна згода, не тихий дефолт.
DEFAULT_AUTO_DISPATCH_ENABLED = False

# Підключення реальної батареї (2026-08-26) — дефолт "simulator" (поточна,
# єдина раніше існуюча поведінка). tcp_port=502 — реальний стандартний
# Modbus-TCP порт (НЕ 5020 — те число внутрішнє для симулятора, обране щоб
# не вимагати root/CAP_NET_BIND_SERVICE у контейнері; 5020 лишається
# захардкодженим лише для самого симулятора, не тут). baudrate/parity/
# stopbits/bytesize — типові значення Modbus RTU (8-N-1, 9600 бод).
DEFAULT_BESS_CONNECTION_TYPE = "simulator"
DEFAULT_BESS_TCP_HOST = "127.0.0.1"
DEFAULT_BESS_TCP_PORT = 502
DEFAULT_BESS_SERIAL_PORT = ""
DEFAULT_BESS_SERIAL_BAUDRATE = 9600
DEFAULT_BESS_SERIAL_PARITY = "N"
DEFAULT_BESS_SERIAL_STOPBITS = 1
DEFAULT_BESS_SERIAL_BYTESIZE = 8
DEFAULT_BESS_MODBUS_UNIT_ID = 1

# Акциз (3.2%) і втрати трансформаторного обладнання (1.5-2.5%) — з
# зовнішнього ревью 2026-08-24 (CLAUDE.md п.32). Застосовність до цієї
# комерційної схеми (учасник ринку РДН, FTM-арбітраж) НЕ підтверджена —
# користувач сам не впевнений, потрібна консультація юриста/бухгалтера.
# Поля лише ЗБЕРІГАЮТЬСЯ в Settings (щоб не втратити число, коли воно
# з'явиться) — 0.0 за замовчуванням, і жоден розрахунок P&L (TariffService/
# milp_model/reporting) їх поки НЕ читає й НЕ застосовує. Підключити
# розрахунок — окрема майбутня задача, після підтвердження застосовності.
DEFAULT_EXCISE_DUTY_PCT = 0.0
DEFAULT_TRANSFORMER_LOSS_PCT = 0.0

@router.get("/settings", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_system_settings():
    """
    launch_date/osr/voltage_class/margin — з JSON (не мають окремого поля в
    Asset). capacity_kw/power_kw/efficiency_pct — З ТАБЛИЦІ Asset, тієї самої,
    яку реально використовують optimization/run і scheduler для розрахунку
    графіка. Раніше ці два джерела були розсинхронізовані: форма показувала
    значення з JSON, а MILP рахував на зовсім інших числах з Asset — диспетчер
    бачив налаштування "4000 кВт-год", а графік будувався на застарілих 1000
    (реальний баг, знайдений і виправлений).
    """
    import json
    import os
    path = os.path.join(settings.DATA_DIR, "system_settings.json")

    data = {
        "launch_date": settings.BESS_LAUNCH_DATE,
        "osr": "dtek_kiev",
        "voltage_class": 1,
        "margin": 100.0,
        "nuclear_reference_capacity_mw": DEFAULT_NUCLEAR_REFERENCE_CAPACITY_MW,
        "hydro_reference_capacity_mw": DEFAULT_HYDRO_REFERENCE_CAPACITY_MW,
        "baseload_passthrough_ratio": DEFAULT_BASELOAD_PASSTHROUGH_RATIO,
        "bid_reminder_telegram_enabled": DEFAULT_BID_REMINDER_TELEGRAM_ENABLED,
        "auto_dispatch_enabled": DEFAULT_AUTO_DISPATCH_ENABLED,
        "excise_duty_pct": DEFAULT_EXCISE_DUTY_PCT,
        "transformer_loss_pct": DEFAULT_TRANSFORMER_LOSS_PCT,
        "bess_connection_type": DEFAULT_BESS_CONNECTION_TYPE,
        "bess_tcp_host": DEFAULT_BESS_TCP_HOST,
        "bess_tcp_port": DEFAULT_BESS_TCP_PORT,
        "bess_serial_port": DEFAULT_BESS_SERIAL_PORT,
        "bess_serial_baudrate": DEFAULT_BESS_SERIAL_BAUDRATE,
        "bess_serial_parity": DEFAULT_BESS_SERIAL_PARITY,
        "bess_serial_stopbits": DEFAULT_BESS_SERIAL_STOPBITS,
        "bess_serial_bytesize": DEFAULT_BESS_SERIAL_BYTESIZE,
        "bess_modbus_unit_id": DEFAULT_BESS_MODBUS_UNIT_ID,
    }

    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                saved = json.load(f)
                for key in ("launch_date", "osr", "voltage_class", "margin", "nuclear_reference_capacity_mw", "hydro_reference_capacity_mw", "baseload_passthrough_ratio", "bid_reminder_telegram_enabled", "auto_dispatch_enabled", "excise_duty_pct", "transformer_loss_pct", "bess_connection_type", "bess_tcp_host", "bess_tcp_port", "bess_serial_port", "bess_serial_baudrate", "bess_serial_parity", "bess_serial_stopbits", "bess_serial_bytesize", "bess_modbus_unit_id"):
                    if key in saved:
                        data[key] = saved[key]
        except Exception:
            pass

    db = SessionLocal()
    try:
        asset = db.query(Asset).first()
        if asset:
            data["capacity_kw"] = asset.capacity_mwh * 1000.0
            data["power_kw"] = asset.power_mw * 1000.0
            data["efficiency_pct"] = ((asset.efficiency_charge + asset.efficiency_discharge) / 2.0) * 100.0
            data["max_cycles_per_day"] = asset.max_cycles_per_day
        else:
            data["capacity_kw"] = 2000.0
            data["power_kw"] = 1000.0
            data["efficiency_pct"] = 95.0
            data["max_cycles_per_day"] = 1.5
    finally:
        db.close()

    return data

@router.post("/settings", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def save_system_settings(req: SystemSettingsModel):
    import json
    import os
    path = os.path.join(settings.DATA_DIR, "system_settings.json")

    db = SessionLocal()
    try:
        data = {
            "launch_date": req.launch_date,
            "osr": req.osr,
            "voltage_class": req.voltage_class,
            "margin": req.margin,
            "nuclear_reference_capacity_mw": req.nuclear_reference_capacity_mw if req.nuclear_reference_capacity_mw is not None else DEFAULT_NUCLEAR_REFERENCE_CAPACITY_MW,
            "hydro_reference_capacity_mw": req.hydro_reference_capacity_mw if req.hydro_reference_capacity_mw is not None else DEFAULT_HYDRO_REFERENCE_CAPACITY_MW,
            "baseload_passthrough_ratio": req.baseload_passthrough_ratio if req.baseload_passthrough_ratio is not None else DEFAULT_BASELOAD_PASSTHROUGH_RATIO,
            "bid_reminder_telegram_enabled": req.bid_reminder_telegram_enabled if req.bid_reminder_telegram_enabled is not None else DEFAULT_BID_REMINDER_TELEGRAM_ENABLED,
            "auto_dispatch_enabled": req.auto_dispatch_enabled if req.auto_dispatch_enabled is not None else DEFAULT_AUTO_DISPATCH_ENABLED,
            "excise_duty_pct": req.excise_duty_pct if req.excise_duty_pct is not None else DEFAULT_EXCISE_DUTY_PCT,
            "transformer_loss_pct": req.transformer_loss_pct if req.transformer_loss_pct is not None else DEFAULT_TRANSFORMER_LOSS_PCT,
            "bess_connection_type": req.bess_connection_type if req.bess_connection_type is not None else DEFAULT_BESS_CONNECTION_TYPE,
            "bess_tcp_host": req.bess_tcp_host if req.bess_tcp_host is not None else DEFAULT_BESS_TCP_HOST,
            "bess_tcp_port": req.bess_tcp_port if req.bess_tcp_port is not None else DEFAULT_BESS_TCP_PORT,
            "bess_serial_port": req.bess_serial_port if req.bess_serial_port is not None else DEFAULT_BESS_SERIAL_PORT,
            "bess_serial_baudrate": req.bess_serial_baudrate if req.bess_serial_baudrate is not None else DEFAULT_BESS_SERIAL_BAUDRATE,
            "bess_serial_parity": req.bess_serial_parity if req.bess_serial_parity is not None else DEFAULT_BESS_SERIAL_PARITY,
            "bess_serial_stopbits": req.bess_serial_stopbits if req.bess_serial_stopbits is not None else DEFAULT_BESS_SERIAL_STOPBITS,
            "bess_serial_bytesize": req.bess_serial_bytesize if req.bess_serial_bytesize is not None else DEFAULT_BESS_SERIAL_BYTESIZE,
            "bess_modbus_unit_id": req.bess_modbus_unit_id if req.bess_modbus_unit_id is not None else DEFAULT_BESS_MODBUS_UNIT_ID,
        }

        # 2026-08-26: раніше цей запис ПОВНІСТЮ перезаписував файл лише
        # відомими цій моделі ключами — реальний баг, знайдений при додаванні
        # virtual_dispatcher_schedule (окремий ендпоінт нижче): будь-яке
        # збереження загальних Settings мовчки стирало б розклад диспетчера.
        # Тепер зберігаємо поверх уже наявного вмісту (read-merge-write),
        # щоб ключі, якими ця модель не керує, лишались недоторканими.
        existing = {}
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    existing = json.load(f)
            except Exception:
                existing = {}
        existing.update(data)

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(existing, f)

        # Технічні параметри батареї — пишемо в Asset, а не тільки в JSON:
        # саме звідти їх бере MILP-оптимізатор (optimization/run, scheduler).
        asset = db.query(Asset).first()
        if asset:
            asset.capacity_mwh = req.capacity_kw / 1000.0
            asset.power_mw = req.power_kw / 1000.0
            eff = req.efficiency_pct / 100.0
            asset.efficiency_charge = eff
            asset.efficiency_discharge = eff
            if req.max_cycles_per_day is not None:
                # Клип 0.5-5.0 — фізична стеля (більше 5 повних циклів/добу
                # для мережевого BESS нереалістично навіть як ліміт-можливість,
                # нижче 0.5 практично забороняє арбітраж).
                asset.max_cycles_per_day = max(0.5, min(5.0, req.max_cycles_per_day))
            db.commit()

        # Invalidate executive cache file
        cache_dir = settings.DATA_DIR
        for file in os.listdir(cache_dir):
            if file.startswith("executive_cache_"):
                try:
                    os.remove(os.path.join(cache_dir, file))
                except:
                    pass

        return {
            "status": "success",
            "message": "System settings saved successfully and cache cleared."
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error saving settings: {str(e)}")
    finally:
        db.close()


class DispatcherScheduleItem(BaseModel):
    action: str
    hour: int
    minute: int
    enabled: bool = True


@router.get("/dispatcher-schedule", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_dispatcher_schedule():
    """
    "Настроюваний сценарій віртуального диспетчера" (2026-08-26) — розклад
    (`virtual_dispatcher_schedule` у system_settings.json) + перелік
    доступних дій із DISPATCHER_ACTIONS (реєстр у scheduler.py — щоб
    фронтенд показував select із людськими назвами, не хардкодив їх
    окремо; розширення реєстру новою дією автоматично зʼявляється тут).
    """
    import json
    import os
    path = os.path.join(settings.DATA_DIR, "system_settings.json")
    schedule = None
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                schedule = json.load(f).get("virtual_dispatcher_schedule")
        except Exception:
            schedule = None
    if not schedule:
        schedule = [
            {"action": action_id, "hour": h, "minute": m, "enabled": True}
            for action_id, (_fn, _label, h, m) in DISPATCHER_ACTIONS.items()
        ]
    return {
        "schedule": schedule,
        "available_actions": [
            {"action": action_id, "label": label, "default_hour": h, "default_minute": m}
            for action_id, (_fn, label, h, m) in DISPATCHER_ACTIONS.items()
        ],
    }


@router.post("/dispatcher-schedule", dependencies=[Depends(RoleChecker(["Operator", "Manager", "Admin"]))])
async def save_dispatcher_schedule(req: List[DispatcherScheduleItem]):
    """Зберігає розклад і одразу перепланує APScheduler-джоби (живе
    застосування, без рестарту сервера — це лише зміна cron-часу)."""
    import json
    import os
    path = os.path.join(settings.DATA_DIR, "system_settings.json")

    unknown = [item.action for item in req if item.action not in DISPATCHER_ACTIONS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Невідомі дії диспетчера: {unknown}")

    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                existing = json.load(f)
        except Exception:
            existing = {}
    existing["virtual_dispatcher_schedule"] = [item.model_dump() for item in req]

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(existing, f)

    reschedule_virtual_dispatcher_jobs()

    return {"status": "success", "schedule": existing["virtual_dispatcher_schedule"]}
