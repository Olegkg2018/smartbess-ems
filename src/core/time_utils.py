import datetime
from zoneinfo import ZoneInfo

import pandas as pd

KYIV_TZ = ZoneInfo("Europe/Kyiv")
UTC_TZ = datetime.timezone.utc


def kyiv_to_utc(date_str: str, hour: int) -> datetime.datetime:
    """
    Naive-UTC timestamp for the real Kyiv civil hour `hour` (0-23) of
    calendar date `date_str` (YYYY-MM-DD) — CLAUDE.md хронологія п.26/27:
    заміна старого патерну `pd.to_datetime(date_str) + timedelta(hours=h)`
    (scheduler.py/forecast_persistence.py), який тихо будував наївне-UTC
    значення замість справжнього київського — на 2-3г не те, що малось на
    увазі. DST-aware (zoneinfo), коректно обробляє переходи EET/EEST.
    """
    naive_kyiv = datetime.datetime.strptime(date_str, "%Y-%m-%d") + datetime.timedelta(hours=hour)
    aware_kyiv = naive_kyiv.replace(tzinfo=KYIV_TZ)
    return aware_kyiv.astimezone(UTC_TZ).replace(tzinfo=None)


def utc_to_kyiv(naive_utc: datetime.datetime) -> datetime.datetime:
    """Наївний UTC timestamp -> наївний київський timestamp. Лише для показу людині
    (дашборд/бід-лист) — не для зберігання, зберігаємо завжди в UTC."""
    aware_utc = naive_utc.replace(tzinfo=UTC_TZ)
    return aware_utc.astimezone(KYIV_TZ).replace(tzinfo=None)


def kyiv_day_bounds(date_str: str) -> tuple[datetime.datetime, datetime.datetime]:
    """
    [start, end) наївні-UTC межі РЕАЛЬНОЇ київської календарної доби
    `date_str` — заміна патерну `datetime.strptime(date_str, '%Y-%m-%d')` +
    `timedelta(days=1)`, який фактично різав змішане UTC-вікно (хвіст
    київської доби D + голова доби D+1), а не справжню торгову добу
    (CLAUDE.md хронологія п.26). DST-aware — на добу переходу EET/EEST
    коректно повертає вікно 23г або 25г, без додаткової логіки.
    """
    start = kyiv_to_utc(date_str, 0)
    next_date_str = (
        datetime.datetime.strptime(date_str, "%Y-%m-%d") + datetime.timedelta(days=1)
    ).strftime("%Y-%m-%d")
    end = kyiv_to_utc(next_date_str, 0)
    return start, end


def assert_naive_utc(df: pd.DataFrame, col: str = 'Datetime', source: str = '') -> pd.DataFrame:
    """
    Дешева межова перевірка проти регресії: увесь пайплайн (OREE/IDM,
    Open-Meteo, ENTSO-E) з 2026-08-21 приводиться до naive-UTC на межі
    джерела (див. docs/review_ml_forecast_pipeline_2026-08-21.md). Якщо
    колонка раптом tz-aware — це означає, що хтось пропустив конвертацію,
    і краще впасти голосно тут, ніж мовчки зсунути ціну/погоду на 2-3г
    при подальшому naive-merge.
    """
    if col not in df.columns or df.empty:
        return df
    dtype = df[col].dtype
    if getattr(dtype, 'tz', None) is not None:
        raise ValueError(
            f"assert_naive_utc: '{col}' is tz-aware ({dtype.tz}) in {source or 'unknown source'} — "
            f"expected naive UTC. Convert with .dt.tz_convert('UTC').dt.tz_localize(None) before returning."
        )
    return df
