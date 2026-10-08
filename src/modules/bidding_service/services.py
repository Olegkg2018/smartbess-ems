"""
Реальна механіка подачі заявок на РДН (аукціон єдиної ціни) — див.
докстрінг MarketBid у database/models.py для повного пояснення правил
виконання. Диспетчер РУЧНО вносить заявку в кабінет oree.com.ua на основі
того, що показує цей модуль — реального API OREE немає (MEMORY.md §8).
`submit_bids_for_date` (2026-08-26, "віртуальний диспетчер") емулює цей
крок через `oree_client.MockOreeClient`, щоб решта автоматизованого циклу
(звірка, аудит) могла будуватись і тестуватись вже зараз.
"""
import os
import datetime
import pandas as pd

from src.database.models import ChargeDischargePlan, PriceForecast, MarketBid, BidMarginOverride, MarketBidSocFeasibility
from src.modules.optimization_service.milp_model import evaluate_schedule_profit
from src.modules.scada_service.soc_state import get_current_soc_fraction
from src.modules.bidding_service.oree_client import get_oree_client
import src.modules.forecast_service.ml_pipeline as mt
from src.core.time_utils import kyiv_day_bounds, utc_to_kyiv, kyiv_to_utc

DEFAULT_MARGIN_PCT = 2.0

# Легальні межі ціни заявки на OREE (Правила ринку РДН/ВДР, НКРЕКП №308,
# ред. №1169/24.06.2019 зі змінами №832/02.05.2023) — 10.00-50000.00 грн/МВт·год.
# Це ІНША межа, ніж PRICE_FLOOR/PRICE_CAP=16000.0 у ml_pipeline.py/milp_model.py —
# ті клипають ПРОГНОЗ ціни, а не саму ціну заявки, що піде в кабінет oree.com.ua.
# Джерело: MEMORY.md §8.
# 2026-09-29 (CLAUDE.md п.61): межі 10..50000 з правил 2019 не відповідали
# реальному ринку — з серпня 2025 фактичний максимум РДН/ВДР 15000. Тепер
# межі — одне редаговане налаштування (src/core/market_bounds.py), спільне
# для заявок, прогнозу й сценаріїв MILP. Константи лишились як дефолти.
from src.core.market_bounds import (
    get_market_price_bounds, DEFAULT_PRICE_FLOOR_UAH as OREE_BID_PRICE_MIN_UAH,
    DEFAULT_PRICE_CAP_UAH as OREE_BID_PRICE_MAX_UAH,
)


def _is_at_bound(price):
    floor, cap = get_market_price_bounds()
    return price <= floor + 1e-6 or price >= cap - 1e-6


def compute_bid_price(forecast_price, bid_type, margin_pct, margin_uah=None):
    """Ціна заявки до клампу меж: прогноз ± буфер безпеки. Повертає
    (bid_price_raw, applied_margin_pct). Абсолютний буфер (margin_uah, ₴) має
    пріоритет над відсотковим. Відсотковий рахується від МОДУЛЯ прогнозу
    (2026-09-29): при від'ємній ціні (після 1.05.2027) `forecast × (1 + m)`
    для купівлі дало б ціну НИЖЧЕ прогнозу — протилежне суті буфера; для
    додатних цін результат ідентичний попередній формулі."""
    if margin_uah is not None:
        buffer = margin_uah
        # Еквівалентний % — лише для читабельності старих звітів/UI.
        applied_margin_pct = (margin_uah / abs(forecast_price) * 100.0) if forecast_price else 0.0
    else:
        buffer = abs(forecast_price) * margin_pct / 100.0
        applied_margin_pct = margin_pct
    if bid_type == 'sell':
        return forecast_price - buffer, applied_margin_pct
    if bid_type == 'buy':
        return forecast_price + buffer, applied_margin_pct
    return forecast_price, applied_margin_pct


def clamp_bid_price_to_oree_bounds(raw_price_uah: float) -> tuple:
    """Обмежує ціну заявки ринковими межами (Settings). Повертає (clamped_price, was_clamped)."""
    floor, cap = get_market_price_bounds()
    clamped = min(max(raw_price_uah, floor), cap)
    return clamped, clamped != raw_price_uah

# 2026-09-09: раніше СТАТИЧНА константа (стара сума 528.57+1500.0+
# 104.57+100.0=2233.14 ₴/МВт·год) — винесено в редаговане Settings-поле
# (`delivery_tariff_uah_per_mwh`, `optimization.py`). Того ж дня дефолт
# змінено на 0.0 за прямим рішенням користувача: реальний спосіб
# розрахунку тарифу на доставку — на нетто-споживанні (куплено-продано),
# не на повному обсязі купівлі, а точної формули/цифр поки нема — чесніше
# не враховувати цю статтю зовсім, ніж застосовувати вигадане наближення
# до всього обсягу купівлі. Диспетчер може ввести реальне число з
# рахунку/договору в будь-який момент через Settings.
DEFAULT_DELIVERY_TARIFF_UAH_PER_MWH = 0.0
_TARIFF_SETTINGS_CACHE = {}


def get_delivery_tariff_uah_per_mwh() -> float:
    """Читає тариф на доставку (₴/МВт·год) з system_settings.json — той
    самий файловий read-патерн, що вже є для `auto_dispatch_enabled`
    (scheduler.py) / `bid_reminder_telegram_enabled` (telegram_bot.py).
    Дефолт 0.0, якщо ще не налаштовано вручну (2026-09-09, рішення
    користувача — див. коментар вище)."""
    import json
    import os
    from src.core.config import settings
    path = os.path.join(settings.DATA_DIR, "system_settings.json")
    if os.path.exists(path):
        try:
            # Кеш за mtime (2026-09-28): функцію кличуть на кожну заявку/
            # годину звіту — до тисяч разів за експорт періоду. Зміна тарифу
            # в Settings змінює mtime, тож підхоплюється одразу, як і раніше.
            mtime = os.path.getmtime(path)
            if _TARIFF_SETTINGS_CACHE.get('mtime') != mtime:
                with open(path, "r") as f:
                    val = json.load(f).get("delivery_tariff_uah_per_mwh")
                _TARIFF_SETTINGS_CACHE.update(mtime=mtime, val=val)
            val = _TARIFF_SETTINGS_CACHE['val']
            if val is not None:
                return float(val)
        except Exception:
            pass
    return DEFAULT_DELIVERY_TARIFF_UAH_PER_MWH


def get_tariff_kwargs() -> dict:
    """Динамічний замінник колишньої статичної `TARIFF_KWARGS` — той самий
    склад параметрів для `evaluate_schedule_profit` (transmission/
    distribution/dispatch/supplier_margin), але формула там завжди
    використовує лише їхню СУМУ (`total_tariffs_kwh`), тому все редаговане
    число кладеться в `transmission_tariff`, решта — 0 (сигнатура
    evaluate_schedule_profit не змінюється)."""
    return dict(
        transmission_tariff=get_delivery_tariff_uah_per_mwh(),
        distribution_tariff=0.0, dispatch_tariff=0.0, supplier_margin=0.0,
        mode='arbitrage',
    )


BID_PRICE_MODES = ('band', 'breakeven', 'margin')
DEFAULT_BID_PRICE_MODE = 'band'


def get_bid_price_mode() -> str:
    """Режим ціни заявки з system_settings.json:
    - 'band' (дефолт з 2026-10-08) — купівля за P90, продаж за P10 прогнозу,
      але не гірше точки беззбитковості;
    - 'breakeven' — одразу беззбиткова межа;
    - 'margin' — прогноз ± буфер безпеки."""
    import json
    from src.core.config import settings
    try:
        with open(os.path.join(settings.DATA_DIR, "system_settings.json")) as f:
            mode = json.load(f).get("bid_price_mode")
    except Exception:
        mode = None
    return mode if mode in BID_PRICE_MODES else DEFAULT_BID_PRICE_MODE


def compute_breakeven_limits(day_bids, efficiency, deg_cost_uah_per_mwh, tariff_uah_per_mwh=0.0):
    """Граничні (беззбиткові) ціни заявок доби. day_bids — [(bid_type,
    volume_kw, forecast_price)]. Повертає (buy_max, sell_min); None, якщо на
    добі нема протилежної сторони.

    На РДН аукціон єдиної ціни: виконана заявка розраховується за ціною
    ринку, а не за своєю, тож ціна заявки визначає лише, ЧИ виконається
    угода. Тому заявка подається за ціною, до якої угода ще окупається:
    - купівля 1 МВт·год → η МВт·год продажу за середньою очікуваною ціною
      продажу S мінус знос: buy_max = η·(S − знос) − тариф;
    - продаж 1 МВт·год коштує 1/η МВт·год купівлі за середньою ціною B плюс
      знос: sell_min = (B + тариф)/η + знос; без купівлі цієї доби (енергія
      вже в батареї — витрачені кошти не повертаються) sell_min = знос.
    Аналіз 587 звірених заявок (scratch/analysis_bid_margin_sim.py,
    08–10.2026): буфер 2% давав 72%/61% виконання buy/sell, беззбиткова
    ціна — ~100% і +70% прибутку."""
    def wavg(kind):
        rows = [(v, p) for t, v, p in day_bids if t == kind and v > 0]
        vol = sum(v for v, _ in rows)
        return sum(v * p for v, p in rows) / vol if vol > 0 else None

    s_avg, b_avg = wavg('sell'), wavg('buy')
    buy_max = efficiency * (s_avg - deg_cost_uah_per_mwh) - tariff_uah_per_mwh if s_avg is not None else None
    sell_min = (b_avg + tariff_uah_per_mwh) / efficiency + deg_cost_uah_per_mwh if b_avg is not None else deg_cost_uah_per_mwh
    return buy_max, sell_min


def compute_band_bid_price(bid_type, lower_uah, upper_uah, buy_max=None, sell_min=None):
    """Ціна заявки в режимі 'band': купівля за P90 (верхня межа прогнозу),
    продаж за P10 (нижня). Факт виходить за межу лише в ~10% годин, тож
    заявка проходить ~9 разів з 10, а ціна лишається прив'язаною до
    прогнозу, а не до середньої ціни доби, як у беззбитковому режимі
    (там купівля подавалась аж до ~9000 при прогнозі ~300). Додатково не
    гірше точки беззбитковості: buy ≤ buy_max, sell ≥ sell_min.
    None — немає P10/P90 на цю годину (тоді діє буфер).

    Симуляція на 36 добах проду з P10/P90 (scratch/analysis_bid_band_sim.py,
    09–10.2026): буфер 2% — 771 тис., P90/P10 — 1107 тис. (виконання 96%/93%,
    0 збиткових діб), беззбиткова — 1166 тис."""
    if bid_type == 'buy' and upper_uah is not None:
        return min(upper_uah, buy_max) if buy_max is not None else upper_uah
    if bid_type == 'sell' and lower_uah is not None:
        return max(lower_uah, sell_min) if sell_min is not None else lower_uah
    return None


def get_margin_pct(db, asset_id: str, target_date: datetime.datetime) -> float:
    override = db.query(BidMarginOverride).filter(
        BidMarginOverride.asset_id == asset_id, BidMarginOverride.date == target_date,
    ).first()
    return override.margin_pct if override else DEFAULT_MARGIN_PCT


def get_margin_uah(db, asset_id: str, target_date: datetime.datetime):
    """Абсолютний буфер (₴/МВт·год) на цю добу, якщо диспетчер його зберіг —
    None, якщо ще не налаштовано (тоді generate_bids_for_date лишається на
    відсотковому режимі, стара поведінка без змін)."""
    override = db.query(BidMarginOverride).filter(
        BidMarginOverride.asset_id == asset_id, BidMarginOverride.date == target_date,
    ).first()
    return override.margin_uah if override else None


def generate_bids_for_date(db, asset, target_date: datetime.datetime, margin_pct: float = None, margin_uah: float = None, force_full_day: bool = False) -> dict:
    """
    Будує заявки РДН на target_date з уже порахованого MILP-графіка
    (ChargeDischargePlan) + прогнозної ціни (PriceForecast) на ту саму добу,
    зсунутих на буфер безпеки — або відсотковий (margin_pct,
    bid_margin_overrides, інакше DEFAULT_MARGIN_PCT), або, якщо збережено,
    АБСОЛЮТНИЙ у ₴/МВт·год (margin_uah — має пріоритет, 2026-09-08, див.
    докстрінг BidMarginOverride). Не запускає прогноз/оптимізацію заново —
    вимагає, щоб вони вже були пораховані (як і /optimization/plans).

    force_full_day=True — свідомий вихід із заморозки минулих годин (як і
    в run_optimization_background_job) — перезаписує заявки на ВСІ 24
    години, включно з уже минулими. За замовчуванням False.
    """
    if margin_pct is None:
        margin_pct = get_margin_pct(db, asset.id, target_date)
    if margin_uah is None:
        margin_uah = get_margin_uah(db, asset.id, target_date)

    plans = db.query(ChargeDischargePlan).filter(
        ChargeDischargePlan.asset_id == asset.id,
        ChargeDischargePlan.optimized_run_at == target_date,
    ).order_by(ChargeDischargePlan.timestamp).all()
    if not plans:
        return {'status': 'no_plan', 'message': f'Немає порахованого MILP-графіка на {target_date.date()} — спочатку запустіть прогноз/оптимізацію.'}

    forecasts = db.query(PriceForecast).filter(
        PriceForecast.forecast_run_at == target_date,
    ).order_by(PriceForecast.timestamp).all()
    # Ключуємо за реальною київською годиною (не сирою UTC .hour) — коректно
    # й на добу переходу DST (CLAUDE.md п.26/27), а не лише "випадково
    # правильно" через ідентичну логіку генерації по обидва боки.
    forecast_by_hour = {utc_to_kyiv(f.timestamp).hour: f.predicted_price_uah for f in forecasts}
    band_by_hour = {utc_to_kyiv(f.timestamp).hour: (f.lower_bound_uah, f.upper_bound_uah) for f in forecasts}
    if len(forecast_by_hour) != 24:
        return {'status': 'no_forecast', 'message': f'Немає повного прогнозу цін на {target_date.date()} (є {len(forecast_by_hour)}/24 годин).'}

    # 2026-08-26: не переписуємо заявку на вже минулу годину повторним
    # запуском (той самий принцип, що й ChargeDischargePlan у
    # run_optimization_background_job — знайдено тим самим інцидентом:
    # диспетчер кілька разів натиснув "Розрахувати" за ранок, і кожен
    # раз стирав уже подану/звірену заявку на години, що вже минули,
    # включно з фактом подачі (external_order_id) і звірки (executed/
    # actual_price_uah) — реальна історія зникала без сліду). Майбутні
    # години, як і раніше, перераховуються завжди. Межа — КІНЕЦЬ години
    # (не початок) — та сама узгоджена межа, що й у
    # run_optimization_background_job, інакше знову розійдуться.
    now_utc = datetime.datetime.utcnow()

    def plan_side(p):
        if p.target_power_mw > 0.001:
            return 'sell', p.target_power_mw * 1000.0
        if p.target_power_mw < -0.001:
            return 'buy', -p.target_power_mw * 1000.0
        return 'standby', 0.0

    bid_price_mode = get_bid_price_mode()
    buy_max = sell_min = None
    if bid_price_mode in ('breakeven', 'band'):
        # Межі рахуються по ВСІЙ добі (і вже минулих годинах) — це ціна
        # угоди, а не те, що ще можна перезаписати.
        day_bids = []
        for p in plans:
            side, vol = plan_side(p)
            fp = forecast_by_hour.get(utc_to_kyiv(p.timestamp).hour)
            if fp is not None:
                day_bids.append((side, vol, fp))
        buy_max, sell_min = compute_breakeven_limits(
            day_bids, asset.efficiency_charge * asset.efficiency_discharge,
            asset.deg_cost_per_mwh, get_delivery_tariff_uah_per_mwh(),
        )

    bids = []
    for p in plans:
        if not force_full_day and p.timestamp + datetime.timedelta(hours=1) <= now_utc:
            continue
        hour = utc_to_kyiv(p.timestamp).hour
        forecast_price = forecast_by_hour.get(hour)
        if forecast_price is None:
            continue

        bid_type, volume_kw = plan_side(p)

        # Буфер безпеки — АБСОЛЮТНИЙ (₴/МВт·год), якщо налаштовано, інакше
        # відсотковий (стара поведінка). Реальний аналіз 323 звірених заявок
        # (2026-09-08) показав: відсотковий буфер структурно упереджений —
        # buy подається на низьких (денний профіцит) цінах, sell — на
        # високих (вечірній пік), тож та сама помилка прогнозу в гривнях —
        # величезний % від низької ціни й малий % від високої. В абсолютних
        # гривнях buy/sell вимагають майже однакової суми — тому єдиний
        # margin_uah ефективніший для обох напрямків одночасно.
        if bid_price_mode == 'band':
            limit = compute_band_bid_price(bid_type, *band_by_hour.get(hour, (None, None)),
                                           buy_max=buy_max, sell_min=sell_min)
        else:
            limit = buy_max if bid_type == 'buy' else sell_min if bid_type == 'sell' else None
        if limit is not None:
            bid_price_raw = limit
            row_margin_uah = abs(limit - forecast_price)
            applied_margin_pct = row_margin_uah / abs(forecast_price) * 100.0 if forecast_price else 0.0
            row_mode = bid_price_mode
        else:
            bid_price_raw, applied_margin_pct = compute_bid_price(forecast_price, bid_type, margin_pct, margin_uah)
            row_margin_uah = margin_uah
            row_mode = 'margin'
        bid_price, _ = clamp_bid_price_to_oree_bounds(bid_price_raw)

        row = db.query(MarketBid).filter(
            MarketBid.asset_id == asset.id, MarketBid.timestamp == p.timestamp,
        ).first()
        if not row:
            row = MarketBid(asset_id=asset.id, timestamp=p.timestamp)
            db.add(row)
        row.bid_type = bid_type
        row.volume_kw = volume_kw
        row.forecast_price_uah = forecast_price
        row.margin_pct = applied_margin_pct
        row.margin_uah = row_margin_uah
        row.bid_price_mode = row_mode
        row.bid_price_uah = bid_price
        # Lineage (CODE_REVIEW.md п.7-20) — той самий ForecastRun, що дав
        # forecast_price вище (p — той самий ChargeDischargePlan рядок).
        row.forecast_run_id = p.forecast_run_id
        # Нова заявка — попередній стан розрахунку (якщо доба вже колись
        # заселювалась) більше не дійсний, доки не прийде нова факт-ціна.
        row.actual_price_uah = None
        row.executed = None
        row.realized_profit_uah = None
        row.idm_fallback_suggested = False
        row.idm_fallback_price_uah = None
        row.idm_fallback_profit_uah = None
        row.idm_fallback_price_is_actual = None
        row.idm_bid_price_uah = None
        row.settled_at = None
        # Заявка перерахована — стара емульована подача (якщо була) більше
        # не відповідає новим цифрам, submit_bids_for_date подасть заново.
        row.external_order_id = None
        row.oree_submission_status = None
        row.submitted_at = None
        bids.append(row)

    db.commit()
    bid_dicts = [_bid_to_dict(b) for b in bids]
    return {
        'status': 'ok',
        # 2026-08-27: utc_to_kyiv(...), а НЕ сирий target_date.date() —
        # target_date наївний UTC (kyiv_to_utc(date_str,0), київська
        # північ), його власна календарна UTC-дата на день РАНІШЕ за
        # реальну київську (EEST/EET зсув завжди зсуває північ на
        # попередній UTC-день). Знайдено користувачем: `POST /bids/settle`
        # на 2026-08-28 у відповіді повертав "date":"2026-08-27" — сама
        # звірка й записи в БД були коректні (timestamp завжди правильний),
        # хибним було лише це поле-відлуння у відповіді. Той самий фікс у
        # всіх функціях цього файлу, що повертають 'date'.
        'date': utc_to_kyiv(target_date).date().isoformat(),
        'margin_pct': margin_pct,
        'margin_uah': margin_uah,
        'bid_price_mode': bid_price_mode,
        'breakeven_buy_max_uah': buy_max,
        'breakeven_sell_min_uah': sell_min,
        'n_bids': len(bids),
        'n_price_clamped': sum(1 for d in bid_dicts if d['bid_price_legally_clamped']),
        'bids': bid_dicts,
    }


def submit_bids_for_date(db, asset, target_date: datetime.datetime) -> dict:
    """
    Емулює подачу вже згенерованих заявок (`generate_bids_for_date`) через
    `oree_client.get_oree_client()` (MockOreeClient за замовчуванням — див.
    докстрінг oree_client.py, немає реального API OREE). Ідемпотентно:
    чіпає лише заявки з `external_order_id IS NULL` — вже подані повторно
    не переподає.
    """
    day_start, day_end = kyiv_day_bounds(utc_to_kyiv(target_date).strftime('%Y-%m-%d'))
    bids = db.query(MarketBid).filter(
        MarketBid.asset_id == asset.id,
        MarketBid.timestamp >= day_start, MarketBid.timestamp < day_end,
        MarketBid.external_order_id.is_(None),
    ).order_by(MarketBid.timestamp).all()
    if not bids:
        return {'status': 'nothing_to_submit', 'date': utc_to_kyiv(target_date).date().isoformat(), 'n_submitted': 0}

    client = get_oree_client()
    for b in bids:
        result = client.submit_bid(b)
        b.external_order_id = result['external_order_id']
        b.oree_submission_status = result['status']
        b.submitted_at = result['submitted_at']

    db.commit()
    return {
        'status': 'ok',
        'date': utc_to_kyiv(target_date).date().isoformat(),
        'n_submitted': len(bids),
    }


def submit_idm_fallback_bids_for_date(db, asset, target_date: datetime.datetime) -> dict:
    """
    Емулює подачу ВДР-заявки для годин, де РДН-заявка НЕ виконалась
    (`idm_fallback_suggested=True`, встановлюється `settle_bids_for_date`)
    — "настроюваний сценарій віртуального диспетчера", 2026-08-26. Той
    самий `oree_client` (не знає різниці РДН/ВДР — просто подає), результат
    пишеться в ОКРЕМІ поля (`idm_external_order_id`/`idm_submitted_at`),
    щоб не затерти РДН-подачу тієї самої заявки.

    Ідемпотентно і поважає ручне втручання диспетчера: пропускає заявки,
    які вже подані (`idm_external_order_id` заповнено) АБО які диспетчер
    явно підтвердив сам (`idm_fallback_acknowledged=True` — напр. сам подав
    на ВДР через кабінет OREE, або свідомо вирішив нічого не робити).
    """
    day_start, day_end = kyiv_day_bounds(utc_to_kyiv(target_date).strftime('%Y-%m-%d'))
    bids = db.query(MarketBid).filter(
        MarketBid.asset_id == asset.id,
        MarketBid.timestamp >= day_start, MarketBid.timestamp < day_end,
        MarketBid.idm_fallback_suggested.is_(True),
        MarketBid.idm_external_order_id.is_(None),
        MarketBid.idm_fallback_acknowledged.isnot(True),
    ).order_by(MarketBid.timestamp).all()
    if not bids:
        return {'status': 'nothing_to_submit', 'date': utc_to_kyiv(target_date).date().isoformat(), 'n_submitted': 0}

    client = get_oree_client()
    for b in bids:
        result = client.submit_bid(b)
        b.idm_external_order_id = result['external_order_id']
        b.idm_submitted_at = result['submitted_at']

    db.commit()
    return {
        'status': 'ok',
        'date': utc_to_kyiv(target_date).date().isoformat(),
        'n_submitted': len(bids),
    }


def submit_single_idm_fallback_bid(db, asset, target_date: datetime.datetime, hour: int, price_uah: float = None) -> dict:
    """
    Ручна (диспетчерська) подача ОДНІЄЇ ВДР-заявки на конкретну годину —
    на відміну від submit_idm_fallback_bids_for_date вище (масова, за
    розкладом віртуального диспетчера), тут диспетчер явно натискає кнопку
    і МОЖЕ скоригувати запропоновану ціну (2026-08-28).

    price_uah=None — диспетчер погодився з пропозицією без правок,
    використовується idm_fallback_price_uah (ринкова оцінка/факт) як є.
    price_uah заданий — диспетчерська корекція, обмежується тими самими
    легальними межами OREE (clamp_bid_price_to_oree_bounds), що й РДН.

    ВАЖЛИВО: результат записується в ОКРЕМЕ поле idm_bid_price_uah — НЕ
    ідм_fallback_price_uah (те лишається чистим ринковим сигналом,
    reconcile_idm_fallback_for_date і надалі вільно оновлює його реальною
    ціною, не змішуючи з диспетчерською рішенням).

    Ідемпотентно: якщо вже подано (idm_external_order_id заповнено) —
    повертає status='already_submitted', нічого не змінює.
    """
    date_str = utc_to_kyiv(target_date).strftime('%Y-%m-%d')
    ts = kyiv_to_utc(date_str, hour)
    bid = db.query(MarketBid).filter(
        MarketBid.asset_id == asset.id, MarketBid.timestamp == ts,
    ).first()
    if not bid:
        return {'status': 'not_found', 'message': f'Заявку на {date_str} годину {hour} не знайдено'}
    if not bid.idm_fallback_suggested:
        return {'status': 'not_applicable', 'message': 'ВДР-фолбек для цієї години не пропонувався (заявка виконалась на РДН, або ще не звірена)'}
    if bid.idm_external_order_id:
        return {'status': 'already_submitted', 'message': 'Заявку на ВДР вже подано', 'bid': _bid_to_dict(bid)}

    raw_price = price_uah if price_uah is not None else bid.idm_fallback_price_uah
    if raw_price is None:
        return {'status': 'no_price', 'message': 'Немає ні запропонованої, ні вказаної ціни для подачі'}
    clamped_price, was_clamped = clamp_bid_price_to_oree_bounds(raw_price)
    bid.idm_bid_price_uah = clamped_price

    client = get_oree_client()
    result = client.submit_bid(bid)
    bid.idm_external_order_id = result['external_order_id']
    bid.idm_submitted_at = result['submitted_at']
    # Диспетчер щойно сам подав через цю дію — авто-фолбек (заплановану
    # джобу) більше не потрібно турбувати цю годину, той самий прапорець,
    # що й ручне "Позначити виконаним вручну".
    bid.idm_fallback_acknowledged = True

    db.commit()
    return {'status': 'ok', 'price_clamped': was_clamped, 'bid': _bid_to_dict(bid)}


def _replay_soc_feasibility(db, asset, target_date: datetime.datetime, settled_bids) -> dict:
    """
    Послідовний SoC-реплей виконаних заявок (CODE_REVIEW.md п.6, 2026-08-22).

    settle_bids_for_date вище визначає executed/realized_profit_uah ЧИСТО
    через порівняння ціни, погодинно й незалежно — година-18 sell може
    вважатись виконаною й прибутковою, навіть якщо година-3 buy (зарядка)
    провалилась по ціні і фізично заряд узяти було ніде. Ця функція
    прогановує ті самі settled_bids (вже відсортовані за timestamp) через
    послідовний SoC, використовуючи ту саму рекурсію, що вже перевірена в
    MILP (milp_model.py::optimize_battery_schedule, soc[t] = soc[t-1] +
    charge*eff_charge - discharge/eff_discharge) — і пише окремо
    MarketBidSocFeasibility на кожну годину.

    Не вигадуємо штраф imbalance за фізично недоставлену енергію (немає
    реальних даних про ціну небалансу) — soc_feasible=False просто означає
    "SoC не дозволив, ця конкретна дія фізично не відбулась", SoC при
    цьому НЕ змінюється (не вигадуємо часткове виконання). Що саме робити
    з realized_profit_uah у цьому випадку — рішення викликача
    (compute_real_profit_capture_ratio в forecast_accuracy.py).
    """
    date_str = utc_to_kyiv(target_date).strftime('%Y-%m-%d')
    day_start, day_end = kyiv_day_bounds(date_str)
    soc_fraction = get_current_soc_fraction(db, asset, target_date=date_str)
    soc_mwh = soc_fraction * asset.capacity_mwh
    min_soc_mwh = asset.min_soc_pct / 100.0 * asset.capacity_mwh
    max_soc_mwh = asset.max_soc_pct / 100.0 * asset.capacity_mwh

    # Ідемпотентність повторного /bids/settle — та сама delete-then-insert
    # конвенція, що вже прийнята в проекті для PriceForecast/ChargeDischargePlan.
    # Межі — реальна київська доба (kyiv_day_bounds), не наївна UTC (CLAUDE.md п.26/27).
    db.query(MarketBidSocFeasibility).filter(
        MarketBidSocFeasibility.asset_id == asset.id,
        MarketBidSocFeasibility.timestamp >= day_start,
        MarketBidSocFeasibility.timestamp < day_end,
    ).delete()

    # Допуск 1 кВт·год, а не 1e-9: volume_kw зберігається округленим (5 знаків),
    # тож план MILP, що доходить рівно до min/max SoC, у реплеї промахувався на
    # ~1e-9 МВт·год і реально виконану годину позначав "фізично неможлива",
    # а далі каскадом ламались наступні години (2026-10-08: продаж 08:00,
    # купівлі 13-14:00). Після дії SoC притискаємо до меж, щоб похибка не
    # накопичувалась.
    EPS = 1e-3
    soc_map = {}
    for b in settled_bids:
        soc_before = soc_mwh
        feasible = True
        if b.executed and b.bid_type == 'buy':
            proposed = soc_mwh + (b.volume_kw / 1000.0) * asset.efficiency_charge
            feasible = proposed <= max_soc_mwh + EPS
            if feasible:
                soc_mwh = min(proposed, max_soc_mwh)
        elif b.executed and b.bid_type == 'sell':
            proposed = soc_mwh - (b.volume_kw / 1000.0) / asset.efficiency_discharge
            feasible = proposed >= min_soc_mwh - EPS
            if feasible:
                soc_mwh = max(proposed, min_soc_mwh)
        # standby або executed=False — SoC не змінюється, feasible=True (питання неприменимо)

        db.add(MarketBidSocFeasibility(
            timestamp=b.timestamp, asset_id=asset.id,
            soc_feasible=feasible, soc_before_mwh=soc_before, soc_after_mwh=soc_mwh,
            computed_at=datetime.datetime.utcnow(),
        ))
        soc_map[b.timestamp] = feasible

    return soc_map


def settle_bids_for_date(db, asset, target_date: datetime.datetime, actual_prices_by_hour: dict) -> dict:
    """
    Звіряє вже подані заявки (MarketBid) з РЕАЛЬНОЮ ціною РДН
    (actual_prices_by_hour: {hour: ціна}) і визначає, чи заявка "зіграла":
    - sell виконується, якщо bid_price_uah <= actual — продаж за actual.
    - buy виконується, якщо bid_price_uah >= actual — купівля за actual.
    Для НЕ виконаних заявок пропонує альтернативу на ВДР (оцінка ціни через
    ml_pipeline.estimate_idm_price_for_hour, бо реальної ціни ВДР на цю
    годину ще нема — ВДР ще не відбувся).
    """
    date_str = utc_to_kyiv(target_date).strftime('%Y-%m-%d')
    day_start, day_end = kyiv_day_bounds(date_str)
    bids = db.query(MarketBid).filter(
        MarketBid.asset_id == asset.id,
        MarketBid.timestamp >= day_start,
        MarketBid.timestamp < day_end,
    ).order_by(MarketBid.timestamp).all()
    if not bids:
        return {'status': 'no_bids', 'message': f'Немає поданих заявок на {target_date.date()} — спочатку згенеруйте їх.'}

    deg_cost_kwh = asset.deg_cost_per_mwh / 1000.0
    settled = []
    for b in bids:
        # Реальна київська година (не сира UTC .hour) — actual_prices_by_hour
        # ключується так само (bids.py) з 2026-08-23 (CLAUDE.md п.26/27).
        hour = utc_to_kyiv(b.timestamp).hour
        actual = actual_prices_by_hour.get(hour)
        if actual is None:
            continue
        b.actual_price_uah = actual

        if b.bid_type == 'standby':
            b.executed = True
            b.realized_profit_uah = 0.0
            b.idm_fallback_suggested = False
        elif b.bid_type == 'sell':
            b.executed = b.bid_price_uah <= actual
            if b.executed:
                b.realized_profit_uah = evaluate_schedule_profit(
                    [0.0], [b.volume_kw], [actual], degradation_cost=deg_cost_kwh, **get_tariff_kwargs(),
                )
                b.idm_fallback_suggested = False
            else:
                b.realized_profit_uah = 0.0
                idm_price = mt.estimate_idm_price_for_hour(actual, as_of_date=target_date)
                b.idm_fallback_suggested = True
                b.idm_fallback_price_uah = idm_price
                b.idm_fallback_profit_uah = evaluate_schedule_profit(
                    [0.0], [b.volume_kw], [idm_price], degradation_cost=deg_cost_kwh, **get_tariff_kwargs(),
                )
        elif b.bid_type == 'buy':
            b.executed = b.bid_price_uah >= actual
            if b.executed:
                b.realized_profit_uah = evaluate_schedule_profit(
                    [b.volume_kw], [0.0], [actual], degradation_cost=deg_cost_kwh, **get_tariff_kwargs(),
                )
                b.idm_fallback_suggested = False
            else:
                b.realized_profit_uah = 0.0
                idm_price = mt.estimate_idm_price_for_hour(actual, as_of_date=target_date)
                b.idm_fallback_suggested = True
                b.idm_fallback_price_uah = idm_price
                b.idm_fallback_profit_uah = evaluate_schedule_profit(
                    [b.volume_kw], [0.0], [idm_price], degradation_cost=deg_cost_kwh, **get_tariff_kwargs(),
                )

        b.settled_at = datetime.datetime.utcnow()
        settled.append(b)

    soc_map = _replay_soc_feasibility(db, asset, target_date, settled)

    db.commit()

    # Заявка, що "зіграла" по ціні, але фізично неможлива по SoC (CODE_REVIEW.md
    # п.6) — не рахуємо в total_realized_profit_uah (енергія фізично не
    # доставлена/прийнята). realized_profit_uah на самому MarketBid лишається
    # як є (гіпотетична цінність за умови ідеальної доставки) — для аудиту/
    # прозорості, лише агрегат тут і в compute_real_profit_capture_ratio
    # (forecast_accuracy.py) чесно її виключає.
    total_realized = sum(
        (b.realized_profit_uah or 0.0) for b in settled
        if soc_map.get(b.timestamp, True)
    )
    n_executed = sum(1 for b in settled if b.executed)
    n_failed = sum(1 for b in settled if b.executed is False)
    n_soc_infeasible = sum(1 for b in settled if b.executed and not soc_map.get(b.timestamp, True))
    return {
        'status': 'ok',
        'date': utc_to_kyiv(target_date).date().isoformat(),
        'n_settled': len(settled),
        'n_executed': n_executed,
        'n_failed_needs_idm': n_failed,
        'n_soc_infeasible': n_soc_infeasible,
        'total_realized_profit_uah': float(total_realized),
        'bids': [_bid_to_dict(b, soc_feasible=soc_map.get(b.timestamp)) for b in settled],
    }


def reconcile_idm_fallback_for_date(db, asset, target_date: datetime.datetime) -> dict:
    """
    Другий прохід звірки (2026-08-28, "Загальний дохід" у звіті) — ЛИШЕ для
    заявок, де ВДР-фолбек вже запропоновано (idm_fallback_suggested=True,
    settle_bids_for_date), замінює ОЦІНКУ (mt.estimate_idm_price_for_hour,
    порахована заздалегідь, до реальних торгів ВДР) на РЕАЛЬНУ погодинну
    середньозважену ціну ВДР (IdmPrice), щойно вона стає доступна —
    sync_today_idm_prices_from_oree, run_intraday_price_sync (scheduler.py).

    Ідемпотентно: чіпає лише idm_fallback_price_is_actual != True, ніколи не
    перезаписує вже звірену годину повторно. НЕ гарантія виконання — ВДР
    безперервні торги (MEMORY.md §8), не аукціон єдиної ціни, тож навіть
    "реальна" тут — це реальна СЕРЕДНЯ ціна ринку за годину, не підтверджена
    ціна конкретної нашої угоди (MockOreeClient — реального виконання немає
    взагалі). Позначається idm_fallback_price_is_actual=True, щоб звіт міг
    чесно розрізнити "оцінка" від "факт ВДР".
    """
    from src.database.models import IdmPrice

    date_str = utc_to_kyiv(target_date).strftime('%Y-%m-%d')
    day_start, day_end = kyiv_day_bounds(date_str)
    bids = db.query(MarketBid).filter(
        MarketBid.asset_id == asset.id,
        MarketBid.timestamp >= day_start, MarketBid.timestamp < day_end,
        MarketBid.idm_fallback_suggested.is_(True),
        MarketBid.idm_fallback_price_is_actual.isnot(True),
    ).order_by(MarketBid.timestamp).all()
    if not bids:
        return {'status': 'nothing_to_reconcile', 'date': date_str, 'n_reconciled': 0, 'n_pending': 0}

    idm_by_hour = {
        utc_to_kyiv(r.timestamp).hour: r.price_uah
        for r in db.query(IdmPrice).filter(
            IdmPrice.timestamp >= day_start, IdmPrice.timestamp < day_end,
        ).all()
    }

    deg_cost_kwh = asset.deg_cost_per_mwh / 1000.0
    n_reconciled = 0
    for b in bids:
        hour = utc_to_kyiv(b.timestamp).hour
        real_idm = idm_by_hour.get(hour)
        if real_idm is None:
            continue
        b.idm_fallback_price_uah = real_idm
        if b.bid_type == 'sell':
            b.idm_fallback_profit_uah = evaluate_schedule_profit(
                [0.0], [b.volume_kw], [real_idm], degradation_cost=deg_cost_kwh, **get_tariff_kwargs(),
            )
        elif b.bid_type == 'buy':
            b.idm_fallback_profit_uah = evaluate_schedule_profit(
                [b.volume_kw], [0.0], [real_idm], degradation_cost=deg_cost_kwh, **get_tariff_kwargs(),
            )
        b.idm_fallback_price_is_actual = True
        n_reconciled += 1

    db.commit()
    return {
        'status': 'ok', 'date': date_str,
        'n_reconciled': n_reconciled, 'n_pending': len(bids) - n_reconciled,
    }


def compute_imbalance_financials(b: MarketBid) -> dict:
    """
    Розрахунок небалансу з балансуючим ринком (БР) — формула й склад
    полів відтворені з реального облікового Excel-файлу справжнього
    підприємства з батареєю ("УЗЕ Флора", наданий користувачем 2026-09-09):

        небаланс_купівля = max(0, Факт_Заряд - РДН_купівля - ВДР_купівля
                                   - Факт_Розряд + РДН_продаж + ВДР_продаж
                                   + Факт_Власні_потреби)
        небаланс_продаж  = max(0, Факт_Розряд - ВДР_продаж - РДН_продаж
                                   - Факт_Заряд - Факт_Власні_потреби
                                   + ВДР_купівля + РДН_купівля)

    Тобто: дефіцит (реально спожито/недопоставлено більше, ніж заявлено на
    РДН+ВДР) закривається купівлею на БР за `balancing_buy_price_uah`;
    профіцит (реально віддано більше, ніж заявлено) продається на БР за
    `balancing_sell_price_uah`. Реальна знахідка з наданого файлу: НАВІТЬ
    при нульовій активності РДН/ВДР "Факт Власні потреби" (постійне власне
    споживання батареї — контролер/охолодження/освітлення) щогодини
    створює маленький дефіцит, який доводиться закривати на БР — те саме
    "собственные нужды", що обговорювалось з користувачем 2026-09-09.

    РДН/ВДР обсяги беруться з уже наявних полів цієї ж заявки (не
    вигадуються): РДН — volume_kw, якщо executed; ВДР — volume_kw, якщо
    РДН не виконалась і ВДР-фолбек реально подано/підтверджено
    (idm_external_order_id або idm_fallback_acknowledged).

    Повертає None для всіх полів, доки диспетчер не ввів реальні "Факт"-
    показники лічильника (actual_charge_mwh/actual_discharge_mwh/
    actual_own_consumption_mwh) — чесно "ще не звірено", а не вигаданий
    нуль. Ціни (`balancing_*_price_uah`) окремо nullable — обсяг небалансу
    можна порахувати одразу після внесення "Факт"-показників, а вартість
    з'явиться пізніше, коли прийде реальний рахунок з ціною.
    """
    if b.actual_charge_mwh is None or b.actual_discharge_mwh is None or b.actual_own_consumption_mwh is None:
        return {
            'imbalance_buy_mwh': None, 'imbalance_sell_mwh': None,
            'imbalance_buy_cost_uah': None, 'imbalance_sell_revenue_uah': None,
        }

    rdn_buy_mwh = b.volume_kw / 1000.0 if b.bid_type == 'buy' and b.executed else 0.0
    rdn_sell_mwh = b.volume_kw / 1000.0 if b.bid_type == 'sell' and b.executed else 0.0
    vdr_confirmed = bool(b.idm_external_order_id) or bool(b.idm_fallback_acknowledged)
    vdr_buy_mwh = b.volume_kw / 1000.0 if b.bid_type == 'buy' and b.executed is False and vdr_confirmed else 0.0
    vdr_sell_mwh = b.volume_kw / 1000.0 if b.bid_type == 'sell' and b.executed is False and vdr_confirmed else 0.0

    m, n, o = b.actual_charge_mwh, b.actual_discharge_mwh, b.actual_own_consumption_mwh
    imbalance_buy_mwh = max(0.0, m - rdn_buy_mwh - vdr_buy_mwh - n + rdn_sell_mwh + vdr_sell_mwh + o)
    imbalance_sell_mwh = max(0.0, n - vdr_sell_mwh - rdn_sell_mwh - m - o + vdr_buy_mwh + rdn_buy_mwh)

    # Знак — узгоджено з рештою проєкту (costUah/delivery_cost_uah тощо
    # завжди від'ємні, дохід — додатний), а не з сирим Excel (там усе
    # додатне, і "Прибуток" віднімає купівлю окремо).
    imbalance_buy_cost_uah = (
        -round(imbalance_buy_mwh * b.balancing_buy_price_uah, 2)
        if b.balancing_buy_price_uah is not None else None
    )
    imbalance_sell_revenue_uah = (
        round(imbalance_sell_mwh * b.balancing_sell_price_uah, 2)
        if b.balancing_sell_price_uah is not None else None
    )
    return {
        'imbalance_buy_mwh': round(imbalance_buy_mwh, 6),
        'imbalance_sell_mwh': round(imbalance_sell_mwh, 6),
        'imbalance_buy_cost_uah': imbalance_buy_cost_uah,
        'imbalance_sell_revenue_uah': imbalance_sell_revenue_uah,
    }


def save_actual_settlement_for_date(db, asset, target_date: datetime.datetime, hourly_entries: list) -> dict:
    """
    Зберігає реальні дані звірки з БР (Факт Заряд/Розряд/Власні потреби,
    Ціна продажу/докупки БР) для вже наявних заявок (MarketBid) на цю
    добу — той самий "delete-then-insert за добу" дух, що й
    save_manual_overrides (optimization.py), але тут UPSERT в наявний
    рядок заявки (не створює нових рядків): звірка з БР має сенс лише для
    години, де вже є заявка (bid_type/executed відомі — потрібні для
    формули небалансу). `hourly_entries` — список dict з ключами `hour`
    (0-23, реальна київська година) і будь-якою підмножиною
    `actual_charge_mwh`/`actual_discharge_mwh`/`actual_own_consumption_mwh`/
    `balancing_sell_price_uah`/`balancing_buy_price_uah` (None очищає
    поле назад, ключ відсутній — поле не чіпається, часткове оновлення).

    Повертає {'status': 'ok', 'n_updated': ..., 'n_not_found': ...,
    'not_found_hours': [...]} — години без заявки (напр. заявки на цю
    добу ще не формувались) чесно пропускаються, а не вигадують рядок.
    """
    date_str = utc_to_kyiv(target_date).strftime('%Y-%m-%d')
    day_start, day_end = kyiv_day_bounds(date_str)
    bids_by_hour = {
        utc_to_kyiv(b.timestamp).hour: b
        for b in db.query(MarketBid).filter(
            MarketBid.asset_id == asset.id,
            MarketBid.timestamp >= day_start, MarketBid.timestamp < day_end,
        ).all()
    }

    FIELDS = (
        'actual_charge_mwh', 'actual_discharge_mwh', 'actual_own_consumption_mwh',
        'balancing_sell_price_uah', 'balancing_buy_price_uah',
    )
    n_updated = 0
    not_found_hours = []
    for entry in hourly_entries:
        hour = entry.get('hour')
        bid = bids_by_hour.get(hour)
        if bid is None:
            not_found_hours.append(hour)
            continue
        for field in FIELDS:
            if field in entry:
                setattr(bid, field, entry[field])
        n_updated += 1

    db.commit()
    return {
        'status': 'ok', 'date': date_str,
        'n_updated': n_updated, 'n_not_found': len(not_found_hours),
        'not_found_hours': not_found_hours,
    }


IDM_RANGE_LOOKBACK_DAYS = 7
_IDM_RANGE_CACHE = {}


def idm_reference_range(kyiv_date_str: str) -> dict:
    """
    Реальний діапазон угод ВДР за кожну київську годину (2026-09-29, CLAUDE.md
    п.61) — з добового файлу OREE (oree_xlsx.py → historical_data_merged.csv:
    IDM_Min_Price / IDM_Max_Price / IDM_Last_Price). Якщо ВДР на цю добу вже
    опублікований — його факт (`same_day=True`); інакше — найсвіжіша доба за
    останні IDM_RANGE_LOOKBACK_DAYS днів із даними для цієї години (ВДР-
    таблиця на oree поповнюється пізно ввечері, тож "сьогодні/завтра" майже
    завжди береться з учора). Для диспетчера — орієнтир, за якими цінами
    РЕАЛЬНО торгували, поряд з оцінкою/фактом середньозваженої ціни.
    Повертає {година: {min, max, last, ref_date, same_day}} (години без даних
    відсутні — не вигадуємо).
    """
    import src.modules.market_data_service.data_manager as dm
    try:
        mtime = os.path.getmtime(dm.MERGED_DATA_PATH)
    except OSError:
        return {}
    key = (kyiv_date_str, mtime)
    if key in _IDM_RANGE_CACHE:
        return _IDM_RANGE_CACHE[key]

    df = dm.load_merged_csv_cached(['Datetime', 'IDM_Min_Price', 'IDM_Max_Price', 'IDM_Last_Price'])
    if 'IDM_Min_Price' not in df.columns:
        return {}
    target = datetime.date.fromisoformat(kyiv_date_str)
    lo = kyiv_to_utc((target - datetime.timedelta(days=IDM_RANGE_LOOKBACK_DAYS)).isoformat(), 0)
    hi = kyiv_to_utc((target + datetime.timedelta(days=1)).isoformat(), 0)
    df = df[(df['Datetime'] >= lo) & (df['Datetime'] < hi)].dropna(subset=['IDM_Min_Price', 'IDM_Max_Price'])
    kyiv = df['Datetime'].dt.tz_localize('UTC').dt.tz_convert('Europe/Kyiv')
    df = df.assign(kdate=kyiv.dt.date, khour=kyiv.dt.hour).sort_values('Datetime')

    out = {}
    for hour, g in df.groupby('khour'):
        row = g.iloc[-1]  # найсвіжіша доба з даними для цієї години
        out[int(hour)] = {
            'min': float(row['IDM_Min_Price']), 'max': float(row['IDM_Max_Price']),
            'last': float(row['IDM_Last_Price']) if row['IDM_Last_Price'] == row['IDM_Last_Price'] else None,
            'ref_date': row['kdate'].isoformat(), 'same_day': row['kdate'] == target,
        }
    if len(_IDM_RANGE_CACHE) > 64:
        _IDM_RANGE_CACHE.clear()
    _IDM_RANGE_CACHE[key] = out
    return out


def _bid_to_dict(b: MarketBid, soc_feasible=None) -> dict:
    """soc_feasible=None означає "не порахований" (заявка ще не проходила
    settle, або викликач не запросив MarketBidSocFeasibility) — не плутати
    з False ("порахований і фізично неможливий"). Див. _replay_soc_feasibility."""
    price_clamped = _is_at_bound(b.bid_price_uah)

    # 2026-09-09: ЧИСТА вартість енергії (ціна × обсяг), БЕЗ тарифів на
    # доставку і без деградації — за проханням користувача, диспетчер має
    # бачити, по чому купує/продає енергію, а не нетто-число з уже
    # вплетеним тарифом (`realized_profit_uah`/`idm_fallback_profit_uah`
    # НЕ змінюються — лишаються реальним фінансовим підсумком для звітності/
    # ROI/Executive Summary; тарифи/деградація й надалі там законно
    # присутні, просто НЕ показуються в "Заявка РДН"). None — ще не
    # звірено; 0.0 — standby або не виконано (енергія фізично не пройшла).
    volume_mw = b.volume_kw / 1000.0
    if b.executed is None:
        energy_profit_uah = None
    elif b.executed and b.bid_type == 'sell':
        energy_profit_uah = b.actual_price_uah * volume_mw
    elif b.executed and b.bid_type == 'buy':
        energy_profit_uah = -b.actual_price_uah * volume_mw
    else:
        energy_profit_uah = 0.0

    if not b.idm_fallback_suggested or b.idm_fallback_price_uah is None:
        idm_fallback_energy_profit_uah = None
    elif b.bid_type == 'sell':
        idm_fallback_energy_profit_uah = b.idm_fallback_price_uah * volume_mw
    elif b.bid_type == 'buy':
        idm_fallback_energy_profit_uah = -b.idm_fallback_price_uah * volume_mw
    else:
        idm_fallback_energy_profit_uah = None

    kyiv_ts = utc_to_kyiv(b.timestamp)
    idm_range = idm_reference_range(kyiv_ts.date().isoformat()).get(kyiv_ts.hour)

    return {
        'energy_profit_uah': energy_profit_uah,
        'idm_fallback_energy_profit_uah': idm_fallback_energy_profit_uah,
        # Реальний діапазон угод ВДР за цю годину (idm_reference_range) —
        # None, якщо даних за останній тиждень немає.
        'idm_range': idm_range,
        # Реальна київська година (CLAUDE.md п.26/27) — саме та, яку
        # диспетчер має ввести в кабінет oree.com.ua, а не сира UTC .hour.
        'hour': utc_to_kyiv(b.timestamp).hour,
        'bid_type': b.bid_type,
        'volume_kw': b.volume_kw,
        'forecast_price_uah': b.forecast_price_uah,
        'margin_pct': b.margin_pct,
        # None — стандартний відсотковий режим (margin_pct вище й є реально
        # застосованим значенням). Заповнено — застосований АБСОЛЮТНИЙ буфер
        # (₴/МВт·год); margin_pct тоді містить лише еквівалентний %, для
        # зворотної сумісності зі старими звітами.
        'margin_uah': b.margin_uah,
        'bid_price_mode': b.bid_price_mode,
        'bid_price_uah': b.bid_price_uah,
        'actual_price_uah': b.actual_price_uah,
        'executed': b.executed,
        'realized_profit_uah': b.realized_profit_uah,
        'soc_feasible': soc_feasible,
        'idm_fallback_suggested': b.idm_fallback_suggested,
        'idm_fallback_price_uah': b.idm_fallback_price_uah,
        'idm_fallback_profit_uah': b.idm_fallback_profit_uah,
        # True — реальна звірена ціна ВДР (reconcile_idm_fallback_for_date);
        # False/None — усе ще лише оцінка на момент звірки РДН, ВДР для цієї
        # години ще не відбувся або ще не досинканий.
        'idm_fallback_price_is_actual': b.idm_fallback_price_is_actual,
        'bid_price_legally_clamped': price_clamped,
        'oree_bid_price_bounds_uah': dict(zip(('min', 'max'), get_market_price_bounds())),
        'forecast_run_id': b.forecast_run_id,
        # Емуляція подачі (oree_client.py) — НЕ реальна подача на біржу.
        'external_order_id': b.external_order_id,
        'oree_submission_status': b.oree_submission_status,
        'submitted_at': b.submitted_at.isoformat() + 'Z' if b.submitted_at else None,
        # ВДР-фолбек — окремо від РДН-подачі вище (та сама заявка, інший ринок).
        'idm_fallback_acknowledged': b.idm_fallback_acknowledged,
        'idm_external_order_id': b.idm_external_order_id,
        'idm_submitted_at': b.idm_submitted_at.isoformat() + 'Z' if b.idm_submitted_at else None,
        # Ціна, яку диспетчер свідомо обрав подати на ВДР
        # (submit_single_idm_fallback_bid) — None, доки не подано цим
        # шляхом. Окремо від idm_fallback_price_uah (ринковий сигнал).
        'idm_bid_price_uah': b.idm_bid_price_uah,
        'idm_bid_price_legally_clamped': (
            b.idm_bid_price_uah is not None and _is_at_bound(b.idm_bid_price_uah)
        ),
        # Звірка з БР (2026-09-09) — реальні "Факт"-показники лічильника і
        # ціни небалансу, введені диспетчером вручну (див. докстрінг
        # compute_imbalance_financials). None — ще не введено.
        'actual_charge_mwh': b.actual_charge_mwh,
        'actual_discharge_mwh': b.actual_discharge_mwh,
        'actual_own_consumption_mwh': b.actual_own_consumption_mwh,
        'balancing_sell_price_uah': b.balancing_sell_price_uah,
        'balancing_buy_price_uah': b.balancing_buy_price_uah,
        **compute_imbalance_financials(b),
    }


def build_daily_action_summary(db, asset, target_date: datetime.datetime) -> dict:
    """
    "Що робити зараз" по вже ІСНУЮЧОМУ стану MarketBid на target_date.
    Тільки ЧИТАЄ — нічого не генерує й не звіряє сама (щоб не перетворитись
    на приховану автоматизацію подачі заявок, явно відкладену користувачем).
    """
    date_str = utc_to_kyiv(target_date).strftime('%Y-%m-%d')
    day_start, day_end = kyiv_day_bounds(date_str)
    bids = db.query(MarketBid).filter(
        MarketBid.asset_id == asset.id,
        MarketBid.timestamp >= day_start,
        MarketBid.timestamp < day_end,
    ).order_by(MarketBid.timestamp).all()

    actions = []

    if not bids:
        actions.append({
            'severity': 'action', 'hour': None,
            'text': f'Заявки РДН на {date_str} ще не сформовано — натисніть «Сформувати заявки зараз» (потрібен готовий прогноз/MILP-графік на цю дату).',
        })
        return {'status': 'ok', 'date': date_str, 'has_bids': False, 'all_settled': False,
                'n_total': 0, 'n_executed': 0, 'n_needs_idm_action': 0, 'n_price_clamped': 0, 'actions': actions}

    dicts = [_bid_to_dict(b) for b in bids if b.bid_type != 'standby']
    n_unsettled = sum(1 for d in dicts if d['executed'] is None)
    n_executed = sum(1 for d in dicts if d['executed'] is True)
    n_needs_idm = sum(1 for d in dicts if d['executed'] is False and d['idm_fallback_suggested'])
    n_clamped = sum(1 for d in dicts if d['bid_price_legally_clamped'])

    if n_unsettled > 0:
        actions.append({
            'severity': 'action', 'hour': None,
            'text': f'Подайте вручну {n_unsettled} заявок РДН на {date_str} у кабінеті oree.com.ua (закриття воріт — 12:00 напередодні). Після публікації факту ціни (~13:00) натисніть «Звірити з фактом OREE».',
        })

    if n_clamped > 0:
        actions.append({
            'severity': 'warning', 'hour': None,
            'text': f'{n_clamped} год. з ціною заявки, скоригованою до законної межі OREE (10.00–50000.00 грн/МВт·год) — перевірте вручну перед поданням.',
        })

    for d in dicts:
        if d['executed'] is False and d['idm_fallback_suggested']:
            actions.append({
                'severity': 'warning', 'hour': d['hour'],
                # Година 1-24, як на oree.com.ua і в Excel (d['hour'] — київська 0-23).
                'text': (f"Година {d['hour'] + 1} ({d['hour']:02d}:00–{d['hour'] + 1:02d}:00): заявка РДН НЕ виконана (заявлено {round(d['bid_price_uah'])}, факт {round(d['actual_price_uah'] or 0)} грн/МВт·год) — "
                         f"подайте на ВДР {round(d['volume_kw'])} кВт за ~{round(d['idm_fallback_price_uah'] or 0)} грн/МВт·год "
                         f"(очікуваний прибуток {round(d['idm_fallback_profit_uah'] or 0)} грн)."),
            })

    if not actions and n_executed > 0:
        actions.append({'severity': 'ok', 'hour': None, 'text': f'Усі {n_executed} заявок РДН на {date_str} виконано — дій не потрібно.'})

    return {
        'status': 'ok', 'date': date_str, 'has_bids': True, 'all_settled': n_unsettled == 0,
        'n_total': len(dicts), 'n_executed': n_executed, 'n_needs_idm_action': n_needs_idm,
        'n_price_clamped': n_clamped, 'actions': actions,
    }
