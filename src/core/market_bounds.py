"""
Єдине джерело ринкових меж ціни РДН/ВДР (2026-09-29, CLAUDE.md п.61).

Раніше в коді жили три різні межі: 10..50000 для заявок (правила OREE 2019),
10..16000 для прогнозу моделі, і окремий clip для оцінки ВДР. Реальні дані
(historical_data_merged.csv) показують: з серпня 2025 максимум і на РДН, і на
ВДР — рівно 15000 грн/МВт·год (до того — 9000), мінімум — 10. Тепер межі —
редаговане налаштування (Settings), дефолт = чинні 10 / 15000.

Закон 12087-д (market coupling): з 1 травня 2027 прайс-кепи на РДН/ВДР/БР
скасовуються, з'являться ВІД'ЄМНІ ціни — тоді достатньо змінити межі в
Settings (напр. -5000 / 50000), код не чіпати. Підлога < 0 дозволена.
"""
import json
import os

from src.core.config import settings

DEFAULT_PRICE_FLOOR_UAH = 10.0
DEFAULT_PRICE_CAP_UAH = 15000.0

_CACHE = {}


def get_market_price_bounds():
    """(floor, cap) грн/МВт·год з system_settings.json (кеш за mtime).
    Некоректна пара (floor >= cap) чесно ігнорується на користь дефолтів."""
    path = os.path.join(settings.DATA_DIR, "system_settings.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return DEFAULT_PRICE_FLOOR_UAH, DEFAULT_PRICE_CAP_UAH
    if _CACHE.get('mtime') == mtime:
        return _CACHE['bounds']
    floor, cap = DEFAULT_PRICE_FLOOR_UAH, DEFAULT_PRICE_CAP_UAH
    try:
        with open(path, "r") as f:
            saved = json.load(f)
        f_val = saved.get("market_price_floor_uah")
        c_val = saved.get("market_price_cap_uah")
        f_val = float(f_val) if f_val is not None else floor
        c_val = float(c_val) if c_val is not None else cap
        if f_val < c_val:
            floor, cap = f_val, c_val
    except Exception:
        pass
    _CACHE.update(mtime=mtime, bounds=(floor, cap))
    return floor, cap


def clip_price(value):
    floor, cap = get_market_price_bounds()
    return min(max(float(value), floor), cap)
