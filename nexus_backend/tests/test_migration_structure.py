"""迁移纪律：兼容头、RLS/写门覆盖与版本唯一性（面向 088/070 之后的新迁移）。"""

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "nexus_backend" / "migrations"
HEADER = re.compile(r"^\s*--\s*compatibility\s*:\s*(incremental|breaking)\s*$", re.M)
POSTGRES_DISCIPLINE = 88
SQLITE_DISCIPLINE = 70


def migration_files(dialect):
    files = {}
    for path in sorted((MIGRATIONS / dialect).glob("[0-9][0-9][0-9]_*.sql")):
        files[int(os.path.basename(path).split("_", 1)[0])] = path.read_text(encoding="utf-8")
    return files


def test_versions_are_unique_per_dialect():
    for dialect in ("sqlite", "postgres"):
        versions = [
            int(os.path.basename(path).split("_", 1)[0])
            for path in (MIGRATIONS / dialect).glob("*.sql")
        ]
        assert len(versions) == len(set(versions)), dialect


def test_new_migrations_declare_compatibility_header():
    for dialect, threshold in (("sqlite", SQLITE_DISCIPLINE), ("postgres", POSTGRES_DISCIPLINE)):
        for version, source in migration_files(dialect).items():
            if version < threshold:
                continue
            head = "\n".join(source.splitlines()[:5])
            assert HEADER.search(head), f"{dialect} {version:03d} 缺少 compatibility 头"


def test_new_tables_carry_rls_force_and_write_gate():
    files = migration_files("postgres")
    created = []
    for version, source in files.items():
        for matched in re.finditer(
            r"CREATE TABLE (?:IF NOT EXISTS )?accounting\.([a-z_]+)", source
        ):
            created.append((version, matched.group(1)))
    for version, table in created:
        if version < POSTGRES_DISCIPLINE:
            continue
        later = "".join(source for other, source in files.items() if other >= version)
        assert re.search(rf"ALTER TABLE accounting\.{table} ENABLE ROW LEVEL SECURITY", later), (
            f"{table} 缺少 ENABLE ROW LEVEL SECURITY"
        )
        assert re.search(rf"ALTER TABLE accounting\.{table} FORCE ROW LEVEL SECURITY", later), (
            f"{table} 缺少 FORCE ROW LEVEL SECURITY"
        )
        assert re.search(
            rf"ON accounting\.{table} FOR EACH ROW EXECUTE FUNCTION "
            r"accounting\.company_write_table_guard",
            later,
        ), f"{table} 缺少批量写门触发器"
