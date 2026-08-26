"""
Абстракція клієнта подачі заявки на біржу (OREE) — "віртуальний диспетчер",
2026-08-26. OREE не публікує власної API-документації (дослідження
2026-08-06, MEMORY.md §8): служба підтримки покриває лише браузер/Excel-
подачу й проблеми з КЕП. Технічна платформа OREE — XMTRADE|PXS, брендована
реалізація вендора XMtrade/ISOT, який також постачає платформу словацькому
оператору OKTE — той публічно документує REST+WebSocket API
(okte.sk/en/api-documentation/), єдиний реальний орієнтир для форми
інтерфейсу нижче.

MockOreeClient — за замовчуванням і єдина реалізація, яка зараз реально
працює. Явно НЕ підключена до жодного реального ендпоінту — генерує
синтетичний order_id, щоб решта циклу (звірка, аудит, UI) могла
будуватись і тестуватись вже зараз, до появи реального доступу до API.
"""
import uuid
import datetime

from src.core.config import settings


class MockOreeClient:
    """Емуляція подачі заявки. НЕ підключено до жодного реального
    ендпоінту OREE — синтетичний order_id, статус завжди 'accepted'."""

    def submit_bid(self, bid) -> dict:
        return {
            'external_order_id': f"MOCK-{uuid.uuid4().hex[:12]}",
            'status': 'accepted',
            'submitted_at': datetime.datetime.utcnow(),
        }


def get_oree_client():
    """settings.OREE_CLIENT_MODE — 'mock' (дефолт, єдиний реально готовий
    режим) або 'live' (реальний API OREE — недоступний, немає ні
    документації, ні облікових даних, MEMORY.md §8; NotImplementedError
    чесно, а не вдавана готовність)."""
    mode = settings.OREE_CLIENT_MODE
    if mode == 'mock':
        return MockOreeClient()
    if mode == 'live':
        raise NotImplementedError(
            "OREE_CLIENT_MODE='live' — реальний API OREE ще не підключено "
            "(немає публічної документації й облікових даних, MEMORY.md §8)."
        )
    raise ValueError(f"Unknown OREE_CLIENT_MODE: {mode!r}")
