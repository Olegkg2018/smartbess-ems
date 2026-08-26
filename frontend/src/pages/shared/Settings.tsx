import { useApp } from '../../state/AppContext';

export default function Settings() {
  const {
    osr, setOsr, voltageClass, setVoltageClass, margin, setMargin,
    capacity, setCapacity, power, setPower, efficiency, setEfficiency,
    maxCyclesPerDay, setMaxCyclesPerDay,
    bidReminderTelegramEnabled, setBidReminderTelegramEnabled,
    autoDispatchEnabled, setAutoDispatchEnabled,
    exciseDutyPct, setExciseDutyPct, transformerLossPct, setTransformerLossPct,
    launchDate, setLaunchDate, saveSettings,
    bessConnectionType, setBessConnectionType, bessTcpHost, setBessTcpHost, bessTcpPort, setBessTcpPort,
    bessSerialPort, setBessSerialPort, bessSerialBaudrate, setBessSerialBaudrate,
    bessSerialParity, setBessSerialParity, bessSerialStopbits, setBessSerialStopbits,
    bessSerialBytesize, setBessSerialBytesize, bessModbusUnitId, setBessModbusUnitId,
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
          <label className="form-label">Макс. циклів заряд розряд на добу</label>
          <input
            type="number" min={0.5} max={5} step={0.1} className="form-input"
            value={maxCyclesPerDay}
            onChange={(e) => setMaxCyclesPerDay(Number(e.target.value))}
          />
          <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '6px 0 0' }}>
            Це стеля можливостей для MILP, а не примус — другий цикл на добу модель використає, лише якщо денний
            спред цін реально його окупить (з урахуванням тарифів, зносу і подвійних втрат КПД). Орієнтир: за
            типового заряду в районі 2000–5000 ₴/МВт·год потрібен спред приблизно у 2–5 разів — чим дешевший
            заряд, тим більший спред потрібен (тарифи й знос — фіксована сума, а не відсоток).
          </p>
        </div>

        <div className="form-group">
          <label className="form-label">Дата початку роботи (Launch Date)</label>
          <input type="date" className="form-input" value={launchDate} onChange={(e) => setLaunchDate(e.target.value)} />
        </div>

        <div className="form-group">
          <label className="form-label" style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={bidReminderTelegramEnabled}
              onChange={(e) => setBidReminderTelegramEnabled(e.target.checked)}
            />
            Telegram-нагадування про дії з заявками РДН/ВДР (щодня о 10:00)
          </label>
        </div>

        <div className="form-group">
          <label className="form-label" style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={autoDispatchEnabled}
              onChange={(e) => setAutoDispatchEnabled(e.target.checked)}
            />
            Автоматична подача заявок (без ручного підтвердження диспетчера)
          </label>
          <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '6px 0 0' }}>
            Вимкнено (за замовчуванням) — система лише готує заявки й нагадує диспетчеру,
            остаточну подачу робить людина вручну. Увімкнено — заявки на завтра подаються
            автоматично щоранку о 06:00, без очікування на диспетчера. Це підстраховка на
            випадок, якщо диспетчер не встигне подати заявки вчасно (батарея інакше або
            простоює, або — гірше — заявки виставляться на нульовий обсяг), а не заміна
            ручного контролю за замовчуванням.
          </p>
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

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '16px' }}>Підключення батареї (SCADA/Modbus)</h3>

        <div className="form-group">
          <label className="form-label">Джерело даних BESS</label>
          <select
            className="form-select"
            value={bessConnectionType}
            onChange={(e) => setBessConnectionType(e.target.value)}
          >
            <option value="simulator">Вбудований симулятор (за замовчуванням, для тестування)</option>
            <option value="tcp">Реальна батарея — Modbus TCP/IP</option>
            <option value="serial">Реальна батарея — Modbus RTU (RS-485/COM-порт)</option>
            <option value="disabled">Вимкнено (без телеметрії й керування)</option>
          </select>
        </div>

        {bessConnectionType === 'tcp' && (
          <>
            <div className="form-group">
              <label className="form-label">IP-адреса батареї</label>
              <input type="text" className="form-input" value={bessTcpHost} onChange={(e) => setBessTcpHost(e.target.value)} placeholder="192.168.1.50" />
            </div>
            <div className="form-group">
              <label className="form-label">TCP-порт</label>
              <input type="number" className="form-input" value={bessTcpPort} onChange={(e) => setBessTcpPort(Number(e.target.value))} />
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '6px 0 0' }}>
                Стандартний Modbus-TCP порт — 502. Уточніть в документації вашого пристрою (напр. Huawei
                LUNA2000/SmartLogger), якщо виробник використовує інший.
              </p>
            </div>
          </>
        )}

        {bessConnectionType === 'serial' && (
          <>
            <div className="form-group">
              <label className="form-label">COM-порт</label>
              <input
                type="text" className="form-input" value={bessSerialPort}
                onChange={(e) => setBessSerialPort(e.target.value)}
                placeholder="COM3 (Windows) або /dev/ttyUSB0 (Linux)"
              />
            </div>
            <div className="form-group">
              <label className="form-label">Швидкість (бод)</label>
              <select className="form-select" value={bessSerialBaudrate} onChange={(e) => setBessSerialBaudrate(Number(e.target.value))}>
                <option value={9600}>9600 (типово для Modbus RTU)</option>
                <option value={19200}>19200</option>
                <option value={38400}>38400</option>
                <option value={57600}>57600</option>
                <option value={115200}>115200</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Парність / стоп-біти / біти даних</label>
              <div style={{ display: 'flex', gap: '8px' }}>
                <select className="form-select" value={bessSerialParity} onChange={(e) => setBessSerialParity(e.target.value)}>
                  <option value="N">Без парності (N)</option>
                  <option value="E">Парна (E)</option>
                  <option value="O">Непарна (O)</option>
                </select>
                <select className="form-select" value={bessSerialStopbits} onChange={(e) => setBessSerialStopbits(Number(e.target.value))}>
                  <option value={1}>1 стоп-біт</option>
                  <option value={2}>2 стоп-біти</option>
                </select>
                <select className="form-select" value={bessSerialBytesize} onChange={(e) => setBessSerialBytesize(Number(e.target.value))}>
                  <option value={8}>8 біт даних</option>
                  <option value={7}>7 біт даних</option>
                </select>
              </div>
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '6px 0 0' }}>
                Типове поєднання — 8-N-1 (8 біт даних, без парності, 1 стоп-біт). Точні значення вказані в
                документації вашого пристрою.
              </p>
            </div>
          </>
        )}

        {(bessConnectionType === 'tcp' || bessConnectionType === 'serial') && (
          <div className="form-group">
            <label className="form-label">Modbus Unit ID (адреса пристрою)</label>
            <input type="number" min={0} max={247} className="form-input" value={bessModbusUnitId} onChange={(e) => setBessModbusUnitId(Number(e.target.value))} />
          </div>
        )}

        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '10px', lineHeight: 1.5 }}>
          Зміни в підключенні батареї застосовуються лише після перезапуску сервера — це не гаряче
          перепідключення живого фізичного з'єднання.
        </p>

        <button className="btn" style={{ width: '100%', marginTop: '10px' }} onClick={saveSettings}>
          Зберегти налаштування
        </button>
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '16px' }}>Незавершені пункти (2026-08-24 ревью)</h3>
        <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '16px', lineHeight: 1.5 }}>
          Нижче — параметри, застосовність яких до цього активу ще НЕ підтверджена (юрист/бухгалтер) і дані,
          доступ до яких ще не отримано. Значення зберігаються тут, щоб не загубити, коли з'являться, але{' '}
          <strong>поки НЕ впливають на жоден розрахунок P&L</strong> — підключення розрахунку до цих чисел
          робиться окремо, після підтвердження.
        </p>

        <div className="form-group">
          <label className="form-label">Акцизний збір (%, ще не підтверджено)</label>
          <input
            type="number" min={0} max={100} step={0.1} className="form-input"
            value={exciseDutyPct}
            onChange={(e) => setExciseDutyPct(Number(e.target.value))}
          />
        </div>

        <div className="form-group">
          <label className="form-label">Втрати трансформаторного обладнання (%, ще не підтверджено)</label>
          <input
            type="number" min={0} max={100} step={0.1} className="form-input"
            value={transformerLossPct}
            onChange={(e) => setTransformerLossPct(Number(e.target.value))}
          />
        </div>

        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '12px', lineHeight: 1.5 }}>
          <strong>Штраф за небаланс (Балансуючий ринок):</strong> реальні погодинні ціни небалансу не мають
          підтвердженого джерела — публічні сторінки Укренерго блокують автоматичний доступ, а знайдений
          сторонній каталог (energy-map.info) для цього конкретного датасету застарілий. Доступ можливий лише
          через особистий кабінет учасника ринку (MMS). Поки реального джерела немає — параметра тут навмисно
          немає (вигадане число порушило б головний принцип проєкту "не вигадувати").
        </p>
      </div>
    </div>
  );
}
