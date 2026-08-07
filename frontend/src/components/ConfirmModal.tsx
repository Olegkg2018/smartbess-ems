import { AlertTriangle } from 'lucide-react';

interface Props {
  title: string;
  message: string;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmModal({ title, message, confirmLabel = 'Підтвердити', onConfirm, onCancel }: Props) {
  return (
    <div className="modal-overlay">
      <div className="modal-content">
        <div className="modal-header">
          <AlertTriangle style={{ color: 'var(--color-rose)' }} />
          <span>{title}</span>
        </div>

        <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', margin: '12px 0' }}>{message}</p>

        <div style={{ display: 'flex', gap: '12px', marginTop: '20px' }}>
          <button className="btn btn-secondary" style={{ flex: 1 }} onClick={onCancel}>
            Скасувати
          </button>
          <button className="btn btn-danger" style={{ flex: 1 }} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
