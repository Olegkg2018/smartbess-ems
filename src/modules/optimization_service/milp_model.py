import pulp
import numpy as np
from typing import Dict, Any, List

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
    **bess_params
) -> Dict[str, Any]:
    """
    Solves BESS optimization for Base, Pessimistic, and Aggressive price schedules,
    and runs Monte Carlo simulations to estimate Value at Risk (VaR 95%).
    """
    prices = np.array(prices)
    
    # 1. Generate scenarios
    # Pessimistic: Base price minus 1.64 standard deviations (5% lower limit)
    prices_pess = np.clip(prices * np.exp(-1.64 * volatility), 10.0, 16000.0).tolist()
    # Aggressive: Base price plus 1.64 standard deviations (95% upper limit)
    prices_aggr = np.clip(prices * np.exp(1.64 * volatility), 10.0, 16000.0).tolist()
    prices_base = prices.tolist()
    
    # 2. Run optimizations
    base_res = optimize_battery_schedule(prices_base, **bess_params)
    pess_res = optimize_battery_schedule(prices_pess, **bess_params)
    aggr_res = optimize_battery_schedule(prices_aggr, **bess_params)
    
    # 3. Monte Carlo runs for Value at Risk
    np.random.seed(42)
    sim_profits = []
    
    for _ in range(num_simulations):
        # Generate stochastic prices
        noise = np.random.normal(0, volatility, size=len(prices))
        sim_p = np.clip(prices * np.exp(noise), 10.0, 16000.0).tolist()
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
            "mean_simulated_profit_uah": float(np.mean(sim_profits))
        },
        "scenarios": {
            "base": base_res,
            "pessimistic": pess_res,
            "aggressive": aggr_res
        }
    }
