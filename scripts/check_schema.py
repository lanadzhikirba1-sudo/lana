#!/usr/bin/env python3
"""
Проверка соответствия docs/data_model.md и схемы PostgreSQL.

Запуск:
  python3 scripts/check_schema.py

Требования:
  - переменная окружения DATABASE_URL
  - пакет psycopg (pip install psycopg[binary])
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from repo_dotenv import merge_repo_dotenv


DOC_PATH = "docs/data_model.md"


@dataclass
class ColumnSpec:
    name: str
    type_name: str


def normalize_type(type_name: str) -> str:
    raw = type_name.strip().lower()
    mapping = {
        "int8": "bigint",
        "bigint": "bigint",
        "int": "integer",
        "integer": "integer",
        "smallint": "smallint",
        "uuid": "uuid",
        "text": "text",
        "boolean": "boolean",
        "bytea": "bytea",
        "timestamptz": "timestamp with time zone",
        "timestamp with time zone": "timestamp with time zone",
        "time": "time without time zone",
        "time without time zone": "time without time zone",
    }
    return mapping.get(raw, raw)


def parse_data_model_markdown(path: str) -> Dict[str, Dict[str, ColumnSpec]]:
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Section: "## 1. therapists"
    table_header_re = re.compile(r"^##\s+\d+\.\s+([a-z_][a-z0-9_]*)\s*$")
    row_re = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")

    result: Dict[str, Dict[str, ColumnSpec]] = {}
    current_table: str | None = None
    in_table = False
    seen_header_separator = False

    for line in lines:
        header_match = table_header_re.match(line.strip())
        if header_match:
            current_table = header_match.group(1)
            result[current_table] = {}
            in_table = False
            seen_header_separator = False
            continue

        if current_table is None:
            continue

        stripped = line.strip()
        # Таблицы в docs/data_model.md: три колонки — «Поле | Тип данных | Назначение»
        if re.match(r"^\|\s*Поле\s*\|", stripped) and "Тип данных" in stripped:
            in_table = True
            seen_header_separator = False
            continue
        if in_table and not seen_header_separator:
            if stripped.startswith("|") and "---" in stripped:
                seen_header_separator = True
            continue
        if in_table and stripped.startswith("|"):
            match = row_re.match(stripped)
            if not match:
                continue
            col_name = match.group(1).strip()
            col_type = match.group(2).strip()
            if col_name.lower() == "поле" and col_type.lower().startswith("тип"):
                continue
            if col_name and col_type:
                result[current_table][col_name] = ColumnSpec(
                    name=col_name,
                    type_name=normalize_type(col_type),
                )
            continue
        if in_table and stripped == "":
            in_table = False

    return result


def fetch_db_schema(
    database_url: str, schema_name: str, table_names: List[str]
) -> Dict[str, Dict[str, ColumnSpec]]:
    try:
        import psycopg  # type: ignore
    except ImportError:
        print("ERROR: Не найден пакет psycopg.")
        print("Установите: python3 -m pip install psycopg[binary]")
        sys.exit(2)

    query = """
        select
            table_name,
            column_name,
            data_type
        from information_schema.columns
        where table_schema = %s
          and table_name = any(%s)
        order by table_name, ordinal_position;
    """

    db_result: Dict[str, Dict[str, ColumnSpec]] = {t: {} for t in table_names}
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (schema_name, table_names))
            for table_name, column_name, data_type in cur.fetchall():
                db_result[table_name][column_name] = ColumnSpec(
                    name=column_name,
                    type_name=normalize_type(data_type),
                )

    return db_result


def compare_schema(
    expected: Dict[str, Dict[str, ColumnSpec]],
    actual: Dict[str, Dict[str, ColumnSpec]],
) -> int:
    errors: List[str] = []
    warnings: List[str] = []

    expected_tables = set(expected.keys())
    actual_tables = {t for t, cols in actual.items() if cols}

    missing_tables = sorted(expected_tables - actual_tables)
    extra_tables = sorted(actual_tables - expected_tables)

    for t in missing_tables:
        errors.append(f"Отсутствует таблица в БД: {t}")
    for t in extra_tables:
        warnings.append(f"Лишняя таблица в БД (нет в docs): {t}")

    for table in sorted(expected_tables):
        expected_cols = expected.get(table, {})
        actual_cols = actual.get(table, {})
        if not actual_cols:
            continue

        expected_col_names = set(expected_cols.keys())
        actual_col_names = set(actual_cols.keys())

        missing_cols = sorted(expected_col_names - actual_col_names)
        extra_cols = sorted(actual_col_names - expected_col_names)

        for c in missing_cols:
            errors.append(f"{table}: отсутствует колонка {c}")
        for c in extra_cols:
            warnings.append(f"{table}: лишняя колонка (нет в docs) {c}")

        for c in sorted(expected_col_names & actual_col_names):
            exp_t = expected_cols[c].type_name
            act_t = actual_cols[c].type_name
            if exp_t != act_t:
                errors.append(
                    f"{table}.{c}: тип не совпадает (docs={exp_t}, db={act_t})"
                )

    print("=== Проверка схемы PostgreSQL vs docs/data_model.md ===")
    if warnings:
        print("\nПредупреждения:")
        for w in warnings:
            print(f"- {w}")

    if errors:
        print("\nОшибки:")
        for e in errors:
            print(f"- {e}")
        print("\nИтог: FAIL")
        return 1

    print("\nИтог: OK, схема совпадает.")
    return 0


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    merge_repo_dotenv(root)

    database_url = os.getenv("DATABASE_URL")
    schema_name = os.getenv("DB_SCHEMA", "public")

    if not database_url:
        print("ERROR: не задана переменная окружения DATABASE_URL")
        print("Скопируйте .env.example в .env и заполните DATABASE_URL")
        return 2

    if not os.path.exists(DOC_PATH):
        print(f"ERROR: не найден файл {DOC_PATH}")
        return 2

    expected = parse_data_model_markdown(DOC_PATH)
    if not expected:
        print("ERROR: не удалось распарсить таблицы из docs/data_model.md")
        return 2

    table_names = sorted(expected.keys())
    actual = fetch_db_schema(database_url, schema_name, table_names)
    return compare_schema(expected, actual)


if __name__ == "__main__":
    raise SystemExit(main())
