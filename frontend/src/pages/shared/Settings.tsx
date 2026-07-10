import { useApp } from '../../state/AppContext';

export default function Settings() {
  const {
    osr, setOsr, voltageClass, setVoltageClass, margin, setMargin,
    capacity, setCapacity, power, setPower, efficiency, setEfficiency,
    launchDate, setLaunchDate, saveSettings,
  } = useApp();

  return (
    <div className="grid-2">
      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '16px' }}>Тарифи Обленерго та Постачальника</h3>

        <div className="form-group">
          <label className="form-label">Оператор Системи Розподілу (ОСР)</label>
          <select className="form-select" value={osr} onChange={(e) => setOsr(e.target.value)}>
            <option value="dtek_kiev">ДТЕК Київські електромережі</option>
            <option value="dtek_kiev_regional">ДТЕК Київські регіональні електромережі</option>
            <option value="lviv">Львівобленерго</option>
            <option value="kharkiv">Харківобленерго</option>
          </select>
        </div>

        <div className="form-group">
          <label className="form-label">Клас напруги підключення</label>
          <select className="form-select" value={voltageClass} onChange={(e) => setVoltageClass(Number(e.target.value))}>
            <option value={1}>1 Клас (менше тариф на розподіл)</option>
            <option value={2}>2 Клас (більше тариф на розподіл)</option>
          </select>
        </div>

        <div className="form-group">
          <label className="form-label">Маржа постачальника (грн/МВт-год)</label>
          <input type="number" className="form-input" value={margin} onChange={(e) => setMargin(Number(e.target.value))} />
        </div>
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '16px' }}>Технічні ліміти BESS накопичувача</h3>

        <div className="form-group">
          <label className="form-label">Максимальна ємність батареї (кВт-год)</label>
          <input type="number" className="form-input" value={capacity} onChange={(e) => setCapacity(Number(e.target.value))} />
        </div>

        <div className="form-group">
          <label className="form-label">Максимальна потужність заряду/розряду (кВт)</label>
          <input type="number" className="form-input" value={power} onChange={(e) => setPower(Number(e.target.value))} />
        </div>

        <div className="form-group">
          <label className="form-label">КПД циклу (%)</label>
          <input type="number" className="form-input" value={efficiency} onChange={(e) => setEfficiency(Number(e.target.value))} />
        </div>

        <div className="form-group">
          <label className="form-label">Дата початку роботи (Launch Date)</label>
          <input type="date" className="form-input" value={launchDate} onChange={(e) => setLaunchDate(e.target.value)} />
        </div>

        <button className="btn" style={{ width: '100%', marginTop: '10px' }} onClick={saveSettings}>
          Зберегти налаштування
        </button>

        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '16px', lineHeight: 1.5 }}>
          Ручні "ринкові фактори" (ціна газу, виведення АЕС тощо) прибрано з цього екрану — модель прогнозування
          тепер бере ці дані з реальних джерел автоматично. Поточний стан див. на екрані
          «Стан енергосистеми» (Dispatcher Console).
        </p>
      </div>
    </div>
  );
}
