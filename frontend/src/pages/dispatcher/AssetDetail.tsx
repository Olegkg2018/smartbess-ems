import { useState } from 'react';
import { Lock, AlertTriangle } from 'lucide-react';
import { useApp } from '../../state/AppContext';

function formatAge(timestamp: string | null): string {
  if (!timestamp) return 'немає даних';
  const ageSec = Math.max(0, (Date.now() - new Date(timestamp).getTime()) / 1000);
  const hhmmss = new Date(timestamp).toLocaleTimeString('uk-UA');
  if (ageSec < 60) return `${hhmmss} (${Math.round(ageSec)}с тому)`;
  return `${hhmmss} (${Math.round(ageSec / 60)}хв тому)`;
}

export default function AssetDetail() {
  const { activeRole, capacity, power, scadaStatus, triggerFourEyesApproval } = useApp();
  const [forcePowerKw, setForcePowerKw] = useState(150);

  const connected = scadaStatus?.connected ?? false;
  const socPct = scadaStatus?.soc_pct ?? null;
  const powerMw = scadaStatus?.power_mw ?? null;
  const tempC = scadaStatus?.battery_temp_c ?? null;
  const sohPct = scadaStatus?.soh_pct ?? null;

  return (
    <div className="grid-2">
      <div className="glass-card">
        <h3 className="card-title">Телеметрія симулятора BESS (Modbus)</h3>
        <p style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', margin: '4px 0 0' }}>
          Реального обладнання не підключено — це дані Modbus-симулятора. Оновлено: {formatAge(scadaStatus?.timestamp ?? null)}.
        </p>

        {!connected && (
          <div style={{ background: 'rgba(239, 68, 68, 0.08)', border: '1px solid rgba(239, 68, 68, 0.25)', padding: '10px 12px', borderRadius: '8px', margin: '12px 0', display: 'flex', gap: '8px', alignItems: 'center' }}>
            <AlertTriangle size={16} style={{ color: 'var(--color-rose)', flexShrink: 0 }} />
            <span style={{ fontSize: '0.8rem', color: 'var(--color-rose)' }}>Немає свіжих даних від симулятора — значення нижче можуть бути застарілими або відсутні.</span>
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', margin: '30px 0' }}>
          <div style={{
            width: '180px', height: '180px', borderRadius: '50%',
            border: '8px solid rgba(5, 150, 105, 0.15)', borderTopColor: connected ? 'var(--color-emerald)' : 'var(--text-secondary)',
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', position: 'relative',
          }}>
            <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', fontWeight: 600 }}>Battery SoC</span>
            <span style={{ fontSize: '2.5rem', fontWeight: 700, color: connected ? 'var(--color-emerald)' : 'var(--text-secondary)' }}>
              {socPct != null ? `${socPct.toFixed(1)} %` : '—'}
            </span>
            <span style={{ fontSize: '0.75rem', color: '#6b7280' }}>
              {socPct != null ? `${Math.round(capacity * socPct / 100)} кВт-год / ${capacity} кВт-год` : `— / ${capacity} кВт-год`}
            </span>
          </div>
        </div>

        <table className="data-table">
          <tbody>
            <tr><td>Номінальна ємність</td><td style={{ textAlign: 'right', fontWeight: 600 }}>{capacity} кВт-год</td></tr>
            <tr><td>Макс. потужність</td><td style={{ textAlign: 'right', fontWeight: 600 }}>{power} кВт</td></tr>
            <tr><td>Поточна активна потужність</td><td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--color-blue)' }}>
              {powerMw != null ? `${(powerMw * 1000).toFixed(1)} кВт (${powerMw < 0 ? 'Заряд' : powerMw > 0 ? 'Розряд' : 'Пауза'})` : '—'}
            </td></tr>
            <tr><td>Температура осередків</td><td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--color-amber)' }}>
              {tempC != null ? `${tempC.toFixed(1)} °C` : '—'}
            </td></tr>
            <tr><td>Технічний стан (SOH)</td><td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--color-emerald)' }}>
              {sohPct != null ? `${sohPct.toFixed(2)} %` : '—'}
            </td></tr>
          </tbody>
        </table>
      </div>

      <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
        <div>
          <h3 className="card-title">Демонстраційна панель ручного диспетчингу (демо)</h3>
          <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: '8px 0 20px 0' }}>
            Ця панель НЕ підключена до backend і НЕ надсилає команди на BESS — лише демонструє UX Four-Eyes підтвердження
            і пише демо-запис в аудит-лог. Для реальної зміни графіка диспетчеризації використовуйте «Ручна потужність»
            на сторінці Optimization Schedule.
          </p>

          {activeRole === 'Viewer' && (
            <div style={{ background: 'rgba(239, 68, 68, 0.08)', border: '1px solid rgba(239, 68, 68, 0.25)', padding: '12px', borderRadius: '8px', marginBottom: '20px', display: 'flex', gap: '8px', alignItems: 'center' }}>
              <Lock size={16} style={{ color: 'var(--color-rose)' }} />
              <span style={{ fontSize: '0.8rem', color: 'var(--color-rose)' }}><strong>Увага:</strong> Для подачі команд потрібна роль Operator або Manager.</span>
            </div>
          )}

          <div className="form-group">
            <label className="form-label">Потужність демо-команди (кВт)</label>
            <input
              type="number"
              className="form-input"
              value={forcePowerKw}
              onChange={(e) => setForcePowerKw(Number(e.target.value))}
              disabled={activeRole === 'Viewer'}
            />
          </div>
        </div>

        <div style={{ display: 'flex', gap: '12px' }}>
          <button className="btn" style={{ flex: 1 }} disabled={activeRole === 'Viewer'} onClick={() => triggerFourEyesApproval(`[DEMO] FORCE CHARGE ${forcePowerKw} kW (не надсилається на BESS)`)}>
            Демо-заряд (-{forcePowerKw} кВт)
          </button>
          <button className="btn btn-danger" style={{ flex: 1 }} disabled={activeRole === 'Viewer'} onClick={() => triggerFourEyesApproval(`[DEMO] FORCE DISCHARGE ${forcePowerKw} kW (не надсилається на BESS)`)}>
            Демо-розряд (+{forcePowerKw} кВт)
          </button>
        </div>
      </div>
    </div>
  );
}
