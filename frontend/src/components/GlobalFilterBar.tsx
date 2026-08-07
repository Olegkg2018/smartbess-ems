import { useApp } from '../state/AppContext';

export default function GlobalFilterBar() {
  const { targetDate, setTargetDate, selectedModel, setSelectedModel, operationalMode, setOperationalMode, loading, runForecastAndOptimization } = useApp();

  return (
    <div className="glass-card" style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', alignItems: 'flex-end', padding: '16px 20px', marginBottom: '20px' }}>
      <div className="form-group" style={{ margin: 0, flex: '1 1 160px' }}>
        <label className="form-label">Дата моделювання</label>
        <input type="date" className="form-input" value={targetDate} onChange={(e) => setTargetDate(e.target.value)} />
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
