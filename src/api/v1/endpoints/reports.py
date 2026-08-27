import datetime
import io
from fastapi import APIRouter, HTTPException, Query, Depends, Response
from sqlalchemy.orm import Session
from sqlalchemy import func

from src.database.session import SessionLocal
from src.database.models import Asset, ChargeDischargePlan, BessTelemetry, PriceForecast
from src.modules.reporting_service.services import ReportingService
from src.modules.reporting_service.forecast_accuracy import compute_rolling_accuracy, get_profit_capture_ratio
from src.core.security import RoleChecker
from src.api.v1.endpoints.optimization import get_manual_overrides
from src.api.v1.endpoints.forecast import get_actual_prices
from src.core.time_utils import kyiv_to_utc, utc_to_kyiv

router = APIRouter()

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
async def export_forecast_period_excel(start_date: str, end_date: str):
    """
    Погодинний Excel-звіт по прогнозу ціни (сторінка "Neural Price
    Predictor") за ДОВІЛЬНИЙ період — та сама якість/стиль, що
    export-day (заголовок, форматування, freeze panes), але без
    диспетчерського заряду/розряду (це не про BESS-диспетчеризацію, а
    про сам прогноз) і з можливістю вказати діапазон дат, а не одну добу.

    Один рядок на (дата, година) — до MAX_EXPORT_FORECAST_PERIOD_DAYS*24
    рядків. Факт ціни — та сама логіка, що й get_actual_prices на
    single-day звіті (спершу локальна БД, інакше живий запит до
    oree.com.ua, якщо доба вже минула) — для довгого періоду це означає
    послідовний виклик на кожну добу з неповним локальним покриттям, тому
    період свідомо обмежений (MAX_EXPORT_FORECAST_PERIOD_DAYS), щоб не
    перетворити один запит на сотні живих HTTP-викликів. P10/P90 —
    реальний conformal-калібрований інтервал моделі (`PriceForecast.lower_
    bound_uah`/`upper_bound_uah`), якщо порахований для цієї доби — інакше
    чесно порожньо, не вигадуємо.

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
        rows_by_date = {}
        for date_str in dates:
            target_dt = kyiv_to_utc(date_str, 0)
            forecasts = db.query(PriceForecast).filter(
                PriceForecast.forecast_run_at == target_dt
            ).order_by(PriceForecast.timestamp).all()
            rows_by_date[date_str] = {
                utc_to_kyiv(f.timestamp).hour: (f.predicted_price_uah, f.lower_bound_uah, f.upper_bound_uah)
                for f in forecasts
            }
    finally:
        db.close()

    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Прогноз ціни"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F2937")
    title_font = Font(bold=True, size=13)

    ws["A1"] = f"SmartBESS EMS — Neural Price Predictor: прогноз ціни за {start_date} — {end_date}"
    ws["A1"].font = title_font
    ws["A2"] = f"{n_days} діб, погодинно"
    ws["A2"].font = Font(italic=True, color="6B7280")

    headers = [
        "Дата", "Година", "Прогноз ціни, ₴/МВт·год", "P10 (нижня межа), ₴/МВт·год",
        "P90 (верхня межа), ₴/МВт·год", "Факт ціни, ₴/МВт·год", "Різниця Факт-Прогноз, ₴/МВт·год", "Похибка, %",
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

    widths = [12, 10, 22, 22, 22, 18, 24, 12]
    for col_idx, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = w

    last_row = row
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)

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
