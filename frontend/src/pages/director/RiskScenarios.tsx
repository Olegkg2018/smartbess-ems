import { LineChart, Line, CartesianGrid, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { useApp } from '../../state/AppContext';

export default function RiskScenarios() {
  const { optimizationResult } = useApp();

  const scenarios = optimizationResult?.scenarios;
  const summary = optimizationResult?.summary;

  const scenariosData = scenarios
    ? Array.from({ length: 24 }, (_, i) => ({
        hour: i + 1,
        base: scenarios.base?.schedule?.[i]?.price_forecast_uah_mwh,
        pessimistic: scenarios.pessimistic?.schedule?.[i]?.price_forecast_uah_mwh,
        aggressive: scenarios.aggressive?.schedule?.[i]?.price_forecast_uah_mwh,
      }))
    : [];

  return (
    <div>
      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '16px' }}>Сценарії коливань цін РДН (Base vs Pessimistic vs Aggressive)</h3>
        <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', margin: '0 0 16px' }}>
          {summary?.price_band_source === 'quantile_model_p10_p90'
            ? 'Pessimistic/Aggressive — реальні P10/P90 з conformal-каліброваної quantile-регресії моделі (walk-forward покриття ~80% на реальних даних), не припущення про волатильність.'
            : 'Pessimistic/Aggressive — статистично обґрунтовані відхилення (±1.64σ, log-normal) від базового прогнозу (запасний варіант — квантильні моделі ще не порахували інтервал для цієї дати).'}
        </p>
        <div style={{ width: '100%', height: 320 }}>
          <ResponsiveContainer>
            <LineChart data={scenariosData}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="hour" stroke="var(--text-secondary)" />
              <YAxis stroke="var(--text-secondary)" />
              <Tooltip contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }} />
              <Legend />
              <Line type="monotone" dataKey="base" name="Базовий прогноз" stroke="var(--color-blue)" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="pessimistic" name="Песимістичний (5-й перцентиль)" stroke="var(--color-rose)" strokeWidth={1.5} dot={false} />
              <Line type="monotone" dataKey="aggressive" name="Агресивний (95-й перцентиль)" stroke="var(--color-emerald)" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid-3">
        <div className="kpi-card">
          <span className="kpi-title">Очікуваний прибуток (Base)</span>
          <span className="kpi-value">{summary ? Math.round(summary.base_expected_profit_uah).toLocaleString() : '—'} грн</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Найгірший сценарій (Monte Carlo, {summary?.confidence_level_pct ?? 95}%)</span>
          <span className="kpi-value">{summary ? Math.round(summary.worst_case_profit_uah).toLocaleString() : '—'} грн</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Value at Risk (VaR {summary?.confidence_level_pct ?? 95}%)</span>
          <span className="kpi-value">{summary ? Math.round(summary.value_at_risk_uah).toLocaleString() : '—'} грн/день</span>
        </div>
      </div>
    </div>
  );
}
