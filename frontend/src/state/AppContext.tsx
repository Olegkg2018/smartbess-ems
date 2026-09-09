import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import * as api from '../api/client';
import type { UserRole, Asset, PriceBand, ActualPrices, GenerationAdjustment, PriceShift, InitialSoc, GridStress, BidMargin, MarketBid, ScadaStatus } from '../api/client';

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
  // 2026-09-08: раніше costUah включав мережевий тариф на купівлю
  // (плюсувався мовчки) — за проханням користувача винесено в окреме поле,
  // щоб "Заявка РДН"/KPI показували чисту вартість купленої енергії окремо
  // від витрат на доставку (для обліку, не впливає на саму заявку/диспетчеризацію).
  deliveryCostUah: number;
  degradationUah: number;
};

const TARIFF_UAH_PER_KWH = (528.57 + 1500.0 + 104.57 + 100.0) / 1000.0;
const DEGRADATION_UAH_PER_KWH = 1.2;

interface AppState {
  activeRole: UserRole;
  setActiveRole: (r: UserRole) => void;

  assets: Asset[];
  activeAssetId: string | null;

  scadaStatus: ScadaStatus | null;

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

  // Ручний відсотковий зсув прогнозу ціни на targetDate — post-inference
  // корекція на разову ринкову аномалію (не переучує модель, не входить у
  // ФІЧІ — див. PriceShiftOverride докстрінг у models.py)
  priceShift: PriceShift | null;
  setPriceShiftDraft: (p: PriceShift) => void;
  savePriceShiftAndRecalculate: () => Promise<void>;

  // SoC на 00:00 targetDate: ручне значення > SCADA-телеметрія > фолбек 20%
  initialSoc: InitialSoc | null;
  saveInitialSocAndRecalculate: (capacityKwh: number) => Promise<void>;
  clearInitialSocAndRecalculate: () => Promise<void>;

  // Обсяг ГПВ (у "чергах") на targetDate: автосигнал з Telegram-постів
  // Укренерго > ручна оцінка диспетчера > відсутній. НЕ впливає на поточний
  // прогноз ціни (ознака ще не в продових FEATURES моделі, короткий обсяг
  // реальної історії), лише накопичує реальні дані для майбутнього бектесту.
  gridStress: GridStress | null;
  saveGridStressOverride: (queues: number | null, note: string | null) => Promise<void>;
  clearGridStressOverride: () => Promise<void>;

  // Маржа заявки РДН на targetDate: sell=прогноз*(1-маржа), buy=прогноз*(1+маржа) —
  // ручний буфер, наскільки жертвувати очікуваним прибутком заради вищої
  // ймовірності виконання заявки (реальний механізм аукціону, не гарантоване
  // виконання за прогнозом). Заявки (bids) — окремо, з реальним статусом
  // виконання/фактичною ціною OREE після звірки.
  bidMargin: BidMargin | null;
  saveBidMarginAndRegenerate: (marginPct: number, marginUah?: number | null) => Promise<void>;
  clearBidMarginAndRegenerate: () => Promise<void>;
  bids: MarketBid[] | null;
  refreshBids: () => Promise<void>;
  actionSummary: api.ActionSummary | null;
  refreshActionSummary: () => Promise<void>;
  generateBidsNow: () => Promise<void>;
  settleBidsNow: () => Promise<void>;
  acknowledgeIdmFallbackNow: (hour: number) => Promise<void>;
  submitIdmFallbackBidNow: (hour: number, priceUah: number | null) => Promise<void>;

  dispatcherSchedule: api.DispatcherScheduleItem[] | null;
  dispatcherActions: api.DispatcherAction[];
  refreshDispatcherSchedule: () => Promise<void>;
  saveDispatcherScheduleNow: (schedule: api.DispatcherScheduleItem[]) => Promise<void>;

  // BESS technical settings
  osr: string; setOsr: (v: string) => void;
  voltageClass: number; setVoltageClass: (v: number) => void;
  margin: number; setMargin: (v: number) => void;
  capacity: number; setCapacity: (v: number) => void;
  power: number; setPower: (v: number) => void;
  efficiency: number; setEfficiency: (v: number) => void;
  maxCyclesPerDay: number; setMaxCyclesPerDay: (v: number) => void;
  bidReminderTelegramEnabled: boolean; setBidReminderTelegramEnabled: (v: boolean) => void;
  autoDispatchEnabled: boolean; setAutoDispatchEnabled: (v: boolean) => void;
  bessConnectionType: string; setBessConnectionType: (v: string) => void;
  bessTcpHost: string; setBessTcpHost: (v: string) => void;
  bessTcpPort: number; setBessTcpPort: (v: number) => void;
  bessSerialPort: string; setBessSerialPort: (v: string) => void;
  bessSerialBaudrate: number; setBessSerialBaudrate: (v: number) => void;
  bessSerialParity: string; setBessSerialParity: (v: string) => void;
  bessSerialStopbits: number; setBessSerialStopbits: (v: number) => void;
  bessSerialBytesize: number; setBessSerialBytesize: (v: number) => void;
  bessModbusUnitId: number; setBessModbusUnitId: (v: number) => void;
  exciseDutyPct: number; setExciseDutyPct: (v: number) => void;
  transformerLossPct: number; setTransformerLossPct: (v: number) => void;
  // 2026-09-09: тариф на доставку (₴/МВт·год) — раніше захардкоджений
  // (bidding_service.py::TARIFF_KWARGS, сума 2233.14), тепер редагований.
  deliveryTariffUahPerMwh: number; setDeliveryTariffUahPerMwh: (v: number) => void;
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
  const [scadaStatus, setScadaStatus] = useState<ScadaStatus | null>(null);

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
  const [priceShift, setPriceShift] = useState<PriceShift | null>(null);
  const [initialSoc, setInitialSoc] = useState<InitialSoc | null>(null);
  const [gridStress, setGridStress] = useState<GridStress | null>(null);
  const [bidMargin, setBidMargin] = useState<BidMargin | null>(null);
  const [bids, setBids] = useState<MarketBid[] | null>(null);
  const [actionSummary, setActionSummary] = useState<api.ActionSummary | null>(null);
  const [dispatcherSchedule, setDispatcherSchedule] = useState<api.DispatcherScheduleItem[] | null>(null);
  const [dispatcherActions, setDispatcherActions] = useState<api.DispatcherAction[]>([]);

  const [osr, setOsr] = useState('dtek_kiev_regional');
  const [voltageClass, setVoltageClass] = useState(1);
  const [margin, setMargin] = useState(100);
  const [capacity, setCapacity] = useState(1000);
  const [power, setPower] = useState(250);
  const [efficiency, setEfficiency] = useState(95);
  const [maxCyclesPerDay, setMaxCyclesPerDay] = useState(1.5);
  const [bidReminderTelegramEnabled, setBidReminderTelegramEnabled] = useState(true);
  // За замовчуванням ВИМКНЕНО — власник батареї свідомо вмикає повну
  // автоматизацію подачі заявок (2026-08-26, "віртуальний диспетчер").
  // Безпечний дефолт: реальний фінансовий/ринковий ризик, явна згода.
  const [autoDispatchEnabled, setAutoDispatchEnabled] = useState(false);
  // Підключення реальної батареї (2026-08-26) — дефолти дзеркалять
  // DEFAULT_BESS_* в optimization.py (tcp_port=502 — реальний Modbus-TCP
  // стандарт, не внутрішній 5020 симулятора).
  const [bessConnectionType, setBessConnectionType] = useState('simulator');
  const [bessTcpHost, setBessTcpHost] = useState('127.0.0.1');
  const [bessTcpPort, setBessTcpPort] = useState(502);
  const [bessSerialPort, setBessSerialPort] = useState('');
  const [bessSerialBaudrate, setBessSerialBaudrate] = useState(9600);
  const [bessSerialParity, setBessSerialParity] = useState('N');
  const [bessSerialStopbits, setBessSerialStopbits] = useState(1);
  const [bessSerialBytesize, setBessSerialBytesize] = useState(8);
  const [bessModbusUnitId, setBessModbusUnitId] = useState(1);
  const [exciseDutyPct, setExciseDutyPct] = useState(0);
  const [transformerLossPct, setTransformerLossPct] = useState(0);
  const [deliveryTariffUahPerMwh, setDeliveryTariffUahPerMwh] = useState(2233.14);
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

  // Реальний стан SCADA-телеметрії — раніше бейдж і сторінка Asset Detail
  // показували статичні захардкоджені значення незалежно від того, чи живий
  // симулятор. Опитуємо раз на ~20с, поки додаток відкрито.
  useEffect(() => {
    if (!activeAssetId) return;
    let cancelled = false;
    const poll = () => {
      api.fetchScadaStatus(activeRole, activeAssetId).then((s) => {
        if (!cancelled) setScadaStatus(s);
      }).catch(() => {
        if (!cancelled) setScadaStatus(null);
      });
    };
    poll();
    const interval = setInterval(poll, 20000);
    return () => { cancelled = true; clearInterval(interval); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeAssetId]);

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
      if (data.max_cycles_per_day != null) setMaxCyclesPerDay(data.max_cycles_per_day);
      if (data.bid_reminder_telegram_enabled != null) setBidReminderTelegramEnabled(data.bid_reminder_telegram_enabled);
      if (data.auto_dispatch_enabled != null) setAutoDispatchEnabled(data.auto_dispatch_enabled);
      if (data.bess_connection_type != null) setBessConnectionType(data.bess_connection_type);
      if (data.bess_tcp_host != null) setBessTcpHost(data.bess_tcp_host);
      if (data.bess_tcp_port != null) setBessTcpPort(data.bess_tcp_port);
      if (data.bess_serial_port != null) setBessSerialPort(data.bess_serial_port);
      if (data.bess_serial_baudrate != null) setBessSerialBaudrate(data.bess_serial_baudrate);
      if (data.bess_serial_parity != null) setBessSerialParity(data.bess_serial_parity);
      if (data.bess_serial_stopbits != null) setBessSerialStopbits(data.bess_serial_stopbits);
      if (data.bess_serial_bytesize != null) setBessSerialBytesize(data.bess_serial_bytesize);
      if (data.bess_modbus_unit_id != null) setBessModbusUnitId(data.bess_modbus_unit_id);
      if (data.excise_duty_pct != null) setExciseDutyPct(data.excise_duty_pct);
      if (data.transformer_loss_pct != null) setTransformerLossPct(data.transformer_loss_pct);
      if (data.delivery_tariff_uah_per_mwh != null) setDeliveryTariffUahPerMwh(data.delivery_tariff_uah_per_mwh);
    }).catch(() => {});
    api.fetchDispatcherSchedule(activeRole).then((r) => {
      setDispatcherSchedule(r.schedule);
      setDispatcherActions(r.available_actions);
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
        max_cycles_per_day: maxCyclesPerDay,
        bid_reminder_telegram_enabled: bidReminderTelegramEnabled,
        auto_dispatch_enabled: autoDispatchEnabled,
        excise_duty_pct: exciseDutyPct,
        transformer_loss_pct: transformerLossPct,
        delivery_tariff_uah_per_mwh: deliveryTariffUahPerMwh,
        bess_connection_type: bessConnectionType,
        bess_tcp_host: bessTcpHost,
        bess_tcp_port: bessTcpPort,
        bess_serial_port: bessSerialPort,
        bess_serial_baudrate: bessSerialBaudrate,
        bess_serial_parity: bessSerialParity,
        bess_serial_stopbits: bessSerialStopbits,
        bess_serial_bytesize: bessSerialBytesize,
        bess_modbus_unit_id: bessModbusUnitId,
      });
      addLog('SETTINGS', `Параметри системи збережено. Дата запуску: ${launchDate}.`, 'success');
    } catch (e: any) {
      addLog('API', `Помилка збереження налаштувань: ${e.message}`, 'error');
    }
  }, [activeRole, launchDate, osr, voltageClass, margin, capacity, power, efficiency, maxCyclesPerDay, bidReminderTelegramEnabled, autoDispatchEnabled, exciseDutyPct, transformerLossPct, deliveryTariffUahPerMwh, bessConnectionType, bessTcpHost, bessTcpPort, bessSerialPort, bessSerialBaudrate, bessSerialParity, bessSerialStopbits, bessSerialBytesize, bessModbusUnitId, addLog]);

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
  // оновлювались вище). Диспетчер бачив прогноз/графік заряду для однієї
  // дати поверх графіку ручних заявок для іншої дати. Тепер при зміні дати:
  // 1) optimizationResult одразу скидається (для нової дати він ще не порахований —
  //    сценарний VaR-аналіз завжди вимагає явного «Розрахувати»);
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

    api.fetchPriceShift(activeRole, targetDate)
      .then((ps) => { if (!cancelled) setPriceShift(ps); })
      .catch(() => { if (!cancelled) setPriceShift(null); });

    api.fetchGridStress(activeRole, targetDate)
      .then((gs) => { if (!cancelled) setGridStress(gs); })
      .catch(() => { if (!cancelled) setGridStress(null); });

    if (activeAssetId) {
      api.fetchInitialSoc(activeRole, activeAssetId, targetDate)
        .then((soc) => { if (!cancelled) setInitialSoc(soc); })
        .catch(() => { if (!cancelled) setInitialSoc(null); });

      api.fetchBidMargin(activeRole, activeAssetId, targetDate)
        .then((m) => { if (!cancelled) setBidMargin(m); })
        .catch(() => { if (!cancelled) setBidMargin(null); });

      api.fetchBids(activeRole, activeAssetId, targetDate)
        .then((r) => { if (!cancelled) setBids(r.bids); })
        .catch(() => { if (!cancelled) setBids(null); });

      api.fetchActionSummary(activeRole, activeAssetId, targetDate)
        .then((s) => { if (!cancelled) setActionSummary(s); })
        .catch(() => { if (!cancelled) setActionSummary(null); });
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
    // 2026-08-26: раніше ця функція ЗАВЖДИ сама перераховувала SoC "з опівночі"
    // для всіх 24 годин, ігноруючи `expected_soc_mwh`, який бекенд уже
    // коректно порахував (з правильним розщепленням "минуле заморожено /
    // майбутнє від живої SCADA-телеметрії", п.43-45) — звідси графік
    // розходився із заявками й реальним станом батареї, навіть коли жодного
    // ручного оверрайду не було. Тепер: доки диспетчер НІЧОГО не редагував
    // (`is_overridden===false`) і бекенд має число для години — довіряємо
    // РІВНО бекендовому SoC. Локальна симуляція (як і раніше) вмикається
    // лише ПІСЛЯ першої ручної правки/прогалини в плані — для live-прев'ю
    // ще незбереженої зміни, де бекенд ще не знає нового курсу.
    let diverged = false;

    return manualOverrides.map((o: any) => {
      // Клип до реальної макс. потужності БЕСС — введена вручну команда може
      // в разі перевищувати фізичну потужність батареї.
      const commandedKW = Math.max(-power, Math.min(power, o.power_mw * 1000.0));
      const priceKWh = o.price_uah / 1000.0;

      if (!diverged && !o.is_overridden && o.expected_soc_mwh != null) {
        const chargeKW = commandedKW < 0 ? Math.abs(commandedKW) : 0;
        const dischargeKW = commandedKW > 0 ? commandedKW : 0;
        const revenueUah = dischargeKW * priceKWh;
        const costUah = chargeKW * priceKWh;
        const deliveryCostUah = chargeKW * TARIFF_UAH_PER_KWH;
        const degradationUah = dischargeKW * DEGRADATION_UAH_PER_KWH;
        runningSoc = o.expected_soc_mwh * 1000.0;
        return {
          hour: o.hour, charge: chargeKW, discharge: dischargeKW, soc: runningSoc,
          price: o.price_uah, isManual: false, revenueUah, costUah, deliveryCostUah, degradationUah,
        };
      }
      diverged = true;

      let chargeKW = 0, dischargeKW = 0, revenueUah = 0, costUah = 0, deliveryCostUah = 0, degradationUah = 0;

      if (commandedKW < 0) {
        // Реально виконана потужність — якщо батарея вже на межі SoC (90%),
        // подальший заряд фізично неможливий, навіть якщо команда більша.
        const maxChargeKW = Math.max(0, (maxSocKwh - runningSoc) / effRatio);
        chargeKW = Math.min(Math.abs(commandedKW), maxChargeKW);
        costUah = chargeKW * priceKWh;
        deliveryCostUah = chargeKW * TARIFF_UAH_PER_KWH;
        runningSoc = Math.min(maxSocKwh, runningSoc + chargeKW * effRatio);
      } else if (commandedKW > 0) {
        const maxDischargeKW = Math.max(0, (runningSoc - minSocKwh) * effRatio);
        dischargeKW = Math.min(commandedKW, maxDischargeKW);
        revenueUah = dischargeKW * priceKWh;
        degradationUah = dischargeKW * DEGRADATION_UAH_PER_KWH;
        runningSoc = Math.max(minSocKwh, runningSoc - dischargeKW / effRatio);
      }

      return {
        // 2026-08-26: раніше було `o.hour + 1` — графік підписував години на
        // 1 пізніше за реальні (диспетчер порівнював заявку на годину 19 з
        // барами на графіку, підписаними "20") — той самий реальний
        // Kyiv-hour, що й у таблиці заявок (`b.hour`), без зсуву.
        hour: o.hour,
        charge: chargeKW,
        discharge: dischargeKW,
        soc: runningSoc,
        price: o.price_uah,
        isManual: !!o.is_overridden,
        revenueUah, costUah, deliveryCostUah, degradationUah,
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

  const refreshBids = useCallback(async () => {
    if (!activeAssetId) return;
    try {
      const r = await api.fetchBids(activeRole, activeAssetId, targetDate);
      setBids(r.bids);
    } catch {
      setBids(null);
    }
  }, [activeRole, activeAssetId, targetDate]);

  const refreshActionSummary = useCallback(async () => {
    if (!activeAssetId) return;
    try {
      const s = await api.fetchActionSummary(activeRole, activeAssetId, targetDate);
      setActionSummary(s);
    } catch {
      setActionSummary(null);
    }
  }, [activeRole, activeAssetId, targetDate]);

  const generateBidsNow = useCallback(async () => {
    if (!activeAssetId) return;
    try {
      const r = await api.generateBids(activeRole, activeAssetId, targetDate);
      const clampSuffix = r.n_price_clamped > 0 ? `, ${r.n_price_clamped} год. з ціною скоригованою до межі OREE` : '';
      addLog('BIDS', `Заявки РДН на ${targetDate} сформовано (маржа ${r.margin_pct}%, ${r.n_bids} годин${clampSuffix}).`, r.n_price_clamped > 0 ? 'warn' : 'success');
      await refreshBids();
      await refreshActionSummary();
    } catch (e: any) {
      addLog('API', `Помилка формування заявок: ${e.message}`, 'error');
    }
  }, [activeRole, activeAssetId, targetDate, addLog, refreshBids, refreshActionSummary]);

  const settleBidsNow = useCallback(async () => {
    if (!activeAssetId) return;
    try {
      const r = await api.settleBids(activeRole, activeAssetId, targetDate);
      addLog('BIDS', `Заявки на ${targetDate} звірено з фактом OREE: виконано ${r.n_executed}, не виконано ${r.n_failed_needs_idm} (пропозиція ВДР).`, r.n_failed_needs_idm > 0 ? 'warn' : 'success');
      await refreshBids();
      await refreshActionSummary();
    } catch (e: any) {
      addLog('API', `Помилка звірки заявок: ${e.message}`, 'error');
    }
  }, [activeRole, activeAssetId, targetDate, addLog, refreshBids, refreshActionSummary]);

  const acknowledgeIdmFallbackNow = useCallback(async (hour: number) => {
    if (!activeAssetId) return;
    try {
      await api.acknowledgeIdmFallback(activeRole, activeAssetId, targetDate, hour);
      addLog('BIDS', `Година ${hour} на ${targetDate}: диспетчер підтвердив, що ВДР-фолбек опрацьовано вручну.`, 'success');
      await refreshBids();
      await refreshActionSummary();
    } catch (e: any) {
      addLog('API', `Помилка підтвердження ВДР-фолбеку: ${e.message}`, 'error');
    }
  }, [activeRole, activeAssetId, targetDate, addLog, refreshBids, refreshActionSummary]);

  // priceUah=null — подати за запропонованою ціною без правок; вказано —
  // диспетчерська корекція (2026-08-28, "подача заявки на ВДР з можливістю
  // скоригувати ціну").
  const submitIdmFallbackBidNow = useCallback(async (hour: number, priceUah: number | null) => {
    if (!activeAssetId) return;
    try {
      const result: any = await api.submitIdmFallbackBid(activeRole, activeAssetId, targetDate, hour, priceUah);
      if (result?.price_clamped) {
        addLog('BIDS', `Година ${hour} на ${targetDate}: ціну ВДР-заявки скориговано до легальних меж OREE.`, 'warn');
      }
      addLog('BIDS', `Година ${hour} на ${targetDate}: заявку на ВДР подано.`, 'success');
      await refreshBids();
      await refreshActionSummary();
    } catch (e: any) {
      addLog('API', `Помилка подачі заявки на ВДР: ${e.message}`, 'error');
    }
  }, [activeRole, activeAssetId, targetDate, addLog, refreshBids, refreshActionSummary]);

  const refreshDispatcherSchedule = useCallback(async () => {
    try {
      const r = await api.fetchDispatcherSchedule(activeRole);
      setDispatcherSchedule(r.schedule);
      setDispatcherActions(r.available_actions);
    } catch (e: any) {
      addLog('API', `Помилка завантаження сценарію віртуального диспетчера: ${e.message}`, 'error');
    }
  }, [activeRole, addLog]);

  const saveDispatcherScheduleNow = useCallback(async (schedule: api.DispatcherScheduleItem[]) => {
    try {
      await api.saveDispatcherSchedule(activeRole, schedule);
      addLog('SETTINGS', 'Сценарій роботи віртуального диспетчера збережено та застосовано (без рестарту сервера).', 'success');
      await refreshDispatcherSchedule();
    } catch (e: any) {
      addLog('API', `Помилка збереження сценарію віртуального диспетчера: ${e.message}`, 'error');
    }
  }, [activeRole, addLog, refreshDispatcherSchedule]);

  const refreshBidMargin = useCallback(async () => {
    if (!activeAssetId) return;
    try {
      const m = await api.fetchBidMargin(activeRole, activeAssetId, targetDate);
      setBidMargin(m);
    } catch {
      setBidMargin(null);
    }
  }, [activeRole, activeAssetId, targetDate]);

  // marginUah=null — звичайний відсотковий режим; задано — АБСОЛЮТНИЙ буфер
  // ₴/МВт·год, пріоритетний над marginPct (2026-09-08).
  const saveBidMarginAndRegenerate = useCallback(async (marginPct: number, marginUah: number | null = null) => {
    if (!activeAssetId) return;
    try {
      await api.saveBidMargin(activeRole, activeAssetId, targetDate, marginPct, marginUah);
      addLog('SETTINGS', marginUah != null
        ? `Абсолютний буфер заявки на ${targetDate} збережено: ${marginUah} ₴/МВт·год.`
        : `Маржу заявки на ${targetDate} збережено: ${marginPct}%.`, 'success');
      await refreshBidMargin();
      await generateBidsNow();
    } catch (e: any) {
      addLog('API', `Помилка збереження маржі: ${e.message}`, 'error');
    }
  }, [activeRole, activeAssetId, targetDate, addLog, refreshBidMargin, generateBidsNow]);

  const clearBidMarginAndRegenerate = useCallback(async () => {
    if (!activeAssetId) return;
    try {
      await api.clearBidMargin(activeRole, activeAssetId, targetDate);
      addLog('SETTINGS', `Ручну маржу заявки на ${targetDate} прибрано — знову дефолт.`, 'info');
      await refreshBidMargin();
      await generateBidsNow();
    } catch (e: any) {
      addLog('API', `Помилка скидання маржі: ${e.message}`, 'error');
    }
  }, [activeRole, activeAssetId, targetDate, addLog, refreshBidMargin, generateBidsNow]);

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

  const setPriceShiftDraft = useCallback((p: PriceShift) => {
    setPriceShift(p);
  }, []);

  const savePriceShiftAndRecalculate = useCallback(async () => {
    if (!priceShift) return;
    try {
      await api.savePriceShift(activeRole, { ...priceShift, date: targetDate });
      addLog('SETTINGS', `Ручний зсув прогнозу на ${targetDate} збережено (${priceShift.shift_pct > 0 ? '+' : ''}${priceShift.shift_pct}%).`, 'success');
      await runForecastAndOptimization();
    } catch (e: any) {
      addLog('API', `Помилка збереження зсуву прогнозу: ${e.message}`, 'error');
    }
  }, [activeRole, targetDate, priceShift, addLog, runForecastAndOptimization]);

  const refreshGridStress = useCallback(async () => {
    try {
      const gs = await api.fetchGridStress(activeRole, targetDate);
      setGridStress(gs);
    } catch {
      setGridStress(null);
    }
  }, [activeRole, targetDate]);

  const saveGridStressOverride = useCallback(async (queues: number | null, note: string | null) => {
    try {
      await api.saveGridStress(activeRole, targetDate, queues, note);
      addLog('SETTINGS', `Ручну оцінку обсягу ГПВ на ${targetDate} збережено (${queues ?? '—'} черги). Накопичується для майбутнього перенавчання моделі — на поточний прогноз ще не впливає.`, 'success');
      await refreshGridStress();
    } catch (e: any) {
      addLog('API', `Помилка збереження оцінки ГПВ: ${e.message}`, 'error');
    }
  }, [activeRole, targetDate, addLog, refreshGridStress]);

  const clearGridStressOverride = useCallback(async () => {
    try {
      await api.clearGridStress(activeRole, targetDate);
      addLog('SETTINGS', `Ручну оцінку обсягу ГПВ на ${targetDate} прибрано.`, 'info');
      await refreshGridStress();
    } catch (e: any) {
      addLog('API', `Помилка скидання оцінки ГПВ: ${e.message}`, 'error');
    }
  }, [activeRole, targetDate, addLog, refreshGridStress]);

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
      alert('Будь ласка, введіть підтвердження.');
      return;
    }
    // Демо-панель: жодного виклику API немає, команда НІКУДИ не надсилається
    // (backend-ендпоінта для неї не існує — на відміну від Optimization
    // Schedule → "Ручна потужність", яка реально пише ManualOverride в БД).
    // Формулювання нижче навмисно чесне, а не "успішно виконано".
    const time = new Date().toISOString().replace('T', ' ').substring(0, 19);
    setAuditLogs((prev) => [
      { time, user: activeRole === 'Admin' ? 'admin@smartbess.ua' : 'operator@smartbess.ua', action: `[DEMO, не надіслано на BESS] ${pendingAction}`, ip: '127.0.0.1', status: 'DEMO' },
      ...prev,
    ]);
    addLog('EMS', `[DEMO] Дію записано в аудит-лог: ${pendingAction}. Реальна команда на контролер BESS НЕ надсилається.`, 'info');
    setShowApprovalModal(false);
    setApprovalToken('');
    alert('Дію записано в демо-аудит-лог. Реальна команда на контролер BESS НЕ надсилається — цей функціонал ще не підключено до backend.');
  }, [approvalToken, activeRole, pendingAction, addLog]);

  const value = useMemo<AppState>(() => ({
    activeRole, setActiveRole,
    assets, activeAssetId,
    scadaStatus,
    targetDate, setTargetDate, selectedModel, setSelectedModel, operationalMode, setOperationalMode,
    loading, forecastPrices, priceBand, actualPrices, optimizationResult, manualOverrides, setManualOverrides,
    dispatchProfile,
    runForecastAndOptimization, saveOverrides, resetOverridesToOptimal,
    executiveReport, marketConditions, forecastAccuracy,
    generationAdjustment, setGenerationAdjustmentDraft, saveGenerationAdjustmentAndRecalculate,
    priceShift, setPriceShiftDraft, savePriceShiftAndRecalculate,
    initialSoc, saveInitialSocAndRecalculate, clearInitialSocAndRecalculate,
    gridStress, saveGridStressOverride, clearGridStressOverride,
    bidMargin, saveBidMarginAndRegenerate, clearBidMarginAndRegenerate,
    bids, refreshBids, actionSummary, refreshActionSummary, generateBidsNow, settleBidsNow, acknowledgeIdmFallbackNow, submitIdmFallbackBidNow,
    dispatcherSchedule, dispatcherActions, refreshDispatcherSchedule, saveDispatcherScheduleNow,
    osr, setOsr, voltageClass, setVoltageClass, margin, setMargin,
    capacity, setCapacity, power, setPower, efficiency, setEfficiency,
    maxCyclesPerDay, setMaxCyclesPerDay, bidReminderTelegramEnabled, setBidReminderTelegramEnabled,
    autoDispatchEnabled, setAutoDispatchEnabled,
    bessConnectionType, setBessConnectionType, bessTcpHost, setBessTcpHost, bessTcpPort, setBessTcpPort,
    bessSerialPort, setBessSerialPort, bessSerialBaudrate, setBessSerialBaudrate,
    bessSerialParity, setBessSerialParity, bessSerialStopbits, setBessSerialStopbits,
    bessSerialBytesize, setBessSerialBytesize, bessModbusUnitId, setBessModbusUnitId,
    exciseDutyPct, setExciseDutyPct, transformerLossPct, setTransformerLossPct,
    deliveryTariffUahPerMwh, setDeliveryTariffUahPerMwh,
    launchDate, setLaunchDate, saveSettings,
    capex, setCapex, discountRate, setDiscountRate, lifetime, setLifetime,
    systemLogs, addLog, auditLogs,
    showApprovalModal, pendingAction, approvalToken, setApprovalToken, triggerFourEyesApproval, executeApprovedAction, cancelApproval,
  }), [
    activeRole, assets, activeAssetId, scadaStatus, targetDate, selectedModel, operationalMode,
    loading, forecastPrices, priceBand, actualPrices, optimizationResult, manualOverrides,
    dispatchProfile,
    runForecastAndOptimization, saveOverrides, resetOverridesToOptimal,
    executiveReport, marketConditions, forecastAccuracy,
    generationAdjustment, setGenerationAdjustmentDraft, saveGenerationAdjustmentAndRecalculate,
    priceShift, setPriceShiftDraft, savePriceShiftAndRecalculate,
    initialSoc, saveInitialSocAndRecalculate, clearInitialSocAndRecalculate,
    gridStress, saveGridStressOverride, clearGridStressOverride,
    bidMargin, saveBidMarginAndRegenerate, clearBidMarginAndRegenerate,
    bids, refreshBids, actionSummary, refreshActionSummary, generateBidsNow, settleBidsNow, acknowledgeIdmFallbackNow, submitIdmFallbackBidNow,
    dispatcherSchedule, dispatcherActions, refreshDispatcherSchedule, saveDispatcherScheduleNow,
    osr, voltageClass, margin, capacity, power, efficiency, maxCyclesPerDay, bidReminderTelegramEnabled, autoDispatchEnabled, launchDate, saveSettings,
    bessConnectionType, bessTcpHost, bessTcpPort, bessSerialPort, bessSerialBaudrate, bessSerialParity, bessSerialStopbits, bessSerialBytesize, bessModbusUnitId,
    capex, discountRate, lifetime, systemLogs, addLog, auditLogs,
    showApprovalModal, pendingAction, approvalToken, triggerFourEyesApproval, executeApprovedAction, cancelApproval,
  ]);

  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}
