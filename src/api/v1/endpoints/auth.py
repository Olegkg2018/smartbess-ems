import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal

from src.core.config import settings

router = APIRouter()

UserRole = Literal["Viewer", "Operator", "Manager", "Admin"]


class MockLoginRequest(BaseModel):
    role: UserRole


@router.post("/mock-login")
async def mock_login(req: MockLoginRequest):
    """
    Видає підписаний mock-JWT для обраної ролі. Існує лише в OIDC_MOCK_MODE —
    це заміна старої схеми, де фронтенд сам збирав alg:none токен (будь-хто
    міг підробити роль через curl без жодного запиту до backend). Підпис
    MOCK_JWT_SECRET ніколи не потрапляє в браузер, тому підробка токена поза
    цим ендпоінтом більше неможлива — хоча сам логін і далі не питає пароль
    (canned demo-акаунт на роль), це свідомий компроміс без реального IdP.
    """
    if not settings.OIDC_MOCK_MODE:
        raise HTTPException(status_code=404, detail="Not found")

    payload = {
        "preferred_username": f"{req.role.lower()}@smartbess.ua",
        "roles": [req.role],
        "realm_access": {"roles": [req.role]},
        "resource_access": {"smartbess-platform": {"roles": [req.role]}},
    }
    token = jwt.encode(payload, settings.MOCK_JWT_SECRET, algorithm="HS256")
    return {"token": token}
