import { AlertTriangle, CheckCircle2, Info } from 'lucide-react';
import { useApp } from '../state/AppContext';

const SEVERITY_STYLE: Record<string, { bg: string; border: string; color: string; icon: any }> = {
  action:  { bg: 'rgba(59, 130, 246, 0.08)',  border: 'rgba(59, 130, 246, 0.3)',  color: '#3b82f6', icon: Info },
  warning: { bg: 'rgba(245, 158, 11, 0.08)',  border: 'rgba(217, 119, 6, 0.3)',   color: '#d97706', icon: AlertTriangle },
  ok:      { bg: 'rgba(5, 150, 105, 0.08)',   border: 'rgba(5, 150, 105, 0.3)',   color: '#059669', icon: CheckCircle2 },
  info:    { bg: 'rgba(255,255,255,0.02)',    border: 'rgba(255,255,255,0.08)',   color: '#9ca3af', icon: Info },
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
            <div key={idx} style={{ display: 'flex', gap: '10px', background: s.bg, border: `1px solid ${s.border}`, padding: '12px', borderRadius: '6px', fontSize: '0.85rem' }}>
              <Icon size={16} style={{ color: s.color, flexShrink: 0 }} />
              <span>{a.text}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
