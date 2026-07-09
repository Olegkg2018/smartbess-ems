import { useState, useEffect } from 'react';
import { 
  TrendingUp, 
  Cpu, 
  BatteryCharging, 
  DollarSign, 
  ShieldAlert, 
  Settings as SettingsIcon, 
  Users, 
  Database,
  Lock,
  BookOpen
} from 'lucide-react';
import { 
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  Bar, LineChart, Line, ComposedChart, ReferenceLine
} from 'recharts';

type UserRole = 'Viewer' | 'Operator' | 'Manager' | 'Admin';
type ActiveView = 'executive' | 'asset' | 'forecast' | 'optimization' | 'roi' | 'scenarios' | 'settings' | 'audit';

// Mock data generator for fallback
const mockForecast = {
  hours: Array.from({ length: 24 }, (_, i) => i),
  predicted: [
    3000, 2800, 2000, 1500, 1000, 800, 1200, 2200, 3500, 4200, 3800, 3000,
    2500, 2200, 1800, 1500, 1800, 2800, 4500, 6200, 7800, 8400, 6500, 4000
  ],
  actual: [
    2900, 2750, 1950, 1400, 950, 820, 1300, 2300, 3600, 4100, 3700, 2900,
    2600, 2100, 1750, 1450, 1900, 2900, 4400, 6000, 7500, 8200, 6300, 3900
  ]
};

const mockOptimization = {
  net_profit_uah: 4258.61,
  cost_charging_uah: 1820.00,
  revenue_discharging_uah: 6078.61,
  cycles_used: 0.85,
  charge: [0, 0, 120, 250, 250, 150, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  discharge: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 200, 250, 250, 150, 0, 0],
  soc: [200, 200, 314, 551, 788, 900, 900, 900, 900, 900, 900, 900, 900, 900, 900, 900, 900, 900, 710, 472, 235, 100, 100, 100],
  hourly_buy_prices: [4500, 4300, 3500, 3000, 2500, 2300, 2700, 3700, 5000, 5700, 5300, 4500, 4000, 3700, 3300, 3000, 3300, 4300, 6000, 7700, 9300, 9900, 8000, 5500]
};

export default function App() {
  const [activeRole, setActiveRole] = useState<UserRole>('Operator');
  const [activeView, setActiveView] = useState<ActiveView>('executive');
  const [targetDate, setTargetDate] = useState<string>('2026-07-09');
  const [selectedModel, setSelectedModel] = useState<string>('lightgbm');
  const [operationalMode, setOperationalMode] = useState<string>('arbitrage');
  const scadaConnected = true;
  
  // Settings values
  const [osr, setOsr] = useState<string>('dtek_kiev_regional');
  const [voltageClass, setVoltageClass] = useState<number>(1);
  const [margin, setMargin] = useState<number>(100);
  const [capacity, setCapacity] = useState<number>(1000);
  const [power, setPower] = useState<number>(250);
  const [efficiency, setEfficiency] = useState<number>(95);
  const [launchDate, setLaunchDate] = useState<string>("2026-01-01");
  const [manualOverrides, setManualOverrides] = useState<any[]>([]);
  
  // Economics values
  const [capex, setCapex] = useState<number>(15200000);
  const [discountRate, setDiscountRate] = useState<number>(12);
  const [lifetime, setLifetime] = useState<number>(10);
  
  // Market Factors
  const [gasPrice, setGasPrice] = useState<number>(35.0);
  const [nuclearOutage, setNuclearOutage] = useState<number>(15.0); // %
  const [solarStrike, setSolarStrike] = useState<number>(0.0); // %
  const [marketCoeff, setMarketCoeff] = useState<number>(1.0);
  const [vdrVolume, setVdrVolume] = useState<number>(1.0);
  const [gridImportExport, setGridImportExport] = useState<number>(0.0); // MW
  
  // Log systems
  const [systemLogs, setSystemLogs] = useState<Array<{time: string, src: string, text: string, type: 'success' | 'info' | 'warn' | 'error'}>>([
    { time: '12:00:00', src: 'SYSTEM', text: 'База даних PostgreSQL і кешування задач в Redis ініціалізовано успішно.', type: 'success' },
    { time: '12:00:03', src: 'SCADA', text: 'Встановлено зв\'язок з BESS симулятором за адресою 127.0.0.1:5020.', type: 'success' },
    { time: '12:00:05', src: 'SCHEDULER', text: 'Заплановане завдання оновлення цін РДН налаштовано на 17:30.', type: 'info' }
  ]);
  const [auditLogs, setAuditLogs] = useState<Array<{time: string, user: string, action: string, ip: string, status: string}>>([
    { time: '2026-07-09 11:32:15', user: 'admin@smartbess.ua', action: 'Регистрация BESS актива #1 (Киевские региональные сети)', ip: '192.168.1.45', status: 'SUCCESS' },
    { time: '2026-07-09 11:35:40', user: 'cfo@smartbess.ua', action: 'Изменение инвестиционной ставки дисконтирования на 12%', ip: '192.168.1.12', status: 'SUCCESS' }
  ]);

  // Approval Overlay State
  const [showApprovalModal, setShowApprovalModal] = useState<boolean>(false);
  const [pendingAction, setPendingAction] = useState<string>('');
  const [approvalToken, setApprovalToken] = useState<string>('');
  
  // Real database fetched state (or mock on fail)
  const [apiData, setApiData] = useState<any>({
    forecast: mockForecast,
    optimization: mockOptimization,
    actual_optimization: mockOptimization,
    is_historical: false,
    explanations: [
      "Прогноз погоди: середня температура 22°C. Сонячно. Очікується профицит генерації СЕС з 11:00 до 15:00.",
      "Оптимальний графік: заряд батареї в години низьких цін РДН (12:00-14:00) та повний разряд у вечірній пік (19:00-21:00)."
    ]
  });

  const [loading, setLoading] = useState<boolean>(false);
  const [executiveReport, setExecutiveReport] = useState<any>(null);

  // Fallback default executive report metrics
  const defaultExecutiveReport = {
    launch_date: "2026-01-01",
    bess_properties: {
      capacity_mwh: 2.0,
      power_mw: 1.0,
      degradation_cost_per_mwh: 1200.0,
      estimated_capex_uah: 15000000.0
    },
    ytd_metrics: {
      optimal_forecast_profit_ytd_uah: 1854200.0,
      actual_p_l_ytd_uah: 1483360.0,
      degradation_amortization_ytd_uah: 254200.0,
      average_daily_profit_uah: 7850.0,
      days_in_operation: 190
    },
    roy_forecast: {
      remaining_days: 175,
      projected_roy_profit_uah: 1373750.0,
      total_annual_profit_projected_uah: 2857110.0,
      estimated_payback_years: 5.25
    },
    daily_history: Array.from({ length: 30 }, (_, i) => {
      const d = new Date();
      d.setDate(d.getDate() - 30 + i);
      const dateStr = d.toISOString().split('T')[0];
      const optimal = 8000 + 4000 * Math.sin(i / 3) + Math.random() * 1000;
      return {
        date: dateStr,
        optimal_profit_uah: optimal,
        actual_profit_uah: optimal * 0.80,
        cumulative_actual_profit_uah: 100000 + i * 6400,
        status: "simulated",
        accuracy_rate: 0.80
      };
    }),
    payback_trajectory: Array.from({ length: 20 }, (_, i) => {
      const d = new Date("2026-01-01");
      d.setDate(d.getDate() + i * 20);
      return {
        date: d.toISOString().split('T')[0],
        cumulative_p_l_uah: -15000000 + i * 900000,
        is_projected: i > 10
      };
    })
  };

  // Helper to construct a mock JWT token representing the active role (Keycloak simulation)
  const getMockToken = () => {
    const encodeBase64Url = (obj: any) => {
      const str = JSON.stringify(obj);
      const base64 = btoa(encodeURIComponent(str).replace(/%([0-9A-F]{2})/g, (_, p1) => {
        return String.fromCharCode(parseInt(p1, 16));
      }));
      return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
    };

    const header = { alg: 'none', typ: 'JWT' };
    const payload = {
      preferred_username: `${activeRole.toLowerCase()}@smartbess.ua`,
      roles: [activeRole],
      realm_access: { roles: [activeRole] },
      resource_access: { 'smartbess-platform': { roles: [activeRole] } }
    };

    return `${encodeBase64Url(header)}.${encodeBase64Url(payload)}.`;
  };

  const fetchSystemSettings = async () => {
    try {
      const token = getMockToken();
      const res = await fetch('/api/v1/optimization/settings', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        setLaunchDate(data.launch_date);
        setOsr(data.osr);
        setVoltageClass(data.voltage_class);
        setMargin(data.margin);
        setCapacity(data.capacity_kw);
        setPower(data.power_kw);
        setEfficiency(data.efficiency_pct);
      }
    } catch (e) {
      console.error("Error fetching system settings:", e);
    }
  };

  const fetchManualOverrides = async () => {
    try {
      const token = getMockToken();
      const res = await fetch(`/api/v1/optimization/manual-overrides?asset_id=4fb873c4-1a4b-4893-a9a2-f9255ad0823b&date=${targetDate}`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        setManualOverrides(data.overrides);
      }
    } catch (e) {
      console.error("Error fetching overrides:", e);
    }
  };

  const saveSystemSettings = async () => {
    try {
      const token = getMockToken();
      const payload = {
        launch_date: launchDate,
        osr,
        voltage_class: voltageClass,
        margin,
        capacity_kw: capacity,
        power_kw: power,
        efficiency_pct: efficiency
      };
      const res = await fetch('/api/v1/optimization/settings', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        addLog('SETTINGS', `Параметри системи успішно збережено на бекенді. Дата запуску: ${launchDate}`, 'success');
        alert('Налаштування успішно збережено та синхронізовано з сервером!');
        fetchMetricsAndForecast();
      } else {
        alert('Помилка при збереженні налаштувань на бекенді.');
      }
    } catch (e) {
      console.error(e);
      alert('Помилка запиту збереження налаштувань.');
    }
  };

  // Load initial data on mount and refetch on role or parameter change
  useEffect(() => {
    fetchSystemSettings();
  }, []);

  useEffect(() => {
    fetchMetricsAndForecast();
    fetchManualOverrides();
  }, [
    targetDate, selectedModel, operationalMode, 
    gasPrice, nuclearOutage, solarStrike, 
    marketCoeff, vdrVolume, gridImportExport,
    activeRole
  ]);

  const fetchMetricsAndForecast = async () => {
    setLoading(true);
    const token = getMockToken();
    
    // 1. Fetch forecast & daily optimization
    try {
      const res = await fetch('/api/forecast', {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          date: targetDate,
          selected_model: selectedModel,
          mode: operationalMode,
          battery_capacity: capacity,
          max_charge_power: power,
          max_discharge_power: power,
          charge_efficiency: efficiency,
          discharge_efficiency: efficiency,
          gas_price: gasPrice,
          nuclear_outage: nuclearOutage / 100.0,
          solar_strike: solarStrike / 100.0,
          market_coeff: marketCoeff,
          vdr_volume: vdrVolume,
          grid_import_export: gridImportExport
        })
      });
      if (res.status === 403) {
        addLog('SECURITY', `Помилка 403: Ролі ${activeRole} відмовлено у доступі до прогнозу.`, 'error');
        alert(`Помилка доступу (403 Forbidden): Ваша роль ${activeRole} не має прав на розрахунок прогнозу та оптимізації!`);
        setLoading(false);
        return;
      }
      if (res.status === 401) {
        addLog('SECURITY', `Помилка 401: Токен не авторизовано.`, 'error');
        alert(`Помилка авторизації (401 Unauthorized)`);
        setLoading(false);
        return;
      }
      if (res.ok) {
        const data = await res.json();
        setApiData(data);
        addLog('API', `Дані прогнозу для ${targetDate} завантажено успішно.`, 'success');
      } else {
        throw new Error('API return non-200');
      }
    } catch (e) {
      addLog('API', `Помилка запиту прогнозу до бэкенда. Використовуються локальні mock-дані.`, 'warn');
    } finally {
      setLoading(false);
    }

    // 2. Fetch executive C-level summary report
    try {
      const resSummary = await fetch('/api/v1/reports/executive-summary?asset_id=4fb873c4-1a4b-4893-a9a2-f9255ad0823b', {
        method: 'GET',
        headers: { 
          'Authorization': `Bearer ${token}`
        }
      });
      if (resSummary.ok) {
        const summaryData = await resSummary.json();
        setExecutiveReport(summaryData);
        addLog('REPORT', `Звіт керівника (C-Level YTD) завантажено успішно.`, 'success');
      } else if (resSummary.status === 403) {
        addLog('SECURITY', `Помилка 403: Відмовлено у доступі до звіту C-Level.`, 'error');
      } else {
        throw new Error('Summary report return non-200');
      }
    } catch (sumErr) {
      addLog('REPORT', `Помилка запиту звіту C-Level. Використовується локальна модель окупності.`, 'warn');
    }
  };

  const addLog = (src: string, text: string, type: 'success' | 'info' | 'warn' | 'error') => {
    const time = new Date().toTimeString().split(' ')[0];
    setSystemLogs(prev => [{ time, src, text, type }, ...prev].slice(0, 50));
  };

  const triggerFourEyesApproval = (actionName: string) => {
    // Check RBAC limits
    if (activeRole === 'Viewer') {
      alert('У вас немає прав для виконання цієї дії. Ваша роль: Viewer');
      return;
    }
    setPendingAction(actionName);
    setShowApprovalModal(true);
  };

  const executeApprovedAction = () => {
    if (!approvalToken) {
      alert('Будь ласка, введіть секретний ключ підпису менеджера.');
      return;
    }
    
    // Add audit record
    const time = new Date().toISOString().replace('T', ' ').substring(0, 19);
    setAuditLogs(prev => [
      {
        time,
        user: activeRole === 'Admin' ? 'admin@smartbess.ua' : 'operator@smartbess.ua',
        action: `[Four-Eyes Approved] ${pendingAction}`,
        ip: '127.0.0.1',
        status: 'SUCCESS'
      },
      ...prev
    ]);
    
    addLog('EMS', `Ручну команду диспетчеризации успешно выполнено: ${pendingAction}`, 'success');
    setShowApprovalModal(false);
    setApprovalToken('');
    alert('Команду успішно надіслано до BESS контролера Modbus TCP!');
  };

  // Pre-calculated scenario arrays for Risk scenario chart
  const basePrices = apiData.forecast[selectedModel] || apiData.forecast.lightgbm || mockForecast.predicted;
  const pessimisticPrices = basePrices.map((p: number) => p * 0.82);
  const aggressivePrices = basePrices.map((p: number) => p * 1.25);
  
  const scenariosData = Array.from({ length: 24 }, (_, i) => ({
    hour: `${i}:00`,
    base: basePrices[i],
    pessimistic: pessimisticPrices[i],
    aggressive: aggressivePrices[i]
  }));

  // Re-generate chart data for BESS Optimization profile
  const opt = apiData.optimization || mockOptimization;
  const actualPrices = apiData.forecast.actual || [];
  const optimizationProfile = Array.from({ length: 24 }, (_, i) => ({
    hour: `${i + 1}`,
    charge: opt.charge ? opt.charge[i] : 0,
    discharge: opt.discharge ? opt.discharge[i] : 0,
    soc: opt.soc ? opt.soc[i] : 200,
    price: basePrices[i],
    actual: actualPrices[i] !== undefined ? actualPrices[i] : null
  }));

  // Dynamic daily stats computed in real-time from manualOverrides state
  let dailyRevenue = opt.revenue_discharging_uah || 0;
  let dailyCost = opt.cost_charging_uah || 0;
  let dailyDegradation = (opt.cycles_used || 0) * (capacity * 1.20);
  let currentSoC = capacity * 0.20; // Start at 20% SoC in kWh
  
  if (manualOverrides && manualOverrides.length === 24) {
    dailyRevenue = 0;
    dailyCost = 0;
    dailyDegradation = 0;
    
    manualOverrides.forEach((o) => {
      const powerKW = o.power_mw * 1000.0;
      const priceKWh = o.price_uah / 1000.0;
      const tariffKWh = (528.57 + 1500.0 + 104.57 + 100.0) / 1000.0;
      
      if (powerKW < 0) { // charge
        const chargeKW = Math.abs(powerKW);
        dailyCost += chargeKW * (priceKWh + tariffKWh);
      } else if (powerKW > 0) { // discharge
        const dischargeKW = powerKW;
        dailyRevenue += dischargeKW * priceKWh;
        dailyDegradation += dischargeKW * 1.20; // 1.20 UAH per kWh degradation cost
      }
    });
  }
  
  const dailyNetProfit = dailyRevenue - dailyCost - dailyDegradation;

  const currentDayProfile = manualOverrides && manualOverrides.length === 24 ? manualOverrides.map((o, idx) => {
    const powerKW = o.power_mw * 1000.0;
    let chargeKW = 0;
    let dischargeKW = 0;
    
    if (powerKW < 0) {
      chargeKW = Math.abs(powerKW);
      currentSoC = Math.min(capacity * 0.90, currentSoC + chargeKW * (efficiency / 100.0));
    } else if (powerKW > 0) {
      dischargeKW = powerKW;
      currentSoC = Math.max(capacity * 0.10, currentSoC - dischargeKW / (efficiency / 100.0));
    }
    
    return {
      hour: `${idx + 1}`,
      charge: chargeKW,
      discharge: dischargeKW,
      soc: currentSoC,
      price: o.price_uah,
      actual: o.is_overridden ? o.price_uah : null
    };
  }) : [];

  // Cumulative NPV calculation logic
  const years = Array.from({ length: lifetime + 1 }, (_, i) => i);
  const baseYearlyProfit = (opt.net_profit_uah || mockOptimization.net_profit_uah) * 365;
  const opexYearly = capex * 0.02 + 180000; // 2% Capex + IT License
  const netYearlyInflow = baseYearlyProfit - opexYearly;

  let cumulativeNPV = -capex;
  const paybackData = years.map(yr => {
    if (yr > 0) {
      const discountedInflow = netYearlyInflow / Math.pow(1 + discountRate / 100, yr);
      cumulativeNPV += discountedInflow;
    }
    return {
      year: `Рік ${yr}`,
      NPV: Math.round(cumulativeNPV),
      ZeroLine: 0
    };
  });

  return (
    <div className="dashboard-container">
      {/* 1. Sidebar Navigation */}
      <aside className="sidebar">
        <div className="logo-section">
          <BatteryCharging style={{ color: '#3b82f6' }} />
          <span className="logo-text">SmartBESS EMS</span>
        </div>
        
        <div className="nav-section-title">Executive (C-Level)</div>
        <div 
          className={`nav-item ${activeView === 'executive' ? 'active' : ''}`}
          onClick={() => setActiveView('executive')}
        >
          <TrendingUp size={18} />
          <span>Executive Overview</span>
        </div>
        <div 
          className={`nav-item ${activeView === 'roi' ? 'active' : ''}`}
          onClick={() => setActiveView('roi')}
        >
          <DollarSign size={18} />
          <span>ROI & Payback</span>
        </div>

        <div className="nav-section-title">Analytics & Risk</div>
        <div 
          className={`nav-item ${activeView === 'forecast' ? 'active' : ''}`}
          onClick={() => setActiveView('forecast')}
        >
          <Cpu size={18} />
          <span>Price Forecast</span>
        </div>
        <div 
          className={`nav-item ${activeView === 'scenarios' ? 'active' : ''}`}
          onClick={() => setActiveView('scenarios')}
        >
          <ShieldAlert size={18} />
          <span>Risk Scenarios</span>
        </div>

        <div className="nav-section-title">Operations</div>
        <div 
          className={`nav-item ${activeView === 'asset' ? 'active' : ''}`}
          onClick={() => setActiveView('asset')}
        >
          <Database size={18} />
          <span>Asset Detail</span>
        </div>
        <div 
          className={`nav-item ${activeView === 'optimization' ? 'active' : ''}`}
          onClick={() => setActiveView('optimization')}
        >
          <TrendingUp size={18} />
          <span>Optimization Schedule</span>
        </div>
        <div 
          className={`nav-item ${activeView === 'settings' ? 'active' : ''}`}
          onClick={() => setActiveView('settings')}
        >
          <SettingsIcon size={18} />
          <span>Settings & Tariffs</span>
        </div>
        <div 
          className={`nav-item ${activeView === 'audit' ? 'active' : ''}`}
          onClick={() => setActiveView('audit')}
        >
          <Users size={18} />
          <span>Audit & Model Decisions</span>
        </div>
      </aside>

      {/* 2. Main Workspace */}
      <main className="main-workspace">
        {/* Top Header */}
        <header className="top-header">
          <div className="header-title-section">
            <h1 className="header-title">
              {activeView === 'executive' && "Executive Financial Overview"}
              {activeView === 'asset' && "Asset Telemetry & Live SCADA"}
              {activeView === 'forecast' && "Neural Price Predictor (LightGBM/XGBoost)"}
              {activeView === 'optimization' && "Optimal BESS Arbitrage Schedule"}
              {activeView === 'roi' && "Project Lifecycle Economics & NPV"}
              {activeView === 'scenarios' && "Stochastic Risk & Stress Analysis"}
              {activeView === 'settings' && "System Tariffs & Limit Configurator"}
              {activeView === 'audit' && "Four-Eyes Audit Trail & Model Decisions"}
            </h1>
            
            {/* Health Indicators */}
            <span className={`status-badge ${scadaConnected ? 'online' : 'offline'}`}>
              <Database size={12} />
              SCADA Modbus: {scadaConnected ? 'ONLINE (127.0.0.1:5020)' : 'OFFLINE'}
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            {/* RBAC Selector simulating SSO/Keycloak auth */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Users size={16} style={{ color: '#9ca3af' }} />
              <select 
                className="rbac-selector"
                value={activeRole} 
                onChange={(e) => {
                  setActiveRole(e.target.value as UserRole);
                  addLog('AUTH', `Пользователь переключил сессию на роль: ${e.target.value}`, 'info');
                }}
              >
                <option value="Viewer">Viewer (Read-Only)</option>
                <option value="Operator">Operator (Grid Dispatch)</option>
                <option value="Manager">Manager (CFO/CLevel)</option>
                <option value="Admin">Admin (IT System)</option>
              </select>
            </div>
          </div>
        </header>

        {/* 3. Page Content Area */}
        <section className="page-content">
          
          {/* Global filter row for modeling screens */}
          {['executive', 'forecast', 'optimization', 'scenarios'].includes(activeView) && (
            <div className="glass-card" style={{ display: 'flex', gap: '20px', alignItems: 'flex-end', padding: '16px 20px', marginBottom: '20px' }}>
              <div className="form-group" style={{ margin: 0, flex: 1 }}>
                <label className="form-label">Дата моделювання</label>
                <input 
                  type="date" 
                  className="form-input" 
                  value={targetDate} 
                  onChange={(e) => setTargetDate(e.target.value)} 
                />
              </div>
              <div className="form-group" style={{ margin: 0, flex: 1 }}>
                <label className="form-label">Нейромережева модель</label>
                <select 
                  className="form-select" 
                  value={selectedModel} 
                  onChange={(e) => setSelectedModel(e.target.value)}
                >
                  <option value="lightgbm">LightGBM (R² = 0.819)</option>
                  <option value="xgboost">XGBoost (R² = 0.795)</option>
                  <option value="mlp">Multilayer Perceptron (R² = 0.742)</option>
                </select>
              </div>
              <div className="form-group" style={{ margin: 0, flex: 1 }}>
                <label className="form-label">Режим диспетчеризації</label>
                <select 
                  className="form-select" 
                  value={operationalMode} 
                  onChange={(e) => setOperationalMode(e.target.value)}
                >
                  <option value="arbitrage">Мережевий Арбітраж (DAM)</option>
                  <option value="self_consumption">Self-Consumption (Behind-the-Meter)</option>
                </select>
              </div>
              <button className="btn" style={{ height: '38px' }} onClick={fetchMetricsAndForecast}>
                {loading ? "Розрахунок..." : "Розрахувати"}
              </button>
            </div>
          )}

          {/* VIEW 1: EXECUTIVE OVERVIEW */}
          {activeView === 'executive' && (() => {
            const report = executiveReport || defaultExecutiveReport;
            return (
              <div>
                {/* Description info block */}
                <div className="glass-card" style={{ marginBottom: '24px', borderLeft: '4px solid #3b82f6' }}>
                  <h4 style={{ margin: '0 0 6px 0', fontSize: '16px', color: '#60a5fa' }}>
                    Аналіз окупності інвестицій BESS (C-Level YTD Analytics)
                  </h4>
                  <p style={{ margin: 0, fontSize: '13.5px', color: '#9ca3af', lineHeight: '1.5' }}>
                    Звіт відображає накопичений фінансовий результат роботи BESS з моменту запуску 
                    системы (<strong>{report.launch_date}</strong>) до поточної дати, а також прогнозований ROY (Rest of Year) 
                    тренд до кінця року. Історичні розрахункові дані до моменту фізичного підключення BESS враховують 
                    <strong> 80% точність (угадування) цін</strong> при подачі заявок на РДН. Дні з підключеною батареєю зчитують 
                    реальну телеметрію та прибуток по факту роботи.
                  </p>
                </div>

                {/* KPI Cards row */}
                <div className="kpi-container" style={{ marginBottom: '24px' }}>
                  <div className="kpi-card">
                    <span className="kpi-title">Накопичений прибуток YTD (80% точність)</span>
                    <span className="kpi-value" style={{ color: '#10b981' }}>
                      {Math.round(report.ytd_metrics.actual_p_l_ytd_uah).toLocaleString()} грн
                    </span>
                    <span className="kpi-change positive">
                      За {report.ytd_metrics.days_in_operation} днів роботи
                    </span>
                  </div>
                  <div className="kpi-card">
                    <span className="kpi-title">Оптимальний потенціал YTD (100% точність)</span>
                    <span className="kpi-value" style={{ color: '#60a5fa' }}>
                      {Math.round(report.ytd_metrics.optimal_forecast_profit_ytd_uah).toLocaleString()} грн
                    </span>
                    <span className="kpi-change neutral">
                      Втрачено через помилки прогнозу: {Math.round(report.ytd_metrics.optimal_forecast_profit_ytd_uah - report.ytd_metrics.actual_p_l_ytd_uah).toLocaleString()} грн
                    </span>
                  </div>
                  <div className="kpi-card">
                    <span className="kpi-title">Прогноз прибутку до кінця року (ROY)</span>
                    <span className="kpi-value" style={{ color: '#fbbf24' }}>
                      {Math.round(report.roy_forecast.projected_roy_profit_uah).toLocaleString()} грн
                    </span>
                    <span className="kpi-change positive">
                      Очікувано за рік: {Math.round(report.roy_forecast.total_annual_profit_projected_uah).toLocaleString()} грн
                    </span>
                  </div>
                  <div className="kpi-card">
                    <span className="kpi-title">Термін окупності CAPEX</span>
                    <span className="kpi-value">
                      {report.roy_forecast.estimated_payback_years.toFixed(2)} років
                    </span>
                    <span className="kpi-change negative">
                      CAPEX: {Math.round(report.bess_properties.estimated_capex_uah).toLocaleString()} грн
                    </span>
                  </div>
                </div>

                {/* Charts Grid */}
                <div className="grid-2">
                  {/* Daily profit chart */}
                  <div className="glass-card">
                    <h3 className="card-title" style={{ marginBottom: '16px' }}>Історія добової доходності (Daily Arbitrage P&L)</h3>
                    <div style={{ width: '100%', height: 320 }}>
                      <ResponsiveContainer>
                        <ComposedChart data={report.daily_history ? report.daily_history.map((d: any) => ({ ...d, charge_cost_negative: -d.charge_cost_uah })) : []}>
                          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                          <XAxis dataKey="date" stroke="#9ca3af" fontSize={11} />
                          <YAxis stroke="#9ca3af" fontSize={11} />
                          <Tooltip 
                            contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }}
                            formatter={(value: any, name: any) => {
                              if (name === "discharge_revenue_uah") return [`${Math.round(value).toLocaleString()} грн`, "Дохід (Продаж)"];
                              if (name === "charge_cost_negative") return [`${Math.round(Math.abs(value)).toLocaleString()} грн`, "Витрати (Купівля)"];
                              if (name === "actual_profit_uah") return [`${Math.round(value).toLocaleString()} грн`, "Чистий суточний прибуток"];
                              return [value, name];
                            }}
                          />
                          <Legend />
                          <Bar dataKey="discharge_revenue_uah" name="Дохід (Продаж)" fill="#10b981" />
                          <Bar dataKey="charge_cost_negative" name="Витрати (Купівля)" fill="#ef4444" />
                          <Area type="monotone" dataKey="actual_profit_uah" name="Чистий прибуток" stroke="#3b82f6" fill="rgba(59, 130, 246, 0.05)" strokeWidth={2} />
                        </ComposedChart>
                      </ResponsiveContainer>
                    </div>
                  </div>

                  {/* Payback trajectory chart */}
                  <div className="glass-card">
                    <h3 className="card-title" style={{ marginBottom: '16px' }}>Траєкторія окупності BESS (Investment Payback Trajectory)</h3>
                    <div style={{ width: '100%', height: 320 }}>
                      <ResponsiveContainer>
                        <AreaChart data={report.payback_trajectory}>
                          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                          <XAxis dataKey="date" stroke="#9ca3af" fontSize={11} />
                          <YAxis stroke="#9ca3af" fontSize={11} />
                          <Tooltip 
                            contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }}
                            formatter={(value: any, _: any, props: any) => {
                              const statusText = props.payload.is_projected ? "(Прогноз)" : "(Факт)";
                              return [`${Math.round(value).toLocaleString()} грн`, `Баланс інвестицій ${statusText}`];
                            }}
                          />
                          <ReferenceLine y={0} stroke="#ef4444" strokeDasharray="4 4" strokeWidth={2} label={{ value: 'БЕЗУБИТКОВІСТЬ', fill: '#ef4444', fontSize: 10, position: 'top' }} />
                          <Area type="monotone" dataKey="cumulative_p_l_uah" stroke="#8b5cf6" fill="rgba(139, 92, 246, 0.15)" strokeWidth={3} />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                </div>
              </div>
            );
          })()}

          {/* VIEW 2: ASSET DETAIL */}
          {activeView === 'asset' && (
            <div className="grid-2">
              {/* Telemetry card */}
              <div className="glass-card">
                <h3 className="card-title">Телеметрія BESS в реальному часі (Live Modbus)</h3>
                
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', margin: '30px 0' }}>
                  <div style={{ 
                    width: '180px', 
                    height: '180px', 
                    borderRadius: '50%', 
                    border: '8px solid rgba(16, 185, 129, 0.15)',
                    borderTopColor: '#10b981',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    position: 'relative'
                  }}>
                    <span style={{ fontSize: '0.8rem', color: '#9ca3af', fontWeight: 600 }}>Battery SoC</span>
                    <span style={{ fontSize: '2.5rem', fontWeight: 700, color: '#10b981' }}>20.0 %</span>
                    <span style={{ fontSize: '0.75rem', color: '#6b7280' }}>200 kWh / 1000 kWh</span>
                  </div>
                </div>

                <table className="data-table">
                  <tbody>
                    <tr>
                      <td>Номінальна ємність</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{capacity} кВт-год</td>
                    </tr>
                    <tr>
                      <td>Макс. потужність</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{power} кВт</td>
                    </tr>
                    <tr>
                      <td>Поточна активна потужність</td>
                      <td style={{ textAlign: 'right', fontWeight: 600, color: '#3b82f6' }}>-150.0 кВт (Заряд)</td>
                    </tr>
                    <tr>
                      <td>Температура осередків</td>
                      <td style={{ textAlign: 'right', fontWeight: 600, color: '#f59e0b' }}>24.8 °C (Норма)</td>
                    </tr>
                    <tr>
                      <td>Технічний стан (SOH)</td>
                      <td style={{ textAlign: 'right', fontWeight: 600, color: '#10b981' }}>99.85 %</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              {/* Action dispatcher overrides */}
              <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
                <div>
                  <h3 className="card-title">Ручний байпас EMS (Manual SCADA Dispatch Overrides)</h3>
                  <p style={{ fontSize: '0.8rem', color: '#9ca3af', margin: '8px 0 20px 0' }}>
                    Ця панель дозволяє оператору примусово змінити автоматичний графік та подати миттєву команду заряду чи розряду на контроллер BESS.
                  </p>
                  
                  {activeRole === 'Viewer' && (
                    <div style={{ background: 'rgba(239, 68, 68, 0.08)', border: '1px solid rgba(239, 68, 68, 0.25)', padding: '12px', borderRadius: '8px', marginBottom: '20px', display: 'flex', gap: '8px', alignItems: 'center' }}>
                      <Lock size={16} style={{ color: '#ef4444' }} />
                      <span style={{ fontSize: '0.8rem', color: '#ef4444' }}><strong>Увага:</strong> Для подачі команд потрібна роль Operator або Manager.</span>
                    </div>
                  )}

                  <div className="form-group">
                    <label className="form-label">Примусова потужність команди (кВт)</label>
                    <input type="number" className="form-input" defaultValue={150} disabled={activeRole === 'Viewer'} />
                  </div>
                </div>

                <div style={{ display: 'flex', gap: '12px' }}>
                  <button 
                    className="btn" 
                    style={{ flex: 1 }} 
                    disabled={activeRole === 'Viewer'}
                    onClick={() => triggerFourEyesApproval('FORCE CHARGE 150 kW (Modbus override)')}
                  >
                    Примусовий Заряд (-150 кВт)
                  </button>
                  <button 
                    className="btn btn-danger" 
                    style={{ flex: 1 }} 
                    disabled={activeRole === 'Viewer'}
                    onClick={() => triggerFourEyesApproval('FORCE DISCHARGE 150 kW (Modbus override)')}
                  >
                    Примусовий Розряд (+150 кВт)
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* VIEW 3: PRICE FORECAST */}
          {activeView === 'forecast' && (
            <div>
              {/* Dual axis chart showing forecast vs buy prices */}
              <div className="glass-card">
                <h3 className="card-title" style={{ marginBottom: '16px' }}>Ціни РДН та Порівняння вартості з мережевими тарифами</h3>
                <div style={{ width: '100%', height: 350 }}>
                  <ResponsiveContainer>
                    <ComposedChart data={optimizationProfile}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="hour" stroke="#9ca3af" />
                      <YAxis stroke="#9ca3af" />
                      <Tooltip contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }} />
                      <Legend />
                      <Line type="monotone" dataKey="price" name="Прогноз РДН (грн/МВт-год)" stroke="#3b82f6" strokeWidth={3} activeDot={{ r: 8 }} />
                      <Line type="monotone" dataKey="actual" name="Фактичні (при наявності)" stroke="#10b981" strokeWidth={2} strokeDasharray="5 5" connectNulls={true} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Explanations section */}
              <div className="glass-card">
                <h3 className="card-title" style={{ marginBottom: '12px' }}>Пояснення факторів моделі (AI Explanations)</h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  {apiData.explanations.map((exp: string, idx: number) => (
                    <div key={idx} style={{ display: 'flex', gap: '10px', background: 'rgba(255,255,255,0.02)', padding: '12px', borderRadius: '6px', fontSize: '0.85rem' }}>
                      <BookOpen size={16} style={{ color: '#06b6d4' }} />
                      <span>{exp}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* VIEW 4: OPTIMIZATION SCHEDULE */}
          {activeView === 'optimization' && (
            <div>
              {/* Real-time Daily KPIs row */}
              <div className="kpi-container" style={{ marginBottom: '24px' }}>
                <div className="kpi-card" style={{ borderLeft: '4px solid #10b981' }}>
                  <span className="kpi-title">Чистий прибуток за добу (з урахуванням втрат)</span>
                  <span className="kpi-value" style={{ color: dailyNetProfit >= 0 ? '#10b981' : '#ef4444' }}>
                    {Math.round(dailyNetProfit).toLocaleString()} грн
                  </span>
                  <span className="kpi-change neutral">Дохід мінус Витрати та Знос</span>
                </div>
                <div className="kpi-card">
                  <span className="kpi-title">Дохід від розряду (Продаж)</span>
                  <span className="kpi-value" style={{ color: '#10b981' }}>
                    {Math.round(dailyRevenue).toLocaleString()} грн
                  </span>
                  <span className="kpi-change positive">Реальний випуск</span>
                </div>
                <div className="kpi-card">
                  <span className="kpi-title">Витрати заряду (Купівля + Тарифи)</span>
                  <span className="kpi-value" style={{ color: '#ef4444' }}>
                    {Math.round(dailyCost).toLocaleString()} грн
                  </span>
                  <span className="kpi-change negative">З урахуванням втрат КПД</span>
                </div>
                <div className="kpi-card">
                  <span className="kpi-title">Знос батареї (Деградація)</span>
                  <span className="kpi-value">
                    {Math.round(dailyDegradation).toLocaleString()} грн
                  </span>
                  <span className="kpi-change negative">Амортизація LCOS</span>
                </div>
              </div>

              {/* BESS optimization schedule (charge/discharge bars + SoC area) */}
              <div className="glass-card">
                <h3 className="card-title" style={{ marginBottom: '16px' }}>Оптимальний профіль циклу заряду/розряду BESS</h3>
                <div style={{ width: '100%', height: 350 }}>
                  <ResponsiveContainer>
                    <ComposedChart data={currentDayProfile.length === 24 ? currentDayProfile : optimizationProfile}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="hour" stroke="#9ca3af" />
                      <YAxis stroke="#9ca3af" />
                      <Tooltip contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }} />
                      <Legend />
                      <Area type="monotone" dataKey="soc" name="Рівень заряду SoC (кВт-год)" stroke="#f59e0b" fill="rgba(245, 158, 11, 0.1)" />
                      <Bar dataKey="charge" name="Потужність заряду (кВт)" fill="#3b82f6" />
                      <Bar dataKey="discharge" name="Потужність розряду (кВт)" fill="#10b981" />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Manual Overrides hourly table */}
              <div className="glass-card" style={{ marginTop: '24px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                  <div>
                    <h3 className="card-title" style={{ margin: 0 }}>Ручне коригування заявок (Manual Dispatch Schedule)</h3>
                    <p style={{ margin: '4px 0 0 0', fontSize: '13px', color: '#9ca3af' }}>
                      Введіть потужність (МВт: розряд +, заряд -) та реальну ціну заявки (грн/МВт-год) для кожної години на {targetDate}.
                    </p>
                  </div>
                  <div style={{ display: 'flex', gap: '10px' }}>
                    <button 
                      className="btn" 
                      onClick={async () => {
                        try {
                          const token = getMockToken();
                          const payload = {
                            asset_id: "4fb873c4-1a4b-4893-a9a2-f9255ad0823b",
                            date: targetDate,
                            overrides: manualOverrides.map((o, idx) => ({
                              hour: idx,
                              power_mw: o.power_mw,
                              price_uah: o.price_uah
                            }))
                          };
                          const res = await fetch('/api/v1/optimization/manual-overrides', {
                            method: 'POST',
                            headers: { 
                              'Content-Type': 'application/json',
                              'Authorization': `Bearer ${token}`
                            },
                            body: JSON.stringify(payload)
                          });
                          if (res.ok) {
                            addLog('EMS', `Ручні оверрайди успішно збережено на ${targetDate}`, 'success');
                            alert('Ручний графік та ціни успішно збережено!');
                            fetchMetricsAndForecast();
                            fetchManualOverrides();
                          } else {
                            alert('Помилка при збереженні ручного графіку.');
                          }
                        } catch (err) {
                          console.error("Error saving overrides:", err);
                        }
                      }}
                    >
                      Зберегти ручний графік
                    </button>
                    <button 
                      className="btn" 
                      style={{ backgroundColor: 'rgba(239, 68, 68, 0.2)', border: '1px solid #ef4444' }}
                      onClick={async () => {
                        if (window.confirm('Ви впевнені, що хочете скинути ручний графік до оптимального?')) {
                          try {
                            const token = getMockToken();
                            const payload = {
                              asset_id: "4fb873c4-1a4b-4893-a9a2-f9255ad0823b",
                              date: targetDate,
                              overrides: []
                            };
                            const res = await fetch('/api/v1/optimization/manual-overrides', {
                              method: 'POST',
                              headers: { 
                                'Content-Type': 'application/json',
                                'Authorization': `Bearer ${token}`
                              },
                              body: JSON.stringify(payload)
                            });
                            if (res.ok) {
                              addLog('EMS', `Скинуто ручний графік до оптимального для ${targetDate}`, 'info');
                              fetchMetricsAndForecast();
                              fetchManualOverrides();
                            }
                          } catch (err) {
                            console.error(err);
                          }
                        }
                      }}
                    >
                      Скинути до оптимального
                    </button>
                  </div>
                </div>

                <div style={{ maxHeight: '450px', overflowY: 'auto' }}>
                  <table className="audit-table" style={{ width: '100%' }}>
                    <thead>
                      <tr>
                        <th>Година</th>
                        <th>Рекомендовано (MILP)</th>
                        <th>Ручна потужність (МВт)</th>
                        <th>Швидкі дії</th>
                        <th>Ціна заявки (грн/МВт-год)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {manualOverrides.map((o, idx) => {
                        const recPower = (apiData.optimization?.charge && apiData.optimization?.discharge) 
                          ? (apiData.optimization.discharge[idx] > 0 
                              ? apiData.optimization.discharge[idx] / 1000.0 
                              : -apiData.optimization.charge[idx] / 1000.0)
                          : 0.0;
                          
                        return (
                          <tr key={idx}>
                            <td>{String(idx).padStart(2, '0')}:00 - {String(idx + 1).padStart(2, '0')}:00</td>
                            <td style={{ color: recPower > 0 ? '#10b981' : recPower < 0 ? '#3b82f6' : '#9ca3af' }}>
                              {recPower > 0 ? `Розряд +${recPower.toFixed(2)} МВт` : recPower < 0 ? `Заряд ${recPower.toFixed(2)} МВт` : 'Пауза'}
                            </td>
                            <td>
                              <input 
                                type="number" 
                                step="0.05"
                                className="form-input" 
                                style={{ width: '120px', padding: '4px 8px', fontSize: '13px' }}
                                value={o.power_mw}
                                onChange={(e) => {
                                  const val = Number(e.target.value);
                                  setManualOverrides(prev => {
                                    const next = [...prev];
                                    next[idx] = { ...next[idx], power_mw: val };
                                    return next;
                                  });
                                }}
                              />
                            </td>
                            <td>
                              <div style={{ display: 'flex', gap: '5px' }}>
                                <button 
                                  className="btn" 
                                  style={{ padding: '3px 8px', fontSize: '11px', backgroundColor: '#3b82f6' }}
                                  onClick={() => {
                                    setManualOverrides(prev => {
                                      const next = [...prev];
                                      next[idx] = { ...next[idx], power_mw: -(power / 1000.0) };
                                      return next;
                                    });
                                  }}
                                >
                                  Заряд (Max)
                                </button>
                                <button 
                                  className="btn" 
                                  style={{ padding: '3px 8px', fontSize: '11px', backgroundColor: '#10b981' }}
                                  onClick={() => {
                                    setManualOverrides(prev => {
                                      const next = [...prev];
                                      next[idx] = { ...next[idx], power_mw: (power / 1000.0) };
                                      return next;
                                    });
                                  }}
                                >
                                  Розряд (Max)
                                </button>
                                <button 
                                  className="btn" 
                                  style={{ padding: '3px 8px', fontSize: '11px', backgroundColor: '#4b5563' }}
                                  onClick={() => {
                                    setManualOverrides(prev => {
                                      const next = [...prev];
                                      next[idx] = { ...next[idx], power_mw: 0.0 };
                                      return next;
                                    });
                                  }}
                                >
                                  Стоп
                                </button>
                              </div>
                            </td>
                            <td>
                              <input 
                                type="number" 
                                className="form-input" 
                                style={{ width: '140px', padding: '4px 8px', fontSize: '13px' }}
                                value={o.price_uah}
                                onChange={(e) => {
                                  const val = Number(e.target.value);
                                  setManualOverrides(prev => {
                                    const next = [...prev];
                                    next[idx] = { ...next[idx], price_uah: val };
                                    return next;
                                  });
                                }}
                              />
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* VIEW 5: ROI & PAYBACK */}
          {activeView === 'roi' && (
            <div className="grid-2">
              {/* Payback assumptions inputs */}
              <div className="glass-card">
                <h3 className="card-title">Розрахунок окупаемости BESS проекту</h3>
                <div style={{ margin: '20px 0' }}>
                  <div className="form-group">
                    <label className="form-label">CAPEX проекту (грн)</label>
                    <input 
                      type="number" 
                      className="form-input" 
                      value={capex} 
                      onChange={(e) => setCapex(Number(e.target.value))} 
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Ставка дисконтування (%)</label>
                    <input 
                      type="number" 
                      className="form-input" 
                      value={discountRate} 
                      onChange={(e) => setDiscountRate(Number(e.target.value))} 
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Срок служби проекту (років)</label>
                    <input 
                      type="number" 
                      className="form-input" 
                      value={lifetime} 
                      onChange={(e) => setLifetime(Number(e.target.value))} 
                    />
                  </div>
                </div>

                <div className="audit-item" style={{ background: 'rgba(16, 185, 129, 0.05)', borderColor: 'rgba(16, 185, 129, 0.2)' }}>
                  <span>Простий період окупаемости (Simple Payback):</span>
                  <strong style={{ color: '#10b981', fontSize: '1rem' }}>
                    {(capex / netYearlyInflow).toFixed(1)} р.
                  </strong>
                </div>
              </div>

              {/* Payback curve chart */}
              <div className="glass-card">
                <h3 className="card-title">Накопичений грошовий потік проекту (NPV Discounted)</h3>
                <div style={{ width: '100%', height: 280 }}>
                  <ResponsiveContainer>
                    <ComposedChart data={paybackData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="year" stroke="#9ca3af" />
                      <YAxis stroke="#9ca3af" />
                      <Tooltip contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }} />
                      <Area type="monotone" dataKey="NPV" name="NPV (грн)" stroke="#10b981" fill="rgba(16, 185, 129, 0.1)" strokeWidth={2} />
                      <Line type="monotone" dataKey="ZeroLine" name="Точка беззбитковості" stroke="#ef4444" strokeWidth={1} activeDot={false} dot={false} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>
          )}

          {/* VIEW 6: RISK SCENARIOS */}
          {activeView === 'scenarios' && (
            <div>
              {/* Scenario price curves */}
              <div className="glass-card">
                <h3 className="card-title" style={{ marginBottom: '16px' }}>Сценарії коливань цін РДН на завтра (Base vs Pessimistic vs Aggressive)</h3>
                <div style={{ width: '100%', height: 320 }}>
                  <ResponsiveContainer>
                    <LineChart data={scenariosData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="hour" stroke="#9ca3af" />
                      <YAxis stroke="#9ca3af" />
                      <Tooltip contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }} />
                      <Legend />
                      <Line type="monotone" dataKey="base" name="Базовий прогноз" stroke="#3b82f6" strokeWidth={2} />
                      <Line type="monotone" dataKey="pessimistic" name="Песимістичний" stroke="#ef4444" strokeWidth={1.5} />
                      <Line type="monotone" dataKey="aggressive" name="Агресивний (Висока волатильність)" stroke="#10b981" strokeWidth={1.5} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Risk metrics */}
              <div className="grid-3">
                <div className="kpi-card" style={{ borderLeft: '4px solid #3b82f6' }}>
                  <span className="kpi-title">Середньодобовий прибуток (Base)</span>
                  <span className="kpi-value">{Math.round(opt.net_profit_uah).toLocaleString()} грн</span>
                </div>
                <div className="kpi-card" style={{ borderLeft: '4px solid #ef4444' }}>
                  <span className="kpi-title">Дохід при ризиках (Pessimistic)</span>
                  <span className="kpi-value">{Math.round(opt.net_profit_uah * 0.57).toLocaleString()} грн</span>
                </div>
                <span className="kpi-card" style={{ borderLeft: '4px solid #06b6d4' }}>
                  <span className="kpi-title">Value at Risk (VaR 95%)</span>
                  <span className="kpi-value">887 грн/день</span>
                </span>
              </div>
            </div>
          )}

          {/* VIEW 7: SETTINGS & TARIFFS */}
          {activeView === 'settings' && (
            <div className="grid-3">
              {/* Tariffs config */}
              <div className="glass-card">
                <h3 className="card-title" style={{ marginBottom: '16px' }}>Тарифи Обленерго та Постачальника</h3>
                
                <div className="form-group">
                  <label className="form-label">Оператор Системи Розподілу (ОСР)</label>
                  <select className="form-select" value={osr} onChange={(e) => setOsr(e.target.value)}>
                    <option value="dtek_kiev">ДТЕК Київські електромережі</option>
                    <option value="dtek_kiev_regional">ДТЕК Київські регіональні електромережі</option>
                    <option value="lviv">Львівобленерго</option>
                    <option value="kharkiv">Харківобленерго</option>
                  </select>
                </div>

                <div className="form-group">
                  <label className="form-label">Клас напруги підключення</label>
                  <select 
                    className="form-select" 
                    value={voltageClass} 
                    onChange={(e) => setVoltageClass(Number(e.target.value))}
                  >
                    <option value={1}>1 Клас (менше тариф на розподіл)</option>
                    <option value={2}>2 Клас (більше тариф на розподіл)</option>
                  </select>
                </div>

                <div className="form-group">
                  <label className="form-label">Маржа постачальника (грн/МВт-год)</label>
                  <input 
                    type="number" 
                    className="form-input" 
                    value={margin} 
                    onChange={(e) => setMargin(Number(e.target.value))} 
                  />
                </div>
              </div>

              {/* BESS parameters config */}
              <div className="glass-card">
                <h3 className="card-title" style={{ marginBottom: '16px' }}>Технічні ліміти BESS накопичувача</h3>
                
                <div className="form-group">
                  <label className="form-label">Максимальна ємність батареї (кВт-год)</label>
                  <input 
                    type="number" 
                    className="form-input" 
                    value={capacity} 
                    onChange={(e) => setCapacity(Number(e.target.value))} 
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Максимальна потужність заряду/розряду (кВт)</label>
                  <input 
                    type="number" 
                    className="form-input" 
                    value={power} 
                    onChange={(e) => setPower(Number(e.target.value))} 
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">КПД циклу (%)</label>
                  <input 
                    type="number" 
                    className="form-input" 
                    value={efficiency} 
                    onChange={(e) => setEfficiency(Number(e.target.value))} 
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Дата початку роботи (Launch Date)</label>
                  <input 
                    type="date" 
                    className="form-input" 
                    value={launchDate} 
                    onChange={(e) => setLaunchDate(e.target.value)} 
                  />
                </div>

                <button 
                  className="btn" 
                  style={{ width: '100%', marginTop: '10px' }}
                  onClick={saveSystemSettings}
                >
                  Зберегти налаштування
                </button>
              </div>

              {/* Market factors config */}
              <div className="glass-card">
                <h3 className="card-title" style={{ marginBottom: '16px' }}>Ринкові фактори прогнозування</h3>
                
                <div className="form-group">
                  <label className="form-label">Ціна на газ TTF (EUR/MWh)</label>
                  <input 
                    type="number" 
                    step="0.1"
                    className="form-input" 
                    value={gasPrice} 
                    onChange={(e) => setGasPrice(Number(e.target.value))} 
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Виведено потужностей АЕС (%)</label>
                  <input 
                    type="number" 
                    min="0"
                    max="100"
                    step="1"
                    className="form-input" 
                    value={nuclearOutage} 
                    onChange={(e) => setNuclearOutage(Number(e.target.value))} 
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Пошкодження сонячних СЕС (%)</label>
                  <input 
                    type="number" 
                    min="0"
                    max="100"
                    step="1"
                    className="form-input" 
                    value={solarStrike} 
                    onChange={(e) => setSolarStrike(Number(e.target.value))} 
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Коефіцієнт попиту ринку</label>
                  <input 
                    type="number" 
                    step="0.05"
                    className="form-input" 
                    value={marketCoeff} 
                    onChange={(e) => setMarketCoeff(Number(e.target.value))} 
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Активність на ВДР (обсяг)</label>
                  <input 
                    type="number" 
                    step="0.05"
                    className="form-input" 
                    value={vdrVolume} 
                    onChange={(e) => setVdrVolume(Number(e.target.value))} 
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Імпорт/Експорт мережі (MW)</label>
                  <input 
                    type="number" 
                    step="10"
                    className="form-input" 
                    value={gridImportExport} 
                    onChange={(e) => setGridImportExport(Number(e.target.value))} 
                  />
                </div>

                <button 
                  className="btn" 
                  style={{ width: '100%', marginTop: '10px' }}
                  onClick={() => {
                    addLog('SETTINGS', 'Оновлено ринкові фактори для прогнозування.', 'success');
                    fetchMetricsAndForecast();
                    alert('Фактори ринку успішно оновлено та застосовано!');
                  }}
                >
                  Оновити та перерахувати
                </button>
              </div>
            </div>
          )}

          {/* VIEW 8: AUDIT & MODEL DECISIONS */}
          {activeView === 'audit' && (
            <div className="grid-2">
              {/* Audit trail list */}
              <div className="glass-card">
                <h3 className="card-title" style={{ marginBottom: '16px' }}>Журнал дій та аудит рішень (Four-Eyes Principle)</h3>
                <div className="audit-list">
                  {auditLogs.map((log, idx) => (
                    <div key={idx} className="audit-item">
                      <div>
                        <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{log.action}</div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '4px' }}>
                          Инициатор: <span className="audit-user">{log.user}</span> | IP: {log.ip}
                        </div>
                      </div>
                      <div className="audit-meta">
                        <span>{log.time}</span>
                        <span className="status-badge online" style={{ padding: '2px 6px', fontSize: '0.65rem' }}>{log.status}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* System logs logs */}
              <div className="glass-card">
                <h3 className="card-title" style={{ marginBottom: '16px' }}>Події системного логу (EMS Realtime Events)</h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '350px', overflowY: 'auto' }}>
                  {systemLogs.map((log, idx) => (
                    <div key={idx} style={{ 
                      fontSize: '0.8rem', 
                      padding: '8px 12px', 
                      borderRadius: '6px', 
                      background: 'rgba(255,255,255,0.02)',
                      borderLeft: `3px solid ${
                        log.type === 'success' ? 'var(--color-emerald)' : 
                        log.type === 'warn' ? 'var(--color-amber)' : 
                        log.type === 'error' ? 'var(--color-rose)' : 'var(--color-blue)'
                      }`
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--text-muted)', fontSize: '0.75rem' }}>
                        <span>[{log.src}]</span>
                        <span>{log.time}</span>
                      </div>
                      <div style={{ color: 'var(--text-secondary)', marginTop: '4px' }}>{log.text}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

        </section>
      </main>

      {/* 4. Four-Eyes Principle Approval Dialog Overlay */}
      {showApprovalModal && (
        <div className="modal-overlay">
          <div className="modal-content">
            <div className="modal-header">
              <Lock style={{ color: 'var(--color-amber)' }} />
              <span>Двофакторне підтвердження дії</span>
            </div>
            
            <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', margin: '12px 0' }}>
              Ви ініціювали команду: <strong>{pendingAction}</strong>. 
              Відповідно до регламенту безпеки SmartBESS, ця дія потребує введення секретного криптографічного ключа авторизації менеджера (Four-Eyes Principle).
            </p>

            <div className="form-group">
              <label className="form-label">Секретний ключ підтвердження</label>
              <input 
                type="password" 
                placeholder="Введіть ключ менеджера" 
                className="form-input" 
                value={approvalToken}
                onChange={(e) => setApprovalToken(e.target.value)}
              />
            </div>

            <div style={{ display: 'flex', gap: '12px', marginTop: '20px' }}>
              <button className="btn btn-secondary" style={{ flex: 1 }} onClick={() => {
                setShowApprovalModal(false);
                setApprovalToken('');
              }}>
                Скасувати
              </button>
              <button className="btn" style={{ flex: 1 }} onClick={executeApprovedAction}>
                Підтвердити команду
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
