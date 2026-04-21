"""
Минимальный HTTP-слой для деплоя (Render и др.).
Полный контракт API — docs/automation.md; реализация эндпоинтов добавляется по мере разработки.
"""

from __future__ import annotations

import base64
import json
import os
from uuid import UUID
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from cryptography.fernet import Fernet

app = FastAPI(
    title="lana-backend",
    version="0.0.3",
    description="Минимальный backend: health/root и Google OAuth callback с записью в БД.",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "lana",
        "health": "/health",
        "docs": "/docs",
    }


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise HTTPException(
            status_code=500,
            detail=f"Required env var is missing: {name}",
        )
    return value


def _extract_calendar_connection_id(state: str | None) -> UUID:
    """
    Извлекает calendar_connection_id из state.

    Поддерживаемые форматы:
    - просто UUID;
    - JSON-строка: {"calendar_connection_id":"..."} или {"calendarConnectionId":"..."}.
    - base64url(JSON) с теми же ключами.
    """
    if not state:
        raise HTTPException(
            status_code=400,
            detail="Missing required query parameter: state",
        )

    # 1) state = UUID
    try:
        return UUID(state)
    except ValueError:
        pass

    # 2) state = JSON
    try:
        parsed = json.loads(state)
        if isinstance(parsed, dict):
            raw_id = parsed.get("calendar_connection_id") or parsed.get("calendarConnectionId")
            if raw_id:
                return UUID(str(raw_id))
    except (json.JSONDecodeError, ValueError):
        pass

    # 3) state = base64url(JSON)
    try:
        padded = state + "=" * (-len(state) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        parsed = json.loads(decoded)
        if isinstance(parsed, dict):
            raw_id = parsed.get("calendar_connection_id") or parsed.get("calendarConnectionId")
            if raw_id:
                return UUID(str(raw_id))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        pass

    raise HTTPException(
        status_code=400,
        detail="Invalid state format: expected calendar_connection_id",
    )


def _encrypt_oauth_credentials(token_data: dict[str, object]) -> bytes:
    """
    Шифрует OAuth-данные перед записью в БД.

    Ожидаемый ключ: Fernet key (urlsafe-base64, 32 bytes) в OAUTH_CREDENTIALS_ENCRYPTION_KEY.
    """
    key = _required_env("OAUTH_CREDENTIALS_ENCRYPTION_KEY")
    try:
        f = Fernet(key.encode("utf-8"))
    except Exception as exc:  # pragma: no cover - формат ключа зависит от env
        raise HTTPException(
            status_code=500,
            detail="Invalid OAUTH_CREDENTIALS_ENCRYPTION_KEY format",
        ) from exc
    payload = json.dumps(token_data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f.encrypt(payload)


def _save_oauth_blob(calendar_connection_id: UUID, encrypted_blob: bytes) -> bool:
    """
    Сохраняет зашифрованный OAuth blob в calendar_connections.
    Возвращает True, если строка найдена и обновлена.
    """
    dsn = _required_env("DATABASE_URL")
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail="Missing dependency: psycopg") from exc

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update public.calendar_connections
                set google_oauth_credentials_encrypted = %s,
                    oauth_credentials_version = 1
                where id = %s
                returning id
                """,
                (encrypted_blob, str(calendar_connection_id)),
            )
            row = cur.fetchone()
    return row is not None


@app.get("/api/v1/google/oauth/callback")
def google_oauth_callback(code: str | None = None, error: str | None = None, state: str | None = None) -> JSONResponse:
    """
    Принимает code от Google OAuth и обменивает на токены.

    Важно: токены в открытом виде не возвращаются в ответе.
    """
    if error:
        return JSONResponse(
            status_code=400,
            content={
                "detail": "Google OAuth returned an error",
                "oauth_error": error,
                "state": state,
            },
        )
    if not code:
        return JSONResponse(
            status_code=400,
            content={"detail": "Missing required query parameter: code"},
        )
    try:
        calendar_connection_id = _extract_calendar_connection_id(state)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    payload = urlencode(
        {
            "code": code,
            "client_id": _required_env("GOOGLE_OAUTH_CLIENT_ID"),
            "client_secret": _required_env("GOOGLE_OAUTH_CLIENT_SECRET"),
            "redirect_uri": _required_env("GOOGLE_OAUTH_REDIRECT_URI"),
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    req = Request(
        url="https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            token_data = json.loads(raw)
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return JSONResponse(
            status_code=502,
            content={
                "detail": "Google token exchange failed",
                "google_status": e.code,
                "google_body": body[:500],
            },
        )
    except URLError as e:
        return JSONResponse(
            status_code=502,
            content={
                "detail": "Google token exchange network error",
                "error": str(e.reason),
            },
        )
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=502,
            content={"detail": "Google token exchange returned non-JSON response"},
        )

    try:
        encrypted_blob = _encrypt_oauth_credentials(token_data)
        saved = _save_oauth_blob(calendar_connection_id, encrypted_blob)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    except Exception as exc:  # pragma: no cover - аварийный барьер
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to persist OAuth credentials", "error": str(exc)[:300]},
        )
    if not saved:
        return JSONResponse(
            status_code=404,
            content={
                "detail": "calendar_connection not found for state",
                "calendar_connection_id": str(calendar_connection_id),
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "detail": "OAuth code exchanged successfully",
            "state_received": bool(state),
            "calendar_connection_id": str(calendar_connection_id),
            "refresh_token_received": bool(token_data.get("refresh_token")),
            "access_token_received": bool(token_data.get("access_token")),
            "id_token_received": bool(token_data.get("id_token")),
            "token_type": token_data.get("token_type"),
            "expires_in": token_data.get("expires_in"),
            "scope": token_data.get("scope"),
            "stored_in": "calendar_connections.google_oauth_credentials_encrypted",
            "oauth_credentials_version": 1,
        },
    )


def _port() -> int:
    raw = os.environ.get("PORT", "8000")
    try:
        return int(raw)
    except ValueError:
        return 8000


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=_port(),
    )
