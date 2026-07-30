"""
Реальні пости з публічних Telegram-каналів (веб-перегляд t.me/s/<channel>,
без Telegram API ключів). Підтверджено розвідкою (scratch/recon_telegram.py):
канал Укренерго реально віддає пости, включно з регулярними "СТАН
ЕНЕРГОСИСТЕМИ" — це найближчий відкритий проксі-сигнал для аварійних
обмежень/ударів по генерації, який раніше вигадувався (Nuclear_Outage,
Solar_Strike) як випадковий шум.

Обмеження, які треба чесно визнати:
- t.me/s/ віддає лише останні пости (пагінація назад через ?before=<id>
  працює, але вглиб на роки йти дорого і канал може існувати не з 2021 року).
- Це keyword-сигнал, а не точна MW-величина втраченої генерації — грубіше за
  ENTSO-E, але реальне, а не вигадане.
"""
import os
import re
import json
import time
import datetime
import requests

from src.core.config import settings

DATA_DIR = settings.DATA_DIR
CACHE_DIR = os.path.join(DATA_DIR, "external_cache", "telegram")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

DEFAULT_CHANNELS = ["ukrenergo", "minenergo_ua"]

KEYWORDS_HIGH = [
    "аварійн", "обстріл", "атак", "ракетн", "дрон", "пошкодж",
    "знеструмлен", "надзвичайн",
]
KEYWORDS_MEDIUM = [
    "обмеження", "графік", "відключен", "дефіцит потужності", "перевантажен",
]

# --- Числовий сигнал зі структурованого блоку "СТАН ЕНЕРГОСИСТЕМИ"/"СПОЖИВАННЯ" -
# Укренерго регулярно публікує (перевірено на реальних кешованих постах, не
# гіпотетично) блок з трьома стабільними пунктами (тренд споживання,
# кількість знеструмлених населених пунктів, вікно рекомендованого/
# примусового обмеження) і розгорнутий блок "СПОЖИВАННЯ" з точними % і, коли
# вводяться примусові погодинні відключення, обсягом у "чергах"
# (напр. "обсягом від 0,5 до 1 черги") — це вже НЕ грубий keyword-прапорець,
# а справжня величина. Регекси побудовані на реальних зразках з
# data/external_cache/telegram/ukrenergo.jsonl, не вигадані.
_TREND_UP_RE = re.compile(r"споживання[^.]*(зрос|зростає|зростати|високому рівні)", re.I)
_TREND_DOWN_RE = re.compile(r"споживання[^.]*(знижен|спада)", re.I)
_TREND_FLAT_RE = re.compile(r"споживання[^.]*сезонним показникам", re.I)

_SAME_TIME_PCT_RE = re.compile(
    r"воно було на\s+([\d,]+)\s*%,?\s*(нижчим|вищим)", re.I
)
_PEAK_PCT_RE = re.compile(
    r"[Вв]ін був на\s+([\d,]+)\s*%\s*(нижчим|вищим)", re.I
)
_QUEUES_RANGE_RE = re.compile(
    r"обсягом\s+(?:від\s+([\d,]+)\s+до\s+)?([\d,]+)\s+черг", re.I
)
_SETTLEMENTS_RE = re.compile(
    r"(близько\s+)?(понад\s+)?(\d+)\s+населен", re.I
)
_OBLAST_WORD_TO_NUM = {
    "одній": 1, "двох": 2, "трьох": 3, "чотирьох": 4, "п'яти": 5, "п’яти": 5,
    "шести": 6, "семи": 7, "восьми": 8, "дев'яти": 9, "дев’яти": 9, "десяти": 10,
}
_OBLASTS_RE = re.compile(
    r"у\s+(" + "|".join(_OBLAST_WORD_TO_NUM.keys()) + r")\s+област", re.I
)


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def parse_energy_status(text: str) -> dict:
    """
    Витягує числовий сигнал з посту "СТАН ЕНЕРГОСИСТЕМИ"/"СПОЖИВАННЯ". Чесно
    повертає None для полів, яких у конкретному пості немає (не підмінює
    нулем чи вигаданим значенням) — пости без цього блоку (новини, історії
    співробітників) дадуть усі None.
    """
    result = {
        "consumption_trend": None,
        "same_time_deviation_pct": None,
        "peak_deviation_pct": None,
        "forced_restriction_queues": None,
        "settlements_affected": None,
        "oblasts_affected": None,
    }

    if _TREND_UP_RE.search(text):
        result["consumption_trend"] = 1
    elif _TREND_DOWN_RE.search(text):
        result["consumption_trend"] = -1
    elif _TREND_FLAT_RE.search(text):
        result["consumption_trend"] = 0

    m = _SAME_TIME_PCT_RE.search(text)
    if m:
        val = _to_float(m.group(1))
        result["same_time_deviation_pct"] = val if m.group(2) == "вищим" else -val

    m = _PEAK_PCT_RE.search(text)
    if m:
        val = _to_float(m.group(1))
        result["peak_deviation_pct"] = val if m.group(2) == "вищим" else -val

    m = _QUEUES_RANGE_RE.search(text)
    if m:
        low = _to_float(m.group(1)) if m.group(1) else None
        high = _to_float(m.group(2))
        result["forced_restriction_queues"] = (low + high) / 2.0 if low is not None else high

    m = _SETTLEMENTS_RE.search(text)
    if m:
        result["settlements_affected"] = int(m.group(3))

    m = _OBLASTS_RE.search(text)
    if m:
        result["oblasts_affected"] = _OBLAST_WORD_TO_NUM[m.group(1).lower()]

    return result

_MSG_RE = re.compile(
    r'data-post="[^"]*?/(\d+)".*?datetime="([^"]+)".*?'
    r'tgme_widget_message_text[^>]*>(.*?)</div>',
    re.S,
)


def _fetch_page(channel: str, before_id: int = None) -> str:
    url = f"https://t.me/s/{channel}"
    if before_id:
        url += f"?before={before_id}"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return r.text
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    return ""


def _strip_tags(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text


def _parse_posts(html: str) -> list:
    posts = []
    for m in _MSG_RE.finditer(html):
        post_id, dt_str, text_html = m.groups()
        posts.append({
            "id": int(post_id),
            "datetime": dt_str,
            "text": _strip_tags(text_html),
        })
    return posts


def _cache_path(channel: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{channel}.jsonl")


def _load_cached_posts(channel: str) -> list:
    path = _cache_path(channel)
    if not os.path.exists(path):
        return []
    posts = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    posts.append(json.loads(line))
                except Exception:
                    pass
    return posts


def sync_channel(channel: str, max_pages: int = 5) -> int:
    """
    Дотягує нові пости каналу (від найновішого назад через ?before=) і дописує
    їх у постійний JSONL-кеш. Повертає кількість НОВИХ постів. Викликається
    щоденно з планувальника — з часом накопичує реальну історію каналу.
    """
    existing = _load_cached_posts(channel)
    known_ids = {p["id"] for p in existing}

    new_posts = []
    before_id = None
    for _ in range(max_pages):
        html = _fetch_page(channel, before_id)
        if not html:
            break
        page_posts = _parse_posts(html)
        if not page_posts:
            break

        fresh = [p for p in page_posts if p["id"] not in known_ids]
        new_posts.extend(fresh)

        oldest_id = min(p["id"] for p in page_posts)
        if oldest_id in known_ids or not fresh:
            break
        before_id = oldest_id
        time.sleep(1.0)

    if new_posts:
        path = _cache_path(channel)
        with open(path, "a", encoding="utf-8") as f:
            for p in new_posts:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")

    return len(new_posts)


def sync_all(channels=None, max_pages: int = 5) -> dict:
    channels = channels or DEFAULT_CHANNELS
    return {ch: sync_channel(ch, max_pages=max_pages) for ch in channels}


def _match_severity(text: str) -> str:
    lower = text.lower()
    if any(kw in lower for kw in KEYWORDS_HIGH):
        return "high"
    if any(kw in lower for kw in KEYWORDS_MEDIUM):
        return "medium"
    return "none"


def daily_grid_stress_signal(channels=None) -> dict:
    """
    Агрегує кешовані пости всіх каналів у щоденний сигнал:
    {date_str: {'grid_stress_high': 0/1, 'grid_stress_medium': 0/1, 'mentions': N,
    'consumption_trend', 'same_time_deviation_pct', 'peak_deviation_pct',
    'forced_restriction_queues', 'settlements_affected', 'oblasts_affected'}}

    grid_stress_high/medium/mentions — грубий keyword-скан (як і раніше, по
    ВСЬОМУ тексту, включно з блоком про обстріли). Нові поля —
    parse_energy_status: точні числа зі структурованого блоку
    СТАН ЕНЕРГОСИСТЕМИ/СПОЖИВАННЯ. День агрегується "останній пост дня
    виграє" для trend/pct/queues (це знімок стану на момент публікації, не
    накопичувальна сума), а settlements/oblasts — максимум за день (гірший
    зафіксований масштаб пошкоджень). Пости без числового поля лишають його
    None — не підміняються нулем.
    """
    channels = channels or DEFAULT_CHANNELS
    daily = {}
    posts_by_day = {}
    for ch in channels:
        for post in _load_cached_posts(ch):
            try:
                dt = datetime.datetime.fromisoformat(post["datetime"].replace("Z", "+00:00"))
            except Exception:
                continue
            date_str = dt.date().isoformat()
            severity = _match_severity(post["text"])
            entry = daily.setdefault(date_str, {"grid_stress_high": 0, "grid_stress_medium": 0, "mentions": 0})
            entry["mentions"] += 1
            if severity == "high":
                entry["grid_stress_high"] = 1
            elif severity == "medium":
                entry["grid_stress_medium"] = 1

            posts_by_day.setdefault(date_str, []).append((post["id"], post["text"]))

    for date_str, posts in posts_by_day.items():
        posts.sort(key=lambda p: p[0])
        entry = daily[date_str]
        entry["consumption_trend"] = None
        entry["same_time_deviation_pct"] = None
        entry["peak_deviation_pct"] = None
        entry["forced_restriction_queues"] = None
        entry["settlements_affected"] = None
        entry["oblasts_affected"] = None
        for _post_id, text in posts:
            parsed = parse_energy_status(text)
            for key in ("consumption_trend", "same_time_deviation_pct", "peak_deviation_pct", "forced_restriction_queues"):
                if parsed[key] is not None:
                    entry[key] = parsed[key]
            for key in ("settlements_affected", "oblasts_affected"):
                if parsed[key] is not None:
                    entry[key] = max(parsed[key], entry[key]) if entry[key] is not None else parsed[key]

    return daily


def get_latest_posts(channels=None, n: int = 3) -> list:
    """Останні N реальних кешованих постів (усіх каналів разом), найновіші перші —
    для панелі диспетчера "Стан енергосистеми"."""
    channels = channels or DEFAULT_CHANNELS
    all_posts = []
    for ch in channels:
        for post in _load_cached_posts(ch):
            all_posts.append({**post, "channel": ch, "severity": _match_severity(post["text"])})
    all_posts.sort(key=lambda p: p["id"], reverse=True)
    return all_posts[:n]
