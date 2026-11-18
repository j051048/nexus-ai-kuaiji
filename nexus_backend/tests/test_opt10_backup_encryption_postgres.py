import base64
import json
import os
import shutil

import psycopg
import pytest

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
# Synthetic 36x20 PNG used by the isolated fixture; all seeded originals share it.
ORIGINAL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAACgAAAAUCAIAAABwJOjsAAAANUlEQVR4nO3NwQEAIAwCMWT/"
    "nfFrF7g+JAvkJNEGr6xqDDKZvRpjzFVTY4y5amqMMVfp8/gCAeEDJZQenysAAAAASUVORK5CYII="
)


def test_postgres_encrypted_backup_and_verify(tmp_path, monkeypatch):
    from scripts import backup_accounting

    assert ADMIN_URL
    assert shutil.which("pg_dump"), "pg_dump must be available for the isolated PostgreSQL run"
    storage = tmp_path / "storage"
    storage.mkdir()
    with psycopg.connect(ADMIN_URL) as conn:
        _, originals = backup_accounting.original_rows(conn, "postgres")
    for item in originals:
        path = storage / item["storage_key"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(ORIGINAL)
    key = base64.b64encode(b"p" * 32).decode("ascii")
    monkeypatch.setenv("ACCOUNTING_BACKUP_KEY", key)
    monkeypatch.setenv("ACCOUNTING_BACKUP_KEY_ID", "pg-test-key")
    destination = tmp_path / "backup"
    backup_accounting.backup(ADMIN_URL, storage, destination)
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dialect"] == "postgres"
    assert manifest["encryption"]["key_id"] == "pg-test-key"
    payload = (destination / "database.dump").read_bytes()
    assert payload.startswith(backup_accounting.ENCRYPTION_MAGIC)
    assert b"PGDMP" not in payload[:64]
    verified = backup_accounting.verify(destination)
    assert verified["dialect"] == "postgres"
    assert all(item.get("encrypted") for item in verified["files"])

    monkeypatch.setenv("ACCOUNTING_BACKUP_KEY", base64.b64encode(b"q" * 32).decode("ascii"))
    with pytest.raises(ValueError):
        backup_accounting.verify(destination)
