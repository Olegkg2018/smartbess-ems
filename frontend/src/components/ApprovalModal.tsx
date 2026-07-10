import { Lock } from 'lucide-react';
import { useApp } from '../state/AppContext';

export default function ApprovalModal() {
  const { showApprovalModal, pendingAction, approvalToken, setApprovalToken, cancelApproval, executeApprovedAction } = useApp();

  if (!showApprovalModal) return null;

  return (
    <div className="modal-overlay">
      <div className="modal-content">
        <div className="modal-header">
          <Lock style={{ color: 'var(--color-amber)' }} />
          <span>Двофакторне підтвердження дії</span>
        </div>

        <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', margin: '12px 0' }}>
          Ви ініціювали команду: <strong>{pendingAction}</strong>.
          Відповідно до регламенту безпеки SmartBESS, ця дія потребує введення секретного криптографічного ключа авторизації менеджера (Four-Eyes Principle).
        </p>

        <div className="form-group">
          <label className="form-label">Секретний ключ підтвердження</label>
          <input
            type="password"
            placeholder="Введіть ключ менеджера"
            className="form-input"
            value={approvalToken}
            onChange={(e) => setApprovalToken(e.target.value)}
          />
        </div>

        <div style={{ display: 'flex', gap: '12px', marginTop: '20px' }}>
          <button className="btn btn-secondary" style={{ flex: 1 }} onClick={cancelApproval}>
            Скасувати
          </button>
          <button className="btn" style={{ flex: 1 }} onClick={executeApprovedAction}>
            Підтвердити команду
          </button>
        </div>
      </div>
    </div>
  );
}
