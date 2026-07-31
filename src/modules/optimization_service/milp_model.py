import pulp
import numpy as np
from typing import Dict, Any, List

from src.modules.tariff_service.services import TariffService

PRICE_FLOOR = TariffService.PRICE_FLOOR_UAH_MWH

def optimize_battery_schedule(
    prices: List[float],
    battery_capacity: float = 1000.0,      # kWh
    max_charge_power: float = 250.0,       # kW
    max_discharge_power: float = 250.0,    # kW
    charge_efficiency: float = 0.95,       # 0.0 to 1.0
    discharge_efficiency: float = 0.95,    # 0.0 to 1.0
    initial_soc: float = 0.20,             # 0.0 to 1.0 (fraction of capacity)
    min_soc: float = 0.10,                 # 0.0 to 1.0 (fraction of capacity)
    max_soc: float = 0.90,                 # 0.0 to 1.0 (fraction of capacity)
    max_cycles_per_day: float = 1.5,       # equivalent full cycles
    degradation_cost: float = 1.20,        # UAH per kWh discharged
    transmission_tariff: float = 528.57,   # UAH/MWh
    distribution_tariff: float = 1500.0,   # UAH/MWh
    dispatch_tariff: float = 104.57,       # UAH/MWh
    supplier_margin: float = 100.0,        # UAH/MWh
    mode: str = 'arbitrage'                 # 'arbitrage' (FTM) or 'self_consumption' (BTM)
) -> Dict[str, Any]:
    """
    Optimizes battery charging and discharging schedule using PuLP MILP solver.
    Works for arbitrary horizon T = len(prices) (e.g. 24 - 168 hours).
    """
    T = len(prices)
    if T == 0:
        return {}

    # 1. Initialize LP problem
    prob = pulp.LpProblem("Battery_Schedule_Optimization", pulp.LpMaximize)
    
    # 2. Decision variables
    x = pulp.LpVariable.dicts("Charge", range(T), lowBound=0, upBound=max_charge_power)
    y = pulp.LpVariable.dicts("Discharge", range(T), lowBound=0, upBound=max_discharge_power)
    soc = pulp.LpVariable.dicts("SoC", range(T), lowBound=min_soc * battery_capacity, upBound=max_soc * battery_capacity)
    u = pulp.LpVariable.dicts("IsCharging", range(T), cat='Binary')
    
    # Total tariffs in UAH/MWh
    total_tariffs = transmission_tariff + distribution_tariff + dispatch_tariff + supplier_margin
    total_tariffs_kwh = total_tariffs / 1000.0
    
    p_buy = []
    p_sell = []
    
    for t in range(T):
        dam_kwh = prices[t] / 1000.0
        p_buy.append(dam_kwh + total_tariffs_kwh)
        
        if mode == 'arbitrage':
            p_sell.append(dam_kwh)
        else: # self_consumption / behind the meter
            p_sell.append(dam_kwh + total_tariffs_kwh)
            
    # 3. Constraints
    init_soc_kwh = initial_soc * battery_capacity
    prob += soc[0] == init_soc_kwh + x[0] * charge_efficiency - y[0] / discharge_efficiency, "SoC_Hour_0"
    
    for t in range(1, T):
        prob += soc[t] == soc[t-1] + x[t] * charge_efficiency - y[t] / discharge_efficiency, f"SoC_Hour_{t}"
        
    for t in range(T):
        prob += x[t] <= max_charge_power * u[t], f"Charge_Binary_Limit_{t}"
        prob += y[t] <= max_discharge_power * (1 - u[t]), f"Discharge_Binary_Limit_{t}"
        
    total_discharge = pulp.lpSum([y[t] for t in range(T)])
    # Adjust cycle limit based on horizon T (e.g. 1.5 cycles per 24 hours)
    cycle_limit = max_cycles_per_day * battery_capacity * (T / 24.0)
    prob += total_discharge <= cycle_limit, "Cycle_Limit"
    
    # Final SoC constraint: battery should end the day discharged completely to min_soc
    prob += soc[T-1] == min_soc * battery_capacity, "Final_SoC_Balance"
    
    # 4. Objective Function: Maximize net financial benefit
    revenue = pulp.lpSum([p_sell[t] * y[t] for t in range(T)])
    cost_charging = pulp.lpSum([p_buy[t] * x[t] for t in range(T)])
    cost_degradation = pulp.lpSum([degradation_cost * y[t] for t in range(T)])
    
    prob += revenue - cost_charging - cost_degradation
    
    # 5. Solve the LP problem
    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    
    charge_schedule = [x[t].varValue if x[t].varValue is not None else 0.0 for t in range(T)]
    discharge_schedule = [y[t].varValue if y[t].varValue is not None else 0.0 for t in range(T)]
    soc_schedule = [soc[t].varValue if soc[t].varValue is not None else init_soc_kwh for t in range(T)]
    
    hourly_p_l = []
    schedule_details = []
    for t in range(T):
        ch = charge_schedule[t]
        dis = discharge_schedule[t]
        
        # Calculate hourly P&L in UAH
        p_l = p_sell[t] * dis - p_buy[t] * ch - degradation_cost * dis
        hourly_p_l.append(p_l)
        
        action = "STANDBY"
        if ch > 0.1:
            action = "CHARGE"
        elif dis > 0.1:
            action = "DISCHARGE"
            
        schedule_details.append({
            "hour": t,
            "action": action,
            "power_kw": -ch if ch > 0 else dis,
            "soc_kwh": soc_schedule[t],
            "price_forecast_uah_mwh": prices[t],
            "hourly_p_l_uah": float(p_l)
        })
        
    net_profit = sum(hourly_p_l)
    actual_cycles = sum(discharge_schedule) / battery_capacity
    
    total_cost_charging = sum(p_buy[t] * charge_schedule[t] for t in range(T))
    total_revenue_discharging = sum(p_sell[t] * discharge_schedule[t] for t in range(T))
    total_degradation = sum(degradation_cost * discharge_schedule[t] for t in range(T))
    
    return {
        'status': pulp.LpStatus[status],
        'charge': charge_schedule,
        'discharge': discharge_schedule,
        'soc': [init_soc_kwh] + soc_schedule,
        'schedule': schedule_details,
        'net_profit_uah': float(net_profit),
        'cost_charging_uah': float(total_cost_charging),
        'revenue_discharging_uah': float(total_revenue_discharging),
        'degradation_cost_uah': float(total_degradation),
        'cycles_used': float(actual_cycles),
        'hourly_buy_prices_mwh': [float(p) * 1000.0 for p in p_buy],
        'hourly_sell_prices_mwh': [float(p) * 1000.0 for p in p_sell],
        'hourly_buy_prices': [float(p) * 1000.0 for p in p_buy],
        'hourly_sell_prices': [float(p) * 1000.0 for p in p_sell]
    }

def optimize_with_scenarios_and_risks(
    prices: List[float],
    volatility: float = 0.18,
    num_simulations: int = 30,
    confidence_level: float = 0.95,
    price_lower: List[float] = None,
    price_upper: List[float] = None,
    **bess_params
) -> Dict[str, Any]:
    """
    Solves BESS optimization for Base, Pessimistic, and Aggressive price schedules,
    and runs Monte Carlo simulations to estimate Value at Risk (VaR 95%).

    Якщо передано price_lower/price_upper (P10/P90, conformal-калібрований
    інтервал з ml_pipeline.predict_price_band — Фаза 3), Pessimistic/Aggressive
    сценарії будуються на РЕАЛЬНИХ квантилях моделі, а не на вигаданому
    припущенні про волатильність ±1.64σ. Ефективна волатильність для Monte
    Carlo теж виводиться з реальної ширини інтервалу (погодинно, тобто
    гетероскедастично), а не з фіксованої константи.
    """
    prices = np.array(prices)
    band_source = 'assumed_volatility'
    hourly_volatility = np.full(len(prices), volatility)
    PRICE_CAP = 16000.0

    if price_lower is not None and price_upper is not None and len(price_lower) == len(prices) and len(price_upper) == len(prices):
        prices_pess = np.clip(np.array(price_lower, dtype=float), PRICE_FLOOR, PRICE_CAP).tolist()
        prices_aggr = np.clip(np.array(price_upper, dtype=float), PRICE_FLOOR, PRICE_CAP).tolist()
        band_source = 'quantile_model_p10_p90'

        # z-score for the 90th percentile of a standard normal (~1.2816):
        # backs out an implied per-hour volatility from the real P90/point ratio,
        # so hours with a genuinely wider model interval get a wider Monte Carlo spread.
        with np.errstate(divide='ignore', invalid='ignore'):
            implied = np.abs(np.log(np.clip(np.array(price_upper, dtype=float), PRICE_FLOOR, PRICE_CAP) / np.maximum(prices, 1.0))) / 1.2816
        hourly_volatility = np.nan_to_num(implied, nan=volatility, posinf=volatility, neginf=volatility)
        hourly_volatility = np.clip(hourly_volatility, 0.03, 1.0)
    else:
        # Fallback: no real quantile band supplied — theoretical assumption, as before.
        prices_pess = np.clip(prices * np.exp(-1.64 * volatility), PRICE_FLOOR, PRICE_CAP).tolist()
        prices_aggr = np.clip(prices * np.exp(1.64 * volatility), PRICE_FLOOR, PRICE_CAP).tolist()

    prices_base = prices.tolist()

    # 2. Run optimizations
    base_res = optimize_battery_schedule(prices_base, **bess_params)
    pess_res = optimize_battery_schedule(prices_pess, **bess_params)
    aggr_res = optimize_battery_schedule(prices_aggr, **bess_params)

    # 3. Monte Carlo runs for Value at Risk
    np.random.seed(42)
    sim_profits = []

    for _ in range(num_simulations):
        # Generate stochastic prices (per-hour volatility, real or assumed)
        noise = np.random.normal(0, 1.0, size=len(prices)) * hourly_volatility
        sim_p = np.clip(prices * np.exp(noise), PRICE_FLOOR, PRICE_CAP).tolist()
        try:
            res = optimize_battery_schedule(sim_p, **bess_params)
            sim_profits.append(res['net_profit_uah'])
        except:
            pass

    if not sim_profits:
        sim_profits = [base_res['net_profit_uah']]

    sim_profits = sorted(sim_profits)

    # Calculate VaR
    alpha = 1.0 - confidence_level
    pct_idx = int(len(sim_profits) * alpha)
    worst_case_profit = sim_profits[pct_idx]
    var_value = base_res['net_profit_uah'] - worst_case_profit

    return {
        "summary": {
            "status": base_res['status'],
            "base_expected_profit_uah": base_res['net_profit_uah'],
            "worst_case_profit_uah": worst_case_profit,
            "value_at_risk_uah": max(0.0, var_value),
            "confidence_level_pct": confidence_level * 100,
            "mean_simulated_profit_uah": float(np.mean(sim_profits)),
            "price_band_source": band_source
        },
        "scenarios": {
            "base": base_res,
            "pessimistic": pess_res,
            "aggressive": aggr_res
        }
    }

def evaluate_schedule_profit(
    charge_kw: List[float],
    discharge_kw: List[float],
    prices: List[float],
    degradation_cost: float = 1.20,
    transmission_tariff: float = 528.57,
    distribution_tariff: float = 1500.0,
    dispatch_tariff: float = 104.57,
    supplier_margin: float = 100.0,
    mode: str = 'arbitrage',
) -> float:
    """
    Рахує грошовий P&L (грн) для ВЖЕ ВІДОМОГО графіка заряду/розряду
    (наприклад, реально виконаного диспетчерського плану) за заданою ціною —
    та сама формула ціноутворення (p_buy/p_sell/тарифи/деградація), що й
    усередині `optimize_battery_schedule`, винесена окремо, щоб порівнювати
    "що реально відбулось" із "що дав би MILP" на однакових цінах (perfect
    foresight capture ratio, forecast_accuracy.py) без дублювання формули.
    """
    total_tariffs_kwh = (transmission_tariff + distribution_tariff + dispatch_tariff + supplier_margin) / 1000.0
    profit = 0.0
    for t in range(len(prices)):
        dam_kwh = prices[t] / 1000.0
        p_buy = dam_kwh + total_tariffs_kwh
        p_sell = dam_kwh if mode == 'arbitrage' else dam_kwh + total_tariffs_kwh
        profit += p_sell * discharge_kw[t] - p_buy * charge_kw[t] - degradation_cost * discharge_kw[t]
    return float(profit)


def optimize_battery_schedule_dam_idm(
    dam_prices: List[float],
    idm_prices: List[float],
    battery_capacity: float = 1000.0,
    max_charge_power: float = 250.0,
    max_discharge_power: float = 250.0,
    charge_efficiency: float = 0.95,
    discharge_efficiency: float = 0.95,
    initial_soc: float = 0.20,
    min_soc: float = 0.10,
    max_soc: float = 0.90,
    max_cycles_per_day: float = 1.5,
    degradation_cost: float = 1.20,
    transmission_tariff: float = 528.57,
    distribution_tariff: float = 1500.0,
    dispatch_tariff: float = 104.57,
    supplier_margin: float = 100.0,
    mode: str = 'arbitrage',
    idm_headroom_fraction: float = 0.3,
) -> Dict[str, Any]:
    """
    Двухступенчатая co-оптимизация РДН (day-ahead) + ВДР (intraday). Экспериментальная
    функция — см. MEMORY.md §6a пункт 1. НЕ подключена к продовому диспетчеру
    (scheduler.py/optimization.py), пока не подтверждена бэктестом на реальных
    исторических парах РДН/ВДР (см. scripts/backtest_dam_idm.py в scratch).

    Модель:
    1. Ступень 1 — обычный `optimize_battery_schedule` на РДН-ценах даёт
       "закоммиченный" график (dam_charge/dam_discharge) — то, что реально
       было бы подано заявкой на РДН за день до поставки.
    2. Ступень 2 — физический график МОЖЕТ отклониться от закоммиченного в
       пределах `idm_headroom_fraction` от максимальной потужності заряду/
       розряду за годину (ковзне вікно навколо dam_charge[t]/dam_discharge[t],
       а не повна свобода) — відхилення розраховується за ціною ВДР.

    Математично (виведено і перевірено окремо, без вигаданих припущень):
    сумарний прибуток = K + V, де
      K = Σ (dam_kwh[t] - idm_kwh[t]) * (dam_discharge[t] - dam_charge[t])
          — константа, вартість вже зафіксованої РДН-позиції, не залежить
          від фізичного рішення ступеня 2.
      V = прибуток ступеня 2, порахований РІВНО ТІЄЮ Ж формулою, що й
          `optimize_battery_schedule`, але з цінами ВДР і звуженими (headroom-
          capped) межами заряду/розряду навколо РДН-графіка.
    Тобто ступінь 2 — це технічно виклик тієї самої MILP-моделі з ціною ВДР
    замість РДН плюс індивідуальні верхня/нижня межі на годину; переписувати
    структуру обмежень не довелось.

    ВАЖЛИВЕ ДОПУЩЕННЯ (чесно, не приховане): модель припускає, що будь-яке
    відхилення від РДН-графіка розраховується за єдиною погодинною ціною ВДР
    без окремого штрафу за небаланс і без обмеження ліквідності понад
    `idm_headroom_fraction`. Реальні правила небалансів/балансуючого ринку
    в Україні для конкретного учасника можуть бути суворішими (асиметричний
    штраф, обмежений доступ) — це НЕ перевірено проти реальної нормативки
    оператора ринку, лише проти реальних історичних цін ВДР/РДН.
    `idm_headroom_fraction=0.3` — свідомо консервативне значення за
    замовчуванням, а не 1.0 (повна свобода), саме через цю невизначеність.

    РЕЗУЛЬТАТ ПЕРЕВІРКИ (оракульний бектест, `scratch/backtest_dam_idm.py`,
    300 реальних діб рівномірно з 2021-2026, РЕАЛЬНІ фактичні ціни ВДР "заднім
    числом" — тобто найкращий можливий випадок, прогнозна версія дасть менше):
    приріст прибутку над чистим РДН-арбітражем виявився МАЛИМ — +0.4% при
    консервативному idm_headroom_fraction=0.3 (7.6 грн/добу з ~1910 грн/добу
    бази), +1.2% навіть при повністю необмеженому headroom=1.0 (23 грн/добу).
    Перевірка коректності моделі підтверджена (headroom=0.0 дає приріст РІВНО
    0 на всіх 300 добах; приріст НІКОЛИ не від'ємний за жодного headroom —
    саме так і має бути математично). Але сам ефект замалий, щоб виправдати
    додану складність і ризик небалансів для активу такого розміру
    (250кВт/1МВт·год) — ВДР в Україні, судячи з реальних даних, надто
    корелює з РДН структурно, щоб давати істотний ДОДАТКОВИЙ арбітраж понад
    те, що вже бере чистий РДН-оптимізатор. НЕ підключено до продового
    диспетчера через це — функція лишається доступною, якщо колись
    переглядати підхід (напр. разом із резервами/балансуючим ринком, а не
    ізольовано ВДР-vs-РДН, або для суттєво більшого активу).
    """
    T = len(dam_prices)
    if T == 0 or len(idm_prices) != T:
        return {}

    dam_result = optimize_battery_schedule(
        dam_prices,
        battery_capacity=battery_capacity, max_charge_power=max_charge_power,
        max_discharge_power=max_discharge_power, charge_efficiency=charge_efficiency,
        discharge_efficiency=discharge_efficiency, initial_soc=initial_soc,
        min_soc=min_soc, max_soc=max_soc, max_cycles_per_day=max_cycles_per_day,
        degradation_cost=degradation_cost, transmission_tariff=transmission_tariff,
        distribution_tariff=distribution_tariff, dispatch_tariff=dispatch_tariff,
        supplier_margin=supplier_margin, mode=mode,
    )
    if not dam_result:
        return {}

    dam_charge = dam_result['charge']
    dam_discharge = dam_result['discharge']

    total_tariffs = transmission_tariff + distribution_tariff + dispatch_tariff + supplier_margin
    total_tariffs_kwh = total_tariffs / 1000.0

    dam_kwh = [p / 1000.0 for p in dam_prices]
    idm_kwh = [p / 1000.0 for p in idm_prices]

    # K — вартість уже зафіксованої РДН-позиції відносно ціни ВДР, константа.
    k_offset = sum((dam_kwh[t] - idm_kwh[t]) * (dam_discharge[t] - dam_charge[t]) for t in range(T))

    headroom_charge = idm_headroom_fraction * max_charge_power
    headroom_discharge = idm_headroom_fraction * max_discharge_power

    prob = pulp.LpProblem("Battery_DAM_IDM_Stage2", pulp.LpMaximize)

    x2, y2, soc2, u2 = {}, {}, {}, {}
    for t in range(T):
        x2[t] = pulp.LpVariable(f"Charge2_{t}", lowBound=max(0.0, dam_charge[t] - headroom_charge),
                                 upBound=min(max_charge_power, dam_charge[t] + headroom_charge))
        y2[t] = pulp.LpVariable(f"Discharge2_{t}", lowBound=max(0.0, dam_discharge[t] - headroom_discharge),
                                 upBound=min(max_discharge_power, dam_discharge[t] + headroom_discharge))
        soc2[t] = pulp.LpVariable(f"SoC2_{t}", lowBound=min_soc * battery_capacity, upBound=max_soc * battery_capacity)
        u2[t] = pulp.LpVariable(f"IsCharging2_{t}", cat='Binary')

    init_soc_kwh = initial_soc * battery_capacity
    prob += soc2[0] == init_soc_kwh + x2[0] * charge_efficiency - y2[0] / discharge_efficiency, "SoC2_Hour_0"
    for t in range(1, T):
        prob += soc2[t] == soc2[t - 1] + x2[t] * charge_efficiency - y2[t] / discharge_efficiency, f"SoC2_Hour_{t}"

    for t in range(T):
        prob += x2[t] <= max_charge_power * u2[t], f"Charge2_Binary_Limit_{t}"
        prob += y2[t] <= max_discharge_power * (1 - u2[t]), f"Discharge2_Binary_Limit_{t}"

    cycle_limit = max_cycles_per_day * battery_capacity * (T / 24.0)
    prob += pulp.lpSum([y2[t] for t in range(T)]) <= cycle_limit, "Cycle2_Limit"
    prob += soc2[T - 1] == min_soc * battery_capacity, "Final2_SoC_Balance"

    p_buy_idm, p_sell_idm = [], []
    for t in range(T):
        p_buy_idm.append(idm_kwh[t] + total_tariffs_kwh)
        p_sell_idm.append(idm_kwh[t] if mode == 'arbitrage' else idm_kwh[t] + total_tariffs_kwh)

    revenue2 = pulp.lpSum([p_sell_idm[t] * y2[t] for t in range(T)])
    cost2_charging = pulp.lpSum([p_buy_idm[t] * x2[t] for t in range(T)])
    cost2_degradation = pulp.lpSum([degradation_cost * y2[t] for t in range(T)])
    prob += revenue2 - cost2_charging - cost2_degradation

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))

    charge2_schedule = [x2[t].varValue if x2[t].varValue is not None else dam_charge[t] for t in range(T)]
    discharge2_schedule = [y2[t].varValue if y2[t].varValue is not None else dam_discharge[t] for t in range(T)]
    soc2_schedule = [soc2[t].varValue if soc2[t].varValue is not None else init_soc_kwh for t in range(T)]

    v_stage2 = sum(
        p_sell_idm[t] * discharge2_schedule[t] - p_buy_idm[t] * charge2_schedule[t] - degradation_cost * discharge2_schedule[t]
        for t in range(T)
    )
    total_profit = k_offset + v_stage2

    idm_adjustment_mwh = [(charge2_schedule[t] - dam_charge[t], discharge2_schedule[t] - dam_discharge[t]) for t in range(T)]

    return {
        'status': pulp.LpStatus[status],
        'dam_stage': {
            'charge': dam_charge,
            'discharge': dam_discharge,
            'net_profit_uah': dam_result['net_profit_uah'],
        },
        'final_charge': charge2_schedule,
        'final_discharge': discharge2_schedule,
        'final_soc': [init_soc_kwh] + soc2_schedule,
        'idm_deviation_charge_kw': [d[0] for d in idm_adjustment_mwh],
        'idm_deviation_discharge_kw': [d[1] for d in idm_adjustment_mwh],
        'dam_locked_value_uah': float(k_offset),
        'idm_stage_value_uah': float(v_stage2),
        'total_net_profit_uah': float(total_profit),
        'dam_only_net_profit_uah': float(dam_result['net_profit_uah']),
        'uplift_vs_dam_only_uah': float(total_profit - dam_result['net_profit_uah']),
    }
