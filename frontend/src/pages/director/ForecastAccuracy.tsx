import { LineChart, Line, CartesianGrid, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { useApp } from '../../state/AppContext';

const SOURCE_LABELS: Record<string, string> = {
  real_dispatch_vs_perfect_foresight: 'Реальний P&L диспетчеризації vs ідеальний прогноз',
  live_forecast_vs_actual: 'Жива точність (прогноз vs факт РДН)',
  walk_forward_backtest: 'Walk-forward бектест (історичні дані)',
  default_fallback_no_data: 'Тимчасовий дефолт — даних ще немає',
};

export default function ForecastAccuracy() {
  const { forecastAccuracy } = useApp();

  if (!forecastAccuracy) {
    return (
      <div className="glass-card">
        <p style={{ color: 'var(--text-muted)' }}>Завантаження звіту точності прогнозу...</p>
      </div>
    );
  }

  const live = forecastAccuracy.live_accuracy;
  const ratio = forecastAccuracy.profit_capture_ratio;

  return (
    <div>
      <div className="glass-card" style={{ marginBottom: '24px', borderLeft: '4px solid #0891b2' }}>
        <h4 style={{ margin: '0 0 6px 0', fontSize: '16px', color: '#0891b2' }}>Наскільки прогноз збігається з реальністю</h4>
        <p style={{ margin: 0, fontSize: '13.5px', color: '#9ca3af', lineHeight: '1.5' }}>
          WAPE (Weighted Absolute Percentage Error) надійніший за MAPE у години з ціною, близькою до нуля —
          саме тому дохід у звіті вище дораховується за WAPE, а не за фіксованою константою.
        </p>
      </div>

      <div className="kpi-container" style={{ marginBottom: '24px' }}>
        <div className="kpi-card" style={{ borderLeft: '4px solid #059669' }}>
          <span className="kpi-title">Коефіцієнт захопленого прибутку</span>
          <span className="kpi-value" style={{ color: '#059669' }}>{(ratio.ratio * 100).toFixed(1)}%</span>
          <span className="kpi-change neutral">Джерело: {SOURCE_LABELS[ratio.source] || ratio.source}</span>
          {ratio.source === 'real_dispatch_vs_perfect_foresight' && (
            <>
              <span className="kpi-change neutral" style={{ display: 'block', marginTop: '4px' }}>
                {ratio.n_days} повних діб · факт {Math.round(ratio.total_actual_profit_uah).toLocaleString()} грн з {Math.round(ratio.total_perfect_foresight_profit_uah).toLocaleString()} грн ідеального прогнозу
              </span>
              <span className="kpi-change neutral" style={{ display: 'block', marginTop: '2px' }}>
                з них {ratio.n_days_market_bid_settled} діб — за реальними звіреними заявками РДН, {ratio.n_days_plan_full_execution_assumed} — старіші, з припущенням 100% виконання плану
              </span>
            </>
          )}
        </div>
        <div className="kpi-card">
          <span className="kpi-title">WAPE (останні {live.days ?? 30} днів)</span>
          <span className="kpi-value">{live.wape !== undefined && live.wape !== null ? `${live.wape.toFixed(1)}%` : '—'}</span>
          <span className="kpi-change neutral">{live.n_hours ? `${live.n_hours} годин зіставлено` : 'Недостатньо даних'}</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Систематичне зміщення (bias)</span>
          <span className="kpi-value">{live.bias_uah !== undefined && live.bias_uah !== null ? `${Math.round(live.bias_uah).toLocaleString()} грн` : '—'}</span>
          <span className="kpi-change neutral">Прогноз {live.bias_uah > 0 ? 'завищує' : 'занижує'} ціну в середньому</span>
        </div>
      </div>

      {live.status === 'insufficient_data' ? (
        <div className="glass-card">
          <p style={{ color: 'var(--text-muted)' }}>{live.message}</p>
        </div>
      ) : (
        <div className="glass-card">
          <h3 className="card-title" style={{ marginBottom: '16px' }}>Щоденна точність прогнозу (WAPE/MAPE)</h3>
          <div style={{ width: '100%', height: 320 }}>
            <ResponsiveContainer>
              <LineChart data={live.daily || []}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="date" stroke="#9ca3af" fontSize={11} />
                <YAxis stroke="#9ca3af" fontSize={11} />
                <Tooltip contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }} />
                <Legend />
                <Line type="monotone" dataKey="wape" name="WAPE (%)" stroke="#059669" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="mape" name="MAPE (%, нестабільний біля нуля)" stroke="#6b7280" strokeWidth={1} strokeDasharray="4 4" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}
