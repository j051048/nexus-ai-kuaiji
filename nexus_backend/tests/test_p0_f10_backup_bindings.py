"""F10 portable reference evidence is complete and matches the DB snapshot."""

from copy import deepcopy
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sqlite3

import pytest


spec = spec_from_file_location(
    "p0_backup_bindings", Path(__file__).resolve().parents[2] / "scripts/backup_accounting.py"
)
recovery = module_from_spec(spec)
spec.loader.exec_module(recovery)


@pytest.fixture
def original(tmp_path):
    database, storage = tmp_path / "snapshot.sqlite", tmp_path / "private"
    (storage / "aa").mkdir(parents=True)
    payload = b"synthetic-private-original"
    (storage / "aa/original").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    binding = {
        "version": 1, "firm_id": "firm-a", "company_id": "company-a",
        "purpose": "event_attachment", "target_kind": "business_event", "target_id": "event-a",
        "attachment_table": "business_attachments", "attachment_id": "attachment-a",
        "event_id": "event-a", "source_id": None, "book_id": None, "response_id": None,
        "storage": {"storage_key": "aa/original", "sha256": digest, "size_bytes": len(payload)},
    }
    with sqlite3.connect(database) as conn:
        conn.executescript("""
            CREATE TABLE schema_migrations(version INTEGER,checksum TEXT);
            INSERT INTO schema_migrations VALUES(1,'synthetic-local-schema');
            CREATE TABLE business_events(id TEXT PRIMARY KEY,firm_id TEXT,company_id TEXT);
            INSERT INTO business_events VALUES('event-a','firm-a','company-a');
            INSERT INTO business_events VALUES('event-other','firm-a','company-a');
            INSERT INTO business_events VALUES('event-foreign','firm-a','company-b');
            CREATE TABLE business_attachments(id TEXT,event_id TEXT,firm_id TEXT,company_id TEXT,
                storage_key TEXT,sha256 TEXT,size_bytes INTEGER,created_by TEXT);
            CREATE TABLE upload_intents(id TEXT,firm_id TEXT,user_id TEXT,purpose TEXT,
                storage_key TEXT,sha256 TEXT,size_bytes INTEGER,state TEXT,consumed_ref TEXT,binding_json TEXT);
        """)
        conn.execute("INSERT INTO business_attachments VALUES(?,?,?,?,?,?,?,?)", (
            "attachment-a", "event-a", "firm-a", "company-a", "aa/original", digest, len(payload), "employee-a"))
        conn.execute("INSERT INTO upload_intents VALUES(?,?,?,?,?,?,?,?,?,?)", (
            "intent-a", "firm-a", "employee-a", "event_attachment", "aa/original", digest,
            len(payload), "consumed", "business_attachments:attachment-a", json.dumps(binding)))
    return database, storage, binding


def update_binding(database, binding):
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE upload_intents SET binding_json=?", (json.dumps(binding),))


def test_manifest_round_trip_retains_every_reference_and_binding(original, tmp_path):
    database, storage, binding = original
    target = tmp_path / "backup"
    manifest = recovery.backup(str(database), storage, target)
    copied = manifest["files"][1]
    assert manifest["originals_policy_version"] == 3
    assert len(copied["references"]) == 2
    attachment, intent = copied["references"]
    assert attachment["row_id"] == "attachment-a" and attachment["links"] == {"event_id": "event-a"}
    assert intent["row_id"] == "intent-a" and intent["upload"]["binding"] == binding
    assert manifest["reference_verification"] == {
        "reference_count": 2, "legacy_unbound_uploads": 0,
        "unidentified_references": 0, "all_consumed_bindings_verified": True,
    }
    assert recovery.verify(target) == manifest
    restored = tmp_path / "restored"
    receipt = recovery.restore(target, restored)
    assert receipt["reference_verification"]["all_consumed_bindings_verified"] is True
    assert receipt["serving_enabled"] is False
    with sqlite3.connect(restored / "database.sqlite") as conn:
        assert conn.execute("SELECT hold FROM restore_state").fetchone()[0] == 1
        assert json.loads(conn.execute("SELECT binding_json FROM upload_intents").fetchone()[0]) == binding


@pytest.mark.parametrize("field,value", [
    ("firm_id", "foreign-firm"), ("company_id", "company-b"),
    ("target_id", "event-other"), ("event_id", "event-other"),
    ("attachment_id", "missing-attachment"), ("purpose", "invoice_import"),
    ("target_kind", "book"), ("version", 999),
])
def test_consumed_binding_cannot_claim_another_scope_or_target(original, tmp_path, field, value):
    database, storage, binding = original
    binding[field] = value
    update_binding(database, binding)
    with pytest.raises(ValueError):
        recovery.backup(str(database), storage, tmp_path / "must-not-complete")
    assert not (tmp_path / "must-not-complete/manifest.json").exists()


@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("size_bytes", 1), ("storage_key", "aa/other")])
def test_binding_must_name_the_exact_original_bytes(original, tmp_path, field, value):
    database, storage, binding = original
    binding["storage"][field] = value
    update_binding(database, binding)
    with pytest.raises(ValueError, match="原件身份"):
        recovery.backup(str(database), storage, tmp_path / "bad-identity")


def test_parent_scope_is_checked_even_if_attachment_and_binding_agree(original, tmp_path):
    database, storage, binding = original
    # An inconsistent row would ordinarily be caught by FKs; restored/synthetic
    # catalogs must independently verify the referenced parent as well.
    binding["target_id"] = binding["event_id"] = "event-foreign"
    update_binding(database, binding)
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE business_attachments SET event_id='event-foreign'")
    with pytest.raises(ValueError, match="目标企业"):
        recovery.backup(str(database), storage, tmp_path / "bad-parent")


def test_same_key_in_two_companies_is_rejected_not_silently_merged(original, tmp_path):
    database, storage, _ = original
    with sqlite3.connect(database) as conn:
        conn.executescript("""CREATE TABLE intake_attachments AS
            SELECT id,intake_id,firm_id,company_id,storage_key,sha256,size_bytes FROM
            (SELECT id,'intake-foreign' AS intake_id,firm_id,'company-b' AS company_id,storage_key,sha256,size_bytes
             FROM business_attachments);""")
    with pytest.raises(ValueError, match="跨企业"):
        recovery.backup(str(database), storage, tmp_path / "bad-cross-company")


def test_legacy_consumed_intent_is_preserved_but_never_reported_as_verified(original, tmp_path):
    database, storage, _ = original
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE upload_intents SET binding_json=NULL,consumed_ref='attachment'")
    target = tmp_path / "legacy"
    manifest = recovery.backup(str(database), storage, target)
    intent = manifest["files"][1]["references"][1]
    assert intent["upload"]["binding"] is None
    assert intent["upload"]["binding_status"] == "legacy_unbound"
    assert manifest["reference_verification"]["legacy_unbound_uploads"] == 1
    assert manifest["reference_verification"]["all_consumed_bindings_verified"] is False
    receipt = recovery.restore(target, tmp_path / "legacy-restore")
    assert receipt["reference_verification"]["all_consumed_bindings_verified"] is False
    assert receipt["in_flight_jobs_hold"] is True


@pytest.mark.parametrize("tamper", ["drop_intent", "change_target", "pretend_legacy"])
def test_manifest_cannot_rewrite_or_omit_snapshot_references(original, tmp_path, tamper):
    database, storage, _ = original
    target, restored = tmp_path / "good-backup", tmp_path / "rejected-restore"
    manifest = recovery.backup(str(database), storage, target)
    copied = manifest["files"][1]
    attachment, intent = copied["references"]
    if tamper == "drop_intent":
        copied["references"] = [attachment]
        copied["tables"] = ["business_attachments"]
    elif tamper == "change_target":
        attachment["links"]["event_id"] = "event-other"
        intent["upload"]["binding"]["target_id"] = "event-other"
        intent["upload"]["binding"]["event_id"] = "event-other"
    else:
        intent["upload"].update(binding=None, binding_status="legacy_unbound", consumed_ref="attachment")
    manifest["reference_verification"] = recovery.reference_verification(manifest["files"])
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="引用"):
        recovery.restore(target, restored)
    assert not restored.exists()


def test_malformed_binding_and_fake_raw_ref_fail_closed(original, tmp_path):
    database, storage, binding = original
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE upload_intents SET binding_json='not-json'")
    with pytest.raises(ValueError, match="JSON"):
        recovery.backup(str(database), storage, tmp_path / "bad-json")
    update_binding(database, binding)
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE upload_intents SET consumed_ref='attachment'")
    with pytest.raises(ValueError, match="引用标识"):
        recovery.backup(str(database), storage, tmp_path / "fake-ref")
