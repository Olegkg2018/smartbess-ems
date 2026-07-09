# Mathematical Optimization Design Document: BESS Arbitrage in Ukraine
## Автор: Operations Research & Energy Optimization Engineer

---

## 1. Постановка задачи и доменная специфика Украины

Оптимизация работы системы накопления электроэнергии (BESS) в Украине имеет две основные конфигурации:
1.  **Front-of-the-Meter (FTM / Арбитраж в сети)**: Закупка электроэнергии из сети по ценам РДН + тарифы, продажа обратно в сеть чисто по цене РДН.
2.  **Behind-the-Meter (BTM / Промышленный потребитель)**: BESS устанавливается на стороне потребителя. Заряд происходит в дешевые часы (снижение закупки из сети по РДН + тарифы), разряд — во время пиков собственного потребления (замещение закупки по высоким ценам РДН + тарифы).

Математическая модель должна максимизировать экономический эффект на скользящем горизонте от 24 до 168 часов (Model Predictive Control).

---

## 2. Математическое описание модели

### 2.1. Индексы и параметры

*   $t \in \{0, 1, \dots, T-1\}$ — дискретные временные интервалы (часы), где $T \in [24, 168]$.
*   $\Delta t = 1.0$ — продолжительность интервала (1 час).
*   $DAM_t$ — прогнозная цена рынка на сутки вперед (РДН) в час $t$ (UAH/MWh).
*   $Tariffs_t$ — суммарный неэнергетический тариф в час $t$ (UAH/MWh).
    $$Tariffs_t = \text{Transmission} + \text{Distribution}_t + \text{Dispatch} + \text{SupplierMargin}$$
*   $E_{cap}$ — номинальная емкость BESS (кВт·ч).
*   $P_{max}^{ch}, P_{max}^{dis}$ — максимальная мощность заряда и разряда (кВт).
*   $\eta_{ch}, \eta_{dis}$ — коэффициент полезного действия (КПД) заряда и разряда BESS ($0 < \eta < 1$).
*   $SoC_{init}$ — начальный уровень заряда BESS в долях от емкости ($0 \le SoC_{init} \le 1$).
*   $SoC_{min}, SoC_{max}$ — минимальный и максимальный допустимые уровни заряда ($0 \le SoC_{min} < SoC_{max} \le 1$).
*   $C_{deg}$ — удельная стоимость деградации батареи на единицу разряда (UAH/kWh).
*   $N_{cycles}$ — максимальный лимит эквивалентных полных циклов заряда/разряда в сутки.

### 2.2. Переменные решения (Decision Variables)

*   $x_t \ge 0$ — мощность заряда BESS в час $t$ (кВт).
*   $y_t \ge 0$ — мощность разряда BESS в час $t$ (кВт).
*   $SoC_t \ge 0$ — энергия, запасенная в BESS на конец часа $t$ (кВт·ч).
*   $u_t \in \{0, 1\}$ — бинарная переменная: $1$, если BESS заряжается в час $t$; $0$, если разряжается или простаивает.

### 2.3. Входные цены (покупка / продажа) в UAH/kWh

*   **Цена покупки (заряд)**:
    $$P_{buy, t} = \frac{DAM_t + Tariffs_t}{1000}$$
*   **Цена продажи (разряд)**:
    $$P_{sell, t} = \begin{cases}
    \frac{DAM_t}{1000}, & \text{для FTM (Арбитраж в сеть)} \\
    \frac{DAM_t + Tariffs_t}{1000}, & \text{для BTM (Собственное потребление)}
    \end{cases}$$

---

### 2.4. Целевая функция (Objective Function)

Максимизация чистой прибыли от операций BESS за вычетом затрат на заряд и амортизацию (деградацию) ячеек:

$$\max_{x, y, SoC, u} \sum_{t=0}^{T-1} \left( P_{sell, t} \cdot y_t - P_{buy, t} \cdot x_t - C_{deg} \cdot y_t \right) \cdot \Delta t$$

---

### 2.5. Ограничения (Constraints)

1.  **Ограничение мощности заряда**:
    $$0 \le x_t \le P_{max}^{ch} \cdot u_t \quad \forall t$$
2.  **Ограничение мощности разряда**:
    $$0 \le y_t \le P_{max}^{dis} \cdot (1 - u_t) \quad \forall t$$
    *(Использование бинарной переменной $u_t$ математически исключает одновременный заряд и разряд BESS в один и тот же час).*

3.  **Динамика изменения уровня заряда (State of Charge)**:
    *   Для первого шага ($t = 0$):
        $$SoC_0 = SoC_{init} \cdot E_{cap} + x_0 \cdot \eta_{ch} \cdot \Delta t - \frac{y_0}{\eta_{dis}} \cdot \Delta t$$
    *   Для последующих шагов ($t \ge 1$):
        $$SoC_t = SoC_{t-1} + x_t \cdot \eta_{ch} \cdot \Delta t - \frac{y_t}{\eta_{dis}} \cdot \Delta t \quad \forall t \ge 1$$

4.  **Границы State of Charge**:
    $$SoC_{min} \cdot E_{cap} \le SoC_t \le SoC_{max} \cdot E_{cap} \quad \forall t$$

5.  **Лимит циклирования (амортизационное ограничение)**:
    Для предотвращения ускоренного износа вводится ограничение на совокупный разряд:
    $$\sum_{t=0}^{T-1} y_t \cdot \Delta t \le N_{cycles} \cdot E_{cap} \cdot \frac{T}{24}$$

6.  **Финальный уровень заряда (Target End-of-Day SoC)**:
    Для сохранения ресурса батареи в конце оптимизационного цикла она должна быть полностью разряжена до минимального порога:
    $$SoC_{T-1} = SoC_{min} \cdot E_{cap}$$

---

## 3. LP vs MILP: Анализ применимости

| Критерий | Linear Programming (LP) | Mixed-Integer Linear Programming (MILP) |
| :--- | :--- | :--- |
| **Бинарные переменные** | Отсутствуют. Переменные только непрерывные. | Присутствуют (например, $u_t \in \{0,1\}$). |
| **Запрет одновременного заряда/разряда** | Решается только косвенно. Если $P_{buy, t} > P_{sell, t}$, солвер сам не выберет одновременное действие. | Гарантируется жестко через бинарные переменные $u_t$ при любых ценах. |
| **Нелинейная деградация** | Невозможно смоделировать. | Можно смоделировать через кусочно-линейную аппроксимацию (SOS2 переменные). |
| **Наличие платы за пиковую мощность** | Невозможно смоделировать. | Легко моделируется через бинарные условия превышения лимитов. |
| **Скорость расчета** | Экстремально высокая (микросекунды). | Зависит от числа интервалов $T$ и целочисленного зазора (MIP gap). |

**Вывод**: Для простого арбитража достаточно LP, но для реальных enterprise систем с учетом физики BESS, штрафов за переток реактивной мощности и пиковых нагрузок предприятия (Peak Shaving) **необходим MILP**.

---

## 4. Сравнение солверов

1.  **PuLP (Python)**:
    *   *Плюсы*: Чистый Python API, легковесный, поддерживает интеграцию с CBC, GLPK, HiGHS.
    *   *Минусы*: Медленная генерация матрицы ограничений на больших горизонтах ($T > 1000$).
2.  **OR-Tools (Google)**:
    *   *Плюсы*: Написан на C++, очень высокая скорость сборки модели, встроенный многопоточный MILP-солвер (SCIP/Bop/GLOP).
    *   *Минусы*: Менее гибкий синтаксис для сложных математических выражений по сравнению с Pyomo/PuLP.
3.  **Gurobi / CPLEX**:
    *   *Плюсы*: Мировой стандарт. Решает MILP в 10-100 раз быстрее бесплатных аналогов.
    *   *Минусы*: Дорогая коммерческая лицензия ($> \$10,000$ на сервер).

---

## 5. Структура данных

### Входные данные (Input JSON)
```json
{
  "bess_params": {
    "capacity_kwh": 1000.0,
    "max_charge_power_kw": 250.0,
    "max_discharge_power_kw": 250.0,
    "charge_efficiency": 0.95,
    "discharge_efficiency": 0.95,
    "initial_soc_pct": 20.0,
    "min_soc_pct": 10.0,
    "max_soc_pct": 90.0,
    "degradation_cost_uah_kwh": 1.20,
    "max_cycles_per_day": 1.5
  },
  "market_params": {
    "oblenergo_name": "DTEK_Kyiv_Grids",
    "voltage_class": 2,
    "supplier_margin_uah_mwh": 100.0,
    "mode": "arbitrage"
  },
  "price_forecast": [3200.50, 2900.00, 10.00, 7800.20] 
}
```

### Выходной план (Output JSON)
```json
{
  "summary": {
    "status": "Optimal",
    "net_profit_uah": 38450.50,
    "cycles_used": 1.12
  },
  "schedule": [
    {
      "hour": 0,
      "price_forecast_uah_mwh": 3200.50,
      "action": "CHARGE",
      "power_kw": -250.0,
      "soc_kwh": 437.5,
      "hourly_p_l_uah": -913.14
    }
  ]
}
```

---

## 6. Почасовой расчет P&L (Profit & Loss)

Для каждого часа $t$ финансовый результат $P\&L_t$ (в UAH) рассчитывается по формуле:

$$P\&L_t = \left( P_{sell, t} \cdot y_t - P_{buy, t} \cdot x_t - C_{deg} \cdot y_t \right) \cdot \Delta t$$

*   При **заряде** ($x_t > 0$): $P\&L_t$ отрицательный (затраты на покупку электроэнергии и тарифов).
*   При **разряде** ($y_t > 0$): $P\&L_t$ положительный (выручка от продажи или сэкономленные затраты за вычетом амортизации ячеек).
*   При **простое** ($x_t=0, y_t=0$): $P\&L_t = 0$.

---

## 7. Сценарный анализ и логика рисков (VaR)

Для оценки рисков волатильности цен РДН применяются три сценария:
1.  **Base (Базовый)**: Оптимизация на основе базового точечного прогноза LightGBM.
2.  **Pessimistic (Пессимистический)**: Прогноз цен снижается на $1.64$ стандартных отклонения (5% квантиль нижнего ценового диапазона).
3.  **Aggressive (Агрессивный)**: Прогноз цен повышается на $1.64$ стандартных отклонения (95% квантиль пикового диапазона).

### Алгоритм расчета VaR (Value at Risk) методом Монте-Карло:
1.  Генерируется $N = 100$ ценовых профилей РДН:
    $$DAM_{t, s} = DAM_{t, base} \cdot e^{\epsilon_s}, \quad \epsilon_s \sim \mathcal{N}(0, \sigma^2)$$
    где $\sigma \approx 18\%$ (историческая волатильность рынка Украины).
2.  Для каждого профиля $s$ решается задача MILP-оптимизации, вычисляя прибыль $Profit_s$.
3.  Строится массив результатов $[Profit_1, Profit_2, \dots, Profit_N]$, отсортированный по возрастанию.
4.  Находится 5-й процентиль прибыли ($Profit_{5\%}$).
5.  **Daily Value at Risk (VaR 95%)** определяется как:
    $$VaR_{95\%} = Profit_{base} - Profit_{5\%}$$

---

## 8. Псевдокод Solver Pipeline

```python
import pulp
import numpy as np

def run_optimization_pipeline(input_data: dict) -> dict:
    # 1. Распаковка входных данных
    bess = input_data["bess_params"]
    market = input_data["market_params"]
    prices = input_data["price_forecast"]
    
    # 2. Вычисление эффективных тарифов через TariffService
    total_tariff_kwh = calculate_tariffs(market["oblenergo_name"], market["voltage_class"])
    
    # 3. Подготовка ценовых векторов покупки/продажи (UAH/kWh)
    p_buy = [p / 1000.0 + total_tariff_kwh for p in prices]
    p_sell = [p / 1000.0 if market["mode"] == "arbitrage" else (p / 1000.0 + total_tariff_kwh) for p in prices]
    
    # 4. Инициализация MILP модели PuLP
    prob = pulp.LpProblem("BESS_Arbitrage", pulp.LpMaximize)
    
    # Переменные
    T = len(prices)
    x = pulp.LpVariable.dicts("Charge", range(T), lowBound=0, upBound=bess["max_charge_power_kw"])
    y = pulp.LpVariable.dicts("Discharge", range(T), lowBound=0, upBound=bess["max_discharge_power_kw"])
    soc = pulp.LpVariable.dicts("SoC", range(T), lowBound=bess["min_soc_pct"]*bess["capacity_kwh"]/100.0, upBound=bess["max_soc_pct"]*bess["capacity_kwh"]/100.0)
    u = pulp.LpVariable.dicts("IsCharging", range(T), cat="Binary")
    
    # 5. Наложение ограничений
    # Динамика SoC
    init_soc_kwh = (bess["initial_soc_pct"] / 100.0) * bess["capacity_kwh"]
    prob += soc[0] == init_soc_kwh + x[0] * bess["charge_efficiency"] - y[0] / bess["discharge_efficiency"]
    for t in range(1, T):
        prob += soc[t] == soc[t-1] + x[t] * bess["charge_efficiency"] - y[t] / bess["discharge_efficiency"]
        
    # Блокировка одновременных процессов и лимиты мощностей
    for t in range(T):
        prob += x[t] <= bess["max_charge_power_kw"] * u[t]
        prob += y[t] <= bess["max_discharge_power_kw"] * (1 - u[t])
        
    # Лимит циклов
    total_discharge = pulp.lpSum([y[t] for t in range(T)])
    prob += total_discharge <= bess["max_cycles_per_day"] * bess["capacity_kwh"] * (T / 24.0)
    
    # Финальный разряд
    prob += soc[T-1] == (bess["min_soc_pct"] / 100.0) * bess["capacity_kwh"]
    
    # 6. Целевая функция
    revenue = pulp.lpSum([p_sell[t] * y[t] for t in range(T)])
    cost_charging = pulp.lpSum([p_buy[t] * x[t] for t in range(T)])
    cost_degradation = pulp.lpSum([bess["degradation_cost_uah_kwh"] * y[t] for t in range(T)])
    
    prob += revenue - cost_charging - cost_degradation
    
    # 7. Запуск солвера (например, HiGHS или CBC)
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    
    # 8. Сбор результатов и расчет почасового P&L
    schedule = []
    for t in range(T):
        ch = x[t].varValue or 0.0
        dis = y[t].varValue or 0.0
        action = "STANDBY"
        if ch > 0.1: action = "CHARGE"
        elif dis > 0.1: action = "DISCHARGE"
        
        hourly_p_l = p_sell[t] * dis - p_buy[t] * ch - bess["degradation_cost_uah_kwh"] * dis
        
        schedule.append({
            "hour": t,
            "action": action,
            "power_kw": -ch if ch > 0 else dis,
            "soc_kwh": soc[t].varValue or init_soc_kwh,
            "hourly_p_l_uah": float(hourly_p_l)
        })
        
    return {
        "status": pulp.LpStatus[prob.status],
        "net_profit_uah": sum(item["hourly_p_l_uah"] for item in schedule),
        "schedule": schedule
    }
```
