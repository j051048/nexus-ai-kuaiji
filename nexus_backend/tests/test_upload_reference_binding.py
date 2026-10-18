"""F10 originals: real parent binding, atomic registration and uncertain-result retry."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import CompanyMemberGrant, DepartmentCreate
from app.business.models import BusinessEventCreate
from app.upload_bindings import reference
from test_business import business, create, payload  # noqa: F401
from test_uploads import uploads, intent  # noqa: F401


def stored(c, actor):
    data, item = intent(c, actor)
    c.uploads.store_content(actor, item["id"], data)
    return item["id"]


def attach(c, actor, upload_id, event):
    return c.uploads.consume(
        actor, upload_id, "event_attachment", event["id"],
        lambda conn, metadata: c.service.add_attachment(
            actor, event["id"], event["version"], metadata, conn=conn
        ),
    )


def state(c, actor, upload_id):
    with c.db.transaction(actor=actor) as conn:
        return dict(conn.execute("SELECT * FROM upload_intents WHERE id=?", (upload_id,)).fetchone())


def test_consumption_binds_actual_parent_and_storage_and_retries_without_writes(uploads):
    c, actor = uploads, uploads.actors["employee"]
    event, upload_id = create(c), stored(c, actor)
    first = attach(c, actor, upload_id, event)
    row = state(c, actor, upload_id)
    binding = json.loads(row["binding_json"])
    assert row["consumed_ref"] == f"business_attachments:{upload_id}"
    assert binding["target_id"] == event["id"] and binding["event_id"] == event["id"]
    assert binding["firm_id"] == actor.firm_id and binding["company_id"] == c.company["id"]
    assert binding["storage"] == {key: row[key] for key in ("storage_key", "sha256", "size_bytes")}
    # Simulate the first response disappearing after COMMIT. The caller still
    # has the old expected_version; retry reads the committed original once.
    second = attach(c, actor, upload_id, event)
    assert first == second and second["version"] == event["version"] + 1
    assert len(second["attachments"]) == 1


def test_reuse_cannot_bind_another_event_or_same_firm_company(uploads):
    c, actor = uploads, uploads.actors["employee"]
    first, another, upload_id = create(c), create(c), stored(c, actor)
    attach(c, actor, upload_id, first)
    with pytest.raises(AccountingError, match="其他企业或事项"):
        attach(c, actor, upload_id, another)
    dept = c.directory.create_department(c.manager, c.other["id"], DepartmentCreate(name="第二企业部门"))
    c.directory.grant_company_member(c.manager, c.other["id"], CompanyMemberGrant(
        email="employee@example.test", role="employee", department_id=dept["id"]
    ))
    other = c.service.create(actor, BusinessEventCreate(company_id=c.other["id"],
        department_id=dept["id"], template_id="reimbursement", payload=payload()))
    with pytest.raises(AccountingError, match="其他企业或事项"):
        attach(c, actor, upload_id, other)
    assert c.service.get_event(actor, other["id"])["attachments"] == []


def test_replay_reauthorizes_the_actual_event_after_membership_is_revoked(uploads):
    c, actor = uploads, uploads.actors["employee"]
    event, upload_id = create(c), stored(c, actor)
    attach(c, actor, upload_id, event)
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("DELETE FROM company_memberships WHERE company_id=? AND user_id=?", (c.company["id"], actor.user_id))
    with pytest.raises(AccountingError) as denied:
        attach(c, actor, upload_id, event)
    assert denied.value.status in {403, 404}


def test_callback_failure_rolls_back_attachment_version_and_claim(uploads):
    c, actor = uploads, uploads.actors["employee"]
    event, upload_id = create(c), stored(c, actor)

    def fail_after_insert(conn, metadata):
        c.service.add_attachment(actor, event["id"], event["version"], metadata, conn=conn)
        raise RuntimeError("injected failure before binding confirmation")

    with pytest.raises(RuntimeError, match="injected"):
        c.uploads.consume(actor, upload_id, "event_attachment", event["id"], fail_after_insert)
    assert state(c, actor, upload_id)["state"] == "stored"
    current = c.service.get_event(actor, event["id"])
    assert current["version"] == event["version"] and not current["attachments"]
    assert len(attach(c, actor, upload_id, event)["attachments"]) == 1


def test_arbitrary_ref_or_missing_registration_cannot_consume_an_intent(uploads):
    c, actor = uploads, uploads.actors["employee"]
    event, upload_id = create(c), stored(c, actor)
    with pytest.raises(AccountingError, match="已登记"):
        c.uploads.consume(actor, upload_id, "event_attachment", event["id"], lambda *_: {})
    assert state(c, actor, upload_id)["state"] == "stored"
    # Confirmation without a shared registration transaction is no longer an API.
    with pytest.raises(TypeError):
        c.uploads.confirm(actor, upload_id, "attachment")
    with pytest.raises(AccountingError) as peer:
        attach(c, c.actors["peer"], upload_id, event)
    assert peer.value.status == 404


@pytest.mark.parametrize("field", ["company_id", "target_id", "event_id", "attachment_id", "attachment_table", "source_id", "storage"])
def test_native_guard_rejects_forged_reference_and_rolls_back_original(uploads, field):
    c, actor = uploads, uploads.actors["employee"]
    event, upload_id = create(c), stored(c, actor)

    def forge(conn, metadata):
        result = c.service.add_attachment(actor, event["id"], event["version"], metadata, conn=conn)
        row = conn.execute("SELECT * FROM upload_intents WHERE id=?", (upload_id,)).fetchone()
        binding = reference(conn, actor, row, event["id"])
        binding[field] = {"storage_key": "other/key", "sha256": "f" * 64, "size_bytes": 10} if field == "storage" else "forged"
        conn.execute("UPDATE upload_intents SET state='consumed',consumed_ref=?,binding_json=? WHERE id=?",
            (f"business_attachments:{upload_id}", json.dumps(binding), upload_id))
        return result

    with pytest.raises(sqlite3.IntegrityError, match="binding"):
        c.uploads.consume(actor, upload_id, "event_attachment", event["id"], forge)
    assert state(c, actor, upload_id)["state"] == "stored"
    assert not c.service.get_event(actor, event["id"])["attachments"]


def test_two_concurrent_retries_only_register_one_attachment(uploads):
    c, actor = uploads, uploads.actors["employee"]
    event, upload_id = create(c), stored(c, actor)
    ready = Barrier(2)

    def send():
        ready.wait(timeout=5)
        return attach(c, actor, upload_id, event)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=15) for future in [pool.submit(send), pool.submit(send)]]
    assert results[0] == results[1]
    assert len(results[0]["attachments"]) == 1
    assert results[0]["version"] == event["version"] + 1


def test_consumed_binding_is_immutable(uploads):
    c, actor = uploads, uploads.actors["employee"]
    event, upload_id = create(c), stored(c, actor)
    attach(c, actor, upload_id, event)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with c.db.transaction(write=True, actor=actor) as conn:
            conn.execute("UPDATE upload_intents SET consumed_ref='attachment' WHERE id=?", (upload_id,))
