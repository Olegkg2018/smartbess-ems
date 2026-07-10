import { ComposedChart, CartesianGrid, XAxis, YAxis, Tooltip, Legend, Area, Bar, ResponsiveContainer } from 'recharts';
import { useApp } from '../../state/AppContext';
import GlobalFilterBar from '../../components/GlobalFilterBar';

const TARIFF_UAH_PER_KWH = (528.57 + 1500.0 + 104.57 + 100.0) / 1000.0;
const DEGRADATION_UAH_PER_KWH = 1.2;

export default function OptimizationSchedule() {
  const { optimizationResult, manualOverrides, setManualOverrides, targetDate, capacity, power, efficiency, saveOverrides, resetOverridesToOptimal } = useApp();

  const baseSchedule = optimizationResult?.scenarios?.base?.schedule || [];

  const chartProfile = baseSchedule.length === 24
    ? baseSchedule.map((s: any) => ({ hour: `${s.hour + 1}`, charge: s.power_kw < 0 ? -s.power_kw : 0, discharge: s.power_kw > 0 ? s.power_kw : 0, soc: s.soc_kwh, price: s.price_forecast_uah_mwh }))
    : [];

  let dailyRevenue = 0, dailyCost = 0, dailyDegradation = 0;
  let runningSoc = capacity * 0.2;
  const overridesComplete = manualOverrides && manualOverrides.length === 24;

  const currentDayProfile = overridesComplete
    ? manualOverrides.map((o: any) => {
        const powerKW = o.power_mw * 1000.0;
        const priceKWh = o.price_uah / 1000.0;
        let chargeKW = 0, dischargeKW = 0;
        if (powerKW < 0) {
          chargeKW = Math.abs(powerKW);
          dailyCost += chargeKW * (priceKWh + TARIFF_UAH_PER_KWH);
          runningSoc = Math.min(capacity * 0.9, runningSoc + chargeKW * (efficiency / 100.0));
        } else if (powerKW > 0) {
          dischargeKW = powerKW;
          dailyRevenue += dischargeKW * priceKWh;
          dailyDegradation += dischargeKW * DEGRADATION_UAH_PER_KWH;
          runningSoc = Math.max(capacity * 0.1, runningSoc - dischargeKW / (efficiency / 100.0));
        }
        return { hour: `${o.hour + 1}`, charge: chargeKW, discharge: dischargeKW, soc: runningSoc, price: o.price_uah };
      })
    : [];

  const dailyNetProfit = dailyRevenue - dailyCost - dailyDegradation;

  return (
    <div>
      <GlobalFilterBar />

      <div className="kpi-container" style={{ marginBottom: '24px' }}>
        <div className="kpi-card" style={{ borderLeft: '4px solid #059669' }}>
          <span className="kpi-title">Чистий прибуток за добу (з урахуванням втрат)</span>
          <span className="kpi-value" style={{ color: dailyNetProfit >= 0 ? '#059669' : '#ef4444' }}>{Math.round(dailyNetProfit).toLocaleString()} грн</span>
          <span className="kpi-change neutral">Дохід мінус Витрати та Знос</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Дохід від розряду (Продаж)</span>
          <span className="kpi-value" style={{ color: '#059669' }}>{Math.round(dailyRevenue).toLocaleString()} грн</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Витрати заряду (Купівля + Тарифи)</span>
          <span className="kpi-value" style={{ color: '#ef4444' }}>{Math.round(dailyCost).toLocaleString()} грн</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Знос батареї (Деградація)</span>
          <span className="kpi-value">{Math.round(dailyDegradation).toLocaleString()} грн</span>
        </div>
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '4px' }}>Потужність заряду/розряду BESS (кВт)</h3>
        <div style={{ width: '100%', height: 220 }}>
          <ResponsiveContainer>
            <ComposedChart data={overridesComplete ? currentDayProfile : chartProfile} margin={{ bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="hour" stroke="#9ca3af" />
              <YAxis stroke="#9ca3af" />
              <Tooltip contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }} />
              <Legend />
              <Bar dataKey="charge" name="Потужність заряду (кВт)" fill="#3b82f6" />
              <Bar dataKey="discharge" name="Потужність розряду (кВт)" fill="#059669" />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '4px' }}>Рівень заряду SoC (кВт-год)</h3>
        <div style={{ width: '100%', height: 180 }}>
          <ResponsiveContainer>
            <ComposedChart data={overridesComplete ? currentDayProfile : chartProfile} margin={{ top: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="hour" stroke="#9ca3af" />
              <YAxis stroke="#9ca3af" />
              <Tooltip contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }} />
              <Area type="monotone" dataKey="soc" name="Рівень заряду SoC (кВт-год)" stroke="#d97706" fill="rgba(217, 119, 6, 0.1)" />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
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
            <button className="btn" onClick={saveOverrides}>Зберегти ручний графік</button>
            <button
              className="btn"
              style={{ backgroundColor: 'rgba(239, 68, 68, 0.2)', border: '1px solid #ef4444' }}
              onClick={() => { if (window.confirm('Ви впевнені, що хочете скинути ручний графік до оптимального?')) resetOverridesToOptimal(); }}
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
              {manualOverrides.map((o: any, idx: number) => {
                const sched = baseSchedule[idx];
                const recPower = sched ? sched.power_kw / 1000.0 : 0.0;
                return (
                  <tr key={idx}>
                    <td>Година {idx + 1} ({String(idx).padStart(2, '0')}:00–{String(idx + 1).padStart(2, '0')}:00)</td>
                    <td style={{ color: recPower > 0 ? '#059669' : recPower < 0 ? '#3b82f6' : '#9ca3af' }}>
                      {recPower > 0 ? `Розряд +${recPower.toFixed(2)} МВт` : recPower < 0 ? `Заряд ${recPower.toFixed(2)} МВт` : 'Пауза'}
                    </td>
                    <td>
                      <input
                        type="number" step="0.05" className="form-input" style={{ width: '120px', padding: '4px 8px', fontSize: '13px' }}
                        value={o.power_mw}
                        onChange={(e) => {
                          const val = Number(e.target.value);
                          setManualOverrides(manualOverrides.map((it: any, i: number) => (i === idx ? { ...it, power_mw: val } : it)));
                        }}
                      />
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: '5px' }}>
                        <button className="btn" style={{ padding: '3px 8px', fontSize: '11px', backgroundColor: '#3b82f6' }}
                          onClick={() => setManualOverrides(manualOverrides.map((it: any, i: number) => (i === idx ? { ...it, power_mw: -(power / 1000.0) } : it)))}>
                          Заряд (Max)
                        </button>
                        <button className="btn" style={{ padding: '3px 8px', fontSize: '11px', backgroundColor: '#059669' }}
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
