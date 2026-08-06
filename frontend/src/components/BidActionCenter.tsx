import { AlertTriangle, CheckCircle2, Info } from 'lucide-react';
import { useApp } from '../state/AppContext';

const SEVERITY_STYLE: Record<string, { bg: string; border: string; color: string; icon: any }> = {
  action:  { bg: 'var(--color-blue-bg)',    border: 'var(--color-blue-border)',    color: 'var(--color-blue)',    icon: Info },
  warning: { bg: 'var(--color-amber-bg)',   border: 'var(--color-amber-border)',   color: 'var(--color-amber)',   icon: AlertTriangle },
  ok:      { bg: 'var(--color-emerald-bg)', border: 'var(--color-emerald-border)', color: 'var(--color-emerald)', icon: CheckCircle2 },
  info:    { bg: 'var(--surface-subtle)',   border: 'var(--border-color)',         color: 'var(--text-secondary)', icon: Info },
};

export default function BidActionCenter() {
  const { actionSummary } = useApp();
  if (!actionSummary) return null;

  return (
    <div className="glass-card">
      <h3 className="card-title" style={{ marginBottom: '12px' }}>Що робити зараз</h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {actionSummary.actions.map((a, idx) => {
          const s = SEVERITY_STYLE[a.severity] ?? SEVERITY_STYLE.info;
          const Icon = s.icon;
          return (
            <div key={idx} style={{ display: 'flex', gap: '10px', background: s.bg, border: `1px solid ${s.border}`, padding: '12px', borderRadius: 'var(--radius-sm)', fontSize: '0.85rem', transition: 'background var(--transition-fast)' }}>
              <Icon size={16} style={{ color: s.color, flexShrink: 0 }} />
              <span>{a.text}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
