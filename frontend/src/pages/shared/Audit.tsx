import { useApp } from '../../state/AppContext';

export default function Audit() {
  const { auditLogs, systemLogs } = useApp();

  return (
    <div className="grid-2">
      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '16px' }}>Журнал дій та аудит рішень (Four-Eyes Principle)</h3>
        <div className="audit-list">
          {auditLogs.length === 0 && <span style={{ color: 'var(--text-muted)' }}>Ще немає підтверджених дій у цій сесії.</span>}
          {auditLogs.map((log, idx) => (
            <div key={idx} className="audit-item">
              <div>
                <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{log.action}</div>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '4px' }}>
                  Инициатор: <span className="audit-user">{log.user}</span> | IP: {log.ip}
                </div>
              </div>
              <div className="audit-meta">
                <span>{log.time}</span>
                <span className="status-badge online" style={{ padding: '2px 6px', fontSize: '0.65rem' }}>{log.status}</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '16px' }}>Події системного логу (EMS Realtime Events)</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '350px', overflowY: 'auto' }}>
          {systemLogs.map((log, idx) => (
            <div key={idx} style={{
              fontSize: '0.8rem', padding: '8px 12px', borderRadius: '6px', background: 'rgba(255,255,255,0.02)',
              borderLeft: `3px solid ${log.type === 'success' ? 'var(--color-emerald)' : log.type === 'warn' ? 'var(--color-amber)' : log.type === 'error' ? 'var(--color-rose)' : 'var(--color-blue)'}`,
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--text-muted)', fontSize: '0.75rem' }}>
                <span>[{log.src}]</span>
                <span>{log.time}</span>
              </div>
              <div style={{ color: 'var(--text-secondary)', marginTop: '4px' }}>{log.text}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
