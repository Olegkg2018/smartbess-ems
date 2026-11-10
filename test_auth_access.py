"""Два рівні доступу: повний (пароль FULL) і лише перегляд (пароль VIEW).

Запуск: pytest test_auth_access.py. TestClient без `with` — lifespan (БД,
планувальник) не стартує, перевіряється лише логіка авторизації/ролей.
"""
import pytest
from fastapi.testclient import TestClient

from app import app
from src.api.v1.endpoints import auth as auth_mod
from src.core.config import settings

FULL = "full-pass-test"
VIEW = "view-pass-test"


@pytest.fixture(autouse=True)
def passwords(monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_PASSWORD_FULL", FULL)
    monkeypatch.setattr(settings, "ACCESS_PASSWORD_VIEW", VIEW)
    monkeypatch.setattr(settings, "OIDC_MOCK_MODE", True)
    auth_mod._failed.clear()
    yield
    auth_mod._failed.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _token(client, role, password):
    r = client.post("/api/v1/auth/mock-login", json={"role": role, "password": password})
    return r


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def test_config_reports_password_required(client):
    assert client.get("/api/v1/auth/config").json() == {"password_required": True}


def test_login_levels(client):
    assert client.post("/api/v1/auth/login", json={"password": FULL}).json() == {"level": "full"}
    assert client.post("/api/v1/auth/login", json={"password": VIEW}).json() == {"level": "view"}


@pytest.mark.parametrize("pw", ["", "wrong", FULL + "x", None])
def test_login_rejects_bad_password(client, pw):
    r = client.post("/api/v1/auth/login", json={"password": pw} if pw is not None else {})
    assert r.status_code == 401
    assert "token" not in r.text


def test_full_password_gets_any_role(client):
    for role in ("Viewer", "Operator", "Manager", "Admin"):
        assert _token(client, role, FULL).status_code == 200


def test_view_password_only_viewer_token(client):
    assert _token(client, "Viewer", VIEW).status_code == 200
    for role in ("Operator", "Manager", "Admin"):
        assert _token(client, role, VIEW).status_code == 403


def test_mock_login_without_password_rejected(client):
    assert client.post("/api/v1/auth/mock-login", json={"role": "Admin"}).status_code == 401


def test_viewer_token_cannot_write(client):
    tok = _token(client, "Viewer", VIEW).json()["token"]
    h = _hdr(tok)
    writes = [
        ("post", "/api/v1/optimization/run", {}),
        ("post", "/api/v1/optimization/settings", {}),
        ("post", "/api/v1/optimization/manual-overrides", {}),
        ("post", "/api/v1/optimization/initial-soc", {}),
        ("delete", "/api/v1/optimization/initial-soc", None),
        ("post", "/api/v1/optimization/dispatcher-schedule", {}),
        ("post", "/api/v1/forecast/run", {}),
        ("post", "/api/v1/bids/margin", {}),
        ("delete", "/api/v1/bids/margin", None),
        ("post", "/api/v1/bids/generate", {}),
        ("post", "/api/v1/bids/settle", {}),
        ("post", "/api/v1/bids/idm-fallback/acknowledge", {}),
        ("post", "/api/v1/bids/idm-fallback/submit", {}),
        ("post", "/api/v1/bids/actual-settlement", {}),
        ("post", "/api/v1/price-shift", {}),
        ("post", "/api/v1/grid-stress", {}),
        ("delete", "/api/v1/grid-stress", None),
        ("post", "/api/v1/generation-adjustments", {}),
        ("post", "/api/v1/notifications/telegram/test", {}),
        ("post", "/api/retrain", {}),
    ]
    for method, url, body in writes:
        r = getattr(client, method)(url, headers=h, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, f"{method.upper()} {url} -> {r.status_code} (очікувався 403 для Viewer)"


def test_forged_or_missing_token_rejected(client):
    assert client.get("/api/v1/assets").status_code in (401, 403)
    assert client.get("/api/v1/assets", headers=_hdr("not.a.jwt")).status_code == 401


def test_rate_limit_blocks_after_failures(client):
    for _ in range(auth_mod.MAX_FAILED):
        assert client.post("/api/v1/auth/login", json={"password": "bad"}).status_code == 401
    # Далі блокується навіть правильний пароль
    assert client.post("/api/v1/auth/login", json={"password": FULL}).status_code == 429
    assert _token(client, "Viewer", VIEW).status_code == 429


def test_open_mode_when_full_password_unset(client, monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_PASSWORD_FULL", "")
    assert client.get("/api/v1/auth/config").json() == {"password_required": False}
    assert client.post("/api/v1/auth/mock-login", json={"role": "Admin"}).status_code == 200


def test_view_disabled_when_view_password_empty(client, monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_PASSWORD_VIEW", "")
    assert client.post("/api/v1/auth/login", json={"password": ""}).status_code == 401


def test_error_does_not_leak_passwords(client):
    r = client.post("/api/v1/auth/login", json={"password": "bad"})
    assert FULL not in r.text and VIEW not in r.text
