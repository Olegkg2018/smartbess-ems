import { useEffect, useState } from 'react';
import { useApp } from '../state/AppContext';

export default function GlobalFilterBar() {
  const { targetDate, setTargetDate, selectedModel, setSelectedModel, operationalMode, setOperationalMode, loading, runForecastAndOptimization } = useApp();

  // 2026-09-28 (ревью продуктивності): набір дати з клавіатури стріляє
  // onChange на кожен завершений сегмент (день/місяць/рік по цифрі), і
  // кожна проміжна дата запускала ~10 запитів. Застосовуємо дату через
  // 400мс після останньої зміни (або одразу по Enter/blur).
  const [dateDraft, setDateDraft] = useState(targetDate);
  useEffect(() => { setDateDraft(targetDate); }, [targetDate]);
  useEffect(() => {
    if (dateDraft === targetDate || !/^\d{4}-\d{2}-\d{2}$/.test(dateDraft)) return;
    const t = setTimeout(() => setTargetDate(dateDraft), 400);
    return () => clearTimeout(t);
  }, [dateDraft, targetDate, setTargetDate]);
  const commitDate = () => {
    if (dateDraft !== targetDate && /^\d{4}-\d{2}-\d{2}$/.test(dateDraft)) setTargetDate(dateDraft);
  };

  return (
    <div className="glass-card" style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', alignItems: 'flex-end', padding: '16px 20px', marginBottom: '20px' }}>
      <div className="form-group" style={{ margin: 0, flex: '1 1 160px' }}>
        <label className="form-label">Дата моделювання</label>
        <input type="date" className="form-input" value={dateDraft} onChange={(e) => setDateDraft(e.target.value)}
          onBlur={commitDate} onKeyDown={(e) => { if (e.key === 'Enter') commitDate(); }} />
      </div>
      <div className="form-group" style={{ margin: 0, flex: '1 1 160px' }}>
        <label className="form-label">Нейромережева модель</label>
        <select className="form-select" value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}>
          <option value="lightgbm">LightGBM</option>
          <option value="xgboost">XGBoost</option>
          <option value="mlp">Multilayer Perceptron</option>
        </select>
      </div>
      <div className="form-group" style={{ margin: 0, flex: '1 1 160px' }}>
        <label className="form-label">Режим диспетчеризації</label>
        <select className="form-select" value={operationalMode} onChange={(e) => setOperationalMode(e.target.value)}>
          <option value="arbitrage">Мережевий Арбітраж (DAM)</option>
          <option value="self_consumption">Self-Consumption (Behind-the-Meter)</option>
        </select>
      </div>
      <button className="btn" style={{ height: '38px', flex: '0 0 auto' }} onClick={runForecastAndOptimization} disabled={loading}>
        {loading ? 'Розрахунок...' : 'Розрахувати'}
      </button>
    </div>
  );
}
