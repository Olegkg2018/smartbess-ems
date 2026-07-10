import { AlertTriangle, Flame, Zap, Gauge } from 'lucide-react';
import { useApp } from '../../state/AppContext';

function formatAsOf(iso: string | null) {
  if (!iso) return 'немає даних';
  try {
    return new Date(iso).toLocaleString('uk-UA');
  } catch {
    return iso;
  }
}

export default function GridRiskPanel() {
  const { marketConditions } = useApp();

  if (!marketConditions) {
    return (
      <div className="glass-card">
        <p style={{ color: 'var(--text-muted)' }}>Завантаження реальних даних про стан енергосистеми...</p>
      </div>
    );
  }

  const stress = marketConditions.grid_stress_today || { grid_stress_high: 0, grid_stress_medium: 0, mentions: 0 };
  const severity = stress.grid_stress_high ? 'high' : stress.grid_stress_medium ? 'medium' : 'none';
  const severityColor = severity === 'high' ? '#ef4444' : severity === 'medium' ? '#d97706' : '#059669';
  const severityText = severity === 'high' ? 'Підвищений ризик (аварії/обстріли)' : severity === 'medium' ? 'Помірний ризик (обмеження)' : 'Без відхилень';

  return (
    <div>
      <div className="glass-card" style={{ marginBottom: '24px', borderLeft: `4px solid ${severityColor}` }}>
        <h4 style={{ margin: '0 0 6px 0', fontSize: '16px', color: severityColor }}>
          <AlertTriangle size={16} style={{ verticalAlign: 'middle', marginRight: '6px' }} />
          Сьогоднішній стан енергосистеми: {severityText}
        </h4>
        <p style={{ margin: 0, fontSize: '13.5px', color: '#9ca3af', lineHeight: '1.5' }}>
          Реальний keyword-сигнал з публічних каналів Укренерго/Міненерго ({stress.mentions} згадувань сьогодні).
          Це операційна обізнаність для диспетчера, а не автоматична поправка прогнозу.
        </p>
      </div>

      <div className="kpi-container" style={{ marginBottom: '24px' }}>
        <div className="kpi-card">
          <span className="kpi-title">
            <Flame size={14} style={{ verticalAlign: 'middle', marginRight: '4px' }} />
            Ціна газу (EUR/МВт·год)
          </span>
          <span className="kpi-value">{marketConditions.gas_price_eur_mwh?.toFixed(2) ?? '—'}</span>
          <span className="kpi-change neutral">станом на {formatAsOf(marketConditions.gas_price_as_of)}</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">
            <Zap size={14} style={{ verticalAlign: 'middle', marginRight: '4px' }} />
            Транскордонний нетто-експорт
          </span>
          <span className="kpi-value" style={{ color: (marketConditions.grid_net_export_mw ?? 0) >= 0 ? '#059669' : '#ef4444' }}>
            {marketConditions.grid_net_export_mw?.toFixed(0) ?? '—'} МВт
          </span>
          <span className="kpi-change neutral">
            {(marketConditions.grid_net_export_mw ?? 0) >= 0 ? 'Україна експортує' : 'Україна імпортує'} · станом на {formatAsOf(marketConditions.grid_net_export_as_of)}
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">
            <Gauge size={14} style={{ verticalAlign: 'middle', marginRight: '4px' }} />
            Джерело
          </span>
          <span className="kpi-value" style={{ fontSize: '0.95rem' }}>ENTSO-E + oree.com.ua + Telegram</span>
          <span className="kpi-change neutral">Реальні відкриті джерела (Фаза 1)</span>
        </div>
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '16px' }}>Останні реальні повідомлення (Укренерго/Міненерго)</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {(marketConditions.latest_posts || []).length === 0 && (
            <span style={{ color: 'var(--text-muted)' }}>Немає кешованих постів — фонова синхронізація ще не запускалась.</span>
          )}
          {(marketConditions.latest_posts || []).map((post: any, idx: number) => (
            <div key={idx} style={{
              fontSize: '0.85rem', padding: '12px', borderRadius: '6px', background: 'rgba(255,255,255,0.02)',
              borderLeft: `3px solid ${post.severity === 'high' ? '#ef4444' : post.severity === 'medium' ? '#d97706' : '#3b82f6'}`,
              whiteSpace: 'pre-wrap',
            }}>
              <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginBottom: '6px' }}>
                @{post.channel} · {formatAsOf(post.datetime)}
              </div>
              {post.text.slice(0, 400)}{post.text.length > 400 ? '…' : ''}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
