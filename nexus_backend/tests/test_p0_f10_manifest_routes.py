"""Cross-check portable evidence against all six actual runtime consumers, locally."""

from copy import deepcopy
import json
import sqlite3
from uuid import uuid4

import pytest

from app.upload_bindings import BINDINGS
from test_business_api import auth, client, create  # noqa: F401
from test_p0_f10_backup_bindings import recovery
from test_upload_binding_http import (
    test_http_consumers_bind_and_replay_exactly_once as exercise_consumer,
)


def one_intent(client, purpose):
    with client.app.state.service.db.transaction() as conn:
        return dict(
            conn.execute(
                "SELECT * FROM upload_intents WHERE purpose=? AND state='consumed'", (purpose,)
            ).fetchone()
        )


def snapshot(client):
    with sqlite3.connect(client.app.state.service.db.path) as conn:
        return recovery.original_rows(conn, "sqlite")[1]


def archived_item(client, tmp_path, intent):
    manifest = recovery.backup(
        client.app.state.service.db.path,
        client.app.state.attachment_store.root,
        tmp_path / "backup",
    )
    assert manifest["originals_policy_version"] == 3
    assert manifest["reference_verification"]["all_consumed_bindings_verified"] is True
    assert recovery.verify(tmp_path / "backup") == manifest
    return next(
        item for item in manifest["files"] if item.get("storage_key") == intent["storage_key"]
    )


# The six original direct-upload consumers. Tax filing receipts have their own
# consumer contract in test_next05_filing_evidence.py; keep it in the portable
# recovery registry but exercise its HTTP path there.
LEGACY_CONSUMERS = {
    key: value for key, value in BINDINGS.items() if key != "tax_filing_attachment"
}


@pytest.mark.parametrize("purpose", list(LEGACY_CONSUMERS))
def test_six_runtime_consumers_are_policy3_compatible(client, tmp_path, purpose):
    _kind, _target, attachment_table, parent_column = BINDINGS[purpose]
    assert LEGACY_CONSUMERS == {
        key: value
        for key, value in recovery.CONSUMED_TARGETS.items()
        if key != "tax_filing_attachment"
    }
    assert BINDINGS["tax_filing_attachment"] == recovery.CONSUMED_TARGETS["tax_filing_attachment"]
    exercise_consumer(client, purpose, attachment_table)
    intent = one_intent(client, purpose)
    binding = json.loads(intent["binding_json"])
    original = next(
        item for item in snapshot(client) if item["storage_key"] == intent["storage_key"]
    )
    copied = archived_item(client, tmp_path, intent)
    assert copied["references"] == original["references"]
    upload = next(ref for ref in copied["references"] if ref["table"] == "upload_intents")
    attachment = next(ref for ref in copied["references"] if ref["table"] == attachment_table)
    assert upload["upload"]["binding"] == binding
    assert attachment["row_id"] == binding["attachment_id"]
    assert attachment["links"][parent_column] == binding["target_id"]
    assert attachment["scope"]["company_id"] == binding["company_id"]


def test_completed_intake_legitimate_shared_original_remains_verifiable(client, tmp_path):
    exercise_consumer(client, "intake_attachment", "intake_attachments")
    intent = one_intent(client, "intake_attachment")
    binding = json.loads(intent["binding_json"])
    employee = auth(client, "employee")
    draft = client.get(f"/api/intake/drafts/{binding['target_id']}", headers=employee).json()
    completed = client.post(
        f"/api/intake/drafts/{binding['target_id']}/complete",
        headers=employee,
        json={
            "expected_version": draft["version"],
            "idempotency_key": uuid4().hex,
            "fields_confirmed": True,
            "submit": False,
            "payload": {
                "occurred_on": "2026-10-10",
                "description": "虚构采购确认",
                "counterparty": "虚构供应商",
                "amount": "20.00",
                "direction": "expense",
                "payment_status": "unpaid",
                "category": "office",
            },
        },
    )
    assert completed.status_code == 200, completed.text
    event = completed.json()["event"]
    copied = archived_item(client, tmp_path, intent)
    refs = {ref["table"]: ref for ref in copied["references"]}
    assert set(refs) == {"upload_intents", "intake_attachments", "business_attachments"}
    assert refs["business_attachments"]["links"]["event_id"] == event["id"]
    assert refs["intake_attachments"]["row_id"] == refs["business_attachments"]["row_id"]
    assert refs["upload_intents"]["upload"]["binding"]["event_id"] is None


def test_accepted_case_legitimate_shared_original_remains_verifiable(client, tmp_path):
    exercise_consumer(client, "case_attachment", "case_attachments")
    intent = one_intent(client, "case_attachment")
    binding = json.loads(intent["binding_json"])
    employee, accountant = auth(client, "employee"), auth(client, "accountant")
    path = f"/api/cases/{binding['target_id']}"
    task = client.get(path, headers=employee).json()
    responded = client.post(
        path + "/respond",
        headers=employee,
        json={
            "expected_version": task["version"],
            "idempotency_key": uuid4().hex,
            "reason": "虚构补件用途已补充",
            "fields": {"用途": "虚构办公采购"},
        },
    )
    assert responded.status_code == 200, responded.text
    reviewed = client.post(
        path + "/review-response",
        headers=accountant,
        json={
            "expected_version": responded.json()["version"],
            "idempotency_key": uuid4().hex,
            "reason": "独立核对虚构补件完整",
            "decision": "accept",
            "supersedes_attachment_ids": [],
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    copied = archived_item(client, tmp_path, intent)
    refs = {ref["table"]: ref for ref in copied["references"]}
    assert set(refs) == {"upload_intents", "case_attachments", "business_attachments"}
    assert refs["business_attachments"]["row_id"] == binding["attachment_id"]
    assert refs["business_attachments"]["links"]["event_id"] == binding["event_id"]
    assert refs["case_attachments"]["links"]["response_id"] == binding["response_id"]


@pytest.mark.parametrize("field", ["event_id", "source_id", "book_id", "response_id"])
def test_portable_binding_cannot_omit_optional_field(client, field):
    exercise_consumer(client, "event_attachment", "business_attachments")
    item = snapshot(client)[0]
    upload = next(ref for ref in item["references"] if ref["table"] == "upload_intents")
    del upload["upload"]["binding"][field]
    with pytest.raises(ValueError, match="字段不完整"):
        recovery._validate_references(item)


def test_portable_binding_rejects_missing_or_forged_matching_original(client):
    exercise_consumer(client, "event_attachment", "business_attachments")
    item = snapshot(client)[0]
    for mutation in ("remove", "row_id", "scope", "parent", "hash"):
        forged = deepcopy(item)
        attachment = next(
            ref for ref in forged["references"] if ref["table"] == "business_attachments"
        )
        if mutation == "remove":
            forged["references"].remove(attachment)
            forged["tables"].remove("business_attachments")
        elif mutation == "row_id":
            attachment["row_id"] = "unrelated-original"
        elif mutation == "scope":
            attachment["scope"]["company_id"] = "other-company"
        elif mutation == "parent":
            attachment["links"]["event_id"] = "other-event"
        else:
            upload = next(ref for ref in forged["references"] if ref["table"] == "upload_intents")
            upload["upload"]["binding"]["storage"]["sha256"] = "0" * 64
        with pytest.raises(ValueError):
            recovery._validate_references(forged)


@pytest.mark.parametrize(
    "purpose,table,parent",
    [
        ("case_attachment", "case_attachments", "case_id"),
        ("intake_attachment", "intake_attachments", "intake_id"),
    ],
)
def test_same_company_original_cannot_be_cloned_to_an_unrelated_event(
    client, purpose, table, parent
):
    exercise_consumer(client, purpose, table)
    intent = one_intent(client, purpose)
    other = create(client, auth(client, "employee"))
    # Simulate a restored/corrupted catalog: legal FK scope and identical bytes
    # are insufficient to claim a transfer from a different case/intake parent.
    # This uses the real schema and preserves all native constraints.
    with client.app.state.service.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO business_attachments(id,event_id,firm_id,company_id,storage_key,filename,"
            "media_type,sha256,size_bytes,created_at,created_by) "
            f"SELECT id,?,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,created_at,created_by FROM {table} WHERE storage_key=?",
            (other["id"], intent["storage_key"]),
        )
    with pytest.raises(ValueError):
        snapshot(client)


def test_direct_event_original_cannot_gain_an_unrelated_reference_without_a_donor(client):
    exercise_consumer(client, "event_attachment", "business_attachments")
    intent = one_intent(client, "event_attachment")
    other = create(client, auth(client, "employee"))
    new_id = uuid4().hex
    # The runtime schema already has a per-table unique storage key. Verify it
    # rejects the duplicate row, then test the equivalent forged portable ref
    # independently rather than weakening a native constraint to create it.
    with pytest.raises(sqlite3.IntegrityError):
        with client.app.state.service.db.transaction(write=True) as conn:
            conn.execute(
                "INSERT INTO business_attachments(id,event_id,firm_id,company_id,storage_key,filename,"
                "media_type,sha256,size_bytes,created_at,created_by) "
                "SELECT ?,?,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,created_at,created_by "
                "FROM business_attachments WHERE storage_key=?",
                (new_id, other["id"], intent["storage_key"]),
            )
    item = next(item for item in snapshot(client) if item["storage_key"] == intent["storage_key"])
    forged = deepcopy(
        next(ref for ref in item["references"] if ref["table"] == "business_attachments")
    )
    forged["row_id"], forged["links"]["event_id"] = new_id, other["id"]
    item["references"].append(forged)
    recovery._validate_references(item)
    with sqlite3.connect(client.app.state.service.db.path) as conn:
        with pytest.raises(ValueError, match="来源关系"):
            recovery._validate_shared_lineage(conn, "sqlite", item)


def test_case_original_cannot_be_copied_before_its_response_is_accepted(client):
    exercise_consumer(client, "case_attachment", "case_attachments")
    intent = one_intent(client, "case_attachment")
    binding = json.loads(intent["binding_json"])
    with client.app.state.service.db.transaction(write=True) as conn:
        assert (
            conn.execute(
                "SELECT state FROM case_responses WHERE id=?", (binding["response_id"],)
            ).fetchone()[0]
            == "draft"
        )
        conn.execute(
            "INSERT INTO business_attachments(id,event_id,firm_id,company_id,storage_key,filename,"
            "media_type,sha256,size_bytes,created_at,created_by) "
            "SELECT id,?,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,created_at,created_by "
            "FROM case_attachments WHERE storage_key=?",
            (binding["event_id"], intent["storage_key"]),
        )
    with pytest.raises(ValueError, match="来源关系"):
        snapshot(client)
