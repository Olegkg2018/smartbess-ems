import datetime
from typing import Optional
from sqlalchemy.orm import Session

from src.database.models import Asset, BessTelemetry, InitialSocOverride, ChargeDischargePlan
from src.core.time_utils import kyiv_to_utc, utc_to_kyiv


def previous_day_calculated_fraction(db: Session, asset: Asset, target_dt: datetime.datetime) -> Optional[float]:
    """Ємність на кінець попередньої доби за вже порахованим MILP-планом
    (ChargeDischargePlan.expected_soc_mwh останньої години попередньої доби,
    optimized_run_at == та доба). Це розрахункове значення (яким план ЗАДУМАВ
    завершити добу), а не факт реального ручного диспетчингу — але значно
    точніше за сліпий фолбек 0.20, і не залежить від того, чи є SCADA.

    `target_dt` — вже kyiv_to_utc(date_str, 0) (рахує викликач). Попередню
    добу рахуємо через дату-рядок, а не простим `-timedelta(days=1)` — на
    добу переходу DST різниця в UTC між двома північчами не рівно 24г
    (CLAUDE.md п.26/27)."""
    prev_date_str = (utc_to_kyiv(target_dt).date() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    prev_dt = kyiv_to_utc(prev_date_str, 0)
    prev_last = (
        db.query(ChargeDischargePlan)
        .filter(ChargeDischargePlan.asset_id == asset.id, ChargeDischargePlan.optimized_run_at == prev_dt)
        .order_by(ChargeDischargePlan.timestamp.desc())
        .first()
    )
    if prev_last is None:
        return None
    return prev_last.expected_soc_mwh / asset.capacity_mwh


def get_current_soc_fraction(db: Session, asset: Asset, target_date: Optional[str] = None, include_midnight_override: bool = True) -> float:
    """
    SoC (частка ємності 0.0-1.0), що використовується як initial_soc для
    day-ahead оптимізації на target_date.

    Пріоритет джерел ЗАЛЕЖИТЬ від того, чи target_date — СЬОГОДНІ (реальна
    київська дата "зараз") чи МАЙБУТНЯ дата (2026-08-27, знайдено
    користувачем):

    Якщо target_date == сьогодні (частковий mid-day перерахунок ТІЄЇ Ж
    доби, п.46c-e): 1) InitialSocOverride → 2) жива BessTelemetry → 3)
    розрахунковий кінець попередньої доби → 4) фолбек 0.20. Тут телеметрія
    ПРАВИЛЬНО стоїть вище плану — "зараз" буквально Є точкою старту
    горизонту, що рахується.

    Якщо target_date — МАЙБУТНЯ дата (напр. рахуємо завтрашній план
    сьогодні вранці, як щоденна 06:00-джоба): 1) InitialSocOverride → 2)
    розрахунковий кінець ПОПЕРЕДНЬОЇ доби (з УЖЕ порахованого плану на
    сьогодні) → 3) жива телеметрія (лише як фолбек, якщо плану на сьогодні
    ще взагалі нема) → 4) фолбек 0.20.

    Чому порядок 2/3 розвернуто для майбутньої дати: жива телеметрія — це
    знімок "просто ЗАРАЗ", а "зараз" ФІЗИЧНО НЕ Є станом батареї на
    північ ЗАВТРА, якщо між "зараз" і північчю ще лишається частина
    сьогоднішнього плану заряду/розряду (типовий випадок для щоденної
    06:00-джоби — попереду ще майже весь день). Раніше тут завжди бралась
    жива телеметрія — знайдено користувачем: план на завтра, порахований
    вранці, стартував з поточного (ранкового) SoC ~2236 кВт·год, хоча
    сьогоднішній ВЛАСНИЙ план передбачав завершити добу на 400 кВт·год
    (типовий розряд до min_soc наприкінці дня) — дві сусідні доби
    показували несумісні межові значення (кінець сьогодні ≠ початок
    завтра), хоча мали б збігатися за побудовою. Розрахунковий кінець
    попередньої доби — це вже задокументований прогноз системи (те саме
    число, якому графік і заявки вже довіряють, п.46f), а не вигадка —
    просто раніше мав НИЖЧИЙ пріоритет за живу телеметрію, хоча для
    МАЙБУТНЬОЇ дати саме він фізично правильний.

    Раніше тут завжди був захардкоджений 0.20 незалежно від реального стану —
    "щоденний план вважав, що батарея завжди починає добу на 20%", хоча
    попередня доба реально могла завершитись на іншому рівні (типово на
    min_soc, бо MILP форсує розряд до min_soc в кінці кожної доби) — звідси
    "розряд о 1:00", хоча батарея вже порожня з учора.

    `include_midnight_override=False` (2026-08-26) — для ЧАСТКОВОГО
    (mid-day, "від зараз") перерахунку `InitialSocOverride` пропускається:
    він за своєю природою означає "SoC РІВНО на 00:00 цієї доби", а частковий
    перерахунок рахує "SoC ПРЯМО ЗАРАЗ" (вже не опівночі) — якщо override
    все одно спрацює, солвер прийме вже застаріле "опівнічне" число за
    поточний стан, замість живої телеметрії, і знову розійдеться з реальністю
    (той самий клас багу, що й повний перерахунок з опівночі посеред дня,
    виправлений раніше того самого дня).
    """
    target_dt = None
    if target_date and asset.capacity_mwh > 0:
        try:
            # kyiv_to_utc, не наївний strptime (CLAUDE.md п.26/27) — має
            # збігатися з ChargeDischargePlan.optimized_run_at, який тепер
            # теж kyiv_to_utc(date_str, 0).
            target_dt = kyiv_to_utc(target_date, 0)
        except (TypeError, ValueError):
            target_dt = None

    min_frac = asset.min_soc_pct / 100.0
    max_frac = asset.max_soc_pct / 100.0

    if target_dt is not None and include_midnight_override:
        override = (
            db.query(InitialSocOverride)
            .filter(InitialSocOverride.date == target_dt, InitialSocOverride.asset_id == asset.id)
            .first()
        )
        if override is not None:
            fraction = (override.capacity_kwh / 1000.0) / asset.capacity_mwh
            return max(min_frac, min(max_frac, fraction))

    today_kyiv_str = utc_to_kyiv(datetime.datetime.utcnow()).strftime('%Y-%m-%d')
    is_future_date = target_date is not None and target_date > today_kyiv_str

    def _telemetry_fraction():
        tel = (
            db.query(BessTelemetry)
            .filter(BessTelemetry.asset_id == asset.id)
            .order_by(BessTelemetry.timestamp.desc())
            .first()
        )
        if tel is not None and asset.capacity_mwh > 0:
            return max(min_frac, min(max_frac, tel.current_soc_mwh / asset.capacity_mwh))
        return None

    def _plan_end_fraction():
        if target_dt is None or asset.capacity_mwh <= 0:
            return None
        fraction = previous_day_calculated_fraction(db, asset, target_dt)
        if fraction is not None:
            return max(min_frac, min(max_frac, fraction))
        return None

    if is_future_date:
        result = _plan_end_fraction()
        if result is not None:
            return result
        result = _telemetry_fraction()
        if result is not None:
            return result
    else:
        result = _telemetry_fraction()
        if result is not None:
            return result
        result = _plan_end_fraction()
        if result is not None:
            return result

    return 0.20
