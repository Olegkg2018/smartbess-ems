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
  initialSocPct: number,
  mode: string,
  simulationsCount = 50
): Promise<any> {
  const created = await authJson<{ job_id: string }>(role, '/api/v1/optimization/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      asset_id: assetId,
      target_date: targetDate,
      initial_soc_pct: initialSocPct,
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
