from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4
import hashlib
import json
import pytest
from app.accounting.database import Database
from test_accounting import service, book, make_draft
from test_intelligence_workflow import ai_case, create_event

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
