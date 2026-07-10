import { BookOpen, CheckCircle2 } from 'lucide-react';
import { ComposedChart, CartesianGrid, XAxis, YAxis, Tooltip, Legend, Line, Area, Bar, Cell, ResponsiveContainer } from 'recharts';
import { useApp } from '../../state/AppContext';
import GlobalFilterBar from '../../components/GlobalFilterBar';

const COLOR_CHARGE_PLANNED = 'rgba(59, 130, 246, 0.35)';
const COLOR_CHARGE_MANUAL = '#3b82f6';
const COLOR_DISCHARGE_PLANNED = 'rgba(5, 150, 105, 0.35)';
const COLOR_DISCHARGE_MANUAL = '#059669';

export default function PriceForecast() {
  const { forecastPrices, priceBand, actualPrices, manualOverrides } = useApp();

  const hasBand = !!priceBand && priceBand.lower_bound_uah.length === (forecastPrices || []).length
    && priceBand.lower_bound_uah.every((v) => v !== null);
  const hasActual = !!actualPrices?.available && actualPrices.actual_prices_uah.length === 24;

  // manualOverrides вже містить ЕФЕКТИВНЕ рішення на кожну годину (ручний
  // оверрайд або, якщо його немає, останній порахований MILP-план для цієї
  // дати — так віддає бекенд) і коректно оновлюється при зміні дати, на
  // відміну від optimizationResult (він існує лише одразу після натискання
  // «Розрахувати» і для НОВОЇ дати ще порожній) — тому графік малюємо саме
  // з manualOverrides, а не з optimizationResult.
  const overridesReady = manualOverrides && manualOverrides.length === 24;
  const hasDispatch = overridesReady;

  // Година 1..24 (як на oree.com.ua і в самому РДН), а не 0:00-23:00.
  const chartData = (forecastPrices || []).map((p, i) => {
    const override = overridesReady ? manualOverrides[i] : null;
    const isManual = !!override?.is_overridden;
    const effectiveKW = override ? override.power_mw * 1000.0 : 0;

    return {
      hour: i + 1,
      price: p,
      actual: hasActual ? actualPrices!.actual_prices_uah[i] : undefined,
      lower: hasBand ? (priceBand!.lower_bound_uah[i] as number) : undefined,
      bandWidth: hasBand ? (priceBand!.upper_bound_uah[i] as number) - (priceBand!.lower_bound_uah[i] as number) : undefined,
      charge: effectiveKW < 0 ? -effectiveKW : 0,
      discharge: effectiveKW > 0 ? effectiveKW : 0,
      isManual,
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

  return (
    <div>
      <GlobalFilterBar />

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '16px' }}>
          Прогноз ціни РДН та графік заряду/розряду (1–24){hasActual && <span style={{ color: '#0891b2', fontSize: '0.75rem', marginLeft: '10px' }}>● Факт з oree.com.ua доступний</span>}
        </h3>
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
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '12px' }}>Ключові спостереження</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {insights.map((exp, idx) => (
            <div key={idx} style={{ display: 'flex', gap: '10px', background: 'rgba(255,255,255,0.02)', padding: '12px', borderRadius: '6px', fontSize: '0.85rem' }}>
              {hasActual && idx === insights.length - 1 ? <CheckCircle2 size={16} style={{ color: '#059669' }} /> : <BookOpen size={16} style={{ color: '#0891b2' }} />}
              <span>{exp}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
