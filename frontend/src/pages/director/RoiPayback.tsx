import { ComposedChart, Area, Line, CartesianGrid, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { useApp } from '../../state/AppContext';

export default function RoiPayback() {
  const { capex, setCapex, discountRate, setDiscountRate, lifetime, setLifetime, executiveReport } = useApp();

  const avgDailyProfit = executiveReport?.ytd_metrics?.average_daily_profit_uah ?? 0;
  const baseYearlyProfit = avgDailyProfit * 365;
  const opexYearly = capex * 0.02 + 180000;
  const netYearlyInflow = baseYearlyProfit - opexYearly;

  const years = Array.from({ length: lifetime + 1 }, (_, i) => i);
  let cumulativeNPV = -capex;
  const paybackData = years.map((yr) => {
    if (yr > 0) {
      const discountedInflow = netYearlyInflow / Math.pow(1 + discountRate / 100, yr);
      cumulativeNPV += discountedInflow;
    }
    return { year: `Рік ${yr}`, NPV: Math.round(cumulativeNPV), ZeroLine: 0 };
  });

  return (
    <div className="grid-2">
      <div className="glass-card">
        <h3 className="card-title">Розрахунок окупності BESS проекту</h3>
        <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', margin: '8px 0 16px' }}>
          Річний прибуток оцінюється як середньодобовий реальний прибуток YTD ({Math.round(avgDailyProfit).toLocaleString()} грн/день) × 365.
        </p>
        <div style={{ margin: '20px 0' }}>
          <div className="form-group">
            <label className="form-label">CAPEX проекту (грн)</label>
            <input type="number" className="form-input" value={capex} onChange={(e) => setCapex(Number(e.target.value))} />
          </div>
          <div className="form-group">
            <label className="form-label">Ставка дисконтування (%)</label>
            <input type="number" className="form-input" value={discountRate} onChange={(e) => setDiscountRate(Number(e.target.value))} />
          </div>
          <div className="form-group">
            <label className="form-label">Термін служби проекту (років)</label>
            <input type="number" className="form-input" value={lifetime} onChange={(e) => setLifetime(Number(e.target.value))} />
          </div>
        </div>

        <div className="audit-item" style={{ background: 'rgba(5, 150, 105, 0.05)', borderColor: 'rgba(5, 150, 105, 0.2)' }}>
          <span>Простий період окупності (Simple Payback):</span>
          <strong style={{ color: '#059669', fontSize: '1rem' }}>
            {netYearlyInflow > 0 ? (capex / netYearlyInflow).toFixed(1) : '—'} р.
          </strong>
        </div>
      </div>

      <div className="glass-card">
        <h3 className="card-title">Накопичений грошовий потік проекту (NPV Discounted)</h3>
        <div style={{ width: '100%', height: 280 }}>
          <ResponsiveContainer>
            <ComposedChart data={paybackData}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="year" stroke="#9ca3af" />
              <YAxis stroke="#9ca3af" />
              <Tooltip contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }} />
              <Area type="monotone" dataKey="NPV" name="NPV (грн)" stroke="#059669" fill="rgba(5, 150, 105, 0.1)" strokeWidth={2} />
              <Line type="monotone" dataKey="ZeroLine" name="Точка беззбитковості" stroke="#ef4444" strokeWidth={1} activeDot={false} dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
