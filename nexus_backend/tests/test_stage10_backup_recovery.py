from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4
import hashlib
import json
import sqlite3
import pytest
from app.accounting.database import Database
from test_accounting import service, book, make_draft
from test_intelligence_workflow import ai_case, create_event
from test_shared_original_storage import SyntheticStorage
from app.accounting.domain import AccountingError

path = Path(__file__).resolve().parents[2] / "scripts" / "backup_accounting.py"
spec = spec_from_file_location("backup_accounting_stage10", path)
recovery = module_from_spec(spec)
spec.loader.exec_module(recovery)


def test_consistent_sqlite_backup_and_new_target_restore_drill(service, book, tmp_path):
    make_draft(service, book)
    storage, target, restored = tmp_path / "private", tmp_path / "backup", tmp_path / "recovery"
    storage.mkdir()
    value = recovery.backup(service.db.path, storage, target)
    assert value["contains_credentials"] is False and value["consistency"] == "sqlite_backup_snapshot"
    assert recovery.verify(target) == value
    receipt = recovery.restore(target, restored)
    assert receipt["verified_files"] == 1 and receipt["serving_enabled"] is False
    copied = Database(restored / "database.sqlite")
    with copied.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM drafts WHERE book_id=?", (book,)).fetchone()[0] == 1
    assert not (target / "manifest.json").read_text().find(service.db.path) >= 0


def test_hash_mismatch_prevents_any_restore_write(service, tmp_path):
    storage = tmp_path / "private"; storage.mkdir()
    target = tmp_path / "backup"
    recovery.backup(service.db.path, storage, target)
    with (target / "database.sqlite").open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="哈希不符"):
        recovery.restore(target, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_backup_and_restore_never_overwrite_existing_files(service, tmp_path):
    storage = tmp_path / "private"; storage.mkdir()
    target = tmp_path / "backup"
    recovery.backup(service.db.path, storage, target)
    with pytest.raises(ValueError, match="不能覆盖"):
        recovery.backup(service.db.path, storage, target)
    restored = tmp_path / "existing"; restored.mkdir()
    (restored / "important.txt").write_text("keep")
    with pytest.raises(ValueError, match="不能覆盖"):
        recovery.restore(target, restored)
    assert (restored / "important.txt").read_text() == "keep"


def test_manifest_traversal_is_rejected_before_restore(service, tmp_path):
    storage = tmp_path / "private"; storage.mkdir()
    target = tmp_path / "backup"
    recovery.backup(service.db.path, storage, target)
    manifest = json.loads((target / "manifest.json").read_text())
    manifest["files"].append({"path": "originals/../../outside", "sha256": "a" * 64, "size_bytes": 1})
    (target / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        recovery.restore(target, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_originals_restored_match_exact_snapshot_and_missing_blob_fails(ai_case, tmp_path):
    c = ai_case
    event = create_event(c)
    target, restored = tmp_path / "original-backup", tmp_path / "original-restore"
    manifest = recovery.backup(c.db.path, c.storage.root, target)
    assert len(manifest["files"]) == 2
    assert recovery.restore(target, restored)["verified_files"] == 2
    original = event["attachments"][0]
    with c.db.transaction(actor=c.actors["employee"]) as conn:
        key = conn.execute("SELECT storage_key FROM business_attachments WHERE id=?", (original["id"],)).fetchone()[0]
    assert recovery.sha256(restored / "originals" / key) == original["sha256"]
    (c.storage.root / key).write_bytes(b"corrupt-original")
    with pytest.raises(ValueError, match="哈希不符"):
        recovery.backup(c.db.path, c.storage.root, tmp_path / "failed-backup")
    assert not (tmp_path / "failed-backup" / "manifest.json").exists()


def test_remote_snapshot_originals_are_backed_up_and_restored_without_shared_disk(ai_case, tmp_path):
    c, remote = ai_case, SyntheticStorage()
    c.storage = remote.store()
    event = create_event(c)
    independent_reader = remote.store()
    archive, restored = tmp_path / "cloud-backup", tmp_path / "cloud-restore"
    manifest = recovery.backup(c.db.path, None, archive, storage_store=independent_reader)
    assert c.storage.root is independent_reader.root is None
    assert len(manifest["files"]) == 2 and recovery.verify(archive) == manifest
    receipt = recovery.restore(archive, restored)
    assert receipt["serving_enabled"] is False and receipt["verified_files"] == 2
    with c.db.transaction(actor=c.actors["employee"]) as conn:
        original = dict(conn.execute("SELECT storage_key,sha256,size_bytes FROM business_attachments WHERE id=?",
                                     (event["attachments"][0]["id"],)).fetchone())
    assert (restored / "originals" / original["storage_key"]).read_bytes() == independent_reader.read(original)
    assert "synthetic-storage.example" not in str(manifest) and "sb_secret_" not in str(manifest)
    assert all(request.method == "GET" for request in remote.requests[2:])


def test_remote_corrupt_original_does_not_complete_backup(ai_case, tmp_path):
    remote = SyntheticStorage()
    ai_case.storage = remote.store()
    create_event(ai_case)
    key = next(iter(remote.blobs))
    remote.blobs[key] = b"corrupt"
    target = tmp_path / "incomplete-cloud-backup"
    with pytest.raises(AccountingError, match="完整性"):
        recovery.backup(ai_case.db.path, None, target, storage_store=remote.store())
    assert not (target / "manifest.json").exists()


def test_public_remote_bucket_rejected_before_backup_directory_created(ai_case, tmp_path):
    remote = SyntheticStorage()
    remote.public = True
    target = tmp_path / "must-not-create"
    with pytest.raises(AccountingError, match="私有"):
        recovery.backup(ai_case.db.path, None, target, storage_store=remote.store())
    assert not target.exists()


def test_every_registered_original_table_is_backed_up_and_restored(tmp_path):
    database = tmp_path / "source.sqlite"
    storage = tmp_path / "private"
    storage.mkdir()
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE schema_migrations(version TEXT,checksum TEXT)")
        conn.execute("INSERT INTO schema_migrations VALUES('001','checksum')")
        for table, _ in recovery.ORIGINAL_TABLES:
            conn.execute(
                f"CREATE TABLE {table}(storage_key TEXT,sha256 TEXT,size_bytes INTEGER,state TEXT)"
            )
            key = f"aa/{table}"
            payload = table.encode("utf-8")
            digest = hashlib.sha256(payload).hexdigest()
            target = storage / key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            conn.execute(
                f"INSERT INTO {table} VALUES(?,?,?,?)",
                (key, digest, len(payload), "stored"),
            )
    archive, restored = tmp_path / "backup", tmp_path / "restore"
    manifest = recovery.backup(str(database), storage, archive)
    assert manifest["original_tables"] == [table for table, _ in recovery.ORIGINAL_TABLES]
    # One database plus one original for every registered table.
    assert len(manifest["files"]) == len(recovery.ORIGINAL_TABLES) + 1
    assert recovery.verify(archive) == manifest
    receipt = recovery.restore(archive, restored)
    assert receipt["verified_files"] == len(recovery.ORIGINAL_TABLES) + 1
    for table, _ in recovery.ORIGINAL_TABLES:
        key = f"aa/{table}"
        assert (restored / "originals" / key).read_bytes() == table.encode("utf-8")
    # A missing blob must block the backup even when the database row exists.
    (storage / "aa/invoice_import_batches").unlink()
    with pytest.raises(ValueError, match="原件不存在"):
        recovery.backup(str(database), storage, tmp_path / "incomplete")
    assert not (tmp_path / "incomplete" / "manifest.json").exists()
