import { useEffect, useState } from 'react';
import { ComposedChart, CartesianGrid, XAxis, YAxis, Tooltip, Legend, Area, Bar, Line, ReferenceLine, ResponsiveContainer } from 'recharts';
import { AlertTriangle, Radio, Pencil, CalendarClock, History, FileDown } from 'lucide-react';
import { useApp } from '../../state/AppContext';
import GlobalFilterBar from '../../components/GlobalFilterBar';
import BidGateCountdown from '../../components/BidGateCountdown';
import BidActionCenter from '../../components/BidActionCenter';
import * as api from '../../api/client';

export default function OptimizationSchedule() {
  const {
    optimizationResult, manualOverrides, setManualOverrides, dispatchProfile, targetDate, capacity, power, saveOverrides, resetOverridesToOptimal,
    initialSoc, saveInitialSocAndRecalculate, clearInitialSocAndRecalculate, forecastPrices,
    bidMargin, saveBidMarginAndRegenerate, clearBidMarginAndRegenerate, bids, generateBidsNow, settleBidsNow,
    activeRole, activeAssetId, addLog,
  } = useApp();

  const [exporting, setExporting] = useState(false);
  const handleExport = async () => {
    if (!activeAssetId) return;
    setExporting(true);
    try {
      await api.exportDayExcel(activeRole, activeAssetId, targetDate);
      addLog('EXPORT', `Excel-звіт за ${targetDate} завантажено.`, 'success');
    } catch (e: any) {
      addLog('API', `Помилка експорту в Excel: ${e.message}`, 'error');
    } finally {
      setExporting(false);
    }
  };

  // forecastPrices === null означає, що для targetDate ще ЖОДНОГО разу не
  // рахували прогноз/MILP (а не що результат "порожній" — MILP може бути
  // легітимно нічого не робити, якщо арбітраж невигідний). Диспетчер це сплутав із "не рахує" — тому явне повідомлення замість порожнього графіка.
  const neverCalculated = forecastPrices === null;

  const baseSchedule = optimizationResult?.scenarios?.base?.schedule || [];

  // Фолбек лише одразу після "Розрахувати", поки manualOverrides (і похідний
  // dispatchProfile) ще не підвантажились для нової дати.
  const chartProfile = baseSchedule.length === 24
    ? baseSchedule.map((s: any) => ({ hour: `${s.hour + 1}`, charge: s.power_kw < 0 ? -s.power_kw : 0, discharge: s.power_kw > 0 ? s.power_kw : 0, soc: s.soc_kwh, price: s.price_forecast_uah_mwh }))
    : [];

  const hasProfile = dispatchProfile.length === 24;
  // ManualOverride "заморожує" ціну/потужність на момент збереження
  // (saveOverrides шле весь масив, навіть непроторкнуті години) і сам
  // не оновлюється, якщо прогноз/оптимізацію перерахували пізніше — реальний
  // кейс плутанини, знайдений диспетчером (графік показував застарілу ціну
  // після виправлення прогнозу, поки хтось не натиснув "Скинути").
  const hasOverrides = manualOverrides.some((o: any) => o.is_overridden);
  // dispatchProfile (AppContext) — ЄДИНЕ джерело правди для заряду/розряду/
  // SoC, спільне з Price Forecast: раніше кожна сторінка рахувала це окремо,
  // і при ручному оверрайді, що перевищував реальну ємність батареї, графіки
  // на двох сторінках показували різні стовпчики (реальний баг, знайдений
  // диспетчером).
  const currentDayProfile = hasProfile
    ? dispatchProfile.map((d) => ({ hour: `${d.hour}`, charge: d.charge, discharge: d.discharge, soc: d.soc, price: d.price }))
    : [];

  const dailyRevenue = dispatchProfile.reduce((s, d) => s + d.revenueUah, 0);
  const dailyCost = dispatchProfile.reduce((s, d) => s + d.costUah, 0);
  const dailyDegradation = dispatchProfile.reduce((s, d) => s + d.degradationUah, 0);
  const dailyNetProfit = dailyRevenue - dailyCost - dailyDegradation;

  const [socDraft, setSocDraft] = useState<string>('');
  useEffect(() => {
    if (initialSoc) setSocDraft(String(Math.round(initialSoc.capacity_kwh)));
  }, [initialSoc]);

  const [marginDraft, setMarginDraft] = useState<string>('');
  useEffect(() => {
    if (bidMargin) setMarginDraft(String(bidMargin.margin_pct));
  }, [bidMargin]);

  const socSourceLabel: Record<string, { text: string; color: string; icon: any }> = {
    manual: { text: 'Ручне значення диспетчера', color: 'var(--color-blue)', icon: Pencil },
    scada_telemetry: { text: 'Реальна SCADA-телеметрія', color: 'var(--color-emerald)', icon: Radio },
    calculated_previous_day: { text: 'Розрахунок з кінця попередньої доби (учорашній MILP-план)', color: '#8b5cf6', icon: History },
    fallback_default: { text: "Фолбек 20% — немає ні телеметрії, ні розрахунку за попередню добу", color: 'var(--color-amber)', icon: AlertTriangle },
  };

  return (
    <div>
      <GlobalFilterBar />

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '4px' }}>SoC батареї на початок доби (00:00 {targetDate})</h3>
        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0 0 12px' }}>
          Визначає, з якого реального рівня заряду MILP планує добу. Пріоритет: ручне значення → SCADA-телеметрія → розрахунок з кінця попередньої доби → фолбек 20% (лише якщо немає нічого з вищого).
        </p>
        {!initialSoc ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Завантаження...</p>
        ) : (
          <>
            {(() => {
              const info = socSourceLabel[initialSoc.source];
              const Icon = info.icon;
              return (
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '14px', fontSize: '0.85rem', color: info.color }}>
                  <Icon size={16} />
                  <span>Джерело: {info.text} — {Math.round(initialSoc.capacity_kwh).toLocaleString()} кВт·год ({initialSoc.capacity_pct.toFixed(1)}%)</span>
                </div>
              );
            })()}
            <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-end', flexWrap: 'wrap' }}>
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label className="form-label">Ємність на 00:00 (кВт·год)</label>
                <input
                  type="number" min={0} max={capacity} step={10} className="form-input" style={{ width: '180px' }}
                  value={socDraft}
                  onChange={(e) => setSocDraft(e.target.value)}
                />
              </div>
              <button className="btn" onClick={() => saveInitialSocAndRecalculate(Number(socDraft))}>
                Зберегти вручну і перерахувати
              </button>
              {initialSoc.has_manual_override && (
                <button
                  className="btn"
                  style={{ backgroundColor: 'rgba(239, 68, 68, 0.2)', border: '1px solid var(--color-rose)' }}
                  onClick={clearInitialSocAndRecalculate}
                >
                  Скинути на автоматичне
                </button>
              )}
            </div>
          </>
        )}
      </div>

      <div style={{ marginBottom: '24px' }}>
        <BidActionCenter />
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '4px' }}>Заявка РДН на {targetDate}</h3>
        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0 0 12px' }}>
          Ціна прогнозу — не те, що подається в заявку. Заявка на аукціоні єдиної ціни виконується за реальною ціною OREE,
          якщо наша ціна "прохідна": продаж виконається, якщо наша ціна ≤ факт; купівля — якщо наша ціна ≥ факт. Маржа зсуває
          нашу ціну від прогнозу в бік більшої ймовірності виконання (продаж дешевше, купівля дорожче) — це РУЧНЕ налаштування
          ризику, а не сам прогноз.
        </p>
        <BidGateCountdown targetDate={targetDate} />
        {!bidMargin ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Завантаження...</p>
        ) : (
          <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: '14px' }}>
            <div className="form-group" style={{ marginBottom: 0 }}>
              <label className="form-label">Відсоток буфера безпеки (Safety Buffer %)</label>
              <input
                type="number" min={0} max={50} step={0.5} className="form-input" style={{ width: '140px' }}
                value={marginDraft}
                onChange={(e) => setMarginDraft(e.target.value)}
              />
            </div>
            <button className="btn" onClick={() => saveBidMarginAndRegenerate(Number(marginDraft))}>
              Зберегти маржу і сформувати заявки
            </button>
            {bidMargin.source === 'manual' && (
              <button
                className="btn"
                style={{ backgroundColor: 'rgba(239, 68, 68, 0.2)', border: '1px solid var(--color-rose)' }}
                onClick={clearBidMarginAndRegenerate}
              >
                Скинути на дефолт
              </button>
            )}
            <button className="btn" style={{ backgroundColor: 'rgba(59, 130, 246, 0.15)', border: '1px solid var(--color-blue)' }} onClick={generateBidsNow}>
              Сформувати заявки зараз
            </button>
            <button className="btn" style={{ backgroundColor: 'rgba(217, 119, 6, 0.15)', border: '1px solid var(--color-amber)' }} onClick={settleBidsNow}>
              Звірити з фактом OREE
            </button>
          </div>
        )}

        {bids && bids.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--text-muted)' }}>
                  <th style={{ padding: '4px 8px' }}>Год</th>
                  <th style={{ padding: '4px 8px' }}>Тип</th>
                  <th style={{ padding: '4px 8px' }}>Обсяг (кВт)</th>
                  <th style={{ padding: '4px 8px' }}>Прогноз (грн/МВт·год)</th>
                  <th style={{ padding: '4px 8px' }}>Ціна заявки (ручна, з маржею)</th>
                  <th style={{ padding: '4px 8px' }}>Факт OREE</th>
                  <th style={{ padding: '4px 8px' }}>Статус</th>
                  <th style={{ padding: '4px 8px' }}>P&L / ВДР-пропозиція</th>
                </tr>
              </thead>
              <tbody>
                {bids.filter((b) => b.bid_type !== 'standby').map((b) => (
                  <tr key={b.hour} style={{ borderTop: '1px solid var(--border-color, #333)' }}>
                    <td style={{ padding: '4px 8px' }}>{b.hour}</td>
                    <td style={{ padding: '4px 8px' }}>{b.bid_type === 'sell' ? 'Продаж' : 'Купівля'}</td>
                    <td style={{ padding: '4px 8px' }}>{Math.round(b.volume_kw)}</td>
                    <td style={{ padding: '4px 8px' }}>{Math.round(b.forecast_price_uah).toLocaleString()}</td>
                    <td style={{ padding: '4px 8px', color: 'var(--color-blue)' }}>
                      {Math.round(b.bid_price_uah).toLocaleString()} (ручна, маржа {b.margin_pct}%)
                      {b.bid_price_legally_clamped && (
                        <span
                          title={`Ціна скоригована до законної межі OREE (${b.oree_bid_price_bounds_uah.min}–${b.oree_bid_price_bounds_uah.max} грн/МВт·год)`}
                          style={{ display: 'inline-flex', verticalAlign: 'middle', marginLeft: '4px' }}
                        >
                          <AlertTriangle size={13} style={{ color: 'var(--color-amber)' }} />
                        </span>
                      )}
                    </td>
                    <td style={{ padding: '4px 8px' }}>{b.actual_price_uah != null ? Math.round(b.actual_price_uah).toLocaleString() : '—'}</td>
                    <td style={{ padding: '4px 8px' }}>
                      {b.executed === null ? '⏳ очікує факту' : b.executed ? '✅ виконано' : '❌ не виконано'}
                    </td>
                    <td style={{ padding: '4px 8px' }}>
                      {b.executed ? (
                        <span style={{ color: 'var(--color-emerald)' }}>{Math.round(b.realized_profit_uah ?? 0).toLocaleString()} грн</span>
                      ) : b.idm_fallback_suggested ? (
                        <span style={{ color: 'var(--color-amber)' }}>
                          ВДР ~{Math.round(b.idm_fallback_price_uah ?? 0).toLocaleString()} грн/МВт·год → {Math.round(b.idm_fallback_profit_uah ?? 0).toLocaleString()} грн
                        </span>
                      ) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="kpi-container" style={{ marginBottom: '24px' }}>
        <div className="kpi-card" style={{ borderLeft: '4px solid var(--color-emerald)' }}>
          <span className="kpi-title">Чистий прибуток за добу (з урахуванням втрат)</span>
          <span className="kpi-value" style={{ color: dailyNetProfit >= 0 ? 'var(--color-emerald)' : 'var(--color-rose)' }}>{Math.round(dailyNetProfit).toLocaleString()} грн</span>
          <span className="kpi-change neutral">Дохід мінус Витрати та Знос</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Дохід від розряду (Продаж)</span>
          <span className="kpi-value" style={{ color: 'var(--color-emerald)' }}>{Math.round(dailyRevenue).toLocaleString()} грн</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Витрати заряду (Купівля + Тарифи)</span>
          <span className="kpi-value" style={{ color: 'var(--color-rose)' }}>{Math.round(dailyCost).toLocaleString()} грн</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Знос батареї (Деградація)</span>
          <span className="kpi-value">{Math.round(dailyDegradation).toLocaleString()} грн</span>
        </div>
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '4px' }}>Потужність заряду/розряду та рівень SoC BESS</h3>
        {neverCalculated ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '10px', padding: '60px 20px', color: 'var(--text-muted)' }}>
            <CalendarClock size={32} />
            <p style={{ margin: 0, fontSize: '0.9rem', textAlign: 'center' }}>
              Прогноз і графік заряду/розряду ще жодного разу не розраховувались на {targetDate}.<br />
              Натисніть «Розрахувати» вище, щоб отримати їх (не автоматично — щоб не запускати важкі ML/MILP розрахунки на кожну зміну дати).
            </p>
          </div>
        ) : (
          <>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0 0 12px' }}>
              Пунктирні лінії — межі ємності батареї: {Math.round(capacity * 0.9).toLocaleString()} кВт·год (макс. SoC 90%) і {Math.round(capacity * 0.1).toLocaleString()} кВт·год (мін. SoC 10%) з {Math.round(capacity).toLocaleString()} кВт·год загальної ємності.
              Стовпчики показують реально виконану потужність — якщо батарея вже на межі ємності, подальша команда заряду/розряду не виконується і стовпчик зменшується, навіть якщо введена потужність більша.
              Фіолетова лінія — ціна прогнозу (або ручна заявка, якщо годину скориговано вручну), для якої й порахований цей графік.
            </p>
            <div style={{ width: '100%', height: 380 }}>
              <ResponsiveContainer>
                <ComposedChart data={hasProfile ? currentDayProfile : chartProfile}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis dataKey="hour" stroke="var(--text-secondary)" />
                  <YAxis yAxisId="power" stroke="var(--text-secondary)" label={{ value: 'кВт', angle: -90, position: 'insideLeft', fill: 'var(--text-secondary)', fontSize: 11 }} />
                  <YAxis yAxisId="soc" orientation="right" domain={[0, capacity]} stroke="var(--text-secondary)" label={{ value: 'кВт·год', angle: 90, position: 'insideRight', fill: 'var(--text-secondary)', fontSize: 11 }} />
                  <YAxis yAxisId="price" hide domain={['auto', 'auto']} />
                  <Tooltip contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }} />
                  <Legend />
                  <ReferenceLine yAxisId="soc" y={capacity * 0.9} stroke="var(--color-amber)" strokeDasharray="4 4" />
                  <ReferenceLine yAxisId="soc" y={capacity * 0.1} stroke="var(--color-amber)" strokeDasharray="4 4" />
                  <Bar yAxisId="power" dataKey="charge" name="Потужність заряду (кВт)" fill="var(--color-blue)" />
                  <Bar yAxisId="power" dataKey="discharge" name="Потужність розряду (кВт)" fill="var(--color-emerald)" />
                  <Area yAxisId="soc" type="monotone" dataKey="soc" name="Рівень заряду SoC (кВт-год)" stroke="var(--color-amber)" fill="rgba(217, 119, 6, 0.12)" strokeWidth={2} />
                  <Line yAxisId="price" type="monotone" dataKey="price" name="Ціна прогнозу / заявки (грн/МВт-год)" stroke="#a78bfa" strokeWidth={2} dot={{ r: 2 }} connectNulls />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </>
        )}
      </div>

      <div className="glass-card" style={{ marginTop: '24px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
          <div>
            <h3 className="card-title" style={{ margin: 0 }}>Ручне коригування заявок (Manual Dispatch Schedule)</h3>
            <p style={{ margin: '4px 0 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
              Введіть потужність (МВт: розряд +, заряд -) та реальну ціну заявки (грн/МВт-год) для кожної години на {targetDate}.
            </p>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px' }}>
            <button className="btn btn-secondary" onClick={handleExport} disabled={exporting} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <FileDown size={14} /> {exporting ? 'Експорт...' : 'Експорт в Excel'}
            </button>
            <button className="btn" onClick={saveOverrides}>Зберегти ручний графік</button>
            <button
              className="btn"
              style={{ backgroundColor: 'rgba(239, 68, 68, 0.2)', border: '1px solid var(--color-rose)' }}
              onClick={() => { if (window.confirm('Ви впевнені, що хочете скинути ручний графік до оптимального?')) resetOverridesToOptimal(); }}
            >
              Скинути до оптимального
            </button>
          </div>
        </div>

        {hasOverrides && (
          <div style={{
            display: 'flex', gap: '10px', marginBottom: '16px', padding: '12px', borderRadius: '6px',
            background: 'rgba(217, 119, 6, 0.08)', border: '1px solid rgba(217, 119, 6, 0.3)', fontSize: '13px',
          }}>
            <AlertTriangle size={16} style={{ color: 'var(--color-amber)', flexShrink: 0, marginTop: '1px' }} />
            <span>
              На цю дату вже збережено ручний графік — він «заморожує» ціну/потужність на момент збереження і НЕ
              оновлюється сам, навіть якщо прогноз чи оптимізацію перерахували пізніше. Якщо після збереження
              графіка ви ще раз натискали «Розрахувати» — натисніть «Скинути до оптимального», щоб побачити
              свіжий розрахунок, інакше графік і чистий прибуток показують застарілі числа.
            </span>
          </div>
        )}

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
              {manualOverrides.map((o: any, idx: number) => {
                const sched = baseSchedule[idx];
                const recPower = sched ? sched.power_kw / 1000.0 : 0.0;
                return (
                  <tr key={idx}>
                    <td>Година {idx + 1} ({String(idx).padStart(2, '0')}:00–{String(idx + 1).padStart(2, '0')}:00)</td>
                    <td style={{ color: recPower > 0 ? 'var(--color-emerald)' : recPower < 0 ? 'var(--color-blue)' : 'var(--text-secondary)' }}>
                      {recPower > 0 ? `Розряд +${recPower.toFixed(2)} МВт` : recPower < 0 ? `Заряд ${recPower.toFixed(2)} МВт` : 'Пауза'}
                    </td>
                    <td>
                      <input
                        type="number" step="0.05" min={-power / 1000.0} max={power / 1000.0}
                        className="form-input" style={{ width: '120px', padding: '4px 8px', fontSize: '13px' }}
                        value={o.power_mw}
                        onChange={(e) => {
                          const raw = Number(e.target.value);
                          // Клип до реальної макс. потужності БЕСС (Asset.power_mw) —
                          // без цього можна було ввести значення, у рази більше за фізичну
                          // потужність батареї (реальний баг, знайдений диспетчером).
                          const val = Number.isFinite(raw) ? Math.max(-power / 1000.0, Math.min(power / 1000.0, raw)) : raw;
                          setManualOverrides(manualOverrides.map((it: any, i: number) => (i === idx ? { ...it, power_mw: val } : it)));
                        }}
                      />
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: '5px' }}>
                        <button className="btn" style={{ padding: '3px 8px', fontSize: '11px', backgroundColor: 'var(--color-blue)' }}
                          onClick={() => setManualOverrides(manualOverrides.map((it: any, i: number) => (i === idx ? { ...it, power_mw: -(power / 1000.0) } : it)))}>
                          Заряд (Max)
                        </button>
                        <button className="btn" style={{ padding: '3px 8px', fontSize: '11px', backgroundColor: 'var(--color-emerald)' }}
                          onClick={() => setManualOverrides(manualOverrides.map((it: any, i: number) => (i === idx ? { ...it, power_mw: power / 1000.0 } : it)))}>
                          Розряд (Max)
                        </button>
                        <button className="btn" style={{ padding: '3px 8px', fontSize: '11px', backgroundColor: '#4b5563' }}
                          onClick={() => setManualOverrides(manualOverrides.map((it: any, i: number) => (i === idx ? { ...it, power_mw: 0.0 } : it)))}>
                          Стоп
                        </button>
                      </div>
                    </td>
                    <td>
                      <input
                        type="number" step="0.01" className="form-input" style={{ width: '140px', padding: '4px 8px', fontSize: '13px' }}
                        value={Math.round(o.price_uah * 100) / 100}
                        onChange={(e) => {
                          const val = Number(e.target.value);
                          setManualOverrides(manualOverrides.map((it: any, i: number) => (i === idx ? { ...it, price_uah: val } : it)));
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
  );
}
