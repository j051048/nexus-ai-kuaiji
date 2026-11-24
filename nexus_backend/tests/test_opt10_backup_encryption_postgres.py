import base64
import hashlib
import json
import os
import shutil
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="需要专用 PostgreSQL 管理员测试连接")
# Synthetic 36x20 PNG used by the isolated fixture; all seeded originals share it.
ORIGINAL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAACgAAAAUCAIAAABwJOjsAAAANUlEQVR4nO3NwQEAIAwCMWT/"
    "nfFrF7g+JAvkJNEGr6xqDDKZvRpjzFVTY4y5amqMMVfp8/gCAeEDJZQenysAAAAASUVORK5CYII="
)
ORIGINAL_SHA256 = hashlib.sha256(ORIGINAL).hexdigest()


def _target_database_url(admin_url: str, backup_accounting):
    with psycopg.connect(admin_url) as conn:
        _, originals = backup_accounting.original_rows(conn, "postgres")
    matches_fixture = all(
        item["size_bytes"] == len(ORIGINAL) and item["sha256"] == ORIGINAL_SHA256
        for item in originals
    )
    if matches_fixture:
        return admin_url, originals, None
    # Shared CI test databases contain heterogeneous attachment rows whose files
    # live in earlier tests' tmp_path directories. Provision a clean ephemeral
    # database on the same local instance so pg_dump encryption and verification
    # still run end-to-end without skipping.
    temp_db = "opt10_enc_" + uuid4().hex[:12]
    parsed = urlsplit(admin_url)
    isolated_url = urlunsplit(parsed._replace(path="/" + temp_db))
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(temp_db)))
    from scripts.migrate_accounting import migrate

    migrate(isolated_url)
    with psycopg.connect(isolated_url) as conn:
        _, isolated_originals = backup_accounting.original_rows(conn, "postgres")
    return isolated_url, isolated_originals, temp_db


def test_postgres_encrypted_backup_and_verify(tmp_path, monkeypatch):
    from scripts import backup_accounting

    assert ADMIN_URL
    assert shutil.which("pg_dump"), "pg_dump must be available for the isolated PostgreSQL run"
    storage = tmp_path / "storage"
    storage.mkdir()
    target_url, originals, temp_db = _target_database_url(ADMIN_URL, backup_accounting)
    try:
        for item in originals:
            path = storage / item["storage_key"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(ORIGINAL)
        key = base64.b64encode(b"p" * 32).decode("ascii")
        monkeypatch.setenv("ACCOUNTING_BACKUP_KEY", key)
        monkeypatch.setenv("ACCOUNTING_BACKUP_KEY_ID", "pg-test-key")
        destination = tmp_path / "backup"
        backup_accounting.backup(target_url, storage, destination)
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["dialect"] == "postgres"
        assert manifest["encryption"]["key_id"] == "pg-test-key"
        payload = (destination / "database.dump").read_bytes()
        assert payload.startswith(backup_accounting.ENCRYPTION_MAGIC)
        assert b"PGDMP" not in payload[:64]
        verified = backup_accounting.verify(destination)
        assert verified["dialect"] == "postgres"
        assert verified["files"] and all(item.get("encrypted") for item in verified["files"])

        monkeypatch.setenv("ACCOUNTING_BACKUP_KEY", base64.b64encode(b"q" * 32).decode("ascii"))
        with pytest.raises(ValueError):
            backup_accounting.verify(destination)
    finally:
        if temp_db:
            with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(temp_db)
                    )
                )
