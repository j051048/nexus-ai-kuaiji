"""Static guards for SQL constructs that only behave on SQLite.

These are static checks on source/migrations; they do not need PostgreSQL.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "nexus_backend" / "app"
PG = ROOT / "nexus_backend" / "migrations" / "postgres"

BOOL_BIND = re.compile(r"\b(?:AND|OR)\s+\?\s*\)", re.IGNORECASE)
TRIGGER = re.compile(
    r"CREATE\s+TRIGGER\s+([a-z0-9_]+)\s+.*?EXECUTE\s+FUNCTION\s+([a-z0-9_.]+)\s*\(\s*\)",
    re.IGNORECASE | re.DOTALL,
)
FUNCTION = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([a-z0-9_.]+)\s*\([^)]*\).*?\$([a-z0-9_]+)\$(.*?)\$\2\$",
    re.IGNORECASE | re.DOTALL,
)
TABLES = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+accounting\.([a-z0-9_]+)\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)


def _sql_blocks():
    for path in sorted(APP.rglob("*.py")):
        yield path, path.read_text(encoding="utf-8")


def test_boolean_parameters_are_not_bound_directly_in_sql():
    offenders = []
    for path, text in _sql_blocks():
        for match in BOOL_BIND.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(ROOT)}:{line}")
    assert offenders == []


def test_postgres_trigger_names_match_tg_name_whitelists():
    files = {path: path.read_text(encoding="utf-8") for path in sorted(PG.glob("*.sql"))}
    functions = {}
    for path, text in files.items():
        for match in FUNCTION.finditer(text):
            name = match.group(1).split(".")[-1]
            functions[name] = match.group(3)
    offenders = []
    for path, text in files.items():
        for match in TRIGGER.finditer(text):
            trigger, function = match.group(1), match.group(2).split(".")[-1]
            body = functions.get(function)
            if not body or "TG_NAME" not in body:
                continue
            single = re.search(r"TG_NAME\s*<>\s*'([a-z0-9_]+)'", body, re.IGNORECASE)
            if single:
                accepted = {single.group(1)}
            else:
                array = re.search(
                    r"TG_NAME\s*<>\s*ALL\s*\(\s*ARRAY\s*\[(.*?)\]", body, re.IGNORECASE | re.DOTALL
                )
                if not array:
                    continue
                accepted = set(re.findall(r"'([a-z0-9_]+)'", array.group(1), re.IGNORECASE))
            if trigger.lower() not in {item.lower() for item in accepted}:
                offenders.append(f"{path.name}:{trigger} -> {function}")
    assert offenders == []


def test_recent_multitenant_tables_enable_and_force_rls():
    """Each table created after the RLS line must end up both enabled and forced."""
    texts = []
    tables = set()
    for path in sorted(PG.glob("*.sql")):
        version = int(path.name.split("_", 1)[0])
        if version < 300:
            continue
        text = path.read_text(encoding="utf-8")
        texts.append(text)
        for match in TABLES.finditer(text):
            table, body = match.group(1), match.group(2)
            if "company_id" in body:
                tables.add(table)
    offenders = []
    for table in sorted(tables):
        enabled = any(
            re.search(
                rf"ALTER\s+TABLE\s+accounting\.{table}\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
                text,
                re.IGNORECASE,
            )
            for text in texts
        )
        forced = any(
            re.search(
                rf"ALTER\s+TABLE\s+accounting\.{table}\s+FORCE\s+ROW\s+LEVEL\s+SECURITY",
                text,
                re.IGNORECASE,
            )
            for text in texts
        )
        if not enabled or not forced:
            offenders.append(f"{table}: enabled={enabled} forced={forced}")
    assert offenders == []
