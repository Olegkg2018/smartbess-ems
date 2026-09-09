import { useEffect, useState } from 'react';
import { ComposedChart, CartesianGrid, XAxis, YAxis, Tooltip, Legend, Area, Bar, Line, ReferenceLine, ResponsiveContainer } from 'recharts';
import { AlertTriangle, Radio, Pencil, CalendarClock, History, FileDown, Clock, CheckCircle2, XCircle, ChevronDown, ChevronUp } from 'lucide-react';
import { useApp } from '../../state/AppContext';
import GlobalFilterBar from '../../components/GlobalFilterBar';
import BidGateCountdown from '../../components/BidGateCountdown';
import BidActionCenter from '../../components/BidActionCenter';
import ConfirmModal from '../../components/ConfirmModal';
import * as api from '../../api/client';
import type { DayBidReportHour } from '../../api/client';

// Той самий Kyiv wall-clock підхід, що вже є в BidGateCountdown.tsx —
// не діляться спільним файлом (обидва прості й самодостатні), щоб не
// плодити передчасну абстракцію заради однієї функції.
function kyivNow(): Date {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Kyiv', hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(new Date());
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '00';
  return new Date(`${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}:${get('second')}`);
}

function kyivWallClock(dateStr: string, hour: number): Date {
  return new Date(`${dateStr}T${String(hour).padStart(2, '0')}:00:00`);
}

// Поріг "давно не оновлювалось" для ВДР-оцінки (2026-09-08, розслідування
// -8536 грн для заявки, що не зіграла на РДН) — 2 год з моменту початку
// години. НЕ обов'язково означає збій синку: реальний архів ВДР на
// oree.com.ua сам по собі отримує рядок за "сьогодні" лише пізно ввечері
// (спостережено, ~22:50 Kyiv) — це чесний індикатор "оцінка ще не
// підтверджена", а не звинувачення в поломці.
const IDM_ESTIMATE_STALE_MS = 2 * 60 * 60 * 1000;

export default function OptimizationSchedule() {
  const {
    optimizationResult, manualOverrides, setManualOverrides, dispatchProfile, targetDate, capacity, power, saveOverrides, resetOverridesToOptimal,
    initialSoc, saveInitialSocAndRecalculate, clearInitialSocAndRecalculate, forecastPrices,
    bidMargin, saveBidMarginAndRegenerate, clearBidMarginAndRegenerate, bids, generateBidsNow, settleBidsNow, acknowledgeIdmFallbackNow,
    submitIdmFallbackBidNow,
    activeRole, activeAssetId, addLog,
  } = useApp();

  // 2026-09-09: чи давно минула ця година, а ВДР-фолбек все ще "оцінка"
  // (не звірено реальною ціною) — див. IDM_ESTIMATE_STALE_MS вище.
  const isStaleIdmEstimate = (hour: number) => {
    const hourStart = kyivWallClock(targetDate, hour);
    return kyivNow().getTime() - hourStart.getTime() > IDM_ESTIMATE_STALE_MS;
  };

  // Чернетка скоригованої ціни заявки на ВДР, за годиною (2026-08-28) —
  // порожньо = диспетчер ще не правив, "Подати на ВДР" піде за
  // запропонованою ціною. Той самий "draft + save" патерн, що marginDraft.
  // Використовується і в "Заявка РДН", і тепер у "Ручне коригування заявок"
  // (2026-09-08) — та сама годинна карта чернеток, одна на диспетчера/добу.
  const [idmPriceDraft, setIdmPriceDraft] = useState<Record<number, string>>({});

  // Погодинний звіт прогноз+заявки за поточну добу (ті самі поля, що й
  // Excel-звіт за період, "тільки за добу", 2026-09-08) — для розширеної
  // таблиці "Ручне коригування заявок" нижче. null = ще не завантажено/
  // немає що показати (напр. заявки ще не сформовано).
  const [dayBidReport, setDayBidReport] = useState<DayBidReportHour[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    if (!activeAssetId || !targetDate) { setDayBidReport(null); return; }
    api.fetchDayBidReport(activeRole, activeAssetId, targetDate)
      .then((res) => { if (!cancelled) setDayBidReport(res.hours); })
      .catch(() => { if (!cancelled) setDayBidReport(null); });
    return () => { cancelled = true; };
  }, [activeRole, activeAssetId, targetDate]);
  const dayBidReportByHour = new Map<number, DayBidReportHour>((dayBidReport || []).map((h) => [h.hour, h]));

  // Звіт за добу/період (Excel) — перенесено сюди з Price Forecast
  // (2026-09-08), єдиний експортер тепер обслуговує і одну добу
  // (за замовчуванням — поточна), і довільний діапазон: раніше тут була
  // окрема кнопка "Експорт в Excel" лише за поточну добу
  // (/reports/export-day) — прибрано, той самий .xlsx-формат/поля тепер
  // дає export-forecast-period при start===end.
  const [periodStart, setPeriodStart] = useState('');
  const [periodEnd, setPeriodEnd] = useState('');
  useEffect(() => {
    if (targetDate && !periodEnd) {
      setPeriodStart(targetDate);
      setPeriodEnd(targetDate);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetDate]);
  const [exportingPeriod, setExportingPeriod] = useState(false);
  const handleExportPeriod = async () => {
    if (!periodStart || !periodEnd || !activeAssetId) return;
    setExportingPeriod(true);
    try {
      await api.exportForecastPeriodExcel(activeRole, activeAssetId, periodStart, periodEnd);
      const label = periodStart === periodEnd ? periodStart : `${periodStart} — ${periodEnd}`;
      addLog('EXPORT', `Excel-звіт по прогнозу та заявках за ${label} завантажено.`, 'success');
    } catch (e: any) {
      addLog('API', `Помилка експорту звіту: ${e.message}`, 'error');
    } finally {
      setExportingPeriod(false);
    }
  };

  // forecastPrices === null означає, що для targetDate ще ЖОДНОГО разу не
  // рахували прогноз/MILP (а не що результат "порожній" — MILP може
  // легітимно нічого не робити, якщо арбітраж невигідний). Диспетчер це сплутав із "не рахує" — тому явне повідомлення замість порожнього графіка.
  const neverCalculated = forecastPrices === null;

  const baseSchedule = optimizationResult?.scenarios?.base?.schedule || [];

  // Фолбек лише одразу після "Розрахувати", поки manualOverrides (і
  // похідний dispatchProfile) ще не підвантажились для нової дати.
  //
  // 2026-08-27: знайдено диспетчером — лінія SoC малювалась в ТІЙ САМІЙ
  // точці x=година, що й стовпчик потужності цієї години, хоча `soc`
  // означає "рівень НАПРИКІНЦІ цієї години" — на графіку виглядало так,
  // ніби заряд години 12 (стовпчик при x=12) уже стався ДО години 12
  // (лінія росла від x=11 до x=12), хоча реально він відбувається ПІД ЧАС
  // години 12 (має рости від x=12 до x=13). SoC — рівневий показник із
  // 25 межами на 24 інтервали (00:00...24:00), а не 24 значеннями по
  // одному на інтервал, як потужність/ціна — тому для лінії SoC свідомо
  // 25 точок (0..24): x=N — SoC НА ПОЧАТКУ години N (=кінець години N-1,
  // для x=0 — initialSoc), і додаткова 25-та точка x=24 — SoC наприкінці
  // доби. Стовпчики потужності/ціна лишаються на своїх 24 позиціях
  // (x=0..23) — тепер вони візуально стоять МІЖ двома межевими точками
  // SoC, які й показують результат саме цього стовпчика.
  const chartProfile = baseSchedule.length === 24
    ? [
        ...baseSchedule.map((s: any, i: number) => ({
          hour: `${s.hour}`,
          charge: s.power_kw < 0 ? -s.power_kw : 0,
          discharge: s.power_kw > 0 ? s.power_kw : 0,
          soc: i === 0 ? (initialSoc?.capacity_kwh ?? s.soc_kwh) : baseSchedule[i - 1].soc_kwh,
          price: s.price_forecast_uah_mwh,
        })),
        { hour: '24', soc: baseSchedule[23].soc_kwh },
      ]
    : [];

  const hasProfile = dispatchProfile.length === 24;
  // ManualOverride "заморожує" ціну/потужність на момент збереження
  // (saveOverrides шле весь масив, навіть непроторкнуті години) і сам
  // не оновлюється, якщо прогноз/оптимізацію перерахували пізніше — реальний
  // кейс плутанини, знайдений диспетчером (графік показував застарілу ціну
  // після виправлення прогнозу, поки хтось не натиснув "Скинути").
  const hasOverrides = manualOverrides.some((o: any) => o.is_overridden);
  // dispatchProfile (AppContext) — ЄДИНЕ джерело правди для заряду/розряду/
  // SoC, спільне з Price Forecast: раніше кожна сторінка рахувала це окремо,
  // і при ручному оверрайді, що перевищував реальну ємність батареї, графіки
  // на двох сторінках показували різні стовпчики (реальний баг, знайдений
  // диспетчером).
  // 25 точок (0..24), не 24 — той самий зсув меж SoC, що й у chartProfile
  // вище (коментар там пояснює причину).
  const currentDayProfile = hasProfile
    ? [
        ...dispatchProfile.map((d, i) => ({
          hour: `${d.hour}`,
          charge: d.charge,
          discharge: d.discharge,
          soc: i === 0 ? (initialSoc?.capacity_kwh ?? d.soc) : dispatchProfile[i - 1].soc,
          price: d.price,
        })),
        { hour: '24', soc: dispatchProfile[23].soc },
      ]
    : [];

  const dailyRevenue = dispatchProfile.reduce((s, d) => s + d.revenueUah, 0);
  const dailyCost = dispatchProfile.reduce((s, d) => s + d.costUah, 0);
  // 2026-09-09: за проханням диспетчера картки "Витрати на доставку"/"Знос
  // батареї (Деградація)" прибрано з цієї сторінки повністю — обидві статті
  // не впливають на рішення "по чому купити/продати" для диспетчера, повний
  // фінрезультат (з тарифами/зносом) лишається в Executive Summary/Director
  // Dashboard. "Чистий прибуток" тут — чиста купівля-продаж, без відрахувань.
  const dailyNetProfit = dailyRevenue - dailyCost;

  const [socDraft, setSocDraft] = useState<string>('');
  useEffect(() => {
    if (initialSoc) setSocDraft(String(Math.round(initialSoc.capacity_kwh)));
  }, [initialSoc]);

  const [marginDraft, setMarginDraft] = useState<string>('');
  // 2026-09-08: АБСОЛЮТНИЙ буфер (₴/МВт·год) — альтернатива відсотковому
  // режиму, пріоритетна над ним, якщо обрана. Реальний аналіз показав:
  // buy-заявки (низька денна ціна) потребують у рази більшого % за sell
  // (висока вечірня ціна) для того самого реального захисту в гривнях —
  // абсолютний буфер уникає цієї асиметрії.
  const [marginMode, setMarginMode] = useState<'pct' | 'uah'>('pct');
  const [marginUahDraft, setMarginUahDraft] = useState<string>('');
  useEffect(() => {
    if (bidMargin) {
      setMarginDraft(String(bidMargin.margin_pct));
      if (bidMargin.margin_uah != null) {
        setMarginMode('uah');
        setMarginUahDraft(String(bidMargin.margin_uah));
      } else {
        setMarginMode('pct');
      }
    }
  }, [bidMargin]);

  // Графік і таблиця ручних корективів згорнуті за замовчуванням — на добу
  // з уже сформованими заявками найважливіше на екрані це центр дій і
  // таблиця заявок, а не 24-рядкова таблиця чи графік, які актуальні лише
  // коли диспетчер свідомо хоче їх переглянути чи скоригувати.
  const [chartOpen, setChartOpen] = useState(false);
  const [overridesOpen, setOverridesOpen] = useState(false);
  const [showResetConfirm, setShowResetConfirm] = useState(false);

  const socSourceLabel: Record<string, { text: string; color: string; icon: any }> = {
    manual: { text: 'Ручне значення диспетчера', color: 'var(--color-blue)', icon: Pencil },
    scada_telemetry: { text: 'Реальна SCADA-телеметрія', color: 'var(--color-emerald)', icon: Radio },
    calculated_previous_day: { text: 'Розрахунок з кінця попередньої доби (учорашній MILP-план)', color: '#8b5cf6', icon: History },
    fallback_default: { text: "Фолбек 20% — немає ні телеметрії, ні розрахунку за попередню добу", color: 'var(--color-amber)', icon: AlertTriangle },
  };

  return (
    <div>
      <GlobalFilterBar />

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '4px' }}>SoC батареї на початок доби (00:00 {targetDate})</h3>
        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0 0 12px' }}>
          Визначає, з якого реального рівня заряду MILP планує добу. Пріоритет: ручне значення → SCADA-телеметрія → розрахунок з кінця попередньої доби → фолбек 20% (лише якщо немає нічого з вищого).
        </p>
        {!initialSoc ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Завантаження...</p>
        ) : (
          <>
            {(() => {
              const info = socSourceLabel[initialSoc.source];
              const Icon = info.icon;
              return (
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '14px', fontSize: '0.85rem', color: info.color }}>
                  <Icon size={16} />
                  <span>Джерело: {info.text} — {Math.round(initialSoc.capacity_kwh).toLocaleString()} кВт·год ({initialSoc.capacity_pct.toFixed(1)}%)</span>
                </div>
              );
            })()}
            <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-end', flexWrap: 'wrap' }}>
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label className="form-label">Ємність на 00:00 (кВт·год)</label>
                <input
                  type="number" min={0} max={capacity} step={10} className="form-input" style={{ width: '180px' }}
                  value={socDraft}
                  onChange={(e) => setSocDraft(e.target.value)}
                />
              </div>
              <button className="btn" onClick={() => saveInitialSocAndRecalculate(Number(socDraft))}>
                Зберегти вручну і перерахувати
              </button>
              {initialSoc.has_manual_override && (
                <button className="btn btn-danger" onClick={clearInitialSocAndRecalculate}>
                  Скинути на автоматичне
                </button>
              )}
            </div>
          </>
        )}
      </div>

      <div style={{ marginBottom: '24px' }}>
        <BidActionCenter />
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={{ marginBottom: '4px' }}>Заявка РДН на {targetDate}</h3>
        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0 0 12px' }}>
          Ціна прогнозу — не те, що подається в заявку. Заявка на аукціоні єдиної ціни виконується за реальною ціною OREE,
          якщо наша ціна "прохідна": продаж виконається, якщо наша ціна ≤ факт; купівля — якщо наша ціна ≥ факт. Маржа зсуває
          нашу ціну від прогнозу в бік більшої ймовірності виконання (продаж дешевше, купівля дорожче) — це РУЧНЕ налаштування
          ризику, а не сам прогноз.
        </p>
        <BidGateCountdown targetDate={targetDate} />
        {!bidMargin ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Завантаження...</p>
        ) : (
          <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: '14px' }}>
            <div className="form-group" style={{ marginBottom: 0 }}>
              <label className="form-label">Тип буфера</label>
              <select
                className="form-input" style={{ width: '160px' }}
                value={marginMode}
                onChange={(e) => setMarginMode(e.target.value as 'pct' | 'uah')}
                title="Відсоток — стара поведінка, але систематично упереджений: buy подається на низькій ціні, sell на високій, та сама помилка прогнозу в гривнях — це великий % від низької ціни й малий від високої. Абсолютний буфер (₴/МВт·год) уникає цієї асиметрії — рекомендовано."
              >
                <option value="pct">Відсоток (%)</option>
                <option value="uah">Абсолютний, ₴/МВт·год</option>
              </select>
            </div>
            {marginMode === 'pct' ? (
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label className="form-label">Відсоток буфера безпеки (Safety Buffer %)</label>
                <input
                  type="number" min={0} max={50} step={0.5} className="form-input" style={{ width: '140px' }}
                  value={marginDraft}
                  onChange={(e) => setMarginDraft(e.target.value)}
                />
              </div>
            ) : (
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label className="form-label">Абсолютний буфер, ₴/МВт·год</label>
                <input
                  type="number" min={0} step={50} className="form-input" style={{ width: '160px' }}
                  value={marginUahDraft}
                  onChange={(e) => setMarginUahDraft(e.target.value)}
                  title="Реальний аналіз (2026-09-08): ~2000-2500 ₴/МВт·год дає ~90-95% виконання заявок і для купівлі, і для продажу"
                />
              </div>
            )}
            <button
              className="btn"
              onClick={() => marginMode === 'uah'
                ? saveBidMarginAndRegenerate(Number(marginDraft), Number(marginUahDraft))
                : saveBidMarginAndRegenerate(Number(marginDraft), null)}
            >
              Зберегти буфер і сформувати заявки
            </button>
            {bidMargin.source === 'manual' && (
              <button className="btn btn-danger" onClick={clearBidMarginAndRegenerate}>
                Скинути на дефолт
              </button>
            )}
            <button className="btn" style={{ backgroundColor: 'rgba(59, 130, 246, 0.15)', border: '1px solid var(--color-blue)' }} onClick={generateBidsNow}>
              Сформувати заявки зараз
            </button>
            <button className="btn" style={{ backgroundColor: 'rgba(217, 119, 6, 0.15)', border: '1px solid var(--color-amber)' }} onClick={settleBidsNow}>
              Звірити з фактом OREE
            </button>
          </div>
        )}

        {bids && bids.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Год</th>
                  <th>Тип</th>
                  <th>Обсяг (кВт)</th>
                  <th>Прогноз (грн/МВт·год)</th>
                  <th>Ціна заявки (ручна, з маржею)</th>
                  <th>Факт OREE</th>
                  <th>Статус</th>
                  <th title="Чиста вартість енергії (ціна × обсяг) — без тарифу на доставку і без деградації. Повний фінрезультат (з урахуванням цих витрат) — у 'Ручне коригування заявок' і Executive Summary.">Вартість енергії / ВДР-пропозиція</th>
                </tr>
              </thead>
              <tbody>
                {bids.filter((b) => b.bid_type !== 'standby').map((b) => (
                  <tr key={b.hour} className={b.executed === false ? 'row-alert' : undefined}>
                    <td>{b.hour}</td>
                    <td>{b.bid_type === 'sell' ? 'Продаж' : 'Купівля'}</td>
                    <td>{Math.round(b.volume_kw)}</td>
                    <td>{Math.round(b.forecast_price_uah).toLocaleString()}</td>
                    <td style={{ color: 'var(--color-blue)' }}>
                      {Math.round(b.bid_price_uah).toLocaleString()} (ручна, маржа {b.margin_pct}%)
                      {b.bid_price_legally_clamped && (
                        <span
                          title={`Ціна скоригована до законної межі OREE (${b.oree_bid_price_bounds_uah.min}–${b.oree_bid_price_bounds_uah.max} грн/МВт·год)`}
                          style={{ display: 'inline-flex', verticalAlign: 'middle', marginLeft: '4px' }}
                        >
                          <AlertTriangle size={13} style={{ color: 'var(--color-amber)' }} />
                        </span>
                      )}
                    </td>
                    <td>{b.actual_price_uah != null ? Math.round(b.actual_price_uah).toLocaleString() : '—'}</td>
                    <td>
                      {b.executed === null ? (
                        <span className="status-badge pending"><Clock size={12} /> очікує факту</span>
                      ) : b.executed ? (
                        <span className="status-badge online"><CheckCircle2 size={12} /> виконано</span>
                      ) : (
                        <span className="status-badge offline"><XCircle size={12} /> не виконано</span>
                      )}
                      {b.executed && b.soc_feasible === false && (
                        <span
                          title="Заявка зіграла за ціною на біржі, але фізично неможлива — заряду/місця в акумуляторі не вистачало (послідовна SoC-перевірка). Ця година не враховується у фактичному P&L звіту."
                          style={{ display: 'inline-flex', verticalAlign: 'middle', marginLeft: '4px' }}
                        >
                          <AlertTriangle size={13} style={{ color: 'var(--color-amber)' }} />
                        </span>
                      )}
                    </td>
                    <td>
                      {b.executed && b.soc_feasible === false ? (
                        <span style={{ color: 'var(--color-amber)' }} title="Фізично не доставлено через брак SoC — не зараховано у факт">
                          {Math.round(b.energy_profit_uah ?? 0).toLocaleString()} грн (не зараховано)
                        </span>
                      ) : b.executed ? (
                        <span style={{ color: 'var(--color-emerald)' }}>{Math.round(b.energy_profit_uah ?? 0).toLocaleString()} грн</span>
                      ) : b.idm_fallback_suggested ? (
                        <span style={{ color: 'var(--color-amber)' }}>
                          ВДР ({b.idm_fallback_price_is_actual ? 'факт' : 'оцінка'}) ~{Math.round(b.idm_fallback_price_uah ?? 0).toLocaleString()} грн/МВт·год → {Math.round(b.idm_fallback_energy_profit_uah ?? 0).toLocaleString()} грн
                          {!b.idm_fallback_price_is_actual && isStaleIdmEstimate(b.hour) && (
                            <span
                              title="Реальна ціна ВДР на цю годину ще не опублікована на oree.com.ua (звичайна затримка публікації, не обов'язково збій) — оцінка може відрізнятись від факту."
                              style={{ display: 'inline-flex', verticalAlign: 'middle', marginLeft: '4px' }}
                            >
                              <AlertTriangle size={13} style={{ color: 'var(--color-amber)' }} />
                            </span>
                          )}
                          {b.idm_external_order_id ? (
                            <span style={{ marginLeft: '6px', color: 'var(--color-emerald)' }} title={`Подано на ВДР: ${b.idm_external_order_id}`}>
                              <CheckCircle2 size={12} style={{ verticalAlign: 'middle' }} />
                              {b.idm_bid_price_uah != null
                                ? ` подано за ${Math.round(b.idm_bid_price_uah).toLocaleString()} грн/МВт·год`
                                : ' подано автоматично'}
                            </span>
                          ) : b.idm_fallback_acknowledged ? (
                            <span style={{ marginLeft: '6px', color: 'var(--text-muted)' }} title="Диспетчер підтвердив, що опрацював цю годину вручну">
                              підтверджено вручну
                            </span>
                          ) : (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '5px', marginTop: '4px' }}>
                              <input
                                type="number" step="0.01"
                                className="form-input" style={{ width: '100px', padding: '3px 6px', fontSize: '11px' }}
                                placeholder={String(Math.round((b.idm_fallback_price_uah ?? 0) * 100) / 100)}
                                value={idmPriceDraft[b.hour] ?? ''}
                                onChange={(e) => setIdmPriceDraft({ ...idmPriceDraft, [b.hour]: e.target.value })}
                                title="Скоригувати ціну заявки на ВДР перед подачею — порожньо = подати за запропонованою ціною"
                              />
                              <button
                                className="btn"
                                style={{ padding: '3px 8px', fontSize: '11px', backgroundColor: 'var(--color-blue)' }}
                                title="Подати заявку на ВДР (емуляція) за вказаною, або за запропонованою, якщо поле порожнє"
                                onClick={() => {
                                  const draft = idmPriceDraft[b.hour];
                                  const priceUah = draft && draft.trim() !== '' ? Number(draft) : null;
                                  submitIdmFallbackBidNow(b.hour, priceUah);
                                }}
                              >
                                Подати на ВДР
                              </button>
                              <button
                                className="btn"
                                style={{ padding: '3px 8px', fontSize: '11px', backgroundColor: '#4b5563' }}
                                title="Позначити, що ви самі подали заявку на ВДР (або свідомо вирішили нічого не робити) — автоматична подача цю годину більше не займе"
                                onClick={() => acknowledgeIdmFallbackNow(b.hour)}
                              >
                                Позначити виконаним вручну
                              </button>
                            </div>
                          )}
                        </span>
                      ) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="kpi-container" style={{ marginBottom: '24px' }}>
        <div className="kpi-card">
          <span className="kpi-title">Чистий прибуток за добу (купівля-продаж)</span>
          <span className="kpi-value" style={{ color: dailyNetProfit >= 0 ? 'var(--color-emerald)' : 'var(--color-rose)' }}>{Math.round(dailyNetProfit).toLocaleString()} грн</span>
          <span className="kpi-change neutral" title="Тарифи на доставку і знос батареї сюди не входять — вони не впливають на рішення 'по чому купити/продати', повний фінрезультат з їх урахуванням — в Executive Summary (Director Dashboard).">
            Дохід мінус Витрати (без тарифів/зносу)
          </span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Дохід від розряду (Продаж)</span>
          <span className="kpi-value" style={{ color: 'var(--color-emerald)' }}>{Math.round(dailyRevenue).toLocaleString()} грн</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-title">Витрати заряду (Купівля)</span>
          <span className="kpi-value" style={{ color: 'var(--color-rose)' }}>{Math.round(dailyCost).toLocaleString()} грн</span>
        </div>
      </div>

      <div className="glass-card">
        <div className="card-header-row" style={{ marginBottom: chartOpen ? '4px' : 0 }}>
          <h3 className="card-title" style={{ margin: 0 }}>Потужність заряду/розряду та рівень SoC BESS</h3>
          <button className="btn btn-secondary" style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '4px 10px', fontSize: '0.8rem' }} onClick={() => setChartOpen((v) => !v)}>
            {chartOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />} {chartOpen ? 'Сховати графік' : 'Показати графік'}
          </button>
        </div>
        {!chartOpen ? null : neverCalculated ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '10px', padding: '60px 20px', color: 'var(--text-muted)' }}>
            <CalendarClock size={32} />
            <p style={{ margin: 0, fontSize: '0.9rem', textAlign: 'center' }}>
              Прогноз і графік заряду/розряду ще жодного разу не розраховувались на {targetDate}.<br />
              Натисніть «Розрахувати» вище, щоб отримати їх (не автоматично — щоб не запускати важкі ML/MILP розрахунки на кожну зміну дати).
            </p>
          </div>
        ) : (
          <>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0 0 12px' }}>
              Пунктирні лінії — межі ємності батареї: {Math.round(capacity * 0.9).toLocaleString()} кВт·год (макс. SoC 90%) і {Math.round(capacity * 0.1).toLocaleString()} кВт·год (мін. SoC 10%) з {Math.round(capacity).toLocaleString()} кВт·год загальної ємності.
              Стовпчики показують реально виконану потужність — якщо батарея вже на межі ємності, подальша команда заряду/розряду не виконується і стовпчик зменшується, навіть якщо введена потужність більша.
              Фіолетова лінія — ціна прогнозу (або ручна заявка, якщо годину скориговано вручну), для якої й порахований цей графік.
            </p>
            <div style={{ width: '100%', height: 380 }}>
              <ResponsiveContainer>
                <ComposedChart data={hasProfile ? currentDayProfile : chartProfile}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis dataKey="hour" stroke="var(--text-secondary)" />
                  <YAxis yAxisId="power" stroke="var(--text-secondary)" label={{ value: 'кВт', angle: -90, position: 'insideLeft', fill: 'var(--text-secondary)', fontSize: 11 }} />
                  <YAxis yAxisId="soc" orientation="right" domain={[0, capacity]} stroke="var(--text-secondary)" label={{ value: 'кВт·год', angle: 90, position: 'insideRight', fill: 'var(--text-secondary)', fontSize: 11 }} />
                  <YAxis yAxisId="price" hide domain={['auto', 'auto']} />
                  <Tooltip contentStyle={{ backgroundColor: '#111726', borderColor: '#1f293d' }} />
                  <Legend />
                  <ReferenceLine yAxisId="soc" y={capacity * 0.9} stroke="var(--color-amber)" strokeDasharray="4 4" />
                  <ReferenceLine yAxisId="soc" y={capacity * 0.1} stroke="var(--color-amber)" strokeDasharray="4 4" />
                  <Bar yAxisId="power" dataKey="charge" name="Потужність заряду (кВт)" fill="var(--color-blue)" />
                  <Bar yAxisId="power" dataKey="discharge" name="Потужність розряду (кВт)" fill="var(--color-emerald)" />
                  <Area yAxisId="soc" type="monotone" dataKey="soc" name="Рівень заряду SoC (кВт-год)" stroke="var(--color-amber)" fill="rgba(217, 119, 6, 0.12)" strokeWidth={2} />
                  <Line yAxisId="price" type="monotone" dataKey="price" name="Ціна прогнозу / заявки (грн/МВт-год)" stroke="#a78bfa" strokeWidth={2} dot={{ r: 2 }} connectNulls />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </>
        )}
      </div>

      <div className="glass-card" style={{ marginTop: '24px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
          <div>
            <h3 className="card-title" style={{ margin: 0 }}>Ручне коригування заявок (Manual Dispatch Schedule)</h3>
            <p style={{ margin: '4px 0 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
              Введіть потужність (МВт: розряд +, заряд -) та реальну ціну заявки (грн/МВт-год) для кожної години на {targetDate}.
              Права частина таблиці (прогноз/факт/заявка РДН/ВДР-фолбек/фінансовий результат) — довідково, ті самі поля, що й в Excel-звіті нижче.
            </p>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px' }}>
            <button className="btn" onClick={saveOverrides}>Зберегти ручний графік</button>
            <button className="btn btn-danger" onClick={() => setShowResetConfirm(true)}>
              Скинути до оптимального
            </button>
            <button className="btn btn-secondary" style={{ display: 'flex', alignItems: 'center', gap: '6px' }} onClick={() => setOverridesOpen((v) => !v)}>
              {overridesOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />} {overridesOpen ? 'Сховати таблицю' : 'Показати таблицю'}
            </button>
          </div>
        </div>

        {/* Звіт по прогнозу та заявках (Excel) — перенесено з Price Forecast
            (2026-09-08): один експортер за добу (за замовчуванням поточна)
            або довільний період, замість окремої кнопки "Експорт в Excel"
            лише за поточну добу. */}
        <div style={{
          display: 'flex', gap: '10px', alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: '16px',
          padding: '12px', borderRadius: '6px', background: 'rgba(148, 163, 184, 0.06)', border: '1px solid rgba(148, 163, 184, 0.15)',
        }}>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">Звіт по прогнозу та заявках (Excel), з</label>
            <input type="date" className="form-input" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)} />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">по</label>
            <input type="date" className="form-input" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} />
          </div>
          <button
            className="btn btn-secondary"
            style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
            onClick={handleExportPeriod}
            disabled={exportingPeriod || !periodStart || !periodEnd || !activeAssetId}
            title="Однакові дати 'з'/'по' — звіт лише за одну добу; різні — за весь вказаний період."
          >
            <FileDown size={16} /> {exportingPeriod ? 'Формується...' : 'Завантажити Excel'}
          </button>
        </div>

        {overridesOpen && (
          <>
            {hasOverrides && (
              <div style={{
                display: 'flex', gap: '10px', marginBottom: '16px', padding: '12px', borderRadius: '6px',
                background: 'rgba(217, 119, 6, 0.08)', border: '1px solid rgba(217, 119, 6, 0.3)', fontSize: '13px',
              }}>
                <AlertTriangle size={16} style={{ color: 'var(--color-amber)', flexShrink: 0, marginTop: '1px' }} />
                <span>
                  На цю дату вже збережено ручний графік — він «заморожує» ціну/потужність на момент збереження та НЕ
                  оновлюється сам, навіть якщо прогноз чи оптимізацію перерахували пізніше. Якщо після збереження
                  графіка ви ще раз натискали «Розрахувати» — натисніть «Скинути до оптимального», щоб побачити
                  свіжий розрахунок, інакше графік і чистий прибуток показують застарілі числа.
                </span>
              </div>
            )}

            <div style={{ maxHeight: '520px', overflow: 'auto' }}>
              <table className="audit-table" style={{ width: '100%' }}>
                <thead>
                  <tr>
                    <th>Година</th>
                    <th>Рекомендовано (MILP)</th>
                    <th>Ручна потужність (МВт)</th>
                    <th>Швидкі дії</th>
                    <th>Ціна заявки (грн/МВт-год)</th>
                    <th>Прогноз ціни</th>
                    <th>P10 / P90</th>
                    <th>Факт ціни</th>
                    <th>Різниця Факт-Прогноз</th>
                    <th>Похибка, %</th>
                    <th>Тип заявки РДН</th>
                    <th>Ціна заявки РДН</th>
                    <th>Виконано</th>
                    <th>Плановий прибуток, ₴</th>
                    <th>Витрати на доставку, ₴</th>
                    <th>Деградація, ₴</th>
                    <th>Реалізований прибуток, ₴</th>
                    <th>Загальний дохід, ₴</th>
                    <th>Джерело доходу</th>
                    <th>Купівля/продаж на ВДР</th>
                  </tr>
                </thead>
                <tbody>
                  {manualOverrides.map((o: any, idx: number) => {
                    const sched = baseSchedule[idx];
                    const recPower = sched ? sched.power_kw / 1000.0 : 0.0;
                    const h = dayBidReportByHour.get(idx);
                    const fmt = (v: number | null | undefined) => (v == null ? '—' : Math.round(v).toLocaleString());
                    return (
                      <tr key={idx}>
                        <td>Година {idx + 1} ({String(idx).padStart(2, '0')}:00–{String(idx + 1).padStart(2, '0')}:00)</td>
                        <td style={{ color: recPower > 0 ? 'var(--color-emerald)' : recPower < 0 ? 'var(--color-blue)' : 'var(--text-secondary)' }}>
                          {recPower > 0 ? `Розряд +${recPower.toFixed(2)} МВт` : recPower < 0 ? `Заряд ${recPower.toFixed(2)} МВт` : 'Пауза'}
                        </td>
                        <td>
                          <input
                            type="number" step="0.05" min={-power / 1000.0} max={power / 1000.0}
                            className="form-input" style={{ width: '120px', padding: '4px 8px', fontSize: '13px' }}
                            value={o.power_mw}
                            onChange={(e) => {
                              const raw = Number(e.target.value);
                              // Клип до реальної макс. потужності БЕСС (Asset.power_mw) —
                              // без цього можна було ввести значення, у рази більше за фізичну потужність батареї (реальний баг, знайдений диспетчером).
                              const val = Number.isFinite(raw) ? Math.max(-power / 1000.0, Math.min(power / 1000.0, raw)) : raw;
                              setManualOverrides(manualOverrides.map((it: any, i: number) => (i === idx ? { ...it, power_mw: val } : it)));
                            }}
                          />
                        </td>
                        <td>
                          <div style={{ display: 'flex', gap: '5px' }}>
                            <button className="btn" style={{ padding: '3px 8px', fontSize: '11px', backgroundColor: 'var(--color-blue)' }}
                              onClick={() => setManualOverrides(manualOverrides.map((it: any, i: number) => (i === idx ? { ...it, power_mw: -(power / 1000.0) } : it)))}>
                              Заряд (Max)
                            </button>
                            <button className="btn" style={{ padding: '3px 8px', fontSize: '11px', backgroundColor: 'var(--color-emerald)' }}
                              onClick={() => setManualOverrides(manualOverrides.map((it: any, i: number) => (i === idx ? { ...it, power_mw: power / 1000.0 } : it)))}>
                              Розряд (Max)
                            </button>
                            <button className="btn" style={{ padding: '3px 8px', fontSize: '11px', backgroundColor: '#4b5563' }}
                              onClick={() => setManualOverrides(manualOverrides.map((it: any, i: number) => (i === idx ? { ...it, power_mw: 0.0 } : it)))}>
                              Стоп
                            </button>
                          </div>
                        </td>
                        <td>
                          <input
                            type="number" step="0.01" className="form-input" style={{ width: '140px', padding: '4px 8px', fontSize: '13px' }}
                            value={Math.round(o.price_uah * 100) / 100}
                            onChange={(e) => {
                              const val = Number(e.target.value);
                              setManualOverrides(manualOverrides.map((it: any, i: number) => (i === idx ? { ...it, price_uah: val } : it)));
                            }}
                          />
                        </td>
                        <td>{fmt(h?.forecast_price_uah)}</td>
                        <td style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{fmt(h?.p10_uah)} / {fmt(h?.p90_uah)}</td>
                        <td>{fmt(h?.actual_price_uah)}</td>
                        <td>{h?.diff_uah == null ? '—' : (h.diff_uah > 0 ? '+' : '') + Math.round(h.diff_uah).toLocaleString()}</td>
                        <td>{h?.error_pct == null ? '—' : `${h.error_pct}%`}</td>
                        <td>
                          {h?.bid_type == null ? '—' : h.bid_type === 'sell' ? 'Продаж' : h.bid_type === 'buy' ? 'Купівля' : 'Очікування'}
                        </td>
                        <td>{fmt(h?.bid_price_uah)}</td>
                        <td>
                          {h?.executed == null ? '—' : h.executed ? (
                            <span className="status-badge online"><CheckCircle2 size={12} /> так</span>
                          ) : (
                            <span className="status-badge offline"><XCircle size={12} /> ні</span>
                          )}
                        </td>
                        <td>{fmt(h?.planned_profit_uah)}</td>
                        <td style={{ color: h?.delivery_cost_uah ? 'var(--color-rose)' : undefined }}>{fmt(h?.delivery_cost_uah)}</td>
                        <td style={{ color: h?.degradation_cost_uah ? 'var(--color-rose)' : undefined }}>{fmt(h?.degradation_cost_uah)}</td>
                        <td>{fmt(h?.realized_profit_uah)}</td>
                        <td style={{ fontWeight: 600 }}>{fmt(h?.total_income_uah)}</td>
                        <td style={{ fontSize: '12px' }}>{h?.income_source ?? '—'}</td>
                        <td style={{ minWidth: '220px' }}>
                          {!h?.idm_fallback_suggested ? '—' : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                              <span style={{ fontSize: '11px', color: 'var(--color-amber)' }}>
                                {h.bid_type === 'buy' ? 'Купівля' : 'Продаж'} на ВДР ({h.idm_fallback_price_is_actual ? 'факт' : 'оцінка'}) ~{fmt(h.idm_fallback_price_uah)} ₴/МВт·год
                                {!h.idm_fallback_price_is_actual && isStaleIdmEstimate(idx) && (
                                  <span title="Реальна ціна ВДР на цю годину ще не опублікована на oree.com.ua (звичайна затримка публікації)" style={{ marginLeft: '4px' }}>
                                    <AlertTriangle size={11} style={{ verticalAlign: 'middle', color: 'var(--color-amber)' }} />
                                  </span>
                                )}
                              </span>
                              {h.idm_external_order_id ? (
                                <span style={{ fontSize: '11px', color: 'var(--color-emerald)' }} title={`Подано на ВДР: ${h.idm_external_order_id}`}>
                                  <CheckCircle2 size={12} style={{ verticalAlign: 'middle' }} />
                                  {h.idm_bid_price_uah != null ? ` подано за ${fmt(h.idm_bid_price_uah)}` : ' подано автоматично'}
                                </span>
                              ) : h.idm_fallback_acknowledged ? (
                                <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>підтверджено вручну</span>
                              ) : (
                                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                  <input
                                    type="number" step="0.01"
                                    className="form-input" style={{ width: '90px', padding: '3px 6px', fontSize: '11px' }}
                                    placeholder={String(Math.round((h.idm_fallback_price_uah ?? 0) * 100) / 100)}
                                    value={idmPriceDraft[idx] ?? ''}
                                    onChange={(e) => setIdmPriceDraft({ ...idmPriceDraft, [idx]: e.target.value })}
                                    title="Скоригувати ціну заявки на ВДР перед подачею — порожньо = подати за запропонованою ціною"
                                  />
                                  <button
                                    className="btn" style={{ padding: '2px 6px', fontSize: '10px', backgroundColor: 'var(--color-blue)' }}
                                    title="Подати заявку на ВДР (емуляція)"
                                    onClick={() => {
                                      const draft = idmPriceDraft[idx];
                                      const priceUah = draft && draft.trim() !== '' ? Number(draft) : null;
                                      submitIdmFallbackBidNow(idx, priceUah);
                                    }}
                                  >
                                    Подати
                                  </button>
                                  <button
                                    className="btn" style={{ padding: '2px 6px', fontSize: '10px', backgroundColor: '#4b5563' }}
                                    title="Позначити, що ви самі подали заявку на ВДР (або свідомо вирішили нічого не робити)"
                                    onClick={() => acknowledgeIdmFallbackNow(idx)}
                                  >
                                    Вручну
                                  </button>
                                </div>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      {showResetConfirm && (
        <ConfirmModal
          title="Скинути ручний графік?"
          message="Ви впевнені, що хочете скинути ручний графік до оптимального? Усі ручні корективи на цю добу буде втрачено."
          confirmLabel="Скинути"
          onConfirm={() => { resetOverridesToOptimal(); setShowResetConfirm(false); }}
          onCancel={() => setShowResetConfirm(false)}
        />
      )}
    </div>
  );
}
