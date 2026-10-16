"""向前兼容迁移分类：兼容增量允许旧镜像启动，破坏性迁移仍然拒绝启动。"""

import logging
import sqlite3

import pytest

from app.accounting.database import MIGRATIONS, Database, migration_compatibility


def test_headers_classify_migrations_and_default_to_breaking(tmp_path):
    incremental = tmp_path / "071_incremental.sql"
    incremental.write_text("-- compatibility: incremental\nSELECT 1;\n", encoding="utf-8")
    assert migration_compatibility(incremental) == "incremental"
    unmarked = tmp_path / "072_unmarked.sql"
    unmarked.write_text("ALTER TABLE x DROP COLUMN y;\n", encoding="utf-8")
    assert migration_compatibility(unmarked) == "breaking"
    invalid = tmp_path / "073_invalid.sql"
    invalid.write_text("-- compatibility: anything-else\n", encoding="utf-8")
    assert migration_compatibility(invalid) == "breaking"
    assert (
        migration_compatibility(MIGRATIONS / "sqlite" / "070_migration_compatibility.sql")
        == "incremental"
    )
    assert (
        migration_compatibility(MIGRATIONS / "postgres" / "088_migration_compatibility.sql")
        == "incremental"
    )
    for relative in (
        "sqlite/071_workspace_task_projections.sql",
        "postgres/089_company_write_scope_wave1.sql",
        "postgres/090_workspace_task_projections.sql",
    ):
        assert migration_compatibility(MIGRATIONS / relative) == "incremental", relative
    assert (
        migration_compatibility(MIGRATIONS / "sqlite" / "069_uploads_and_invoice_imports.sql")
        == "breaking"
    )


def test_sqlite_runner_records_compatibility_per_migration(tmp_path):
    database = Database(tmp_path / "records.db")
    with database.transaction() as conn:
        rows = {
            row["version"]: row["compatibility"]
            for row in conn.execute("SELECT version,compatibility FROM schema_migrations")
        }
    database.close()
    assert rows[70] == "incremental"
    assert rows[69] == "breaking"


def insert_future(path, version=999, compatibility="incremental"):
    with sqlite3.connect(path) as raw:
        raw.execute(
            "INSERT INTO schema_migrations(version,checksum,compatibility) VALUES(?,?,?)",
            (version, "future-migration-digest", compatibility),
        )
        raw.commit()


def test_future_incremental_migration_allows_startup_with_warning(tmp_path, caplog):
    path = tmp_path / "ahead.db"
    Database(path).close()
    insert_future(path)
    with caplog.at_level(logging.WARNING, logger="app.accounting.database"):
        Database(path).close()
    assert any("兼容增量" in record.message for record in caplog.records)


def test_future_breaking_migration_still_refuses_startup(tmp_path):
    path = tmp_path / "breaking.db"
    Database(path).close()
    insert_future(path, compatibility="breaking")
    with pytest.raises(RuntimeError, match="未知的迁移版本"):
        Database(path).close()


def test_database_without_compatibility_column_refuses_unknown_versions(tmp_path):
    # Databases created before migration 070 carry no classification column;
    # every unknown version is conservatively treated as breaking.
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as raw:
        raw.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,"
            "checksum TEXT NOT NULL,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        raw.execute("INSERT INTO schema_migrations(version,checksum) VALUES(999,'future')")
        raw.commit()
    with pytest.raises(RuntimeError, match="未知的迁移版本"):
        Database(path).close()


def test_known_migration_checksum_is_still_enforced(tmp_path):
    path = tmp_path / "tampered.db"
    Database(path).close()
    with sqlite3.connect(path) as raw:
        raw.execute("UPDATE schema_migrations SET checksum='tampered' WHERE version=70")
        raw.commit()
    with pytest.raises(RuntimeError, match="校验不符"):
        Database(path).close()
