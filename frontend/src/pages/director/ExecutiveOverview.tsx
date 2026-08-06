import { AreaChart, Area, ComposedChart, Bar, CartesianGrid, XAxis, YAxis, Tooltip, Legend, ReferenceLine, ResponsiveContainer } from 'recharts';
import { useApp } from '../../state/AppContext';

export default function ExecutiveOverview() {
  const { executiveReport } = useApp();

  if (!executiveReport) {
    return (
      <div className="glass-card">
        <p style={{ color: 'var(--text-muted)' }}>Завантаження фінансового звіту...</p>
      </div>
    );
  }

  const report = executiveReport;

  return (
    <div>
      <div className="glass-card" style={{ marginBottom: '24px', borderLeft: '4px solid var(--color-blue)' }}>
        <h4 style={{ margin: '0 0 6px 0', fontSize: '16px', color: '#60a5fa' }}>
          Аналіз окупності інвестицій BESS (C-Level YTD Analytics)
        </h4>
        <p style={{ margin: 0, fontSize: '13.5px', color: 'var(--text-secondary)', lineHeight: '1.5' }}>
          Звіт відображає накопичений фінансовий результат роботи BESS з моменту запуску системи
          (<strong>{report.launch_date}</strong>) до поточної дати, а також прогнозований ROY (Rest of Year) тренд до кінця року.
          Для діб без реальної телеметрії дораховується виміряний коефіцієнт точності прогнозу (див. «Точність прогнозу»),
          а не фіксована константа.
        </p>
      </div>

      <div className="kpi-container" style={{ marginBottom: '24px' }}>
        <div className="kpi-card">
          <span className="kpi-title">Накопичений прибуток YTD</span>
          <span className="kpi-value" style={{ color: 'var(--color-emerald)' }}>{Math.round(report.ytd_metrics.actual_p_l_ytd_uah).toLocaleString()} грн</span>
          <span className="kpi-change positive">За {report.ytd_metrics.days_in_operation} днів роботи</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Оптимальний потенціал YTD (100% точність)</span>
          <span className="kpi-value" style={{ color: '#60a5fa' }}>{Math.round(report.ytd_metrics.optimal_forecast_profit_ytd_uah).toLocaleString()} грн</span>
          <span className="kpi-change neutral">
            Втрачено через помилки прогнозу: {Math.round(report.ytd_metrics.optimal_forecast_profit_ytd_uah - report.ytd_metrics.actual_p_l_ytd_uah).toLocaleString()} грн
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Прогноз прибутку до кінця року (ROY)</span>
          <span className="kpi-value" style={{ color: '#fbbf24' }}>{Math.round(report.roy_forecast.projected_roy_profit_uah).toLocaleString()} грн</span>
          <span className="kpi-change positive">Очікувано за рік: {Math.round(report.roy_forecast.total_annual_profit_projected_uah).toLocaleString()} грн</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Термін окупності CAPEX</span>
          <span className="kpi-value">{report.roy_forecast.estimated_payback_years.toFixed(2)} років</span>
          <span className="kpi-change negative">CAPEX: {Math.round(report.bess_properties.estimated_capex_uah).toLocaleString()} грн</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Внесок OREE (участь у ринку) YTD</span>
          <span className="kpi-value" style={{ color: 'var(--color-amber)' }}>{Math.round(report.ytd_metrics.oree_participation_fee_ytd_uah ?? 0).toLocaleString()} грн</span>
          <span className="kpi-change neutral">4669.71 грн/міс + 6.88 грн/МВт·год (тариф 2026)</span>
        </div>
      </div>

      <div className="grid-2">
        <div className="glass-card">
          <h3 className="card-title" style={{ marginBottom: '16px' }}>Історія добової дохідності (Daily Arbitrage P&L)</h3>
          <div style={{ width: '100%', height: 320 }}>
            <ResponsiveContainer>
              <ComposedChart data={(report.daily_history || []).map((d: any) => ({ ...d, charge_cost_negative: -d.charge_cost_uah }))}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="date" stroke="var(--text-secondary)" fontSize={11} />
                <YAxis stroke="var(--text-secondary)" fontSize={11} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }}
                  formatter={(value: any, name: any) => {
                    if (name === 'discharge_revenue_uah') return [`${Math.round(value).toLocaleString()} грн`, 'Дохід (Продаж)'];
                    if (name === 'charge_cost_negative') return [`${Math.round(Math.abs(value)).toLocaleString()} грн`, 'Витрати (Купівля)'];
                    if (name === 'actual_profit_uah') return [`${Math.round(value).toLocaleString()} грн`, 'Чистий суточний прибуток'];
                    return [value, name];
                  }}
                />
                <Legend />
                <Bar dataKey="discharge_revenue_uah" name="Дохід (Продаж)" fill="var(--color-emerald)" />
                <Bar dataKey="charge_cost_negative" name="Витрати (Купівля)" fill="var(--color-rose)" />
                <Area type="monotone" dataKey="actual_profit_uah" name="Чистий прибуток" stroke="var(--color-blue)" fill="rgba(59, 130, 246, 0.05)" strokeWidth={2} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="glass-card">
          <h3 className="card-title" style={{ marginBottom: '16px' }}>Траєкторія окупності BESS (Investment Payback Trajectory)</h3>
          <div style={{ width: '100%', height: 320 }}>
            <ResponsiveContainer>
              <AreaChart data={report.payback_trajectory}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="date" stroke="var(--text-secondary)" fontSize={11} />
                <YAxis stroke="var(--text-secondary)" fontSize={11} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }}
                  formatter={(value: any, _: any, props: any) => {
                    const statusText = props.payload.is_projected ? '(Прогноз)' : '(Факт)';
                    return [`${Math.round(value).toLocaleString()} грн`, `Баланс інвестицій ${statusText}`];
                  }}
                />
                <ReferenceLine y={0} stroke="var(--color-rose)" strokeDasharray="4 4" strokeWidth={2} label={{ value: 'БЕЗУБИТКОВІСТЬ', fill: 'var(--color-rose)', fontSize: 10, position: 'top' }} />
                <Area type="monotone" dataKey="cumulative_p_l_uah" stroke="#8b5cf6" fill="rgba(139, 92, 246, 0.15)" strokeWidth={3} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
}
