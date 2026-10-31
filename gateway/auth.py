"""Autentikasi dashboard berbasis sesi cookie."""
import hmac
import os
import secrets
import time
from pathlib import Path

from fastapi import Request

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
SESSION_COOKIE = "og_session"
SESSION_TTL = 86400
_SESSIONS: dict[str, float] = {}


def _settings() -> tuple[str, str]:
    values = {}
    try:
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return os.getenv("APP_USER", values.get("APP_USER", "admin")), os.getenv("APP_PASSWORD", values.get("APP_PASSWORD", ""))


def login(username: str, password: str) -> str | None:
    expected_user, expected_password = _settings()
    if not expected_password or not (hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_password)):
        return None
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = time.time() + SESSION_TTL
    return token


def logout(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE, "")
    _SESSIONS.pop(token, None)


def authenticated(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE, "")
    expires_at = _SESSIONS.get(token)
    if not expires_at:
        return False
    if expires_at <= time.time():
        _SESSIONS.pop(token, None)
        return False
    return True


def require_auth(request: Request) -> None:
    if not authenticated(request):
        from .errors import ApiError
        raise ApiError(401, "Silakan login terlebih dahulu.", error_type="authentication_error", code="unauthorized")
