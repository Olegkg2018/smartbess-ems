import { useState, useEffect } from 'react';
import { useApp } from '../../state/AppContext';
import { LineChart, Line, BarChart, Bar, ComposedChart, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceLine } from 'recharts';
import { AlertCircle, AlertTriangle, Download } from 'lucide-react';
import * as api from '../../api/client';

export default function OptimizationSchedule() {
  const {
    targetDate, optimizationResult, manualOverrides, setManualOverrides, saveOverrides,
    resetOverridesToOptimal, capacity, runForecastAndOptimization, loading, dispatchProfile,
    initialSoc, saveInitialSocAndRecalculate, clearInitialSocAndRecalculate,
    bidMargin, saveBidMarginAndRegenerate, clearBidMarginAndRegenerate,
    bids, generateBidsNow, settleBidsNow,
    activeRole, activeAssetId,
  } = useApp();

  const [socDraft, setSocDraft] = useState('');
  const [marginDraft, setMarginDraft] = useState('');
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    setSocDraft(initialSoc ? String(Math.round(initialSoc.capacity_kwh)) : '');
  }, [initialSoc]);

  useEffect(() => {
    setMarginDraft(bidMargin ? String(bidMargin.margin_pct) : '');
  }, [bidMargin]);

  const hasManualSchedule = manualOverrides.some((o: any) => o.is_overridden);

  const totalRevenue = dispatchProfile.reduce((s, h) => s + h.revenueUah, 0);
  const totalCost = dispatchProfile.reduce((s, h) => s + h.costUah, 0);
  const totalDegradation = dispatchProfile.reduce((s, h) => s + h.degradationUah, 0);
  const netProfit = totalRevenue - totalCost - totalDegradation;

  const chargeChartData = dispatchProfile.map((h) => ({
    hour: h.hour,
    charge: h.charge,
    discharge: h.discharge,
    soc: h.soc,
    price: h.price,
  }));

  const handlePowerChange = (idx: number, value: string) => {
    const powerKw = parseFloat(value) || 0;
    const powerMw = powerKw / 1000.0;
    const updated = [...manualOverrides];
    updated[idx] = { ...updated[idx], power_mw: powerMw, is_overridden: true };
    setManualOverrides(updated);
  };

  const handlePriceChange = (idx: number, value: string) => {
    const price = parseFloat(value) || 0;
    const updated = [...manualOverrides];
    updated[idx] = { ...updated[idx], price_uah: price, is_overridden: true };
    setManualOverrides(updated);
  };

  const handleQuickAction = (idx: number, action: 'charge_max' | 'discharge_max' | 'idle') => {
    const updated = [...manualOverrides];
    const powerMw = capacity > 0 ? capacity / 4000.0 : 0.25;
    if (action === 'charge_max') {
      updated[idx] = { ...updated[idx], power_mw: -powerMw, is_overridden: true };
    } else if (action === 'discharge_max') {
      updated[idx] = { ...updated[idx], power_mw: powerMw, is_overridden: true };
    } else {
      updated[idx] = { ...updated[idx], power_mw: 0, is_overridden: true };
    }
    setManualOverrides(updated);
  };

  const handleExport = async () => {
    if (!activeAssetId) return;
    setExporting(true);
    try {
      const blob = await api.exportDayReport(activeRole, activeAssetId, targetDate);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `smartbess_${targetDate}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      console.error('Export failed', e);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div>
      <div className="glass-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h3 className="card-title" style={{ margin: 0 }}>Оптимізація графіка на {targetDate}</h3>
          <button className="btn" onClick={runForecastAndOptimization} disabled={loading}>
            {loading ? 'Розрахунок...' : 'Розрахувати'}
          </button>
        </div>

        <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: '4px' }}>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">Стартовий SoC на 00:00 (кВт·год) {initialSoc && <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>— джерело: {initialSoc.source === 'manual' ? 'ручне' : initialSoc.source === 'scada' ? 'SCADA' : initialSoc.source === 'prev_day_plan' ? 'розрахунок з попереднього дня' : 'дефолт 20%'}</span>}</label>
            <input
              type="number" min={0} max={capacity} step={10} className="form-input" style={{ width: '160px' }}
              value={socDraft}
              onChange={(e) => setSocDraft(e.target.value)}
            />
          </div>
          <button className="btn" onClick={() => saveInitialSocAndRecalculate(Number(socDraft))}>
            Зберегти SoC і перерахувати
          </button>
          {initialSoc?.source === 'manual' && (
            <button
              className="btn"
              style={{ backgroundColor: 'rgba(239, 68, 68, 0.2)', border: '1px solid #ef4444' }}
              onClick={clearInitialSocAndRecalculate}
            >
              Скинути ручне значення
            </button>
          )}
        </div>
        {!initialSoc && (
          <>
            <div style={{ height: '8px' }} />
          </>
        )}
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '4px' }}>Заявка РДН на {targetDate}</h3>
        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0 0 12px' }}>
          Ціна прогнозу — не те, що подається в заявку. Заявка на аукціоні єдиної ціни виконується за реальною ціною OREE,
          якщо наша ціна "прохідна": продаж виконається, якщо наша ціна ≤ факт; купівля — якщо наша ціна ≥ факт. Маржа зсуває
          нашу ціну від прогнозу в бік більшої ймовірності виконання (продаж дешевше, купівля дорожче) — це РУЧНЕ налаштування
          ризику, а не сам прогноз.
        </p>
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
                style={{ backgroundColor: 'rgba(239, 68, 68, 0.2)', border: '1px solid #ef4444' }}
                onClick={clearBidMarginAndRegenerate}
              >
                Скинути на дефолт
              </button>
            )}
            <button className="btn" style={{ backgroundColor: 'rgba(59, 130, 246, 0.15)', border: '1px solid #3b82f6' }} onClick={generateBidsNow}>
              Сформувати заявки зараз
            </button>
            <button className="btn" style={{ backgroundColor: 'rgba(217, 119, 6, 0.15)', border: '1px solid #d97706' }} onClick={settleBidsNow}>
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
                    <td style={{ padding: '4px 8px', color: '#3b82f6' }}>{Math.round(b.bid_price_uah).toLocaleString()} (ручна, маржа {b.margin_pct}%)</td>
                    <td style={{ padding: '4px 8px' }}>{b.actual_price_uah == null ? '—' : Math.round(b.actual_price_uah).toLocaleString()}</td>
                    <td style={{ padding: '4px 8px' }}>
                      {b.executed === null ? '⏳ очікує факту' : b.executed ? '✅ виконано' : '❌ не виконано'}
                    </td>
                    <td style={{ padding: '4px 8px' }}>
                      {b.executed
                        ? <span style={{ color: '#059669' }}>{Math.round(b.realized_profit_uah ?? 0).toLocaleString()} грн</span>
                        : b.idm_fallback_suggested
                          ? <span style={{ color: '#d97706' }}>ВДР ~{Math.round(b.idm_fallback_price_uah ?? 0).toLocaleString()} грн/МВт·год → {Math.round(b.idm_fallback_profit_uah ?? 0).toLocaleString()} грн</span>
                          : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="kpi-container" style={{ marginBottom: '24px' }}>
        <div className="kpi-card" style={{ borderLeft: '4px solid #059669' }}>
          <span className="kpi-title">Чистий прибуток за добу (з урахуванням втрат)</span>
          <span className="kpi-value" style={{ color: netProfit >= 0 ? '#059669' : '#ef4444' }}>{Math.round(netProfit).toLocaleString()} грн</span>
          <span className="kpi-change neutral">Дохід мінус Витрати та Знос</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Дохід від розряду (Продаж)</span>
          <span className="kpi-value" style={{ color: '#059669' }}>{Math.round(totalRevenue).toLocaleString()} грн</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Витрати заряду (Купівля + Тарифи)</span>
          <span className="kpi-value" style={{ color: '#ef4444' }}>{Math.round(totalCost).toLocaleString()} грн</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Знос батареї (Деградація)</span>
          <span className="kpi-value">{Math.round(totalDegradation).toLocaleString()} грн</span>
        </div>
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '4px' }}>Потужність заряду/розряду та рівень SoC BESS</h3>
        {dispatchProfile.length === 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '10px', padding: '60px 20px', color: 'var(--text-muted)' }}>
            <AlertCircle size={32} />
            <p style={{ margin: 0, fontSize: '0.9rem', textAlign: 'center' }}>
              Прогноз і графік заряду/розряду ще жодного разу не розраховувались на {targetDate}.
              <br />
              Натисніть «Розрахувати» вище, щоб отримати їх (не автоматично — щоб не запускати важкі ML/MILP розрахунки на кожну зміну дати).
            </p>
          </div>
        ) : (
          <>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0 0 12px' }}>
              Пунктирні лінії — межі ємності батареї: {Math.round(capacity * 0.9).toLocaleString()} кВт·год (макс. SoC 90%) і {Math.round(capacity * 0.1).toLocaleString()} кВт·год (мін. SoC 10%) з {Math.round(capacity).toLocaleString()} кВт·год загальної ємності. Стовпчики показують реально виконану потужність — якщо батарея вже на межі ємності, подальша команда заряду/розряду не виконується і стовпчик зменшується, навіть якщо введена потужність більша. Фіолетова лінія — ціна прогнозу (або ручна заявка, якщо годину скориговано вручну), для якої й порахований цей графік.
            </p>
            <div style={{ width: '100%', height: 380 }}>
              <ResponsiveContainer>
                <ComposedChart data={hasManualSchedule ? chargeChartData : chargeChartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis dataKey="hour" stroke="#9ca3af" />
                  <YAxis yAxisId="power" stroke="#9ca3af" label={{ value: 'кВт', angle: -90, position: 'insideLeft', fill: '#9ca3af', fontSize: 11 }} />
                  <YAxis yAxisId="soc" orientation="right" domain={[0, capacity]} stroke="#9ca3af" label={{ value: 'кВт·год', angle: 90, position: 'insideRight', fill: '#9ca3af', fontSize: 11 }} />
                  <YAxis yAxisId="price" hide domain={['auto', 'auto']} />
                  <Tooltip contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }} />
                  <Legend />
                  <ReferenceLine yAxisId="soc" y={capacity * 0.9} stroke="#d97706" strokeDasharray="4 4" />
                  <ReferenceLine yAxisId="soc" y={capacity * 0.1} stroke="#d97706" strokeDasharray="4 4" />
                  <Bar yAxisId="power" dataKey="charge" name="Потужність заряду (кВт)" fill="#3b82f6" />
                  <Bar yAxisId="power" dataKey="discharge" name="Потужність розряду (кВт)" fill="#059669" />
                  <Line yAxisId="soc" type="monotone" dataKey="soc" name="Рівень заряду SoC (кВт-год)" stroke="#d97706" fill="rgba(217, 119, 6, 0.12)" strokeWidth={2} />
                  <Line yAxisId="price" type="monotone" dataKey="price" name="Ціна прогнозу / заявки (грн/МВт-год)" stroke="#a78bfa" strokeWidth={2} dot={{ r: 2 }} connectNulls />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </>
        )}
      </div>

      <div className="glass-card" style={{ marginTop: '24px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <div>
            <h3 className="card-title" style={{ margin: 0 }}>Ручне коригування заявок (Manual Dispatch Schedule)</h3>
            <p style={{ margin: '4px 0 0 0', fontSize: '13px', color: '#9ca3af' }}>
              Введіть потужність (МВт: розряд +, заряд -) та реальну ціну заявки (грн/МВт-год) для кожної години на {targetDate}.
            </p>
          </div>
          <div style={{ display: 'flex', gap: '10px' }}>
            <button className="btn btn-secondary" onClick={handleExport} disabled={exporting} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Download size={14} /> {exporting ? 'Експорт...' : 'Експорт в Excel'}
            </button>
            <button className="btn" onClick={saveOverrides}>
              Зберегти ручний графік
            </button>
            <button
              className="btn"
              style={{ backgroundColor: 'rgba(239, 68, 68, 0.2)', border: '1px solid #ef4444' }}
              onClick={() => {
                if (window.confirm('Ви впевнені, що хочете скинути ручний графік до оптимального?')) {
                  resetOverridesToOptimal();
                }
              }}
            >
              Скинути до оптимального
            </button>
          </div>
        </div>

        {hasManualSchedule && (
          <div style={{ display: 'flex', gap: '10px', marginBottom: '16px', padding: '12px', borderRadius: '6px', background: 'rgba(217, 119, 6, 0.08)', border: '1px solid rgba(217, 119, 6, 0.3)', fontSize: '13px' }}>
            <AlertTriangle size={16} style={{ color: '#d97706', flexShrink: 0, marginTop: '1px' }} />
            <span>
              На цю дату вже збережено ручний графік — він «заморожує» ціну/потужність на момент збереження і НЕ оновлюється
              сам, навіть якщо прогноз чи оптимізацію перерахували пізніше. Якщо після збереження графіка ви ще раз
              натискали «Розрахувати» — натисніть «Скинути до оптимального», щоб побачити свіжий розрахунок, інакше
              графік і чистий прибуток показують застарілі числа.
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
                const dp = dispatchProfile[idx];
                const optimalKw = dp ? dp.charge > 0 ? -dp.charge : dp.discharge : 0;
                return (
                  <tr key={idx}>
                    <td>Година {idx + 1} ({String(idx).padStart(2, '0')}:00–{String(idx + 1).padStart(2, '0')}:00)</td>
                    <td style={{ color: optimalKw > 0 ? '#059669' : optimalKw < 0 ? '#3b82f6' : 'var(--text-muted)' }}>
                      {optimalKw > 0 ? `Розряд ${Math.round(optimalKw)} кВт` : optimalKw < 0 ? `Заряд ${Math.round(Math.abs(optimalKw))} кВт` : 'Простій'}
                    </td>
                    <td>
                      <input
                        type="number" step={10} className="form-input" style={{ width: '110px' }}
                        value={Math.round(o.power_mw * 1000)}
                        onChange={(e) => handlePowerChange(idx, e.target.value)}
                      />
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: '4px' }}>
                        <button className="btn btn-secondary" style={{ padding: '2px 8px', fontSize: '11px' }} onClick={() => handleQuickAction(idx, 'charge_max')}>Заряд</button>
                        <button className="btn btn-secondary" style={{ padding: '2px 8px', fontSize: '11px' }} onClick={() => handleQuickAction(idx, 'discharge_max')}>Розряд</button>
                        <button className="btn btn-secondary" style={{ padding: '2px 8px', fontSize: '11px' }} onClick={() => handleQuickAction(idx, 'idle')}>Стоп</button>
                      </div>
                    </td>
                    <td>
                      <input
                        type="number" step={1} className="form-input" style={{ width: '110px' }}
                        value={Math.round(o.price_uah * 100) / 100}
                        onChange={(e) => handlePriceChange(idx, e.target.value)}
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
