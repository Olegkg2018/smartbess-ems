import { useEffect, useState } from 'react';
import { Clock } from 'lucide-react';

function kyivNow(): Date {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Kyiv', hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(new Date());
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '00';
  return new Date(`${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}:${get('second')}`);
}

function kyivWallClock(dateStr: string, hour: number, minute: number): Date {
  return new Date(`${dateStr}T${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:00`);
}

// Дата-only арифметика через Date.UTC — уникає зсуву дня через локальну tz браузера.
function addDaysToDateStr(dateStr: string, delta: number): string {
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + delta)).toISOString().slice(0, 10);
}

function formatRemaining(ms: number): string {
  if (ms <= 0) return 'закрито';
  const totalMin = Math.floor(ms / 60000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return h > 0 ? `${h} год ${m} хв` : `${m} хв`;
}

interface Props {
  targetDate: string; // дата постачання, 'YYYY-MM-DD'
}

export default function BidGateCountdown({ targetDate }: Props) {
  const [now, setNow] = useState(kyivNow());
  useEffect(() => {
    const id = setInterval(() => setNow(kyivNow()), 1000);
    return () => clearInterval(id);
  }, []);

  const rdnGateDay = addDaysToDateStr(targetDate, -1);
  const rdnDeadline = kyivWallClock(rdnGateDay, 12, 0);
  const rdnMsLeft = rdnDeadline.getTime() - now.getTime();

  // Найближча ще не закрита година постачання ВДР на targetDate: ворота
  // закриваються за 60 хв до початку кожної години.
  let nextVdrHour: number | null = null;
  let nextVdrMsLeft = 0;
  for (let h = 0; h < 24; h++) {
    const hourStart = kyivWallClock(targetDate, h, 0);
    const gate = new Date(hourStart.getTime() - 60 * 60 * 1000);
    if (gate.getTime() > now.getTime()) {
      nextVdrHour = h;
      nextVdrMsLeft = gate.getTime() - now.getTime();
      break;
    }
  }
  const vdrOpen = now.getTime() >= kyivWallClock(rdnGateDay, 15, 0).getTime();

  return (
    <div style={{ display: 'flex', gap: '18px', flexWrap: 'wrap', fontSize: '0.8rem', marginBottom: '12px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: rdnMsLeft > 0 ? (rdnMsLeft < 3600000 ? '#d97706' : '#9ca3af') : '#ef4444' }}>
        <Clock size={14} />
        <span>РДН на {targetDate}: {rdnMsLeft > 0 ? `${formatRemaining(rdnMsLeft)} до закриття воріт (12:00 ${rdnGateDay})` : 'ворота закрито'}</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#9ca3af' }}>
        <Clock size={14} />
        <span>
          ВДР: {!vdrOpen ? `відкриється о 15:00 ${rdnGateDay}` : nextVdrHour !== null ? `найближчі ворота (год ${nextVdrHour}) закриються через ${formatRemaining(nextVdrMsLeft)}` : 'всі години сьогодні закрито'}
        </span>
      </div>
    </div>
  );
}
