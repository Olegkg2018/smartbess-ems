import numpy as np
import numpy_financial as npf
from typing import Dict, Any, List

from src.modules.optimization_service.milp_model import optimize_battery_schedule

class ReportingService:
    @classmethod
    def calculate_project_economics(
        cls,
        capex_uah: float,
        yearly_revenue_uah: float,
        yearly_opex_uah: float,
        discount_rate: float = 0.12,
        lifetime_years: int = 10
    ) -> Dict[str, Any]:
        """
        Calculates standard financial viability metrics for the BESS project.
        - NPV (Net Present Value)
        - IRR (Internal Rate of Return)
        - Simple Payback Period
        - Discounted Payback Period
        - ROI (Return on Investment)
        """
        cash_flows = [-capex_uah]
        net_yearly_cf = yearly_revenue_uah - yearly_opex_uah
        
        for _ in range(lifetime_years):
            cash_flows.append(net_yearly_cf)
            
        # Calculate NPV
        npv = npf.npv(discount_rate, cash_flows)
        
        # Calculate IRR
        try:
            irr = npf.irr(cash_flows)
        except:
            irr = 0.0
            
        # Payback period calculation
        simple_payback = capex_uah / net_yearly_cf if net_yearly_cf > 0 else float('inf')
        
        # Discounted payback
        cumulative_discounted_cf = -capex_uah
        discounted_payback = float('inf')
        for year in range(1, lifetime_years + 1):
            discounted_cf = net_yearly_cf / ((1 + discount_rate) ** year)
            cumulative_discounted_cf += discounted_cf
            if cumulative_discounted_cf >= 0 and discounted_payback == float('inf'):
                # Interpolate fraction of year
                prev_cumulative = cumulative_discounted_cf - discounted_cf
                fraction = abs(prev_cumulative) / discounted_cf
                discounted_payback = (year - 1) + fraction
                break
                
        total_net_profit = (net_yearly_cf * lifetime_years) - capex_uah
        roi = (total_net_profit / capex_uah) * 100 if capex_uah > 0 else 0.0
        
        return {
            "capex_uah": capex_uah,
            "yearly_net_cash_flow_uah": net_yearly_cf,
            "total_net_profit_uah": total_net_profit,
            "roi_pct": float(roi),
            "npv_uah": float(npv),
            "irr_pct": float(irr * 100) if irr is not None else 0.0,
            "simple_payback_years": float(simple_payback),
            "discounted_payback_years": float(discounted_payback)
        }

    @classmethod
    def calculate_value_at_risk(
        cls,
        base_prices: List[float],
        battery_capacity: float = 1000.0,
        max_power: float = 250.0,
        initial_soc: float = 0.20,
        min_soc: float = 0.10,
        max_soc: float = 0.90,
        degradation_cost: float = 1.20,
        distribution_tariff: float = 1100.0,
        confidence_level: float = 0.95,
        num_simulations: int = 50
    ) -> Dict[str, Any]:
        """
        Calculates Value at Risk (VaR) of daily BESS arbitrage profit using Monte Carlo simulations.
        1. Simulates stochastic price scenarios based on base forecasted prices and historical volatility.
        2. Solves battery optimization for each scenario to construct the profit distribution.
        3. Returns expected profit, 5th percentile profit (worst case), and VaR at confidence level.
        """
        # Run base optimization to find baseline expected profit
        base_opt = optimize_battery_schedule(
            prices=base_prices,
            battery_capacity=battery_capacity,
            max_charge_power=max_power,
            max_discharge_power=max_power,
            initial_soc=initial_soc,
            min_soc=min_soc,
            max_soc=max_soc,
            degradation_cost=degradation_cost,
            distribution_tariff=distribution_tariff
        )
        base_profit = base_opt['net_profit_uah']
        
        # Volatility parameter (std dev as % of price)
        # Typically around 15% for daily Ukrainian RDN prices
        volatility = 0.18 
        simulated_profits = []
        
        # Run Monte Carlo simulations
        np.random.seed(42)
        for _ in range(num_simulations):
            # Generate stochastic prices (log-normal noise)
            noise = np.random.normal(0, volatility, size=24)
            sim_prices = [max(10.0, p * np.exp(n)) for p, n in zip(base_prices, noise)]
            
            try:
                opt = optimize_battery_schedule(
                    prices=sim_prices,
                    battery_capacity=battery_capacity,
                    max_charge_power=max_power,
                    max_discharge_power=max_power,
                    initial_soc=initial_soc,
                    min_soc=min_soc,
                    max_soc=max_soc,
                    degradation_cost=degradation_cost,
                    distribution_tariff=distribution_tariff
                )
                simulated_profits.append(opt['net_profit_uah'])
            except:
                pass
                
        if not simulated_profits:
            simulated_profits = [base_profit]
            
        simulated_profits = sorted(simulated_profits)
        
        # Determine worst-case threshold at confidence level
        alpha = 1.0 - confidence_level
        pct_idx = int(len(simulated_profits) * alpha)
        worst_case_profit = simulated_profits[pct_idx]
        
        # VaR (Maximum expected loss of arbitrage profit)
        var_uah = base_profit - worst_case_profit
        
        return {
            "base_expected_profit_uah": float(base_profit),
            "worst_case_profit_uah": float(worst_case_profit),
            "value_at_risk_uah": float(var_uah),
            "confidence_level_pct": confidence_level * 100,
            "min_simulated_profit_uah": float(simulated_profits[0]),
            "max_simulated_profit_uah": float(simulated_profits[-1]),
            "mean_simulated_profit_uah": float(np.mean(simulated_profits))
        }

    @classmethod
    def get_executive_summary_report(cls, db, asset_id: str) -> Dict[str, Any]:
        import datetime
        import pandas as pd
        import json
        import os
        from src.core.config import settings
        from src.database.models import Asset, BessTelemetry, ManualOverride
        
        # 1. Fetch BESS asset properties
        asset = db.query(Asset).filter(Asset.id == asset_id).first()
        if not asset:
            # Fallback mock BESS properties
            capacity_mwh = 2.0
            power_mw = 1.0
            eff_charge = 0.95
            eff_discharge = 0.95
            min_soc = 0.10
            max_soc = 0.90
            deg_cost_mwh = 1200.0
            capex_uah = 15000000.0  # e.g., 15M UAH for 1MW/2MWh
        else:
            capacity_mwh = asset.capacity_mwh
            power_mw = asset.power_mw
            eff_charge = asset.efficiency_charge
            eff_discharge = asset.efficiency_discharge
            min_soc = asset.min_soc_pct / 100.0
            max_soc = asset.max_soc_pct / 100.0
            deg_cost_mwh = asset.deg_cost_per_mwh
            capex_uah = power_mw * 15000000.0  # approximate CAPEX based on power (15M UAH per MW)

        # 2. Setup period from launch_date to today
        launch_date_str = settings.BESS_LAUNCH_DATE  # e.g. "2026-01-01"
        settings_path = os.path.join(settings.DATA_DIR, "system_settings.json")
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r") as sf:
                    s_data = json.load(sf)
                    launch_date_str = s_data.get("launch_date", launch_date_str)
            except Exception:
                pass

        try:
            start_date = datetime.datetime.strptime(launch_date_str, "%Y-%m-%d").date()
        except Exception:
            start_date = datetime.date(2026, 1, 1)
            
        today = datetime.date.today()
        if start_date >= today:
            start_date = today - datetime.timedelta(days=30)
            
        # 3. Try to read from cache to make it sub-millisecond fast!
        cache_path = os.path.join(settings.DATA_DIR, f"executive_cache_{asset_id}.json")
        cached_days = {}
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    cached_days = json.load(f)
            except Exception:
                cached_days = {}
                
        # 4. Load historical prices from CSV
        csv_path = os.path.join(settings.DATA_DIR, "historical_data_merged.csv")
        if not os.path.exists(csv_path):
            csv_path = "/home/oleg/agy_energo/data/historical_data_merged.csv"
            
        try:
            df = pd.read_csv(csv_path)
            df['Datetime'] = pd.to_datetime(df['Datetime'])
            df['Date'] = df['Datetime'].dt.date
        except Exception as e:
            print(f"Error loading historical CSV for reporting: {e}")
            df = pd.DataFrame(columns=['Date', 'Datetime', 'Price'])

        # Find which dates in range [start_date, today] need calculation
        dates_to_calculate = []
        curr = start_date
        while curr < today:
            date_str = curr.isoformat()
            if date_str not in cached_days:
                dates_to_calculate.append(curr)
            curr += datetime.timedelta(days=1)
            
        # Calculate new days
        if dates_to_calculate and not df.empty:
            for d in dates_to_calculate:
                d_str = d.isoformat()
                d_start = datetime.datetime.combine(d, datetime.time.min)
                d_end = datetime.datetime.combine(d, datetime.time.max)
                df_day = df[df['Date'] == d].sort_values('Datetime')
                
                # Check manual overrides for this date
                overrides = db.query(ManualOverride).filter(
                    ManualOverride.asset_id == asset_id,
                    ManualOverride.timestamp >= d_start,
                    ManualOverride.timestamp <= d_end
                ).order_by(ManualOverride.timestamp).all()

                # Check telemetry for this date
                telemetry = db.query(BessTelemetry).filter(
                    BessTelemetry.asset_id == asset_id,
                    BessTelemetry.timestamp >= d_start,
                    BessTelemetry.timestamp <= d_end
                ).order_by(BessTelemetry.timestamp).all()

                # Default assumptions for Ukrainian tariffs (UAH/MWh)
                transmission_tariff = 528.57
                distribution_tariff = 1500.0
                dispatch_tariff = 104.57
                supplier_margin = 100.0
                total_tariffs_kwh = (transmission_tariff + distribution_tariff + dispatch_tariff + supplier_margin) / 1000.0

                if overrides:
                    # Daily Actual P&L from manual overrides
                    actual_profit = 0.0
                    total_discharge_kwh = 0.0
                    charge_cost = 0.0
                    discharge_rev = 0.0
                    for o in overrides:
                        price_kwh = o.price_uah / 1000.0
                        power_kw = o.power_mw * 1000.0
                        
                        if power_kw > 0: # discharge (sell)
                            val = power_kw * price_kwh
                            actual_profit += val
                            discharge_rev += val
                            total_discharge_kwh += power_kw
                        elif power_kw < 0: # charge (buy)
                            val = abs(power_kw) * (price_kwh + total_tariffs_kwh)
                            actual_profit -= val
                            charge_cost += val
                            
                    degr_cost_day = total_discharge_kwh * (deg_cost_mwh / 1000.0)
                    actual_profit -= degr_cost_day
                    
                    cached_days[d_str] = {
                        "optimal_profit_uah": float(actual_profit),
                        "actual_profit_uah": float(actual_profit),
                        "degradation_cost_uah": float(degr_cost_day),
                        "charge_cost_uah": float(charge_cost),
                        "discharge_revenue_uah": float(discharge_rev),
                        "status": "actual",
                        "accuracy_rate": 1.0
                    }
                elif telemetry and len(telemetry) >= 12:
                    # Actual BESS execution P&L from telemetry
                    actual_profit = 0.0
                    total_discharge_kwh = 0.0
                    charge_cost = 0.0
                    discharge_rev = 0.0
                    for tel in telemetry:
                        hour = tel.timestamp.hour
                        price_rows = df_day[df_day['Datetime'].dt.hour == hour]
                        price_uah = price_rows['Price'].values[0] if not price_rows.empty else 3000.0
                        price_kwh = price_uah / 1000.0
                        
                        power_kw = tel.current_power_mw * 1000.0
                        if power_kw > 0: # sell
                            val = power_kw * price_kwh
                            actual_profit += val
                            discharge_rev += val
                            total_discharge_kwh += power_kw
                        elif power_kw < 0: # buy
                            val = abs(power_kw) * (price_kwh + total_tariffs_kwh)
                            actual_profit -= val
                            charge_cost += val
                            
                    # Degradation cost
                    degr_cost_day = total_discharge_kwh * (deg_cost_mwh / 1000.0)
                    actual_profit -= degr_cost_day
                    
                    cached_days[d_str] = {
                        "optimal_profit_uah": float(actual_profit),
                        "actual_profit_uah": float(actual_profit),
                        "degradation_cost_uah": float(degr_cost_day),
                        "charge_cost_uah": float(charge_cost),
                        "discharge_revenue_uah": float(discharge_rev),
                        "status": "actual",
                        "accuracy_rate": 1.0
                    }
                else:
                    # Simulated optimal backtesting dispatch
                    if len(df_day) >= 24:
                        day_prices = df_day['Price'].tolist()
                        
                        try:
                            opt_res = optimize_battery_schedule(
                                prices=day_prices,
                                battery_capacity=capacity_mwh * 1000.0,
                                max_charge_power=power_mw * 1000.0,
                                max_discharge_power=power_mw * 1000.0,
                                charge_efficiency=eff_charge,
                                discharge_efficiency=eff_discharge,
                                initial_soc=0.20,
                                min_soc=min_soc,
                                max_soc=max_soc,
                                degradation_cost=deg_cost_mwh / 1000.0,
                                transmission_tariff=transmission_tariff,
                                distribution_tariff=distribution_tariff,
                                dispatch_tariff=dispatch_tariff,
                                supplier_margin=supplier_margin
                            )
                            opt_profit = opt_res.get('net_profit_uah', 0.0)
                            
                            # Apply 80% accuracy constraint (forecast error multiplier)
                            actual_simulation_profit = opt_profit * 0.80
                            degr_cost_day = sum(opt_res.get('discharge', [])) * (deg_cost_mwh / 1000.0)
                            
                            sim_charge_cost = 0.0
                            sim_discharge_revenue = 0.0
                            charge_list = opt_res.get('charge', [])
                            discharge_list = opt_res.get('discharge', [])
                            for h in range(len(day_prices)):
                                pr_kwh = day_prices[h] / 1000.0
                                if h < len(charge_list) and charge_list[h] > 0:
                                    sim_charge_cost += charge_list[h] * (pr_kwh + total_tariffs_kwh)
                                if h < len(discharge_list) and discharge_list[h] > 0:
                                    sim_discharge_revenue += discharge_list[h] * pr_kwh

                            cached_days[d_str] = {
                                "optimal_profit_uah": float(opt_profit),
                                "actual_profit_uah": float(actual_simulation_profit),
                                "degradation_cost_uah": float(degr_cost_day),
                                "charge_cost_uah": float(sim_charge_cost * 0.80),
                                "discharge_revenue_uah": float(sim_discharge_revenue * 0.80),
                                "status": "simulated",
                                "accuracy_rate": 0.80
                            }
                        except Exception as oe:
                            print(f"Error solving optimization for date {d_str}: {oe}")
                            cached_days[d_str] = {
                                "optimal_profit_uah": 0.0,
                                "actual_profit_uah": 0.0,
                                "degradation_cost_uah": 0.0,
                                "charge_cost_uah": 0.0,
                                "discharge_revenue_uah": 0.0,
                                "status": "simulated",
                                "accuracy_rate": 0.80
                            }
                    else:
                        cached_days[d_str] = {
                            "optimal_profit_uah": 0.0,
                            "actual_profit_uah": 0.0,
                            "degradation_cost_uah": 0.0,
                            "charge_cost_uah": 0.0,
                            "discharge_revenue_uah": 0.0,
                            "status": "simulated",
                            "accuracy_rate": 0.80
                        }
            
            # Save updated cache
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, "w") as f:
                    json.dump(cached_days, f)
            except Exception as se:
                print(f"Error writing executive cache: {se}")

        # 5. Compile daily history lists
        history_list = []
        cumulative_simulated = 0.0
        cumulative_actual = 0.0
        total_degradation = 0.0
        
        sorted_dates = sorted(cached_days.keys())
        for d_str in sorted_dates:
            day_data = cached_days[d_str]
            cumulative_simulated += day_data["optimal_profit_uah"]
            cumulative_actual += day_data["actual_profit_uah"]
            total_degradation += day_data["degradation_cost_uah"]
            
            history_list.append({
                "date": d_str,
                "optimal_profit_uah": day_data["optimal_profit_uah"],
                "actual_profit_uah": day_data["actual_profit_uah"],
                "cumulative_actual_profit_uah": cumulative_actual,
                "charge_cost_uah": day_data.get("charge_cost_uah", 0.0),
                "discharge_revenue_uah": day_data.get("discharge_revenue_uah", 0.0),
                "degradation_cost_uah": day_data.get("degradation_cost_uah", 0.0),
                "status": day_data["status"],
                "accuracy_rate": day_data["accuracy_rate"]
            })
            
        # 6. Forecast until end of year (ROY)
        last_30_actual = [cached_days[k]["actual_profit_uah"] for k in sorted_dates[-30:]] if len(sorted_dates) >= 30 else [cached_days[k]["actual_profit_uah"] for k in sorted_dates]
        avg_daily_actual = np.mean(last_30_actual) if last_30_actual else 8500.0
        if np.isnan(avg_daily_actual):
            avg_daily_actual = 8500.0
            
        end_of_year = datetime.date(today.year, 12, 31)
        remaining_days = max(0, (end_of_year - today).days)
        projected_roy_profit = remaining_days * avg_daily_actual
        
        total_annual_profit_projected = cumulative_actual + projected_roy_profit
        
        # 7. Generate Payback Trajectory Curve
        trajectory = []
        running_payback = -capex_uah
        
        for idx, d_str in enumerate(sorted_dates):
            running_payback += cached_days[d_str]["actual_profit_uah"]
            trajectory.append({
                "date": d_str,
                "cumulative_p_l_uah": float(running_payback),
                "is_projected": False
            })
            
        proj_day = today
        max_proj_days = 365 * 4
        proj_count = 0
        
        while running_payback < 0 and proj_count < max_proj_days:
            proj_day += datetime.timedelta(days=1)
            running_payback += avg_daily_actual
            proj_count += 1
            if proj_count % 7 == 0 or running_payback >= 0:
                trajectory.append({
                    "date": proj_day.isoformat(),
                    "cumulative_p_l_uah": float(running_payback),
                    "is_projected": True
                })
                
        payback_years = len(sorted_dates) / 365.0 + proj_count / 365.0
        
        return {
            "launch_date": launch_date_str,
            "bess_properties": {
                "capacity_mwh": capacity_mwh,
                "power_mw": power_mw,
                "degradation_cost_per_mwh": deg_cost_mwh,
                "estimated_capex_uah": capex_uah
            },
            "ytd_metrics": {
                "optimal_forecast_profit_ytd_uah": cumulative_simulated,
                "actual_p_l_ytd_uah": cumulative_actual,
                "degradation_amortization_ytd_uah": total_degradation,
                "average_daily_profit_uah": avg_daily_actual,
                "days_in_operation": len(sorted_dates)
            },
            "roy_forecast": {
                "remaining_days": remaining_days,
                "projected_roy_profit_uah": projected_roy_profit,
                "total_annual_profit_projected_uah": total_annual_profit_projected,
                "estimated_payback_years": float(payback_years)
            },
            "daily_history": history_list,
            "payback_trajectory": trajectory
        }
