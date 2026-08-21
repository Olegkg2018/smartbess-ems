import datetime

from src.database.models import ForecastRun, ForecastRunHour


def persist_forecast_run(db, target_dt_start, selected_model, predicted_prices, price_band, trigger):
    """
    Append-only запис реального запуску прогнозу — на відміну від
    PriceForecast (яка й далі перезаписується щоразу, її контракт "поточний
    прогноз" не чіпаємо), тут generated_at_utc — справжній wall-clock момент
    розрахунку, і рядки ніколи не видаляються/не перезаписуються. Викликати
    ДОДАТКОВО до існуючого запису PriceForecast, в тій самій транзакції
    (перед db.commit() у виклику).
    """
    run = ForecastRun(
        target_date=target_dt_start,
        generated_at_utc=datetime.datetime.utcnow(),
        trigger=trigger,
        model_version=selected_model,
    )
    db.add(run)
    db.flush()  # потрібен run.id для FK нижче, до загального commit()

    for t in range(24):
        forecast_time = target_dt_start + datetime.timedelta(hours=t)
        db.add(ForecastRunHour(
            forecast_run_id=run.id,
            timestamp=forecast_time,
            predicted_price_uah=float(predicted_prices[t]),
            lower_bound_uah=float(price_band['lower_uah'][t]) if price_band else None,
            upper_bound_uah=float(price_band['upper_uah'][t]) if price_band else None,
        ))
    return run
