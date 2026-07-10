import { Lock } from 'lucide-react';
import { useApp } from '../../state/AppContext';

export default function AssetDetail() {
  const { activeRole, capacity, power, triggerFourEyesApproval } = useApp();

  return (
    <div className="grid-2">
      <div className="glass-card">
        <h3 className="card-title">Телеметрія BESS в реальному часі (Live Modbus)</h3>

        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', margin: '30px 0' }}>
          <div style={{
            width: '180px', height: '180px', borderRadius: '50%',
            border: '8px solid rgba(5, 150, 105, 0.15)', borderTopColor: '#059669',
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', position: 'relative',
          }}>
            <span style={{ fontSize: '0.8rem', color: '#9ca3af', fontWeight: 600 }}>Battery SoC</span>
            <span style={{ fontSize: '2.5rem', fontWeight: 700, color: '#059669' }}>20.0 %</span>
            <span style={{ fontSize: '0.75rem', color: '#6b7280' }}>{Math.round(capacity * 0.2)} кВт-год / {capacity} кВт-год</span>
          </div>
        </div>

        <table className="data-table">
          <tbody>
            <tr><td>Номінальна ємність</td><td style={{ textAlign: 'right', fontWeight: 600 }}>{capacity} кВт-год</td></tr>
            <tr><td>Макс. потужність</td><td style={{ textAlign: 'right', fontWeight: 600 }}>{power} кВт</td></tr>
            <tr><td>Поточна активна потужність</td><td style={{ textAlign: 'right', fontWeight: 600, color: '#3b82f6' }}>-150.0 кВт (Заряд)</td></tr>
            <tr><td>Температура осередків</td><td style={{ textAlign: 'right', fontWeight: 600, color: '#d97706' }}>24.8 °C (Норма)</td></tr>
            <tr><td>Технічний стан (SOH)</td><td style={{ textAlign: 'right', fontWeight: 600, color: '#059669' }}>99.85 %</td></tr>
          </tbody>
        </table>
      </div>

      <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
        <div>
          <h3 className="card-title">Ручний байпас EMS (Manual SCADA Dispatch Overrides)</h3>
          <p style={{ fontSize: '0.8rem', color: '#9ca3af', margin: '8px 0 20px 0' }}>
            Ця панель дозволяє оператору примусово змінити автоматичний графік та подати миттєву команду заряду чи розряду на контроллер BESS.
          </p>

          {activeRole === 'Viewer' && (
            <div style={{ background: 'rgba(239, 68, 68, 0.08)', border: '1px solid rgba(239, 68, 68, 0.25)', padding: '12px', borderRadius: '8px', marginBottom: '20px', display: 'flex', gap: '8px', alignItems: 'center' }}>
              <Lock size={16} style={{ color: '#ef4444' }} />
              <span style={{ fontSize: '0.8rem', color: '#ef4444' }}><strong>Увага:</strong> Для подачі команд потрібна роль Operator або Manager.</span>
            </div>
          )}

          <div className="form-group">
            <label className="form-label">Примусова потужність команди (кВт)</label>
            <input type="number" className="form-input" defaultValue={150} disabled={activeRole === 'Viewer'} />
          </div>
        </div>

        <div style={{ display: 'flex', gap: '12px' }}>
          <button className="btn" style={{ flex: 1 }} disabled={activeRole === 'Viewer'} onClick={() => triggerFourEyesApproval('FORCE CHARGE 150 kW (Modbus override)')}>
            Примусовий Заряд (-150 кВт)
          </button>
          <button className="btn btn-danger" style={{ flex: 1 }} disabled={activeRole === 'Viewer'} onClick={() => triggerFourEyesApproval('FORCE DISCHARGE 150 kW (Modbus override)')}>
            Примусовий Розряд (+150 кВт)
          </button>
        </div>
      </div>
    </div>
  );
}
