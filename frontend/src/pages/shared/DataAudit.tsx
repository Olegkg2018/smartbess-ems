import { useEffect, useState, useCallback } from 'react';
import { AlertTriangle, CheckCircle2, XCircle, RadioTower, RefreshCw } from 'lucide-react';
import { useApp } from '../../state/AppContext';
import * as api from '../../api/client';
import type { DataAudit as DataAuditType } from '../../api/client';

const GROUP_LABELS: Record<string, string> = {
  market: 'Ціна ринку (oree.com.ua)',
  weather_ua: 'Погода Україна (Open-Meteo)',
  grid_flow_eu: 'Транскордонний потік / ЄС (ENTSO-E)',
  weather_neighbors: 'Погода сусідів PL/RO (експериментальна ознака)',
  gas: 'Ціна газу',
  grid_stress_numeric: 'Сигнали ГПВ з Telegram (числові)',
};

const COLUMN_LABELS: Record<string, string> = {
  Price: 'Ціна РДН, ₴/МВт·год',
  IDM_Price: 'Ціна ВДР, ₴/МВт·год',
  DAM_IDM_Spread: 'Спред РДН/ВДР',
  Temperature: 'Температура, °C',
  Cloud_Cover: 'Хмарність, %',
  Wind_Speed: 'Вітер, км/год',
  Shortwave_Radiation: 'Радіація, Вт/м²',
  Solar_Gen: 'Оцінка генерації СЕС, МВт',
  Wind_Gen: 'Оцінка генерації ВЕС, МВт',
  Grid_Net_Export_MW: 'Нетто-експорт, МВт',
  EU_DAM_Price_EUR_MWh: 'Ціна ЄС, €/МВт·год',
  PL_Temperature: 'PL Температура, °C',
  PL_Cloud_Cover: 'PL Хмарність, %',
  PL_Wind_Speed: 'PL Вітер, км/год',
  PL_Shortwave_Radiation: 'PL Радіація, Вт/м²',
  RO_Temperature: 'RO Температура, °C',
  RO_Cloud_Cover: 'RO Хмарність, %',
  RO_Wind_Speed: 'RO Вітер, км/год',
  RO_Shortwave_Radiation: 'RO Радіація, Вт/м²',
  Gas_Price_EUR_MWh: 'Ціна газу, €/МВт·год',
  Grid_Stress_High: 'High-сигнал (keyword)',
  Grid_Stress_Medium: 'Medium-сигнал (keyword)',
  Telegram_Mentions: 'Згадувань у Telegram',
  Grid_Consumption_Trend: 'Тренд споживання',
  Grid_Consumption_Deviation_Pct: 'Відхилення споживання, %',
  Grid_Peak_Deviation_Pct: 'Відхилення піку, %',
  Grid_Forced_Restriction_Queues: 'Обсяг ГПВ, черги',
  Grid_Settlements_Affected: 'Населених пунктів без світла',
  Grid_Oblasts_Affected: 'Областей охоплено',
};

function yesterday() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().split('T')[0];
}

function coverageStatus(hoursWithData: number, hoursTotal: number): { color: string; label: string; icon: React.ReactNode } {
  if (hoursTotal === 0) return { color: '#6b7280', label: 'дата поза межами історії', icon: <XCircle size={14} /> };
  const ratio = hoursWithData / hoursTotal;
  if (ratio === 1) return { color: 'var(--color-emerald)', label: `${hoursWithData}/${hoursTotal} год`, icon: <CheckCircle2 size={14} /> };
  if (ratio === 0) return { color: 'var(--color-rose)', label: `${hoursWithData}/${hoursTotal} год — немає даних`, icon: <XCircle size={14} /> };
  return { color: 'var(--color-amber)', label: `${hoursWithData}/${hoursTotal} год — частково`, icon: <AlertTriangle size={14} /> };
}

function formatNum(v: number | null, digits = 1) {
  if (v === null || v === undefined) return '—';
  return v.toLocaleString('uk-UA', { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

export default function DataAudit() {
  const { activeRole } = useApp();
  const [date, setDate] = useState(yesterday());
  const [audit, setAudit] = useState<DataAuditType | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.fetchDataAudit(activeRole, date);
      setAudit(data);
    } catch (e: any) {
      setError(e.message || 'Помилка завантаження довідки');
      setAudit(null);
    } finally {
      setLoading(false);
    }
  }, [activeRole, date]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div className="glass-card" style={{ borderLeft: '4px solid var(--color-cyan)' }}>
        <h4 style={{ margin: '0 0 6px 0', fontSize: '16px', color: 'var(--color-cyan)' }}>
          <RadioTower size={16} style={{ verticalAlign: 'middle', marginRight: '6px' }} />
          Довідка: що реально було використано в розрахунку за цю дату
        </h4>
        <p style={{ margin: '0 0 16px', fontSize: '13.5px', color: 'var(--text-secondary)', lineHeight: '1.5' }}>
          Діагностичний інструмент для тестування — показує буквально, скільки з 24 годин доби реально заповнені
          (а не порожні), і що саме прочитано з Telegram, без згладжування. Якщо джерело за день не дало жодного
          значення — це видно прямо тут, а не ховається за середнім показником.
        </p>
        <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-end' }}>
          <div className="form-group" style={{ margin: 0, maxWidth: '220px' }}>
            <label className="form-label">Дата для перевірки</label>
            <input type="date" className="form-input" value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
          <button className="btn btn-secondary" style={{ height: '38px', display: 'flex', alignItems: 'center', gap: '6px' }} onClick={load} disabled={loading}>
            <RefreshCw size={14} /> {loading ? 'Завантаження...' : 'Оновити'}
          </button>
        </div>
      </div>

      {error && (
        <div className="glass-card" style={{ borderLeft: '4px solid var(--color-rose)' }}>
          <p style={{ margin: 0, color: 'var(--color-rose)' }}>{error}</p>
        </div>
      )}

      {audit && !audit.csv_covers_date && (
        <div className="glass-card" style={{ borderLeft: '4px solid var(--color-rose)', display: 'flex', gap: '10px' }}>
          <AlertTriangle size={16} style={{ color: 'var(--color-rose)', flexShrink: 0, marginTop: '2px' }} />
          <p style={{ margin: 0, fontSize: '13.5px' }}>
            Ця дата поза межами накопиченої історії (historical_data_merged.csv) — або ще не синхронізована
            (майбутня дата), або дані ще жодного разу не завантажувались. Це очікувано для завтрашньої дати
            прогнозу — реальних фактичних значень на неї ще не існує.
          </p>
        </div>
      )}

      {audit && audit.csv_covers_date && (
        <div className="kpi-container" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
          {Object.entries(audit.groups).map(([groupKey, cols]) => {
            const entries = Object.entries(cols);
            const totalHours = entries.reduce((s, [, c]) => s + c.hours_total, 0);
            const withData = entries.reduce((s, [, c]) => s + c.hours_with_data, 0);
            const status = coverageStatus(withData, totalHours);
            const usedInForecast = entries.some(([, c]) => c.used_in_forecast);
            return (
              <div
                key={groupKey}
                className="kpi-card"
                style={{ borderLeft: `4px solid ${status.color}`, cursor: 'pointer' }}
                onClick={() => setExpandedGroup(expandedGroup === groupKey ? null : groupKey)}
              >
                <span className="kpi-title">{GROUP_LABELS[groupKey] || groupKey}</span>
                <span className="kpi-value" style={{ color: status.color, fontSize: '1.15rem', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  {status.icon} {status.label}
                </span>
                <span className="kpi-change neutral">
                  {usedInForecast ? 'Впливає на прогноз' : 'Не впливає на прогноз (див. звіт)'} · натисніть для деталей
                </span>
              </div>
            );
          })}
        </div>
      )}

      {audit && expandedGroup && audit.groups[expandedGroup] && (
        <div className="glass-card">
          <h3 className="card-title" style={{ marginBottom: '16px' }}>{GROUP_LABELS[expandedGroup] || expandedGroup} — деталі по годинах</h3>
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Поле</th>
                  <th>У прогнозі</th>
                  <th>Покриття</th>
                  <th>Мін</th>
                  <th>Макс</th>
                  <th>Середнє</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(audit.groups[expandedGroup]).map(([col, info]) => {
                  const status = coverageStatus(info.hours_with_data, info.hours_total);
                  return (
                    <tr key={col}>
                      <td>{COLUMN_LABELS[col] || col}</td>
                      <td>{info.used_in_forecast ? 'так' : 'ні'}</td>
                      <td style={{ color: status.color }}>{status.label}</td>
                      <td>{formatNum(info.min)}</td>
                      <td>{formatNum(info.max)}</td>
                      <td>{formatNum(info.mean)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {audit && (
        <div className="glass-card">
          <h3 className="card-title" style={{ marginBottom: '6px' }}>Що прочитано з Telegram за {date}</h3>
          <p style={{ margin: '0 0 16px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            Канали: ukrenergo, minenergo_ua. Показані буквально пости, знайдені в кеші за цю дату, і що з кожного
            з них автоматично витягнуто (parse_energy_status).
          </p>

          {audit.telegram.posts.length === 0 ? (
            <div style={{ display: 'flex', gap: '10px', padding: '12px', background: 'rgba(217, 119, 6, 0.08)', border: '1px solid rgba(217, 119, 6, 0.3)', borderRadius: '6px' }}>
              <AlertTriangle size={16} style={{ color: 'var(--color-amber)', flexShrink: 0 }} />
              <span style={{ fontSize: '13.5px' }}>
                За цю дату в кеші немає жодного поста з жодного з двох каналів. Це або означає, що канали цього дня
                нічого не публікували, або що фонова синхронізація (щоденний job о 17:30) того дня не відпрацювала —
                варто порівняти з офіційним каналом вручну, якщо це підозріле.
              </span>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              {audit.telegram.posts.map((post) => (
                <div key={post.id} style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: '16px' }}>
                  <div style={{
                    fontSize: '0.85rem', padding: '12px', borderRadius: '6px', background: 'rgba(255,255,255,0.02)',
                    borderLeft: `3px solid ${post.severity === 'high' ? 'var(--color-rose)' : post.severity === 'medium' ? 'var(--color-amber)' : 'var(--color-blue)'}`,
                    whiteSpace: 'pre-wrap',
                  }}>
                    <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginBottom: '6px' }}>
                      @{post.channel} · {new Date(post.datetime).toLocaleString('uk-UA')}
                    </div>
                    {post.text.slice(0, 500)}{post.text.length > 500 ? '…' : ''}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    <ParsedRow label="Тренд споживання" value={post.parsed.consumption_trend === 1 ? 'зростає' : post.parsed.consumption_trend === -1 ? 'знижується' : post.parsed.consumption_trend === 0 ? 'без змін' : null} />
                    <ParsedRow label="Відхилення (той самий час)" value={post.parsed.same_time_deviation_pct !== null ? `${post.parsed.same_time_deviation_pct}%` : null} />
                    <ParsedRow label="Відхилення піку" value={post.parsed.peak_deviation_pct !== null ? `${post.parsed.peak_deviation_pct}%` : null} />
                    <ParsedRow label="Обсяг ГПВ, черги" value={post.parsed.forced_restriction_queues} />
                    <ParsedRow label="Населених пунктів" value={post.parsed.settlements_affected} />
                    <ParsedRow label="Областей" value={post.parsed.oblasts_affected} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {audit && (
        <div className="glass-card">
          <h3 className="card-title" style={{ marginBottom: '16px' }}>Ручні поправки диспетчера на цю дату</h3>
          <div className="grid-3" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
            <div>
              <span className="kpi-title">Обсяг ГПВ вручну</span>
              <p style={{ margin: '6px 0 0', fontSize: '0.9rem' }}>
                {audit.manual_overrides.grid_stress
                  ? `${audit.manual_overrides.grid_stress.forced_restriction_queues ?? '—'} черги${audit.manual_overrides.grid_stress.note ? ` (${audit.manual_overrides.grid_stress.note})` : ''}`
                  : 'не вводилось'}
              </p>
            </div>
            <div>
              <span className="kpi-title">Корекція генерації</span>
              <p style={{ margin: '6px 0 0', fontSize: '0.9rem' }}>
                {audit.manual_overrides.generation_adjustment
                  ? `АЕС ${audit.manual_overrides.generation_adjustment.nuclear_pct}% · ГЕС ${audit.manual_overrides.generation_adjustment.hydro_pct}% · СЕС ${audit.manual_overrides.generation_adjustment.solar_pct}% · ВЕС ${audit.manual_overrides.generation_adjustment.wind_pct}%`
                  : 'не вводилась'}
              </p>
            </div>
            <div>
              <span className="kpi-title">Стартовий SoC вручну</span>
              <p style={{ margin: '6px 0 0', fontSize: '0.9rem' }}>
                {audit.manual_overrides.initial_soc && audit.manual_overrides.initial_soc.length > 0
                  ? audit.manual_overrides.initial_soc.map((s) => `${s.capacity_kwh} кВт·год`).join(', ')
                  : 'не вводився'}
              </p>
            </div>
            <div>
              <span className="kpi-title">Зсув прогнозу ціни</span>
              <p style={{ margin: '6px 0 0', fontSize: '0.9rem' }}>
                {audit.manual_overrides.price_shift
                  ? `${audit.manual_overrides.price_shift.shift_pct > 0 ? '+' : ''}${audit.manual_overrides.price_shift.shift_pct}%${audit.manual_overrides.price_shift.note ? ` (${audit.manual_overrides.price_shift.note})` : ''}`
                  : 'не вводився'}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ParsedRow({ label, value }: { label: string; value: string | number | null }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', gap: '10px',
      padding: '9px 12px', background: 'rgba(8, 145, 178, 0.06)', border: '1px solid var(--border-color)',
      borderRadius: '6px', fontSize: '0.85rem',
    }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <b>{value === null || value === undefined ? '—' : value}</b>
    </div>
  );
}
