"""
Вихідні push-сповіщення диспетчеру через Telegram Bot API (@BotFather-токен).

ВАЖЛИВО: цей модуль НЕ читає повідомлення з чужих каналів (Укренерго/
Міненерго) — для цього призначений telegram_public.py (публічний веб-скрейп
t.me/s/, без токена). Bot API дає доступ лише до чатів, де сам бот доданий
адміністратором, і не має методу для довільного читання історії каналу —
це принципове обмеження Bot API, а не недолік реалізації.

У бота вже є активний webhook на зовнішній сервіс користувача — цей модуль
його НЕ чіпає (не викликає setWebhook/deleteWebhook/getUpdates, які
конфліктують з активним webhook). Лише sendMessage, який із webhook не
конфліктує.
"""
import os
import json
import datetime
import requests

from src.core.config import settings

BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
CHAT_ID = settings.TELEGRAM_CHAT_ID
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
NOTIFIED_STATE_PATH = os.path.join(settings.DATA_DIR, "external_cache", "telegram_alerts_sent.json")


def is_configured() -> bool:
    return bool(BOT_TOKEN)


def send_notification(chat_id: str, text: str, parse_mode: str = None) -> dict:
    """Надсилає повідомлення в конкретний chat_id. Не кидає виняток при
    мережевій помилці — повертає {'ok': False, 'error': ...}, щоб не ламати
    основний потік (розрахунок прогнозу/оптимізації) через збій сповіщення."""
    if not BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN не налаштований"}
    if not chat_id:
        return {"ok": False, "error": "chat_id не вказано"}

    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        r = requests.post(f"{API_BASE}/sendMessage", json=payload, timeout=10)
        data = r.json()
        if not data.get("ok"):
            return {"ok": False, "error": data.get("description", "unknown error")}
        return {"ok": True, "message_id": data["result"]["message_id"]}
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}


def get_bot_info() -> dict:
    """getMe — не конфліктує з webhook, безпечно для перевірки токена."""
    if not BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN не налаштований"}
    try:
        r = requests.get(f"{API_BASE}/getMe", timeout=10)
        return r.json()
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}


def _load_sent_state() -> dict:
    if os.path.exists(NOTIFIED_STATE_PATH):
        try:
            with open(NOTIFIED_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_sent_state(state: dict):
    os.makedirs(os.path.dirname(NOTIFIED_STATE_PATH), exist_ok=True)
    with open(NOTIFIED_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def check_and_send_grid_stress_alert() -> dict:
    """
    Перевіряє сьогоднішній РЕАЛЬНИЙ grid-stress сигнал (keyword-сигнал з
    публічних постів Укренерго/Міненерго, telegram_public.daily_grid_stress_signal)
    і надсилає push-сповіщення диспетчеру, якщо виявлено high/medium ризик,
    про який ще не сповіщали сьогодні (щоб не дублювати той самий алерт при
    кожному запуску scheduler'а).
    """
    if not CHAT_ID:
        return {"sent": False, "reason": "TELEGRAM_CHAT_ID не налаштований"}

    import src.modules.external_data_service.telegram_public as ext_tg

    today_str = datetime.date.today().isoformat()
    stress = ext_tg.daily_grid_stress_signal()
    today_signal = stress.get(today_str)

    if not today_signal or not (today_signal.get("grid_stress_high") or today_signal.get("grid_stress_medium")):
        return {"sent": False, "reason": "Сьогодні реального сигналу про ризик не виявлено"}

    severity = "high" if today_signal.get("grid_stress_high") else "medium"

    sent_state = _load_sent_state()
    if sent_state.get(today_str) == severity:
        return {"sent": False, "reason": "Вже сповіщали про цей рівень сьогодні"}

    severity_label = "АВАРІЙНИЙ" if severity == "high" else "ПІДВИЩЕНИЙ"
    text = (
        f"⚠️ SmartBESS EMS — {severity_label} ризик для енергосистеми\n\n"
        f"Реальний сигнал з публічних постів Укренерго/Міненерго "
        f"({today_signal.get('mentions', 0)} згадувань сьогодні).\n"
        f"Перевірте екран «Стан енергосистеми» в Dispatcher Console."
    )
    result = send_notification(CHAT_ID, text)
    if result.get("ok"):
        sent_state[today_str] = severity
        _save_sent_state(sent_state)

    return {"sent": bool(result.get("ok")), "severity": severity, "detail": result}


def _bid_reminder_enabled() -> bool:
    """Читає прапорець з system_settings.json (SystemSettingsModel-патерн, як
    baseload_passthrough_ratio) — за замовчуванням True (нагадування корисне,
    диспетчер сам вимикає при потребі)."""
    path = os.path.join(settings.DATA_DIR, "system_settings.json")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return bool(json.load(f).get("bid_reminder_telegram_enabled", True))
        except Exception:
            pass
    return True


def check_and_send_bid_reminder() -> dict:
    """
    Читає ІСНУЮЧИЙ стан MarketBid на сьогодні і завтра через
    build_daily_action_summary (тільки читання — нічого не генерує/не
    звіряє сама, щоб не перетворитись на приховану автоматизацію подачі
    заявок, явно відкладену користувачем) і надсилає нагадування зі
    списком конкретних дій, якщо такі є.
    """
    if not CHAT_ID:
        return {"sent": False, "reason": "TELEGRAM_CHAT_ID не налаштований"}
    if not _bid_reminder_enabled():
        return {"sent": False, "reason": "Нагадування про заявки вимкнено в системних налаштуваннях"}

    from src.database.session import SessionLocal
    from src.database.models import Asset
    import src.modules.bidding_service.services as bidding

    db = SessionLocal()
    try:
        asset = db.query(Asset).first()
        if not asset:
            return {"sent": False, "reason": "Немає жодного активу (Asset) у системі"}
        today = datetime.datetime.combine(datetime.date.today(), datetime.time.min)
        tomorrow = today + datetime.timedelta(days=1)
        summary_tomorrow = bidding.build_daily_action_summary(db, asset, tomorrow)
        summary_today = bidding.build_daily_action_summary(db, asset, today)
    finally:
        db.close()

    lines = []
    actionable_tomorrow = [a for a in summary_tomorrow['actions'] if a['severity'] in ('action', 'warning')]
    actionable_today = [a for a in summary_today['actions'] if a['severity'] in ('action', 'warning')]
    if actionable_tomorrow:
        lines.append(f"📅 Заявка РДН на {tomorrow.date().isoformat()} (подати сьогодні до 12:00):")
        lines += [f"  • {a['text']}" for a in actionable_tomorrow]
    if actionable_today:
        lines.append(f"⚡ Сьогоднішні заявки ({today.date().isoformat()}):")
        lines += [f"  • {a['text']}" for a in actionable_today]

    if not lines:
        return {"sent": False, "reason": "Немає дій, що потребують уваги"}

    today_str = datetime.date.today().isoformat()
    sent_state = _load_sent_state()
    dedup_key = f"bid_reminder_{today_str}"
    # На відміну від check_and_send_grid_stress_alert (один severity/добу), список
    # дій може легітимно змінитись протягом дня (напр. після звірки
    # ~13:00 з'являється нова порада по ВДР) — тому дедуп за ЗМІСТОМ, а не
    # лише за датою, щоб не подавити нове реально нове повідомлення.
    content_signature = str(hash(tuple(lines)))
    if sent_state.get(dedup_key) == content_signature:
        return {"sent": False, "reason": "Вже надсилали цей самий набір дій сьогодні"}

    text = "🔔 SmartBESS EMS — дії по заявках РДН/ВДР\n\n" + "\n".join(lines)
    result = send_notification(CHAT_ID, text)
    if result.get("ok"):
        sent_state[dedup_key] = content_signature
        _save_sent_state(sent_state)
    return {"sent": bool(result.get("ok")), "detail": result}
