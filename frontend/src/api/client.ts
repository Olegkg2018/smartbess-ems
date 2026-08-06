export type UserRole = 'Viewer' | 'Operator' | 'Manager' | 'Admin';

export function getMockToken(role: UserRole): string {
  const encodeBase64Url = (obj: any) => {
    const str = JSON.stringify(obj);
    const base64 = btoa(
      encodeURIComponent(str).replace(/%([0-9A-F]{2})/g, (_, p1) => {
        return String.fromCharCode(parseInt(p1, 16));
      })
    );
    return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
  };

  const header = { alg: 'none', typ: 'JWT' };
  const payload = {
    preferred_username: `${role.toLowerCase()}@smartbess.ua`,
    roles: [role],
    realm_access: { roles: [role] },
    resource_access: { 'smartbess-platform': { roles: [role] } },
  };

  return `${encodeBase64Url(header)}.${encodeBase64Url(payload)}.`;
}

async function authFetch(role: UserRole, url: string, options: RequestInit = {}): Promise<Response> {
  const token = getMockToken(role);
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> | undefined),
    Authorization: `Bearer ${token}`,
  };
  return fetch(url, { ...options, headers });
}

async function authJson<T>(role: UserRole, url: string, options: RequestInit = {}): Promise<T> {
  const res = await authFetch(role, url, options);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}: ${body.slice(0, 200)}`);
  }
  return res.json();
}

async function pollJob(role: UserRole, jobId: string, { intervalMs = 1500, timeoutMs = 60000 } = {}): Promise<any> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const status = await authJson<any>(role, `/api/v1/jobs/${jobId}`);
    if (status.status === 'completed') return status.result;
    if (status.status === 'failed') throw new Error(status.error || 'Job failed');
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`Job ${jobId} timed out after ${timeoutMs}ms`);
}

export interface Asset {
  id: string;
  name: string;
  capacity_mwh: number;
  power_mw: number;
}

export async function fetchAssets(role: UserRole): Promise<Asset[]> {
  const data = await authJson<{ assets: Asset[] }>(role, '/api/v1/assets');
  return data.assets;
}

/** Прогноз на 24г наперед. Персистить у PriceForecast на бекенді (потрібно для optimization/run). */
export async function runForecastJob(role: UserRole, targetDate: string, selectedModel: string): Promise<number[]> {
  const created = await authJson<{ job_id: string }>(role, '/api/v1/forecast/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_date: targetDate, selected_model: selectedModel }),
  });
  return pollJob(role, created.job_id);
}

export interface PriceBand {
  predicted_prices_uah: number[];
  lower_bound_uah: (number | null)[];
  upper_bound_uah: (number | null)[];
}

/** P10/P90 conformal-калібрований інтервал невизначеності для конкретної дати прогнозу. */
export async function fetchLatestForecastBand(role: UserRole, targetDate: string): Promise<PriceBand> {
  return authJson<PriceBand>(role, `/api/v1/forecast/latest?target_date=${targetDate}`);
}

export interface ActualPrices {
  date: string;
  available: boolean;
  source?: string;
  hours: number[];
  actual_prices_uah: number[];
}

/** Реальна опублікована ціна РДН з oree.com.ua на цю дату, якщо вже є. */
export async function fetchActualPrices(role: UserRole, targetDate: string): Promise<ActualPrices> {
  return authJson<ActualPrices>(role, `/api/v1/forecast/actual?target_date=${targetDate}`);
}

export async function runOptimizationJob(
  role: UserRole,
  assetId: string,
  targetDate: string,
  // undefined -> бекенд бере реальний поточний SoC з SCADA-телеметрії
  // (не вигадана константа 20%, яка "забувала" де реально закінчила
  // попередня доба).
  initialSocPct: number | undefined,
  mode: string,
  simulationsCount = 50
): Promise<any> {
  const created = await authJson<{ job_id: string }>(role, '/api/v1/optimization/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      asset_id: assetId,
      target_date: targetDate,
      initial_soc_pct: initialSocPct ?? null,
      mode,
      simulations_count: simulationsCount,
    }),
  });
  return pollJob(role, created.job_id, { timeoutMs: 90000 });
}

export async function fetchManualOverrides(role: UserRole, assetId: string, date: string) {
  const data = await authJson<{ overrides: any[] }>(
    role,
    `/api/v1/optimization/manual-overrides?asset_id=${assetId}&date=${date}`
  );
  return data.overrides;
}

export async function saveManualOverrides(role: UserRole, assetId: string, date: string, overrides: any[]) {
  return authJson(role, '/api/v1/optimization/manual-overrides', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      asset_id: assetId,
      date,
      overrides: overrides.map((o, idx) => ({ hour: idx, power_mw: o.power_mw, price_uah: o.price_uah })),
    }),
  });
}

export async function fetchSystemSettings(role: UserRole) {
  return authJson<any>(role, '/api/v1/optimization/settings');
}

export async function saveSystemSettings(role: UserRole, payload: any) {
  return authJson(role, '/api/v1/optimization/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function fetchExecutiveSummary(role: UserRole, assetId: string) {
  return authJson<any>(role, `/api/v1/reports/executive-summary?asset_id=${assetId}`);
}

export async function fetchForecastAccuracy(role: UserRole, days = 30) {
  return authJson<any>(role, `/api/v1/reports/forecast-accuracy?days=${days}`);
}

export async function fetchMarketConditions(role: UserRole) {
  return authJson<any>(role, '/api/v1/reports/market-conditions');
}

export interface GenerationAdjustment {
  date: string;
  nuclear_pct: number;
  hydro_pct: number;
  solar_pct: number;
  wind_pct: number;
  note: string | null;
  is_default?: boolean;
}

/** Ручна корекція доступності генерації (АЕС/ГЕС/СЕС/ВЕС) на дату — 100% скрізь = без відхилень. */
export async function fetchGenerationAdjustment(role: UserRole, date: string): Promise<GenerationAdjustment> {
  return authJson<GenerationAdjustment>(role, `/api/v1/generation-adjustments?date=${date}`);
}

export async function saveGenerationAdjustment(role: UserRole, payload: GenerationAdjustment) {
  return authJson(role, '/api/v1/generation-adjustments', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export interface PriceShift {
  date: string;
  shift_pct: number;
  note: string | null;
  is_default?: boolean;
}

/** Ручний відсотковий зсув прогнозу ціни на дату (post-inference, застосовується одноразово до всіх 24 годин) — 0% = без відхилень. */
export async function fetchPriceShift(role: UserRole, date: string): Promise<PriceShift> {
  return authJson<PriceShift>(role, `/api/v1/price-shift?date=${date}`);
}

export async function savePriceShift(role: UserRole, payload: PriceShift) {
  return authJson(role, '/api/v1/price-shift', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export interface InitialSoc {
  asset_id: string;
  date: string;
  capacity_kwh: number;
  capacity_pct: number;
  source: 'manual' | 'scada_telemetry' | 'calculated_previous_day' | 'fallback_default';
  has_manual_override: boolean;
  telemetry_available: boolean;
  previous_day_calculated_available: boolean;
}

/** SoC на 00:00 target_date: ручне значення > SCADA-телеметрія > розрахунок з кінця попередньої доби > фолбек 20%. */
export async function fetchInitialSoc(role: UserRole, assetId: string, date: string): Promise<InitialSoc> {
  return authJson<InitialSoc>(role, `/api/v1/optimization/initial-soc?asset_id=${assetId}&date=${date}`);
}

export async function saveInitialSoc(role: UserRole, assetId: string, date: string, capacityKwh: number) {
  return authJson(role, '/api/v1/optimization/initial-soc', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ asset_id: assetId, date, capacity_kwh: capacityKwh }),
  });
}

export async function clearInitialSoc(role: UserRole, assetId: string, date: string) {
  return authJson(role, `/api/v1/optimization/initial-soc?asset_id=${assetId}&date=${date}`, {
    method: 'DELETE',
  });
}

export interface BidMargin {
  date: string;
  margin_pct: number;
  source: 'manual' | 'default';
}

/** Маржа заявки РДН на добу: sell = прогноз*(1-маржа), buy = прогноз*(1+маржа) — керує ймовірністю виконання. */
export async function fetchBidMargin(role: UserRole, assetId: string, date: string): Promise<BidMargin> {
  return authJson<BidMargin>(role, `/api/v1/bids/margin?asset_id=${assetId}&date=${date}`);
}

export async function saveBidMargin(role: UserRole, assetId: string, date: string, marginPct: number) {
  return authJson(role, '/api/v1/bids/margin', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ asset_id: assetId, date, margin_pct: marginPct }),
  });
}

export async function clearBidMargin(role: UserRole, assetId: string, date: string) {
  return authJson(role, `/api/v1/bids/margin?asset_id=${assetId}&date=${date}`, {
    method: 'DELETE',
  });
}

export interface MarketBid {
  hour: number;
  bid_type: 'sell' | 'buy' | 'standby';
  volume_kw: number;
  forecast_price_uah: number;
  margin_pct: number;
  bid_price_uah: number;
  actual_price_uah: number | null;
  executed: boolean | null;
  realized_profit_uah: number | null;
  idm_fallback_suggested: boolean;
  idm_fallback_price_uah: number | null;
  idm_fallback_profit_uah: number | null;
  bid_price_legally_clamped: boolean;
  oree_bid_price_bounds_uah: { min: number; max: number };
}

export async function fetchBids(role: UserRole, assetId: string, date: string): Promise<{ date: string; asset_id: string; bids: MarketBid[] }> {
  return authJson(role, `/api/v1/bids?asset_id=${assetId}&date=${date}`);
}

export interface GenerateBidsResult {
  status: string;
  date: string;
  margin_pct: number;
  n_bids: number;
  n_price_clamped: number;
  bids: MarketBid[];
}

export async function generateBids(role: UserRole, assetId: string, date: string, marginPct?: number): Promise<GenerateBidsResult> {
  return authJson<GenerateBidsResult>(role, '/api/v1/bids/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ asset_id: assetId, date, margin_pct: marginPct }),
  });
}

export interface SettleBidsResult {
  status: string;
  date: string;
  n_settled: number;
  n_executed: number;
  n_failed_needs_idm: number;
  total_realized_profit_uah: number;
  bids: MarketBid[];
}

export async function settleBids(role: UserRole, assetId: string, date: string): Promise<SettleBidsResult> {
  return authJson<SettleBidsResult>(role, '/api/v1/bids/settle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ asset_id: assetId, date }),
  });
}

export interface ActionItem {
  severity: 'action' | 'warning' | 'info' | 'ok';
  hour: number | null;
  text: string;
}

export interface ActionSummary {
  status: string;
  date: string;
  has_bids: boolean;
  all_settled: boolean;
  n_total: number;
  n_executed: number;
  n_needs_idm_action: number;
  n_price_clamped: number;
  actions: ActionItem[];
}

export async function fetchActionSummary(role: UserRole, assetId: string, date: string): Promise<ActionSummary> {
  return authJson(role, `/api/v1/bids/action-summary?asset_id=${assetId}&date=${date}`);
}

export interface GridStress {
  date: string;
  forced_restriction_queues: number | null;
  source: 'auto_telegram' | 'manual' | 'none';
  auto_available: boolean;
  has_manual_override: boolean;
  manual_forced_restriction_queues: number | null;
  manual_note: string | null;
  auto_consumption_trend: number | null;
  auto_settlements_affected: number | null;
  auto_oblasts_affected: number | null;
}

/**
 * Обсяг ГПВ (у "чергах") на дату: автоматичний сигнал з постів Укренерго в
 * Telegram, якщо опублікований, інакше ручна оцінка диспетчера, інакше
 * відсутній. Єдиного структурованого національного API по ГПВ немає.
 */
export async function fetchGridStress(role: UserRole, date: string): Promise<GridStress> {
  return authJson<GridStress>(role, `/api/v1/grid-stress?date=${date}`);
}

export async function saveGridStress(role: UserRole, date: string, forcedRestrictionQueues: number | null, note: string | null) {
  return authJson(role, '/api/v1/grid-stress', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ date, forced_restriction_queues: forcedRestrictionQueues, note }),
  });
}

export async function clearGridStress(role: UserRole, date: string) {
  return authJson(role, `/api/v1/grid-stress?date=${date}`, {
    method: 'DELETE',
  });
}

export interface DataAuditColumn {
  used_in_forecast: boolean;
  hours_with_data: number;
  hours_total: number;
  min: number | null;
  max: number | null;
  mean: number | null;
  hourly: { hour: string; value: number | null }[];
}

export interface DataAuditTelegramPost {
  id: number;
  channel: string;
  datetime: string;
  text: string;
  severity: 'high' | 'medium' | 'none';
  parsed: {
    consumption_trend: number | null;
    same_time_deviation_pct: number | null;
    peak_deviation_pct: number | null;
    forced_restriction_queues: number | null;
    settlements_affected: number | null;
    oblasts_affected: number | null;
  };
}

export interface DataAudit {
  date: string;
  csv_covers_date: boolean;
  hours_found?: number;
  error?: string;
  groups: Record<string, Record<string, DataAuditColumn>>;
  telegram: {
    posts: DataAuditTelegramPost[];
    daily_aggregate: Record<string, number | null> | null;
  };
  manual_overrides: {
    grid_stress: { forced_restriction_queues: number | null; note: string | null } | null;
    generation_adjustment: { nuclear_pct: number; hydro_pct: number; solar_pct: number; wind_pct: number; note: string | null } | null;
    initial_soc: { asset_id: string; capacity_kwh: number }[] | null;
    price_shift: { shift_pct: number; note: string | null } | null;
  };
}

/**
 * Довідка "що реально було використано в розрахунку за цю дату" — для
 * тестування/діагностики, коли є підозра, що якісь дані не приходять або
 * неправильно розпізнаються (напр. Telegram).
 */
export async function fetchDataAudit(role: UserRole, date: string): Promise<DataAudit> {
  return authJson<DataAudit>(role, `/api/v1/data-audit?date=${date}`);
}

/**
 * Погодинний Excel-звіт за добу (прогноз/факт ціни, заряд/розряд, ціна
 * виконання) — завантажує .xlsx і одразу ініціює скачування у браузері.
 * Саме .xlsx, а не .csv, щоб Excel не "вгадував" типи комірок при відкритті.
 */
export async function exportDayExcel(role: UserRole, assetId: string, date: string): Promise<void> {
  const res = await authFetch(role, `/api/v1/reports/export-day?asset_id=${assetId}&date=${date}`);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}: ${body.slice(0, 200)}`);
  }
  const blob = await res.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `smartbess_${date}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}
