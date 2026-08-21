import pandas as pd


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
