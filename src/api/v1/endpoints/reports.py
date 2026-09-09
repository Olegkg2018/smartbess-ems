import datetime
import io
from fastapi import APIRouter, HTTPException, Query, Depends, Response
from sqlalchemy.orm import Session
from sqlalchemy import func

from src.database.session import SessionLocal
from src.database.models import Asset, ChargeDischargePlan, BessTelemetry, PriceForecast, MarketBid
from src.modules.reporting_service.services import ReportingService
from src.modules.reporting_service.forecast_accuracy import compute_rolling_accuracy, get_profit_capture_ratio
from src.core.security import RoleChecker
from src.api.v1.endpoints.optimization import get_manual_overrides
from src.api.v1.endpoints.forecast import get_actual_prices
from src.core.time_utils import kyiv_to_utc, kyiv_day_bounds, utc_to_kyiv
from src.modules.bidding_service.services import get_delivery_tariff_uah_per_mwh

router = APIRouter()


def _bid_hour_financials(bid: MarketBid, ac, asset: Asset) -> dict:
    """
    Одна точка правди для погодинних фінансових колонок — використовується і
    Excel-звітом за період (export_forecast_period_excel), і живою JSON-
    таблицею за добу (/reports/day-bid-report, "Ручне коригування заявок",
    2026-09-08). "Плановий прибуток"/"Витрати на доставку"/"Деградація" —
    гіпотеза "якби ЗІГРАЛА ця заявка", за РЕАЛЬНОЮ факт-ціною (ac), незалежно
    від executed (той самий принцип, що вже був для "Плановий
    прибуток"/"Тарифи мережі", 2026-08-28) — тепер розщеплено на ТРИ
    компоненти замість двох, щоб деградація теж була видна окремим
    стовпцем, а не змішана з ринковим P&L чи тарифами.

    Ці витрати НЕ впливають на саму заявку (bid_price_uah/executed уже
    визначені раніше, generate_bids_for_date/settle_bids_for_date) — суто
    похідні для обліку фінансового результату.

    Точна тотожність для ВИКОНАНОЇ заявки: planned_profit_uah +
    delivery_cost_uah + degradation_cost_uah == realized_profit_uah (обидві
    сторони — той самий evaluate_schedule_profit з тим самим редагованим
    тарифом на доставку (`get_delivery_tariff_uah_per_mwh`, Settings,
    2026-09-09) / asset.deg_cost_per_mwh, arbitrage-режим — тарифи лише на
    купівлю, деградація лише на продаж). "Реалізований прибуток"/
    "Загальний дохід" — реальний факт (0 для невиконаних), читаються
    напряму з bid, тут НЕ перераховуються.
    """
    volume_mw = bid.volume_kw / 1000.0
    if ac is None:
        planned_profit = delivery_cost = degradation_cost = None
    elif bid.bid_type == "sell":
        planned_profit = ac * volume_mw
        delivery_cost = 0.0
        degradation_cost = -(asset.deg_cost_per_mwh / 1000.0) * bid.volume_kw
    elif bid.bid_type == "buy":
        planned_profit = -ac * volume_mw
        delivery_cost = -get_delivery_tariff_uah_per_mwh() / 1000.0 * bid.volume_kw
        degradation_cost = 0.0
    else:  # standby
        planned_profit = 0.0
        delivery_cost = 0.0
        degradation_cost = 0.0

    if bid.bid_type == "standby":
        total_income = bid.realized_profit_uah
        income_source = "Очікування" if bid.realized_profit_uah is not None else None
    elif bid.executed:
        total_income = bid.realized_profit_uah
        income_source = "РДН"
    elif bid.executed is False:
        if bid.idm_fallback_suggested and bid.idm_fallback_profit_uah is not None:
            total_income = bid.idm_fallback_profit_uah
            income_source = "ВДР (факт)" if bid.idm_fallback_price_is_actual else "ВДР (оцінка)"
        else:
            total_income = bid.realized_profit_uah if bid.realized_profit_uah is not None else 0.0
            income_source = "не реалізовано"
    else:
        total_income = None
        income_source = None

    return {
        "planned_profit_uah": round(planned_profit, 2) if planned_profit is not None else None,
        "delivery_cost_uah": round(delivery_cost, 2) if delivery_cost is not None else None,
        "degradation_cost_uah": round(degradation_cost, 2) if degradation_cost is not None else None,
        "realized_profit_uah": round(bid.realized_profit_uah, 2) if bid.realized_profit_uah is not None else None,
        "total_income_uah": round(total_income, 2) if total_income is not None else None,
        "income_source": income_source,
        "charge_mw": round(volume_mw, 3) if bid.bid_type == "buy" else 0.0,
        "discharge_mw": round(volume_mw, 3) if bid.bid_type == "sell" else 0.0,
    }

@router.get("/executive-summary", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_executive_summary(
    asset_id: str = Query(..., description="UUID of the BESS asset"),
    period: str = Query("month", description="One of day, week, month, year")
):
    db = SessionLocal()
    try:
        report = ReportingService.get_executive_summary_report(db, asset_id)
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error compiling executive summary: {str(e)}")
    finally:
        db.close()

@router.get("/forecast-accuracy", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_forecast_accuracy(
    days: int = Query(30, description="Скільки останніх днів порівняти прогноз/факт")
):
    db = SessionLocal()
    try:
        live = compute_rolling_accuracy(db, days=days)
        ratio = get_profit_capture_ratio(db)
        return {"live_accuracy": live, "profit_capture_ratio": ratio}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error computing forecast accuracy: {str(e)}")
    finally:
        db.close()

@router.get("/market-conditions", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_market_conditions():
    """
    Реальний операційний знімок "на зараз" для панелі диспетчера: остання
    зібрана ціна газу, транскордонний нетто-експорт (ENTSO-E) та keyword-сигнал
    з публічних каналів Укренерго/Міненерго. Замінює мертві повзунки
    "Ринкові фактори прогнозування", які раніше нічого насправді не міняли.
    """
    import os
    import pandas as pd
    from src.core.config import settings
    import src.modules.external_data_service.telegram_public as ext_tg

    result = {
        "gas_price_eur_mwh": None,
        "gas_price_as_of": None,
        "grid_net_export_mw": None,
        "grid_net_export_as_of": None,
        "grid_stress_today": {"grid_stress_high": 0, "grid_stress_medium": 0, "mentions": 0},
        "latest_posts": [],
    }

    csv_path = os.path.join(settings.DATA_DIR, "historical_data_merged.csv")
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path, usecols=["Datetime", "Gas_Price_EUR_MWh", "Grid_Net_Export_MW"])
            df["Datetime"] = pd.to_datetime(df["Datetime"])

            gas_series = df.dropna(subset=["Gas_Price_EUR_MWh"])
            if not gas_series.empty:
                last = gas_series.iloc[-1]
                result["gas_price_eur_mwh"] = float(last["Gas_Price_EUR_MWh"])
                result["gas_price_as_of"] = last["Datetime"].isoformat()

            flow_series = df.dropna(subset=["Grid_Net_Export_MW"])
            if not flow_series.empty:
                last = flow_series.iloc[-1]
                result["grid_net_export_mw"] = float(last["Grid_Net_Export_MW"])
                result["grid_net_export_as_of"] = last["Datetime"].isoformat()
        except Exception as e:
            result["error"] = f"Error reading market conditions from CSV: {str(e)}"

    today_str = datetime.datetime.utcnow().date().isoformat()
    stress = ext_tg.daily_grid_stress_signal()
    if today_str in stress:
        result["grid_stress_today"] = stress[today_str]

    result["latest_posts"] = ext_tg.get_latest_posts(n=3)

    return result

@router.get("/export-day", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def export_day_excel(asset_id: str, date: str):
    """
    Погодинний Excel-звіт за добу: прогноз ціни, факт ціни (якщо є),
    заряд/розряд і ціна виконання (реальний заявочний профіль — та сама
    логіка, що й get_manual_overrides, щоб цифри в файлі завжди збігалися з
    тим, що диспетчер бачить на екрані). Факт ціни — та сама логіка, що й
    get_actual_prices (спершу локальна БД, інакше живий запит до
    oree.com.ua, якщо доба вже опублікована, але кеш ще не досинхронізовано —
    без цього факт міг бути порожнім навіть коли ціна вже реально відома).
    Година підписана 1-24 (не 0-23), як на самому oree.com.ua.

    Різниця Факт-Прогноз показує напрям і величину помилки прогнозу за цю
    конкретну добу (позитивна — факт вищий за прогноз). Заряд/Розряд —
    крайні праві стовпці, з міні-графіком просто в самій колонці (Excel
    Data Bars) — не окрема діаграма збоку, а "стовпчик заряду батареї"
    прямо в комірках, як просив диспетчер.

    Саме .xlsx, а не .csv — у CSV Excel сам вгадує типи комірок при
    відкритті (напр. "1-2" перетворюється на дату) без жодного способу це
    заборонити; у .xlsx тип кожної комірки заданий явно, Excel нічого не
    вгадує.
    """
    try:
        datetime.datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="date має бути у форматі YYYY-MM-DD")

    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.formatting.rule import DataBarRule

    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == asset_id).first()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        target_dt = kyiv_to_utc(date, 0)

        forecasts = db.query(PriceForecast).filter(
            PriceForecast.forecast_run_at == target_dt
        ).order_by(PriceForecast.timestamp).all()
        forecast_by_hour = {utc_to_kyiv(f.timestamp).hour: f.predicted_price_uah for f in forecasts}

        dispatch = await get_manual_overrides(asset_id=asset_id, date=date)
        dispatch_by_hour = {o["hour"]: o for o in dispatch["overrides"]}
        power_limit_mw = asset.power_mw
    finally:
        db.close()

    actual = await get_actual_prices(target_date=date)
    actual_by_hour = (
        dict(zip(actual["hours"], actual["actual_prices_uah"])) if actual.get("available") else {}
    )

    wb = Workbook()
    ws = wb.active
    ws.title = date

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F2937")
    title_font = Font(bold=True, size=13)

    ws["A1"] = f"SmartBESS EMS — погодинний звіт за {date}"
    ws["A1"].font = title_font
    ws["A2"] = f"Актив: {asset.name}"
    ws["A2"].font = Font(italic=True, color="6B7280")

    headers = [
        "Година", "Прогноз ціни, ₴/МВт·год", "Факт ціни, ₴/МВт·год", "Різниця Факт-Прогноз, ₴/МВт·год",
        "Ціна виконання, ₴/МВт·год", "Сума, ₴", "Ручна корекція", "Заряд, МВт", "Розряд, МВт",
    ]
    header_row = 4
    for col_idx, title in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", wrap_text=True)

    for hour in range(24):
        row = header_row + 1 + hour
        d = dispatch_by_hour.get(hour, {})
        power_mw = d.get("power_mw", 0.0) or 0.0
        charge_mw = -power_mw if power_mw < 0 else 0.0
        discharge_mw = power_mw if power_mw > 0 else 0.0
        was_active = power_mw != 0.0

        # Година підписана 1-24 (oree.com.ua "Година 1" = 00:00-01:00), не 0-23.
        ws.cell(row=row, column=1, value=hour + 1).number_format = "0"
        fc = forecast_by_hour.get(hour)
        ws.cell(row=row, column=2, value=round(fc, 2) if fc is not None else None).number_format = "#,##0.00"
        ac = actual_by_hour.get(hour)
        ws.cell(row=row, column=3, value=round(ac, 2) if ac is not None else None).number_format = "#,##0.00"
        diff = (ac - fc) if (ac is not None and fc is not None) else None
        ws.cell(row=row, column=4, value=round(diff, 2) if diff is not None else None).number_format = "+#,##0.00;-#,##0.00"

        # Ціна виконання/Сума мають сенс лише в годинах, де реально відбувся
        # заряд чи розряд — без активності це просто дублювало б прогноз
        # (диспетчер: "мало інформативно"). Сума = ціна × обсяг (₴/МВт·год ×
        # МВт × 1г) зі знаком: від'ємна — витрати на заряд, додатна — дохід
        # від розряду.
        price_uah = d.get("price_uah")
        if was_active and price_uah is not None:
            ws.cell(row=row, column=5, value=round(price_uah, 2)).number_format = "#,##0.00"
            suma = price_uah * power_mw
            ws.cell(row=row, column=6, value=round(suma, 2)).number_format = "+#,##0.00;-#,##0.00"
        else:
            ws.cell(row=row, column=5, value=None)
            ws.cell(row=row, column=6, value=None)

        ws.cell(row=row, column=7, value="так" if d.get("is_overridden") else "ні").alignment = Alignment(horizontal="center")
        ws.cell(row=row, column=8, value=round(charge_mw, 3)).number_format = "#,##0.000"
        ws.cell(row=row, column=9, value=round(discharge_mw, 3)).number_format = "#,##0.000"

    widths = [10, 20, 18, 22, 22, 14, 14, 12, 12]
    for col_idx, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = w

    last_row = header_row + 24
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)

    # Міні-графік заряду/розряду прямо в комірках крайніх правих колонок
    # (Excel Data Bars), а не окрема діаграма збоку — "стовпчик заряду
    # батареї" в самому стовпці, як і просив диспетчер.
    charge_range = f"H{header_row + 1}:H{last_row}"
    discharge_range = f"I{header_row + 1}:I{last_row}"
    charge_rule = DataBarRule(start_type="num", start_value=0, end_type="num", end_value=power_limit_mw, color="3B82F6", showValue=True)
    discharge_rule = DataBarRule(start_type="num", start_value=0, end_type="num", end_value=power_limit_mw, color="059669", showValue=True)
    ws.conditional_formatting.add(charge_range, charge_rule)
    ws.conditional_formatting.add(discharge_range, discharge_rule)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"smartbess_{date}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


MAX_EXPORT_FORECAST_PERIOD_DAYS = 92  # ~квартал — запобігає випадковому запиту на роки поспіль (сотні живих oree-фолбеків підряд)


@router.get("/export-forecast-period", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def export_forecast_period_excel(asset_id: str, start_date: str, end_date: str):
    """
    Погодинний Excel-звіт по прогнозу ціни ТА заявках РДН (сторінка
    "Neural Price Predictor") за ДОВІЛЬНИЙ період — той самий стиль/
    оформлення, що й export-day (заголовок, форматування, freeze panes,
    Data Bars на Заряд/Розряд — як у "Ручне коригування заявок (Manual
    Dispatch Schedule)"), але з можливістю вказати діапазон дат, а не
    одну добу.

    Один рядок на (дата, година) — до MAX_EXPORT_FORECAST_PERIOD_DAYS*24
    рядків. Факт ціни — та сама логіка, що й get_actual_prices на
    single-day звіті (спершу локальна БД, інакше живий запит до
    oree.com.ua, якщо доба вже минула) — для довгого періоду це означає
    послідовний виклик на кожну добу з неповним локальним покриттям, тому
    період свідомо обмежений (MAX_EXPORT_FORECAST_PERIOD_DAYS), щоб не
    перетворити один запит на сотні живих HTTP-викликів. P10/P90 —
    реальний conformal-калібрований інтервал моделі (`PriceForecast.lower_
    bound_uah`/`upper_bound_uah`), якщо порахований для цієї доби — інакше
    чесно порожньо, не вигадуємо. Заявки — реальні `MarketBid` (тип/обсяг/
    ціна/факт виконання/реалізований прибуток), не повторний розрахунок.

    Підсумковий WAPE рахується чесно лише по годинах, де є і прогноз, і
    факт — доба без жодного факту (ще не настала/не опублікована)
    відображається порожньою в звіті, а не нулями чи прогнозом замість
    факту.
    """
    try:
        start_dt = datetime.datetime.strptime(start_date, '%Y-%m-%d').date()
        end_dt = datetime.datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        raise HTTPException(status_code=400, detail="start_date/end_date мають бути у форматі YYYY-MM-DD")
    if end_dt < start_dt:
        raise HTTPException(status_code=400, detail="end_date не може бути раніше start_date")
    n_days = (end_dt - start_dt).days + 1
    if n_days > MAX_EXPORT_FORECAST_PERIOD_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Період завеликий ({n_days} діб) — максимум {MAX_EXPORT_FORECAST_PERIOD_DAYS} діб за один звіт.",
        )

    dates = [(start_dt + datetime.timedelta(days=i)).isoformat() for i in range(n_days)]

    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == asset_id).first()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        power_limit_mw = asset.power_mw

        rows_by_date = {}
        bids_by_date = {}
        for date_str in dates:
            target_dt = kyiv_to_utc(date_str, 0)
            forecasts = db.query(PriceForecast).filter(
                PriceForecast.forecast_run_at == target_dt
            ).order_by(PriceForecast.timestamp).all()
            rows_by_date[date_str] = {
                utc_to_kyiv(f.timestamp).hour: (f.predicted_price_uah, f.lower_bound_uah, f.upper_bound_uah)
                for f in forecasts
            }

            day_start, day_end = kyiv_day_bounds(date_str)
            bids = db.query(MarketBid).filter(
                MarketBid.asset_id == asset_id,
                MarketBid.timestamp >= day_start,
                MarketBid.timestamp < day_end,
            ).order_by(MarketBid.timestamp).all()
            bids_by_date[date_str] = {utc_to_kyiv(b.timestamp).hour: b for b in bids}
    finally:
        db.close()

    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.formatting.rule import DataBarRule

    wb = Workbook()
    ws = wb.active
    ws.title = "Прогноз і заявки"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F2937")
    title_font = Font(bold=True, size=13)

    ws["A1"] = f"SmartBESS EMS — Neural Price Predictor: прогноз і заявки за {start_date} — {end_date}"
    ws["A1"].font = title_font
    ws["A2"] = f"{n_days} діб, погодинно. Актив: {asset.name}"
    ws["A2"].font = Font(italic=True, color="6B7280")

    BID_TYPE_LABELS = {"buy": "Купівля", "sell": "Продаж", "standby": "Очікування"}
    headers = [
        "Дата", "Година", "Прогноз ціни, ₴/МВт·год", "P10 (нижня межа), ₴/МВт·год",
        "P90 (верхня межа), ₴/МВт·год", "Факт ціни, ₴/МВт·год", "Різниця Факт-Прогноз, ₴/МВт·год", "Похибка, %",
        "Тип заявки", "Ціна заявки, ₴/МВт·год", "Виконано",
        # 2026-08-28 (переглянуто): "Плановий прибуток" — гіпотеза "якби
        # ЗІГРАЛИ ВСІ заявки", тобто за РЕАЛЬНОЮ факт-ціною (не ціною заявки —
        # та лише визначає, чи виконається, аукціон єдиної ціни), для КОЖНОЇ
        # заявки незалежно від executed. Чиста енергія, БЕЗ витрат на
        # доставку і без деградації — обидві винесені в окремі колонки
        # (щоб не змішувати ринковий P&L з вартістю доставки/зносом).
        # "Реалізований прибуток" — реальний факт (0 для невиконаних,
        # витрати законно всередині, читається з bid.realized_profit_uah).
        # Порожньо в трьох гіпотетичних колонках, коли факт-ціни ще нема
        # (доба не звірена) — той самий принцип чесного NaN, що й колонка
        # "Факт ціни".
        "Плановий прибуток, ₴",
        # 2026-09-08: перейменовано з "Тарифи мережі, ₴" на прохання
        # користувача — та сама величина (TOTAL_TARIFFS_UAH_PER_MWH на
        # обсяг купівлі, 0 для продажу в arbitrage-режимі), лише зрозуміліша
        # назва. Деградація виділена в окрему колонку поруч (раніше взагалі
        # не показувалась у цьому звіті, хоча реально впливає на
        # "Реалізований прибуток" продажу так само, як витрати на доставку —
        # на купівлю).
        "Витрати на доставку, ₴", "Деградація, ₴", "Реалізований прибуток, ₴",
        # 2026-08-28: "Загальний дохід" — реальний P&L активу з урахуванням
        # ВДР-фолбеку (заявки, які не зіграли на РДН, але все одно потрібно
        # купити/продати на ВДР) — саме ця цифра, а не "Реалізований
        # прибуток" (0 для невиконаних на РДН), придатна для розрахунку
        # окупності. ВДР-частина — наближення (безперервні торги, не
        # аукціон єдиної ціни, MEMORY.md §8; реального виконання взагалі
        # немає, MockOreeClient) — "Джерело доходу" чесно позначає, звідки
        # число: РДН (факт) / ВДР (факт — реальна звірена середня ціна) /
        # ВДР (оцінка — ще не звірено з реальними даними) / не реалізовано.
        "Загальний дохід, ₴", "Джерело доходу", "Заряд, МВт", "Розряд, МВт",
    ]
    header_row = 4
    for col_idx, title in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", wrap_text=True)

    row = header_row
    abs_diff_sum = 0.0
    actual_sum = 0.0
    n_matched_hours = 0
    n_forecast_hours = 0
    n_actual_hours = 0
    for date_str in dates:
        forecast_by_hour = rows_by_date[date_str]
        bid_by_hour = bids_by_date[date_str]
        actual = await get_actual_prices(target_date=date_str)
        actual_by_hour = (
            dict(zip(actual["hours"], actual["actual_prices_uah"])) if actual.get("available") else {}
        )
        for hour in range(24):
            row += 1
            # Година підписана 1-24 (oree.com.ua "Година 1" = 00:00-01:00), як і export-day.
            ws.cell(row=row, column=1, value=date_str)
            ws.cell(row=row, column=2, value=hour + 1).number_format = "0"
            fc, lo, hi = forecast_by_hour.get(hour, (None, None, None))
            if fc is not None:
                n_forecast_hours += 1
            ws.cell(row=row, column=3, value=round(fc, 2) if fc is not None else None).number_format = "#,##0.00"
            ws.cell(row=row, column=4, value=round(lo, 2) if lo is not None else None).number_format = "#,##0.00"
            ws.cell(row=row, column=5, value=round(hi, 2) if hi is not None else None).number_format = "#,##0.00"
            ac = actual_by_hour.get(hour)
            if ac is not None:
                n_actual_hours += 1
            ws.cell(row=row, column=6, value=round(ac, 2) if ac is not None else None).number_format = "#,##0.00"
            if ac is not None and fc is not None:
                diff = ac - fc
                ws.cell(row=row, column=7, value=round(diff, 2)).number_format = "+#,##0.00;-#,##0.00"
                if ac != 0:
                    ws.cell(row=row, column=8, value=round(abs(diff) / abs(ac) * 100.0, 1)).number_format = "0.0"
                abs_diff_sum += abs(diff)
                actual_sum += abs(ac)
                n_matched_hours += 1
            else:
                ws.cell(row=row, column=7, value=None)
                ws.cell(row=row, column=8, value=None)

            bid = bid_by_hour.get(hour)
            if bid is not None:
                ws.cell(row=row, column=9, value=BID_TYPE_LABELS.get(bid.bid_type, bid.bid_type)).alignment = Alignment(horizontal="center")
                ws.cell(row=row, column=10, value=round(bid.bid_price_uah, 2)).number_format = "#,##0.00"
                if bid.executed is None:
                    executed_label = "очікує факту"
                else:
                    executed_label = "так" if bid.executed else "ні"
                ws.cell(row=row, column=11, value=executed_label).alignment = Alignment(horizontal="center")

                fin = _bid_hour_financials(bid, ac, asset)
                ws.cell(row=row, column=12, value=fin["planned_profit_uah"]).number_format = "+#,##0.00;-#,##0.00"
                ws.cell(row=row, column=13, value=fin["delivery_cost_uah"]).number_format = "+#,##0.00;-#,##0.00"
                ws.cell(row=row, column=14, value=fin["degradation_cost_uah"]).number_format = "+#,##0.00;-#,##0.00"
                ws.cell(row=row, column=15, value=fin["realized_profit_uah"]).number_format = "+#,##0.00;-#,##0.00"
                ws.cell(row=row, column=16, value=fin["total_income_uah"]).number_format = "+#,##0.00;-#,##0.00"
                ws.cell(row=row, column=17, value=fin["income_source"]).alignment = Alignment(horizontal="center")
                ws.cell(row=row, column=18, value=fin["charge_mw"]).number_format = "#,##0.000"
                ws.cell(row=row, column=19, value=fin["discharge_mw"]).number_format = "#,##0.000"
            else:
                for col_idx in range(9, 18):
                    ws.cell(row=row, column=col_idx, value=None)
                ws.cell(row=row, column=18, value=0.0).number_format = "#,##0.000"
                ws.cell(row=row, column=19, value=0.0).number_format = "#,##0.000"

    widths = [12, 10, 20, 20, 20, 16, 22, 12, 14, 18, 14, 18, 16, 16, 20, 18, 16, 12, 12]
    for col_idx, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = w

    last_row = row
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)

    # Data Bars на Заряд/Розряд — та сама ідіома, що й export-day/"Ручне
    # коригування заявок" (Optimization Schedule): "стовпчик заряду
    # батареї" прямо в комірці, не окрема діаграма збоку.
    charge_range = f"R{header_row + 1}:R{last_row}"
    discharge_range = f"S{header_row + 1}:S{last_row}"
    charge_rule = DataBarRule(start_type="num", start_value=0, end_type="num", end_value=power_limit_mw, color="3B82F6", showValue=True)
    discharge_rule = DataBarRule(start_type="num", start_value=0, end_type="num", end_value=power_limit_mw, color="059669", showValue=True)
    ws.conditional_formatting.add(charge_range, charge_rule)
    ws.conditional_formatting.add(discharge_range, discharge_rule)

    # Чесний підсумковий WAPE — лише по годинах, де реально є і прогноз, і
    # факт; якщо жодної такої години нема, явний діагностичний текст (чого
    # саме бракує — прогнозу чи факту), а не вигадана цифра чи однакове
    # для обох випадків "немає даних".
    summary_row = last_row + 2
    ws.cell(row=summary_row, column=1, value="Підсумковий WAPE за період:").font = Font(bold=True)
    if n_matched_hours > 0 and actual_sum > 0:
        wape = abs_diff_sum / actual_sum * 100.0
        ws.cell(row=summary_row, column=3, value=round(wape, 2)).number_format = "0.00"
        ws.cell(row=summary_row, column=4, value=f"({n_matched_hours} годин з фактом і прогнозом одночасно із {n_days * 24})").font = Font(italic=True, color="6B7280")
    elif n_forecast_hours == 0:
        msg = "немає розрахованого прогнозу за цей період" + (f" (факт є для {n_actual_hours} годин)" if n_actual_hours else "")
        ws.cell(row=summary_row, column=3, value=msg).font = Font(italic=True, color="6B7280")
    else:
        ws.cell(row=summary_row, column=3, value="немає фактичних даних за цей період").font = Font(italic=True, color="6B7280")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"smartbess_forecast_{start_date}_{end_date}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/day-bid-report", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_day_bid_report(asset_id: str, date: str):
    """
    Погодинний звіт прогноз+заявки за ОДНУ добу як JSON (не .xlsx) — та сама
    точка правди (_bid_hour_financials), що й export_forecast_period_excel
    (start_date==end_date для періоду в 1 добу дав би побайтово ті самі
    числа) — для живої таблиці "Ручне коригування заявок" на Optimization
    Schedule (2026-09-08), щоб показати ті самі поля, що й Excel-звіт за
    період, без завантаження файлу, і дати диспетчеру подати/підтвердити
    ВДР-фолбек прямо з цієї таблиці (ідм_* поля нижче).
    """
    try:
        datetime.datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="date має бути у форматі YYYY-MM-DD")

    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == asset_id).first()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        target_dt = kyiv_to_utc(date, 0)
        forecasts = db.query(PriceForecast).filter(
            PriceForecast.forecast_run_at == target_dt
        ).order_by(PriceForecast.timestamp).all()
        forecast_by_hour = {
            utc_to_kyiv(f.timestamp).hour: (f.predicted_price_uah, f.lower_bound_uah, f.upper_bound_uah)
            for f in forecasts
        }

        day_start, day_end = kyiv_day_bounds(date)
        bids = db.query(MarketBid).filter(
            MarketBid.asset_id == asset_id,
            MarketBid.timestamp >= day_start,
            MarketBid.timestamp < day_end,
        ).order_by(MarketBid.timestamp).all()
        bid_by_hour = {utc_to_kyiv(b.timestamp).hour: b for b in bids}
    finally:
        db.close()

    actual = await get_actual_prices(target_date=date)
    actual_by_hour = (
        dict(zip(actual["hours"], actual["actual_prices_uah"])) if actual.get("available") else {}
    )

    hours_out = []
    for hour in range(24):
        fc, lo, hi = forecast_by_hour.get(hour, (None, None, None))
        ac = actual_by_hour.get(hour)
        row = {
            "hour": hour,
            "forecast_price_uah": round(fc, 2) if fc is not None else None,
            "p10_uah": round(lo, 2) if lo is not None else None,
            "p90_uah": round(hi, 2) if hi is not None else None,
            "actual_price_uah": round(ac, 2) if ac is not None else None,
            "diff_uah": round(ac - fc, 2) if (ac is not None and fc is not None) else None,
            "error_pct": (
                round(abs(ac - fc) / abs(ac) * 100.0, 1)
                if (ac is not None and fc is not None and ac != 0) else None
            ),
        }
        bid = bid_by_hour.get(hour)
        if bid is not None:
            row["bid_type"] = bid.bid_type
            row["volume_kw"] = bid.volume_kw
            row["bid_price_uah"] = round(bid.bid_price_uah, 2)
            row["executed"] = bid.executed
            # ВДР-фолбек — ті самі поля, що вже повертає /bids, потрібні тут,
            # щоб таблиця "Ручне коригування заявок" могла подати/підтвердити
            # заявку на ВДР без окремого запиту до /bids.
            row["idm_fallback_suggested"] = bid.idm_fallback_suggested
            row["idm_fallback_price_uah"] = bid.idm_fallback_price_uah
            row["idm_fallback_price_is_actual"] = bid.idm_fallback_price_is_actual
            row["idm_external_order_id"] = bid.idm_external_order_id
            row["idm_fallback_acknowledged"] = bid.idm_fallback_acknowledged
            row["idm_bid_price_uah"] = bid.idm_bid_price_uah
            row.update(_bid_hour_financials(bid, ac, asset))
        else:
            row.update({
                "bid_type": None, "volume_kw": None, "bid_price_uah": None, "executed": None,
                "idm_fallback_suggested": False, "idm_fallback_price_uah": None,
                "idm_fallback_price_is_actual": None, "idm_external_order_id": None,
                "idm_fallback_acknowledged": None, "idm_bid_price_uah": None,
                "planned_profit_uah": None, "delivery_cost_uah": None, "degradation_cost_uah": None,
                "realized_profit_uah": None, "total_income_uah": None, "income_source": None,
                "charge_mw": 0.0, "discharge_mw": 0.0,
            })
        hours_out.append(row)

    return {"date": date, "asset_id": asset_id, "hours": hours_out}
