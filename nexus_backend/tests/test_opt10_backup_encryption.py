from __future__ import annotations

import base64
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from app.accounting.database import Database
from test_accounting import service
from test_intelligence_workflow import ai_case, create_event

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "backup_accounting.py"


def _load_module():
    spec = spec_from_file_location("backup_accounting_opt10", MODULE_PATH)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _key(byte: int) -> str:
    return base64.b64encode(bytes([byte]) * 32).decode("ascii")


@pytest.fixture()
def recovery():
    return _load_module()


@pytest.fixture()
def encrypted_env(monkeypatch):
    monkeypatch.setenv("ACCOUNTING_BACKUP_KEY", _key(7))
    monkeypatch.setenv("ACCOUNTING_BACKUP_KEY_ID", "test-key-2026")
    monkeypatch.setenv("ACCOUNTING_ENV", "test")


def test_encrypted_sqlite_backup_restores_and_hides_plaintext(service, tmp_path, recovery, encrypted_env):
    storage = tmp_path / "private"
    storage.mkdir()
    target = tmp_path / "backup"
    restored = tmp_path / "restore"

    manifest = recovery.backup(service.db.path, storage, target)

    assert manifest["encryption"] == {
        "algorithm": "AES-256-GCM",
        "format": "nxbk-gcm-v1",
        "key_id": "test-key-2026",
    }
    assert manifest["contains_credentials"] is False
    assert "test-key-2026" in (target / "manifest.json").read_text(encoding="utf-8")
    assert _key(7) not in (target / "manifest.json").read_text(encoding="utf-8")
    ciphertext = (target / "database.sqlite").read_bytes()
    assert ciphertext.startswith(b"NXBK1")
    assert not ciphertext.startswith(b"SQLite format 3\x00")
    assert recovery.verify(target) == manifest

    receipt = recovery.restore(target, restored)
    assert receipt["serving_enabled"] is False
    restored_db = Database(restored / "database.sqlite")
    with restored_db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0


def test_encrypted_originals_restore_to_exact_plaintext(ai_case, tmp_path, recovery, encrypted_env):
    event = create_event(ai_case)
    target = tmp_path / "backup"
    restored = tmp_path / "restore"
    manifest = recovery.backup(ai_case.db.path, ai_case.storage.root, target)

    assert len(manifest["files"]) == 2
    original = next(item for item in manifest["files"] if item["path"].startswith("originals/"))
    assert original["encrypted"] is True
    assert (target / original["path"]).read_bytes().startswith(b"NXBK1")
    receipt = recovery.restore(target, restored)
    assert receipt["verified_files"] == 2
    attachment = event["attachments"][0]
    with ai_case.db.transaction(actor=ai_case.actors["employee"]) as conn:
        key = conn.execute(
            "SELECT storage_key FROM business_attachments WHERE id=?", (attachment["id"],)
        ).fetchone()[0]
    assert recovery.sha256(restored / "originals" / key) == attachment["sha256"]


def test_wrong_key_and_tampered_ciphertext_fail_before_restore(service, tmp_path, recovery, encrypted_env, monkeypatch):
    storage = tmp_path / "private"
    storage.mkdir()
    target = tmp_path / "backup"
    recovery.backup(service.db.path, storage, target)

    monkeypatch.setenv("ACCOUNTING_BACKUP_KEY", _key(9))
    with pytest.raises(ValueError, match="解密失败|密钥"):
        recovery.verify(target)
    assert not (tmp_path / "restore-wrong-key").exists()

    monkeypatch.setenv("ACCOUNTING_BACKUP_KEY", _key(7))
    path = target / "database.sqlite"
    payload = bytearray(path.read_bytes())
    payload[-20] ^= 0x01
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="哈希不符"):
        recovery.restore(target, tmp_path / "restore-tampered")
    assert not (tmp_path / "restore-tampered").exists()


def test_production_requires_a_backup_key_but_local_legacy_still_works(service, tmp_path, recovery, monkeypatch):
    storage = tmp_path / "private"
    storage.mkdir()
    monkeypatch.delenv("ACCOUNTING_BACKUP_KEY", raising=False)
    monkeypatch.setenv("ACCOUNTING_ENV", "production")
    with pytest.raises(ValueError, match="备份加密密钥"):
        recovery.backup(service.db.path, storage, tmp_path / "prod-backup")
    assert not (tmp_path / "prod-backup" / "manifest.json").exists()

    monkeypatch.setenv("ACCOUNTING_ENV", "local")
    legacy = tmp_path / "legacy"
    manifest = recovery.backup(service.db.path, storage, legacy)
    assert "encryption" not in manifest
    assert (legacy / "database.sqlite").read_bytes().startswith(b"SQLite format 3\x00")
    assert recovery.verify(legacy) == manifest
    receipt = recovery.restore(legacy, tmp_path / "legacy-restore")
    assert receipt["serving_enabled"] is False
