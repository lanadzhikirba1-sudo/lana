"""
Минимальный HTTP-слой для деплоя (Render и др.).
Полный контракт API — docs/automation.md; реализация эндпоинтов добавляется по мере разработки.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(
    title="lana-backend",
    version="0.0.1-stub",
    description="Заглушка: health и корень; OAuth callback пока не реализован.",
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


@app.get("/api/v1/google/oauth/callback")
def google_oauth_callback_stub() -> JSONResponse:
    """Реальный обмен code → токены — docs/integrations.md; пока заглушка для валидного маршрута."""
    return JSONResponse(
        status_code=501,
        content={"detail": "OAuth callback not implemented yet"},
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
