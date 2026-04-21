"""
Минимальный HTTP-слой для деплоя (Render и др.).
Полный контракт API — docs/automation.md; реализация эндпоинтов добавляется по мере разработки.
"""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(
    title="lana-backend",
    version="0.0.2",
    description="Минимальный backend: health/root и Google OAuth callback code->token.",
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


@app.get("/api/v1/google/oauth/callback")
def google_oauth_callback(code: str | None = None, error: str | None = None, state: str | None = None) -> JSONResponse:
    """
    Принимает code от Google OAuth и обменивает на токены.

    Важно: в v0 ответ не содержит сами токены; только факт успешного обмена.
    Сохранение в БД и шифрование добавляются отдельным шагом.
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

    return JSONResponse(
        status_code=200,
        content={
            "detail": "OAuth code exchanged successfully",
            "state": state,
            "refresh_token_received": bool(token_data.get("refresh_token")),
            "access_token_received": bool(token_data.get("access_token")),
            "id_token_received": bool(token_data.get("id_token")),
            "token_type": token_data.get("token_type"),
            "expires_in": token_data.get("expires_in"),
            "scope": token_data.get("scope"),
            "storage": "not_implemented_yet",
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
