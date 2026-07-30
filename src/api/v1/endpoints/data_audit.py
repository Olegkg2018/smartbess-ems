import os
import datetime
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from src.core.config import settings
from src.core.security import RoleChecker
from src.database.session import SessionLocal
from src.database.models import GridStressOverride, GenerationAdjustment, InitialSocOverride
import src.modules.external_data_service.telegram_public as ext_tg

router = APIRouter()

# Ознаки, які РЕАЛЬНО впливають на прогноз ціни (base-колонки historical_data_merged.csv,
# з яких у prepare_features рахуються лаги/похідні, що йдуть у FEATURES моделі —
# див. ml_pipeline.py). Решта колонок нижче — реально зібрані дані, які поки
# НЕ впливають на прогноз (короткий бектест/історія — див. коментарі там же).
USED_IN_FORECAST = {
    "Price", "IDM_Price", "DAM_IDM_Spread",
    "Temperature", "Cloud_Cover", "Wind_Speed", "Shortwave_Radiation",
    "Solar_Gen", "Wind_Gen", "Grid_Net_Export_MW",
}

COLUMN_GROUPS = {
    "market": ["Price", "IDM_Price", "DAM_IDM_Spread"],
    "weather_ua": ["Temperature", "Cloud_Cover", "Wind_Speed", "Shortwave_Radiation", "Solar_Gen", "Wind_Gen"],
    "grid_flow_eu": ["Grid_Net_Export_MW", "EU_DAM_Price_EUR_MWh"],
    "weather_neighbors": [
        "PL_Temperature", "PL_Cloud_Cover", "PL_Wind_Speed", "PL_Shortwave_Radiation",
        "RO_Temperature", "RO_Cloud_Cover", "RO_Wind_Speed", "RO_Shortwave_Radiation",
    ],
    "gas": ["Gas_Price_EUR_MWh"],
    "grid_stress_numeric": [
        "Grid_Stress_High", "Grid_Stress_Medium", "Telegram_Mentions",
        "Grid_Consumption_Trend", "Grid_Consumption_Deviation_Pct", "Grid_Peak_Deviation_Pct",
        "Grid_Forced_Restriction_Queues", "Grid_Settlements_Affected", "Grid_Oblasts_Affected",
    ],
}


@router.get("", dependencies=[Depends(RoleChecker(["Viewer", "Operator", "Manager", "Admin"]))])
async def get_data_audit(date: str):
    """
    Довідка "що реально було використано в розрахунку за цю дату" — для
    тестування/діагностики, коли є підозра, що якісь дані не приходять або
    неправильно розпізнаються. Показує по кожному джерелу: скільки з 24 годин
    реально заповнені (а не NaN), сирі значення, і — окремо для Telegram —
    буквально текст постів, які знайдені за цю дату, разом з тим, що з них
    автоматично витягнуто (parse_energy_status). Нічого не приховує і не
    згладжує: якщо джерело за день не дало жодного значення, це видно прямо.
    """
    try:
        target_date = datetime.datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="date має бути у форматі YYYY-MM-DD")

    result = {
        "date": date,
        "csv_covers_date": False,
        "groups": {},
        "telegram": {"posts": [], "daily_aggregate": None},
        "manual_overrides": {},
    }

    csv_path = os.path.join(settings.DATA_DIR, "historical_data_merged.csv")
    if os.path.exists(csv_path):
        all_cols = sorted({c for cols in COLUMN_GROUPS.values() for c in cols})
        # historical_data_merged.csv еволюціонує в часі (нові ознаки типу
        # неighbor weather/ГПВ додались пізніше) — старий файл може ще не
        # містити частину колонок. Читаємо лише ті, що реально є, решту
        # позначаємо як відсутні (0/24), а не падаємо 500-кою — саме такі
        # прогалини і є те, що ця довідка мусить чесно показати.
        header_cols = set(pd.read_csv(csv_path, nrows=0).columns)
        present_cols = [c for c in all_cols if c in header_cols]
        missing_cols = [c for c in all_cols if c not in header_cols]

        df = pd.read_csv(csv_path, usecols=["Datetime"] + present_cols)
        df["Datetime"] = pd.to_datetime(df["Datetime"])
        day_df = df[df["Datetime"].dt.date == target_date].sort_values("Datetime")

        result["csv_covers_date"] = not df.empty and df["Datetime"].min().date() <= target_date <= df["Datetime"].max().date()
        result["hours_found"] = len(day_df)
        if missing_cols:
            result["columns_missing_from_csv"] = missing_cols

        for group_name, cols in COLUMN_GROUPS.items():
            group_out = {}
            for col in cols:
                if col in missing_cols or day_df.empty:
                    series = pd.Series(dtype=float)
                else:
                    series = day_df[col]
                non_null = series.dropna()
                group_out[col] = {
                    "used_in_forecast": col in USED_IN_FORECAST,
                    "hours_with_data": int(non_null.shape[0]),
                    "hours_total": int(len(day_df)),
                    "min": float(non_null.min()) if not non_null.empty else None,
                    "max": float(non_null.max()) if not non_null.empty else None,
                    "mean": float(non_null.mean()) if not non_null.empty else None,
                    "hourly": [
                        {"hour": dt.strftime("%H:%M"), "value": (None if pd.isna(v) else float(v))}
                        for dt, v in zip(day_df["Datetime"], series)
                    ] if not day_df.empty else [],
                }
            result["groups"][group_name] = group_out
    else:
        result["error"] = "historical_data_merged.csv не знайдено — дані ще жодного разу не синхронізовані."

    # --- Telegram: буквально що прочитано за цю дату ---
    daily_signal = ext_tg.daily_grid_stress_signal()
    result["telegram"]["daily_aggregate"] = daily_signal.get(date)

    posts_today = []
    for ch in ext_tg.DEFAULT_CHANNELS:
        for post in ext_tg._load_cached_posts(ch):
            try:
                post_dt = datetime.datetime.fromisoformat(post["datetime"].replace("Z", "+00:00"))
            except Exception:
                continue
            if post_dt.date() != target_date:
                continue
            posts_today.append({
                "id": post["id"],
                "channel": ch,
                "datetime": post["datetime"],
                "text": post["text"],
                "severity": ext_tg._match_severity(post["text"]),
                "parsed": ext_tg.parse_energy_status(post["text"]),
            })
    posts_today.sort(key=lambda p: p["id"])
    result["telegram"]["posts"] = posts_today

    # --- Ручні поправки диспетчера за цю дату ---
    db = SessionLocal()
    try:
        target_dt = datetime.datetime.combine(target_date, datetime.time())

        gso = db.query(GridStressOverride).filter(GridStressOverride.date == target_dt).first()
        result["manual_overrides"]["grid_stress"] = (
            {"forced_restriction_queues": gso.forced_restriction_queues, "note": gso.note} if gso else None
        )

        ga = db.query(GenerationAdjustment).filter(GenerationAdjustment.date == target_dt).first()
        result["manual_overrides"]["generation_adjustment"] = (
            {
                "nuclear_pct": ga.nuclear_pct, "hydro_pct": ga.hydro_pct,
                "solar_pct": ga.solar_pct, "wind_pct": ga.wind_pct, "note": ga.note,
            } if ga else None
        )

        soc_rows = db.query(InitialSocOverride).filter(InitialSocOverride.date == target_dt).all()
        result["manual_overrides"]["initial_soc"] = (
            [{"asset_id": r.asset_id, "capacity_kwh": r.capacity_kwh} for r in soc_rows] if soc_rows else None
        )
    finally:
        db.close()

    return result
