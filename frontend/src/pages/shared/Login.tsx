import { useState } from 'react';
import type { FormEvent } from 'react';
import { Lock } from 'lucide-react';
import { loginWithPassword } from '../../api/client';

interface LoginProps {
  onSuccess: () => void;
}

export default function Login({ onSuccess }: LoginProps) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError(null);
    try {
      await loginWithPassword(password);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не вдалося увійти');
      setBusy(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px' }}>
      <form className="glass-card" onSubmit={submit} style={{ width: '100%', maxWidth: '360px', padding: '28px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <Lock size={20} />
          <h1 style={{ fontSize: '1.15rem', margin: 0 }}>SmartBESS EMS</h1>
        </div>
        <label htmlFor="access-password" style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
          Введіть пароль доступу
        </label>
        <input
          id="access-password"
          type="password"
          autoFocus
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          maxLength={256}
        />
        {error && <div role="alert" style={{ color: 'var(--color-danger, #e5484d)', fontSize: '0.85rem' }}>{error}</div>}
        <button type="submit" disabled={busy || !password}>
          {busy ? 'Перевірка…' : 'Увійти'}
        </button>
      </form>
    </div>
  );
}
