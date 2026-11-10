import hmac
import time
from threading import Lock
from typing import Literal, Optional

import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.core.config import settings

router = APIRouter()

UserRole = Literal["Viewer", "Operator", "Manager", "Admin"]
AccessLevel = Literal["full", "view"]

# Захист від добору пароля: MAX_FAILED невдалих спроб з однієї IP за
# WINDOW_SEC — блокування (429) до спливу вікна. In-memory: достатньо для
# одного процесу uvicorn; після рестарту лічильник скидається.
MAX_FAILED = 5
WINDOW_SEC = 300
_failed: dict[str, list[float]] = {}
_failed_lock = Lock()


class LoginRequest(BaseModel):
    password: str = Field(default="", max_length=256)


class MockLoginRequest(BaseModel):
    role: UserRole
    password: Optional[str] = Field(default=None, max_length=256)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.time()
    with _failed_lock:
        recent = [t for t in _failed.get(ip, []) if now - t < WINDOW_SEC]
        _failed[ip] = recent
        if len(recent) >= MAX_FAILED:
            raise HTTPException(status_code=429, detail="Забагато невдалих спроб входу. Спробуйте пізніше.")


def _register_failure(ip: str) -> None:
    with _failed_lock:
        _failed.setdefault(ip, []).append(time.time())


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def resolve_access_level(password: Optional[str]) -> Optional[AccessLevel]:
    """Повертає рівень доступу за паролем або None, якщо пароль невірний.

    ACCESS_PASSWORD_FULL порожній → вхід відкритий (як було раніше) і всім
    видається 'full'. Якщо FULL заданий, але VIEW порожній — режим перегляду
    вимкнений (порожній пароль ніколи не збігається).
    """
    full = settings.ACCESS_PASSWORD_FULL
    view = settings.ACCESS_PASSWORD_VIEW
    if not full:
        return "full"
    pw = password or ""
    if pw and _same(pw, full):
        return "full"
    if view and pw and _same(pw, view):
        return "view"
    return None


def _issue_token(role: str) -> str:
    payload = {
        "preferred_username": f"{role.lower()}@smartbess.ua",
        "roles": [role],
        "realm_access": {"roles": [role]},
        "resource_access": {"smartbess-platform": {"roles": [role]}},
    }
    return jwt.encode(payload, settings.MOCK_JWT_SECRET, algorithm="HS256")


@router.get("/config")
def auth_config():
    """Чи потрібен пароль (фронтенд показує екран входу)."""
    return {"password_required": bool(settings.ACCESS_PASSWORD_FULL)}


@router.post("/login")
def login(req: LoginRequest, request: Request):
    """Перевірка пароля → рівень доступу. Токен фронтенд отримує окремо
    через /mock-login (з тим самим паролем), тут лише валідація входу."""
    if not settings.OIDC_MOCK_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    ip = _client_ip(request)
    _check_rate_limit(ip)
    level = resolve_access_level(req.password)
    if level is None:
        _register_failure(ip)
        raise HTTPException(status_code=401, detail="Невірний пароль")
    return {"level": level}


@router.post("/mock-login")
def mock_login(req: MockLoginRequest, request: Request):
    """
    Видає підписаний mock-JWT для обраної ролі. Існує лише в OIDC_MOCK_MODE —
    це заміна старої схеми, де фронтенд сам збирав alg:none токен (будь-хто
    міг підробити роль через curl без жодного запиту до backend). Підпис
    MOCK_JWT_SECRET ніколи не потрапляє в браузер, тому підробка токена поза
    цим ендпоінтом більше неможлива.

    Пароль (ACCESS_PASSWORD_FULL/VIEW): повний пароль дозволяє будь-яку роль,
    пароль перегляду — лише Viewer (запис на сервері заборонений для Viewer
    у RoleChecker усіх змінюючих ендпоінтів).
    """
    if not settings.OIDC_MOCK_MODE:
        raise HTTPException(status_code=404, detail="Not found")

    ip = _client_ip(request)
    _check_rate_limit(ip)
    level = resolve_access_level(req.password)
    if level is None:
        _register_failure(ip)
        raise HTTPException(status_code=401, detail="Невірний пароль")
    if level == "view" and req.role != "Viewer":
        _register_failure(ip)
        raise HTTPException(status_code=403, detail="Цей пароль дає доступ лише на перегляд")

    return {"token": _issue_token(req.role)}
