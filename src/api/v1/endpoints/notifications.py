from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from src.core.security import RoleChecker
import src.modules.external_data_service.telegram_bot as telegram_bot

router = APIRouter()


@router.get("/telegram/status", dependencies=[Depends(RoleChecker(["Admin", "Manager"]))])
async def telegram_status():
    """Перевірка токена (getMe) — не чіпає активний webhook користувача."""
    return {
        "configured": telegram_bot.is_configured(),
        "chat_id_configured": bool(telegram_bot.CHAT_ID),
        "bot_info": telegram_bot.get_bot_info(),
    }


class TestNotificationRequest(BaseModel):
    chat_id: Optional[str] = None
    text: Optional[str] = "🔋 SmartBESS EMS: тестове сповіщення. Якщо ви це бачите — інтеграція працює."


@router.post("/telegram/test", dependencies=[Depends(RoleChecker(["Admin", "Manager"]))])
async def telegram_test(req: TestNotificationRequest):
    """Ручна тестова відправка — chat_id можна передати напряму, поки TELEGRAM_CHAT_ID не налаштований в .env."""
    chat_id = req.chat_id or telegram_bot.CHAT_ID
    return telegram_bot.send_notification(chat_id, req.text)


@router.post("/telegram/check-grid-stress", dependencies=[Depends(RoleChecker(["Admin", "Manager", "Operator"]))])
async def telegram_check_grid_stress():
    """Ручний запуск тієї ж перевірки, що й у щоденному scheduler'і — не чекаючи 17:30."""
    return telegram_bot.check_and_send_grid_stress_alert()
