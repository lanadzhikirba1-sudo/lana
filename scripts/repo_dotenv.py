"""Загрузка `.env` из корня репозитория без shell (удобно при `&` в DATABASE_URL)."""

from __future__ import annotations

import os
from pathlib import Path


def parse_dotenv_file(dot: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in dot.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, rest = line.partition("=")
        k = k.strip()
        v = rest.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        out[k] = v
    return out


def merge_repo_dotenv(root: Path) -> None:
    """Если есть root/.env — все пары KEY=VAL попадают в os.environ."""
    dot = root / ".env"
    if not dot.is_file():
        return
    for k, v in parse_dotenv_file(dot).items():
        os.environ[k] = v
