import { useEffect, useState } from 'react';
import { BookOpen, CheckCircle2, AlertTriangle, CalendarClock, RadioTower } from 'lucide-react';
import { ComposedChart, CartesianGrid, XAxis, YAxis, Tooltip, Legend, Line, Area, Bar, Cell, ResponsiveContainer } from 'recharts';
import { useApp } from '../../state/AppContext';
import GlobalFilterBar from '../../components/GlobalFilterBar';

const COLOR_CHARGE_PLANNED = 'rgba(59, 130, 246, 0.35)';
const COLOR_CHARGE_MANUAL = '#3b82f6';
const COLOR_DISCHARGE_PLANNED = 'rgba(5, 150, 105, 0.35)';
const COLOR_DISCHARGE_MANUAL = '#059669';

export default function PriceForecast() {
  const {
    forecastPrices, priceBand, actualPrices, dispatchProfile,
    generationAdjustment, setGenerationAdjustmentDraft, saveGenerationAdjustmentAndRecalculate,
    gridStress, saveGridStressOverride, clearGridStressOverride,
  } = useApp();

  const [queuesDraft, setQueuesDraft] = useState('');
  const [noteDraft, setNoteDraft] = useState('');
  useEffect(() => {
    setQueuesDraft(gridStress?.manual_forced_restriction_queues != null ? String(gridStress.manual_forced_restriction_queues) : '');
    setNoteDraft(gridStress?.manual_note || '');
  }, [gridStress]);

  const hasBand = !!priceBand && priceBand.lower_bound_uah.length === (forecastPrices || []).length
    && priceBand.lower_bound_uah.every((v) => v !== null);
  const hasActual = !!actualPrices?.available && actualPrices.actual_prices_uah.length === 24;

  // dispatchProfile (AppContext) — ЄДИНЕ джерело правди для заряду/розряду/
  // SoC, спільне з Optimization Schedule: реально виконана потужність з
  // урахуванням меж SoC батареї, а не сира введена команда (інакше на цій
  // сторінці й на Optimization Schedule стовпчики могли відрізнятись, якщо
  // ручний оверрайд перевищував реальну ємність — знайдений диспетчером баг).
  const hasDispatch = dispatchProfile.length === 24;

  // Година 1..24 (як на oree.com.ua і в самому РДН), а не 0:00-23:00.
  const chartData = (forecastPrices || []).map((p, i) => {
    const d = hasDispatch ? dispatchProfile[i] : null;

    return {
      hour: i + 1,
      price: p,
      actual: hasActual ? actualPrices!.actual_prices_uah[i] : undefined,
      lower: hasBand ? (priceBand!.lower_bound_uah[i] as number) : undefined,
      bandWidth: hasBand ? (priceBand!.upper_bound_uah[i] as number) - (priceBand!.lower_bound_uah[i] as number) : undefined,
      charge: d ? d.charge : 0,
      discharge: d ? d.discharge : 0,
      isManual: d ? d.isManual : false,
    };
  });

  const manualHoursCount = chartData.filter((d) => d.isManual).length;

  const insights: string[] = [];
  if (forecastPrices && forecastPrices.length === 24) {
    const maxIdx = forecastPrices.indexOf(Math.max(...forecastPrices));
    const minIdx = forecastPrices.indexOf(Math.min(...forecastPrices));
    insights.push(
      `Максимальна ціна прогнозується у ${maxIdx + 1}-й годині на рівні ${Math.round(forecastPrices[maxIdx]).toLocaleString()} грн/МВт-год.`
    );
    insights.push(
      `Мінімальна ціна прогнозується у ${minIdx + 1}-й годині на рівні ${Math.round(forecastPrices[minIdx]).toLocaleString()} грн/МВт-год — найвигідніше вікно для заряду.`
    );
    if (forecastPrices[minIdx] <= 20.0) {
      insights.push('Очікується енергетичний профіцит (ціна близька до нуля) — ознака надлишку відновлюваної генерації в цю годину.');
    }
    if (hasBand) {
      const avgWidth = chartData.reduce((s, d) => s + (d.bandWidth || 0), 0) / chartData.length;
      insights.push(
        `Довірчий інтервал P10–P90 (conformal-калібрований, ~80% реального покриття за бектестом): в середньому ±${Math.round(avgWidth / 2).toLocaleString()} грн/МВт-год навколо прогнозу.`
      );
    }
    if (hasActual) {
      // WAPE (сума абс. похибок / сума факту), а не MAPE — на годинах профіциту
      // (факт близько 0) MAPE ділить на майже нуль і дає сотні-тисячі
      // відсотків, що вводить в оману. Той самий підхід, що й у бекенді
      // (calculate_mape_wape в ml_pipeline.py).
      const sumAbsErr = chartData.reduce((s, d) => s + Math.abs(d.actual! - d.price), 0);
      const sumAbsActual = chartData.reduce((s, d) => s + Math.abs(d.actual!), 0);
      const wape = sumAbsActual > 0 ? (sumAbsErr / sumAbsActual) * 100 : 0;
      insights.push(
        `Факт РДН з oree.com.ua вже опубліковано (джерело: ${actualPrices!.source}) — розбіжність із прогнозом у середньому ${wape.toFixed(1)}% (WAPE).`
      );
    }
    if (hasDispatch) {
      insights.push(
        manualHoursCount > 0
          ? `Графік диспетчеризації: ${manualHoursCount} з 24 годин скориговано вручну оператором, решта — рекомендація MILP-оптимізатора.`
          : 'Графік диспетчеризації повністю відповідає рекомендації MILP-оптимізатора (ручних коригувань немає).'
      );
    }
  } else {
    insights.push('Натисніть «Розрахувати» вище, щоб отримати прогноз на обрану дату.');
  }

  const hasAdjustment = !!generationAdjustment && (
    generationAdjustment.nuclear_pct !== 100 || generationAdjustment.hydro_pct !== 100 ||
    generationAdjustment.solar_pct !== 100 || generationAdjustment.wind_pct !== 100
  );

  return (
    <div>
      <GlobalFilterBar />

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '16px' }}>
          Прогноз ціни РДН та графік заряду/розряду (1–24){hasActual && <span style={{ color: '#0891b2', fontSize: '0.75rem', marginLeft: '10px' }}>● Факт з oree.com.ua доступний</span>}
        </h3>
        {!forecastPrices ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '10px', padding: '60px 20px', color: 'var(--text-muted)' }}>
            <CalendarClock size={32} />
            <p style={{ margin: 0, fontSize: '0.9rem', textAlign: 'center' }}>
              Прогноз ще не розраховувався на цю дату.<br />
              Натисніть «Розрахувати» вище, щоб отримати його (не автоматично — щоб не запускати важкі ML/MILP розрахунки на кожну зміну дати).
            </p>
          </div>
        ) : (
          <>
            {hasDispatch && (
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0 0 12px' }}>
                Бліді стовпчики — рекомендація MILP-оптимізатора; яскраві — година скоригована вручну диспетчером.
              </p>
            )}
            <div style={{ width: '100%', height: 380 }}>
              <ResponsiveContainer>
                <ComposedChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis dataKey="hour" stroke="#9ca3af" type="category" ticks={[1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 24]} />
                  <YAxis yAxisId="price" stroke="#9ca3af" />
                  <YAxis yAxisId="power" orientation="right" stroke="#9ca3af" label={{ value: 'кВт', angle: 90, position: 'insideRight', fill: '#9ca3af', fontSize: 11 }} />
                  <Tooltip contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }} labelFormatter={(h) => `Година ${h}`} />
                  <Legend />
                  {hasBand && (
                    <Area yAxisId="price" dataKey="lower" stackId="band" stroke="none" fill="transparent" legendType="none" tooltipType="none" />
                  )}
                  {hasBand && (
                    <Area yAxisId="price" dataKey="bandWidth" stackId="band" stroke="none" fill="rgba(59,130,246,0.15)" name="Довірчий інтервал P10–P90" />
                  )}
                  {hasDispatch && (
                    <Bar yAxisId="power" dataKey="charge" name="Заряд (кВт)">
                      {chartData.map((d, idx) => (
                        <Cell key={`charge-${idx}`} fill={d.isManual ? COLOR_CHARGE_MANUAL : COLOR_CHARGE_PLANNED} />
                      ))}
                    </Bar>
                  )}
                  {hasDispatch && (
                    <Bar yAxisId="power" dataKey="discharge" name="Розряд (кВт)">
                      {chartData.map((d, idx) => (
                        <Cell key={`discharge-${idx}`} fill={d.isManual ? COLOR_DISCHARGE_MANUAL : COLOR_DISCHARGE_PLANNED} />
                      ))}
                    </Bar>
                  )}
                  <Line yAxisId="price" type="monotone" dataKey="price" name="Прогноз РДН (грн/МВт-год)" stroke="#3b82f6" strokeWidth={3} dot={{ r: 3 }} activeDot={{ r: 8 }} />
                  {hasActual && (
                    <Line yAxisId="price" type="monotone" dataKey="actual" name="Факт РДН (oree.com.ua)" stroke="#0891b2" strokeWidth={2} strokeDasharray="5 5" dot={{ r: 3 }} connectNulls />
                  )}
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </>
        )}
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '12px' }}>Ключові спостереження</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {hasAdjustment && (
            <div style={{ display: 'flex', gap: '10px', background: 'rgba(245, 158, 11, 0.08)', border: '1px solid rgba(217, 119, 6, 0.3)', padding: '12px', borderRadius: '6px', fontSize: '0.85rem' }}>
              <AlertTriangle size={16} style={{ color: '#d97706', flexShrink: 0 }} />
              <span>
                Прогноз враховує ручну корекцію генерації: АЕС {generationAdjustment!.nuclear_pct}%, ГЕС {generationAdjustment!.hydro_pct}%,
                {' '}СЕС {generationAdjustment!.solar_pct}%, ВЕС {generationAdjustment!.wind_pct}%.
                {generationAdjustment!.note ? ` Нотатка: «${generationAdjustment!.note}».` : ''}
              </span>
            </div>
          )}
          {insights.map((exp, idx) => (
            <div key={idx} style={{ display: 'flex', gap: '10px', background: 'rgba(255,255,255,0.02)', padding: '12px', borderRadius: '6px', fontSize: '0.85rem' }}>
              {hasActual && idx === insights.length - 1 ? <CheckCircle2 size={16} style={{ color: '#059669' }} /> : <BookOpen size={16} style={{ color: '#0891b2' }} />}
              <span>{exp}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '4px' }}>Ручна корекція генерації (АЕС/ГЕС/СЕС/ВЕС)</h3>
        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0 0 16px' }}>
          Реальних даних про генерацію АЕС/ГЕС/СЕС/ВЕС по типах немає (Україна припинила публікацію в ENTSO-E з 2022 року).
          СЕС/ВЕС коригують навчену фізичну ознаку моделі напряму — чесний вплив на прогноз.
          АЕС/ГЕС не мають навченої ознаки — % переводиться в МВт-дельту (довідникові потужності) і додається до
          нетто-експорту — приблизна, але не вигадана оцінка через уже навчену модель, а не довільний коефіцієнт.
        </p>
        {!generationAdjustment ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Завантаження...</p>
        ) : (
          <>
            <div className="grid-3" style={{ gridTemplateColumns: 'repeat(4, 1fr)', marginBottom: '16px' }}>
              {([
                ['nuclear_pct', 'АЕС, % робочих потужностей'],
                ['hydro_pct', 'ГЕС, % робочих потужностей'],
                ['solar_pct', 'СЕС, % очікуваної генерації'],
                ['wind_pct', 'ВЕС, % очікуваної генерації'],
              ] as const).map(([key, label]) => (
                <div className="form-group" key={key} style={{ marginBottom: 0 }}>
                  <label className="form-label">{label}</label>
                  <input
                    type="number" min={0} max={150} step={5} className="form-input"
                    value={generationAdjustment[key]}
                    onChange={(e) => setGenerationAdjustmentDraft({ ...generationAdjustment, [key]: Number(e.target.value) })}
                  />
                </div>
              ))}
            </div>
            <div className="form-group">
              <label className="form-label">Нотатка (причина: ремонт, пошкодження, погода)</label>
              <input
                type="text" className="form-input" placeholder="напр.: плановий ремонт енергоблоку Хмельницької АЕС"
                value={generationAdjustment.note || ''}
                onChange={(e) => setGenerationAdjustmentDraft({ ...generationAdjustment, note: e.target.value })}
              />
            </div>
            <button className="btn" onClick={saveGenerationAdjustmentAndRecalculate}>
              Зберегти і перерахувати прогноз
            </button>
          </>
        )}
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '4px' }}>Обсяг ГПВ (погодинних відключень)</h3>
        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0 0 16px' }}>
          Єдиного структурованого державного API по графіках погодинних відключень немає. Автоматично беремо обсяг
          (у "чергах") з постів Укренерго в Telegram, коли він там опублікований. Якщо на цю дату посту немає —
          можна ввести значення вручну зі свого обленерго. Ця ознака поки НЕ впливає на сьогоднішній прогноз ціни
          (в моделі замало реальної історії для навчання) — введене значення лише накопичується для майбутнього
          перенавчання.
        </p>
        {!gridStress ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Завантаження...</p>
        ) : (
          <>
            {gridStress.source === 'auto_telegram' ? (
              <div style={{ display: 'flex', gap: '10px', background: 'rgba(8, 145, 178, 0.08)', border: '1px solid rgba(8, 145, 178, 0.3)', padding: '12px', borderRadius: '6px', fontSize: '0.85rem', marginBottom: '16px' }}>
                <RadioTower size={16} style={{ color: '#0891b2', flexShrink: 0 }} />
                <span>
                  Автоматично з Telegram-каналу Укренерго: обсяг {gridStress.forced_restriction_queues} черги.
                  {gridStress.auto_consumption_trend != null && (
                    gridStress.auto_consumption_trend > 0 ? ' Споживання зростає.' : gridStress.auto_consumption_trend < 0 ? ' Споживання знижується.' : ' Споживання на рівні норми.'
                  )}
                  {gridStress.auto_settlements_affected != null && ` Знеструмлено населених пунктів: ${gridStress.auto_settlements_affected}${gridStress.auto_oblasts_affected ? ` (у ${gridStress.auto_oblasts_affected} областях)` : ''}.`}
                </span>
              </div>
            ) : (
              <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', margin: '0 0 12px' }}>
                {gridStress.source === 'manual'
                  ? 'Автоматичного посту на цю дату немає — використовується ваша ручна оцінка нижче.'
                  : 'Даних на цю дату немає — ні автоматичних, ні ручних.'}
              </p>
            )}

            {gridStress.source !== 'auto_telegram' && (
              <>
                <div className="grid-3" style={{ gridTemplateColumns: '1fr 2fr', marginBottom: '16px' }}>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label">Обсяг ГПВ, черги (напр. 0.5, 1, 2)</label>
                    <input
                      type="number" min={0} max={6} step={0.5} className="form-input"
                      value={queuesDraft}
                      onChange={(e) => setQueuesDraft(e.target.value)}
                      placeholder="невідомо"
                    />
                  </div>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label className="form-label">Джерело (напр.: за даними обленерго)</label>
                    <input
                      type="text" className="form-input"
                      value={noteDraft}
                      onChange={(e) => setNoteDraft(e.target.value)}
                      placeholder="напр.: за даними ДТЕК Київські РЕМ"
                    />
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '10px' }}>
                  <button
                    className="btn"
                    onClick={() => saveGridStressOverride(queuesDraft === '' ? null : Number(queuesDraft), noteDraft || null)}
                  >
                    Зберегти ручну оцінку
                  </button>
                  {gridStress.has_manual_override && (
                    <button className="btn btn-secondary" onClick={clearGridStressOverride}>
                      Прибрати
                    </button>
                  )}
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
