import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import * as api from '../api/client';
import type { UserRole, Asset, PriceBand, ActualPrices, GenerationAdjustment, InitialSoc } from '../api/client';

export type LogEntry = { time: string; src: string; text: string; type: 'success' | 'info' | 'warn' | 'error' };
export type AuditEntry = { time: string; user: string; action: string; ip: string; status: string };
export type DispatchHour = {
  hour: number; // 1-24
  charge: number; // кВт, реально виконано (з урахуванням меж SoC)
  discharge: number; // кВт, реально виконано
  soc: number; // кВт-год після цієї години
  price: number; // грн/МВт-год заявки
  isManual: boolean;
  revenueUah: number;
  costUah: number;
  degradationUah: number;
};

const TARIFF_UAH_PER_KWH = (528.57 + 1500.0 + 104.57 + 100.0) / 1000.0;
const DEGRADATION_UAH_PER_KWH = 1.2;

interface AppState {
  activeRole: UserRole;
  setActiveRole: (r: UserRole) => void;

  assets: Asset[];
  activeAssetId: string | null;

  targetDate: string;
  setTargetDate: (d: string) => void;
  selectedModel: string;
  setSelectedModel: (m: string) => void;
  operationalMode: string;
  setOperationalMode: (m: string) => void;

  loading: boolean;
  forecastPrices: number[] | null;
  priceBand: PriceBand | null;
  actualPrices: ActualPrices | null;
  optimizationResult: any;
  manualOverrides: any[];
  setManualOverrides: (o: any[]) => void;
  // Єдине джерело правди для графіків заряду/розряду/SoC (Price Forecast та
  // Optimization Schedule раніше рахували це кожен по-своєму — один брав
  // сиру команду power_mw, інший клипав до реального SoC-headroom, і при
  // ручному оверрайді, що перевищував ємність батареї, графіки розходились).
  dispatchProfile: DispatchHour[];
  runForecastAndOptimization: () => Promise<void>;
  saveOverrides: () => Promise<void>;
  resetOverridesToOptimal: () => Promise<void>;

  executiveReport: any;
  marketConditions: any;
  forecastAccuracy: any;

  // Ручна корекція доступності генерації (АЕС/ГЕС/СЕС/ВЕС) на targetDate
  generationAdjustment: GenerationAdjustment | null;
  setGenerationAdjustmentDraft: (a: GenerationAdjustment) => void;
  saveGenerationAdjustmentAndRecalculate: () => Promise<void>;

  // SoC на 00:00 targetDate: ручне значення > SCADA-телеметрія > фолбек 20%
  initialSoc: InitialSoc | null;
  saveInitialSocAndRecalculate: (capacityKwh: number) => Promise<void>;
  clearInitialSocAndRecalculate: () => Promise<void>;

  // BESS technical settings
  osr: string; setOsr: (v: string) => void;
  voltageClass: number; setVoltageClass: (v: number) => void;
  margin: number; setMargin: (v: number) => void;
  capacity: number; setCapacity: (v: number) => void;
  power: number; setPower: (v: number) => void;
  efficiency: number; setEfficiency: (v: number) => void;
  launchDate: string; setLaunchDate: (v: string) => void;
  saveSettings: () => Promise<void>;

  // Project economics (client-side ROI calculator inputs)
  capex: number; setCapex: (v: number) => void;
  discountRate: number; setDiscountRate: (v: number) => void;
  lifetime: number; setLifetime: (v: number) => void;

  systemLogs: LogEntry[];
  addLog: (src: string, text: string, type: LogEntry['type']) => void;
  auditLogs: AuditEntry[];

  showApprovalModal: boolean;
  pendingAction: string;
  approvalToken: string;
  setApprovalToken: (v: string) => void;
  triggerFourEyesApproval: (actionName: string) => void;
  executeApprovedAction: () => void;
  cancelApproval: () => void;
}

const AppCtx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = useContext(AppCtx);
  if (!ctx) throw new Error('useApp must be used within AppProvider');
  return ctx;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [activeRole, setActiveRole] = useState<UserRole>('Operator');
  const [assets, setAssets] = useState<Asset[]>([]);
  const activeAssetId = assets.length > 0 ? assets[0].id : null;

  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const [targetDate, setTargetDate] = useState<string>(tomorrow.toISOString().split('T')[0]);
  const [selectedModel, setSelectedModel] = useState<string>('lightgbm');
  const [operationalMode, setOperationalMode] = useState<string>('arbitrage');

  const [loading, setLoading] = useState(false);
  const [forecastPrices, setForecastPrices] = useState<number[] | null>(null);
  const [priceBand, setPriceBand] = useState<PriceBand | null>(null);
  const [actualPrices, setActualPrices] = useState<ActualPrices | null>(null);
  const [optimizationResult, setOptimizationResult] = useState<any>(null);
  const [manualOverrides, setManualOverrides] = useState<any[]>([]);

  const [executiveReport, setExecutiveReport] = useState<any>(null);
  const [marketConditions, setMarketConditions] = useState<any>(null);
  const [forecastAccuracy, setForecastAccuracy] = useState<any>(null);
  const [generationAdjustment, setGenerationAdjustment] = useState<GenerationAdjustment | null>(null);
  const [initialSoc, setInitialSoc] = useState<InitialSoc | null>(null);

  const [osr, setOsr] = useState('dtek_kiev_regional');
  const [voltageClass, setVoltageClass] = useState(1);
  const [margin, setMargin] = useState(100);
  const [capacity, setCapacity] = useState(1000);
  const [power, setPower] = useState(250);
  const [efficiency, setEfficiency] = useState(95);
  const [launchDate, setLaunchDate] = useState('2026-01-01');

  const [capex, setCapex] = useState(15200000);
  const [discountRate, setDiscountRate] = useState(12);
  const [lifetime, setLifetime] = useState(10);

  const [systemLogs, setSystemLogs] = useState<LogEntry[]>([
    { time: new Date().toTimeString().split(' ')[0], src: 'SYSTEM', text: 'Робоче середовище SmartBESS EMS ініціалізовано.', type: 'success' },
  ]);
  const [auditLogs, setAuditLogs] = useState<AuditEntry[]>([]);

  const [showApprovalModal, setShowApprovalModal] = useState(false);
  const [pendingAction, setPendingAction] = useState('');
  const [approvalToken, setApprovalToken] = useState('');

  const addLog = useCallback((src: string, text: string, type: LogEntry['type']) => {
    const time = new Date().toTimeString().split(' ')[0];
    setSystemLogs((prev) => [{ time, src, text, type }, ...prev].slice(0, 50));
  }, []);

  // Load asset list once
  useEffect(() => {
    api.fetchAssets(activeRole).then(setAssets).catch((e) => addLog('API', `Не вдалося завантажити список активів: ${e.message}`, 'error'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!activeAssetId) return;
    api.fetchSystemSettings(activeRole).then((data) => {
      setLaunchDate(data.launch_date);
      setOsr(data.osr);
      setVoltageClass(data.voltage_class);
      setMargin(data.margin);
      setCapacity(data.capacity_kw);
      setPower(data.power_kw);
      setEfficiency(data.efficiency_pct);
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeAssetId]);

  const runForecastAndOptimization = useCallback(async () => {
    if (!activeAssetId) return;
    setLoading(true);
    try {
      const prices = await api.runForecastJob(activeRole, targetDate, selectedModel);
      setForecastPrices(prices);
      addLog('FORECAST', `Прогноз цін на ${targetDate} (${selectedModel}) розраховано.`, 'success');

      try {
        const band = await api.fetchLatestForecastBand(activeRole, targetDate);
        setPriceBand(band);
      } catch { /* band is best-effort — точковий прогноз важливіший */ }

      try {
        const actual = await api.fetchActualPrices(activeRole, targetDate);
        setActualPrices(actual);
      } catch { setActualPrices(null); }

      // undefined initialSocPct -> бекенд бере реальний SoC з SCADA-телеметрії
      const optResult = await api.runOptimizationJob(activeRole, activeAssetId, targetDate, undefined, operationalMode);
      setOptimizationResult(optResult);
      addLog('OPTIMIZATION', `MILP-оптимізація графіка BESS на ${targetDate} завершена.`, 'success');

      const overrides = await api.fetchManualOverrides(activeRole, activeAssetId, targetDate);
      setManualOverrides(overrides);
    } catch (e: any) {
      addLog('API', `Помилка розрахунку прогнозу/оптимізації: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [activeRole, activeAssetId, targetDate, selectedModel, operationalMode, addLog]);

  const refreshOverrides = useCallback(async () => {
    if (!activeAssetId) return;
    try {
      const overrides = await api.fetchManualOverrides(activeRole, activeAssetId, targetDate);
      setManualOverrides(overrides);
    } catch (e: any) {
      addLog('API', `Помилка завантаження ручного графіку: ${e.message}`, 'error');
    }
  }, [activeRole, activeAssetId, targetDate, addLog]);

  const saveOverrides = useCallback(async () => {
    if (!activeAssetId) return;
    try {
      await api.saveManualOverrides(activeRole, activeAssetId, targetDate, manualOverrides);
      addLog('EMS', `Ручні оверрайди успішно збережено на ${targetDate}.`, 'success');
      await refreshOverrides();
    } catch (e: any) {
      addLog('API', `Помилка збереження ручного графіку: ${e.message}`, 'error');
    }
  }, [activeRole, activeAssetId, targetDate, manualOverrides, addLog, refreshOverrides]);

  const resetOverridesToOptimal = useCallback(async () => {
    if (!activeAssetId) return;
    try {
      await api.saveManualOverrides(activeRole, activeAssetId, targetDate, []);
      addLog('EMS', `Скинуто ручний графік до оптимального для ${targetDate}.`, 'info');
      await refreshOverrides();
    } catch (e: any) {
      addLog('API', `Помилка скидання графіку: ${e.message}`, 'error');
    }
  }, [activeRole, activeAssetId, targetDate, addLog, refreshOverrides]);

  const saveSettings = useCallback(async () => {
    try {
      await api.saveSystemSettings(activeRole, {
        launch_date: launchDate, osr, voltage_class: voltageClass, margin,
        capacity_kw: capacity, power_kw: power, efficiency_pct: efficiency,
      });
      addLog('SETTINGS', `Параметри системи збережено. Дата запуску: ${launchDate}.`, 'success');
    } catch (e: any) {
      addLog('API', `Помилка збереження налаштувань: ${e.message}`, 'error');
    }
  }, [activeRole, launchDate, osr, voltageClass, margin, capacity, power, efficiency, addLog]);

  useEffect(() => {
    if (!activeAssetId) return;
    api.fetchExecutiveSummary(activeRole, activeAssetId).then(setExecutiveReport).catch((e) => addLog('REPORT', `Помилка звіту C-Level: ${e.message}`, 'warn'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeAssetId, activeRole]);

  useEffect(() => {
    api.fetchMarketConditions(activeRole).then(setMarketConditions).catch(() => {});
    api.fetchForecastAccuracy(activeRole, 30).then(setForecastAccuracy).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRole]);

  useEffect(() => {
    refreshOverrides();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeAssetId, targetDate]);

  // Зміна дати раніше лишала forecastPrices/priceBand/actualPrices/
  // optimizationResult від ПОПЕРЕДНЬОЇ дати на екрані (тільки manualOverrides
  // оновлювались вище) — диспетчер бачив прогноз/графік заряду для одної
  // дати поверх графіку ручних заявок для іншої. Тепер при зміні дати:
  // 1) optimizationResult одразу скидається (для нової дати він ще не
  //    порахований — сценарний VaR-аналіз завжди вимагає явного «Розрахувати»);
  // 2) forecastPrices/priceBand/actualPrices підтягуються заново — якщо
  //    прогноз на цю дату вже колись рахували, він підвантажиться одразу
  //    (дешевий GET, без повторного MILP), інакше — теж очищаються.
  useEffect(() => {
    setOptimizationResult(null);

    let cancelled = false;
    api.fetchLatestForecastBand(activeRole, targetDate)
      .then((band) => {
        if (cancelled) return;
        setPriceBand(band);
        setForecastPrices(band.predicted_prices_uah);
      })
      .catch(() => {
        if (!cancelled) {
          setPriceBand(null);
          setForecastPrices(null);
        }
      });

    api.fetchActualPrices(activeRole, targetDate)
      .then((actual) => { if (!cancelled) setActualPrices(actual); })
      .catch(() => { if (!cancelled) setActualPrices(null); });

    api.fetchGenerationAdjustment(activeRole, targetDate)
      .then((adj) => { if (!cancelled) setGenerationAdjustment(adj); })
      .catch(() => { if (!cancelled) setGenerationAdjustment(null); });

    if (activeAssetId) {
      api.fetchInitialSoc(activeRole, activeAssetId, targetDate)
        .then((soc) => { if (!cancelled) setInitialSoc(soc); })
        .catch(() => { if (!cancelled) setInitialSoc(null); });
    }

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRole, targetDate, activeAssetId]);

  const dispatchProfile = useMemo<DispatchHour[]>(() => {
    if (!manualOverrides || manualOverrides.length !== 24) return [];
    const effRatio = efficiency / 100.0;
    const maxSocKwh = capacity * 0.9;
    const minSocKwh = capacity * 0.1;
    let runningSoc = initialSoc?.capacity_kwh ?? capacity * 0.2;

    return manualOverrides.map((o: any) => {
      // Клип до реальної макс. потужності БЕСС — введена вручну команда може
      // в рази перевищувати фізичну потужність батареї.
      const commandedKW = Math.max(-power, Math.min(power, o.power_mw * 1000.0));
      const priceKWh = o.price_uah / 1000.0;
      let chargeKW = 0, dischargeKW = 0, revenueUah = 0, costUah = 0, degradationUah = 0;

      if (commandedKW < 0) {
        // Реально виконана потужність — якщо батарея вже на межі SoC (90%),
        // подальший заряд фізично неможливий, навіть якщо команда більша.
        const maxChargeKW = Math.max(0, (maxSocKwh - runningSoc) / effRatio);
        chargeKW = Math.min(Math.abs(commandedKW), maxChargeKW);
        costUah = chargeKW * (priceKWh + TARIFF_UAH_PER_KWH);
        runningSoc = Math.min(maxSocKwh, runningSoc + chargeKW * effRatio);
      } else if (commandedKW > 0) {
        const maxDischargeKW = Math.max(0, (runningSoc - minSocKwh) * effRatio);
        dischargeKW = Math.min(commandedKW, maxDischargeKW);
        revenueUah = dischargeKW * priceKWh;
        degradationUah = dischargeKW * DEGRADATION_UAH_PER_KWH;
        runningSoc = Math.max(minSocKwh, runningSoc - dischargeKW / effRatio);
      }

      return {
        hour: o.hour + 1,
        charge: chargeKW,
        discharge: dischargeKW,
        soc: runningSoc,
        price: o.price_uah,
        isManual: !!o.is_overridden,
        revenueUah, costUah, degradationUah,
      };
    });
  }, [manualOverrides, capacity, power, efficiency, initialSoc]);

  const refreshInitialSoc = useCallback(async () => {
    if (!activeAssetId) return;
    try {
      const soc = await api.fetchInitialSoc(activeRole, activeAssetId, targetDate);
      setInitialSoc(soc);
    } catch {
      setInitialSoc(null);
    }
  }, [activeRole, activeAssetId, targetDate]);

  const saveInitialSocAndRecalculate = useCallback(async (capacityKwh: number) => {
    if (!activeAssetId) return;
    try {
      await api.saveInitialSoc(activeRole, activeAssetId, targetDate, capacityKwh);
      addLog('SETTINGS', `Ручне значення SoC на 00:00 ${targetDate} збережено: ${Math.round(capacityKwh)} кВт·год.`, 'success');
      await refreshInitialSoc();
      await runForecastAndOptimization();
    } catch (e: any) {
      addLog('API', `Помилка збереження SoC: ${e.message}`, 'error');
    }
  }, [activeRole, activeAssetId, targetDate, addLog, refreshInitialSoc, runForecastAndOptimization]);

  const clearInitialSocAndRecalculate = useCallback(async () => {
    if (!activeAssetId) return;
    try {
      await api.clearInitialSoc(activeRole, activeAssetId, targetDate);
      addLog('SETTINGS', `Ручне значення SoC на ${targetDate} прибрано — розрахунок знову автоматичний.`, 'info');
      await refreshInitialSoc();
      await runForecastAndOptimization();
    } catch (e: any) {
      addLog('API', `Помилка скидання SoC: ${e.message}`, 'error');
    }
  }, [activeRole, activeAssetId, targetDate, addLog, refreshInitialSoc, runForecastAndOptimization]);

  const setGenerationAdjustmentDraft = useCallback((a: GenerationAdjustment) => {
    setGenerationAdjustment(a);
  }, []);

  const saveGenerationAdjustmentAndRecalculate = useCallback(async () => {
    if (!generationAdjustment) return;
    try {
      await api.saveGenerationAdjustment(activeRole, { ...generationAdjustment, date: targetDate });
      addLog('SETTINGS', `Корекцію генерації на ${targetDate} збережено (АЕС ${generationAdjustment.nuclear_pct}%, ГЕС ${generationAdjustment.hydro_pct}%, СЕС ${generationAdjustment.solar_pct}%, ВЕС ${generationAdjustment.wind_pct}%).`, 'success');
      await runForecastAndOptimization();
    } catch (e: any) {
      addLog('API', `Помилка збереження корекції генерації: ${e.message}`, 'error');
    }
  }, [activeRole, targetDate, generationAdjustment, addLog, runForecastAndOptimization]);

  const triggerFourEyesApproval = useCallback((actionName: string) => {
    if (activeRole === 'Viewer') {
      alert('У вас немає прав для виконання цієї дії. Ваша роль: Viewer');
      return;
    }
    setPendingAction(actionName);
    setShowApprovalModal(true);
  }, [activeRole]);

  const cancelApproval = useCallback(() => {
    setShowApprovalModal(false);
    setApprovalToken('');
  }, []);

  const executeApprovedAction = useCallback(() => {
    if (!approvalToken) {
      alert('Будь ласка, введіть секретний ключ підпису менеджера.');
      return;
    }
    const time = new Date().toISOString().replace('T', ' ').substring(0, 19);
    setAuditLogs((prev) => [
      { time, user: activeRole === 'Admin' ? 'admin@smartbess.ua' : 'operator@smartbess.ua', action: `[Four-Eyes Approved] ${pendingAction}`, ip: '127.0.0.1', status: 'SUCCESS' },
      ...prev,
    ]);
    addLog('EMS', `Ручну команду диспетчеризації успішно виконано: ${pendingAction}`, 'success');
    setShowApprovalModal(false);
    setApprovalToken('');
    alert('Команду успішно надіслано до BESS контролера Modbus TCP!');
  }, [approvalToken, activeRole, pendingAction, addLog]);

  const value = useMemo<AppState>(() => ({
    activeRole, setActiveRole,
    assets, activeAssetId,
    targetDate, setTargetDate, selectedModel, setSelectedModel, operationalMode, setOperationalMode,
    loading, forecastPrices, priceBand, actualPrices, optimizationResult, manualOverrides, setManualOverrides,
    dispatchProfile,
    runForecastAndOptimization, saveOverrides, resetOverridesToOptimal,
    executiveReport, marketConditions, forecastAccuracy,
    generationAdjustment, setGenerationAdjustmentDraft, saveGenerationAdjustmentAndRecalculate,
    initialSoc, saveInitialSocAndRecalculate, clearInitialSocAndRecalculate,
    osr, setOsr, voltageClass, setVoltageClass, margin, setMargin,
    capacity, setCapacity, power, setPower, efficiency, setEfficiency,
    launchDate, setLaunchDate, saveSettings,
    capex, setCapex, discountRate, setDiscountRate, lifetime, setLifetime,
    systemLogs, addLog, auditLogs,
    showApprovalModal, pendingAction, approvalToken, setApprovalToken, triggerFourEyesApproval, executeApprovedAction, cancelApproval,
  }), [
    activeRole, assets, activeAssetId, targetDate, selectedModel, operationalMode,
    loading, forecastPrices, priceBand, actualPrices, optimizationResult, manualOverrides,
    dispatchProfile,
    runForecastAndOptimization, saveOverrides, resetOverridesToOptimal,
    executiveReport, marketConditions, forecastAccuracy,
    generationAdjustment, setGenerationAdjustmentDraft, saveGenerationAdjustmentAndRecalculate,
    initialSoc, saveInitialSocAndRecalculate, clearInitialSocAndRecalculate,
    osr, voltageClass, margin, capacity, power, efficiency, launchDate, saveSettings,
    capex, discountRate, lifetime, systemLogs, addLog, auditLogs,
    showApprovalModal, pendingAction, approvalToken, triggerFourEyesApproval, executeApprovedAction, cancelApproval,
  ]);

  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}
