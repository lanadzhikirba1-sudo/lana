"""
Минимальный HTTP-слой для деплоя (Render и др.).
Полный контракт API — docs/automation.md; реализация эндпоинтов добавляется по мере разработки.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from cryptography.fernet import Fernet


def _merge_repo_dotenv() -> None:
    """
    Загружает переменные из .env рядом с server.py в os.environ.
    Нужен для локального запуска без `source .env`.
    """
    dot = Path(__file__).resolve().parent / ".env"
    if not dot.is_file():
        return
    for raw in dot.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, rest = line.partition("=")
        k = k.strip()
        v = rest.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        os.environ[k] = v


_merge_repo_dotenv()

app = FastAPI(
    title="lana-backend",
    version="0.0.4",
    description="Минимальный backend: health/root + Google OAuth URL/callback с записью в БД.",
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


def _build_state(calendar_connection_id: UUID, therapist_id: UUID) -> str:
    payload = {
        "calendar_connection_id": str(calendar_connection_id),
        "therapist_id": str(therapist_id),
        "ts": datetime.now(UTC).isoformat(),
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    # base64url без "=" в конце (нормальный формат для state)
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _assert_bot_auth(authorization: str | None, x_bot_api_token: str | None) -> None:
    expected = _required_env("BOT_CONSTRUCTOR_SECRET")
    got = _extract_bearer_token(authorization) or (x_bot_api_token or "").strip()
    if not got or got != expected:
        raise HTTPException(status_code=401, detail="Unauthorized bot token")


def _get_or_create_calendar_connection(therapist_id: UUID, calendar_id: str) -> UUID | None:
    dsn = _required_env("DATABASE_URL")
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail="Missing dependency: psycopg") from exc

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id
                from public.calendar_connections
                where therapist_id = %s
                  and calendar_id = %s
                limit 1
                """,
                (str(therapist_id), calendar_id),
            )
            row = cur.fetchone()
            if row:
                return UUID(str(row[0]))

            # Проверяем, что терапевт существует
            cur.execute("select id from public.therapists where id = %s limit 1", (str(therapist_id),))
            therapist = cur.fetchone()
            if not therapist:
                return None

            cur.execute(
                """
                insert into public.calendar_connections (therapist_id, calendar_id)
                values (%s, %s)
                returning id
                """,
                (str(therapist_id), calendar_id),
            )
            created = cur.fetchone()
            if not created:
                return None
            return UUID(str(created[0]))


@app.post("/api/v1/bot/therapists/{therapist_id}/google/oauth-url")
def create_google_oauth_url(
    therapist_id: UUID,
    authorization: str | None = Header(default=None),
    x_bot_api_token: str | None = Header(default=None),
) -> JSONResponse:
    """
    Выдаёт Google OAuth URL и state (формат из docs/automation.md §9.2).
    """
    _assert_bot_auth(authorization, x_bot_api_token)

    calendar_id = "primary"
    calendar_connection_id = _get_or_create_calendar_connection(therapist_id, calendar_id)
    if not calendar_connection_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Therapist not found", "therapist_id": str(therapist_id)},
        )

    state = _build_state(calendar_connection_id, therapist_id)
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urlencode(
            {
                "client_id": _required_env("GOOGLE_OAUTH_CLIENT_ID"),
                "redirect_uri": _required_env("GOOGLE_OAUTH_REDIRECT_URI"),
                "response_type": "code",
                "scope": "https://www.googleapis.com/auth/calendar",
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
                "state": state,
            }
        )
    )
    return JSONResponse(
        status_code=200,
        content={
            "auth_url": auth_url,
            "state": state,
            "calendar_connection_id": str(calendar_connection_id),
            "calendar_id": calendar_id,
        },
    )


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
