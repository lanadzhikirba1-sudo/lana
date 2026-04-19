#!/usr/bin/env python3
"""
Применяет SQL-миграции из docs/sql к базе из DATABASE_URL.

Порядок:
  1. docs/sql/schema_initial_v1.sql — базовые таблицы (пустая БД)
  2. docs/sql/schema_migrations_v1.sql — notification_jobs и доп. колонки
  3. docs/sql/payment_reminder_jobs_trigger_v1.sql

Если в корне репозитория есть `.env`, скрипт подставляет из него переменные
в окружение (удобно при URL с `&`, когда `source .env` в zsh падает; актуальный
`.env` перекрывает устаревший `DATABASE_URL` из shell).

Запуск (из корня репозитория):
  . .venv/bin/activate   # если psycopg установлен в venv
  python3 scripts/apply_schema_migrations.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from repo_dotenv import merge_repo_dotenv


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    merge_repo_dotenv(root)

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("ERROR: не задана переменная окружения DATABASE_URL")
        print("Задайте её в окружении или в файле .env в корне репозитория.")
        return 2

    files = [
        root / "docs/sql/schema_initial_v1.sql",
        root / "docs/sql/schema_migrations_v1.sql",
        root / "docs/sql/payment_reminder_jobs_trigger_v1.sql",
    ]
    try:
        import psycopg
    except ImportError:
        print("ERROR: установите psycopg: python3 -m pip install 'psycopg[binary]' (лучше в .venv)")
        return 2

    for path in files:
        if not path.is_file():
            print(f"ERROR: не найден файл {path}")
            return 2

    sql = "\n\n".join(p.read_text(encoding="utf-8") for p in files)

    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql)
    except Exception as e:
        print(f"ERROR при выполнении SQL: {e}")
        return 1

    print("OK: миграции применены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
