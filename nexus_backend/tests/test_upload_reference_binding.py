"""F10 originals: real parent binding, atomic registration and uncertain-result retry."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import CompanyMemberGrant, DepartmentCreate
from app.accounting.database import Database
from app.business.storage import AttachmentStore
from app.business.models import BusinessEventCreate
from app.uploads import UploadIntentService
from app.upload_bindings import reference
from test_business import business, create, payload  # noqa: F401
from test_uploads import uploads, intent  # noqa: F401


def stored(c, actor):
    data, item = intent(c, actor)
    c.uploads.store_content(actor, item["id"], data)
    return item["id"]


def attach(c, actor, upload_id, event):
    return c.uploads.consume(
        actor,
        upload_id,
        "event_attachment",
        event["id"],
        lambda conn, metadata: c.service.add_attachment(
            actor, event["id"], event["version"], metadata, conn=conn
        ),
    )


def state(c, actor, upload_id):
    with c.db.transaction(actor=actor) as conn:
        return dict(
            conn.execute("SELECT * FROM upload_intents WHERE id=?", (upload_id,)).fetchone()
        )


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
    dept = c.directory.create_department(
        c.manager, c.other["id"], DepartmentCreate(name="第二企业部门")
    )
    c.directory.grant_company_member(
        c.manager,
        c.other["id"],
        CompanyMemberGrant(
            email="employee@example.test", role="employee", department_id=dept["id"]
        ),
    )
    other = c.service.create(
        actor,
        BusinessEventCreate(
            company_id=c.other["id"],
            department_id=dept["id"],
            template_id="reimbursement",
            payload=payload(),
        ),
    )
    with pytest.raises(AccountingError, match="其他企业或事项"):
        attach(c, actor, upload_id, other)
    assert c.service.get_event(actor, other["id"])["attachments"] == []


def test_replay_reauthorizes_the_actual_event_after_membership_is_revoked(uploads):
    c, actor = uploads, uploads.actors["employee"]
    event, upload_id = create(c), stored(c, actor)
    attach(c, actor, upload_id, event)
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "DELETE FROM company_memberships WHERE company_id=? AND user_id=?",
            (c.company["id"], actor.user_id),
        )
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


def test_lost_commit_acknowledgement_preserves_original_and_retry_does_not_repeat(
    uploads, monkeypatch
):
    c, actor = uploads, uploads.actors["employee"]
    event, upload_id = create(c), stored(c, actor)
    original_transaction = c.db.transaction
    lost = False

    @contextmanager
    def lose_one_ack(*args, **kwargs):
        nonlocal lost
        with original_transaction(*args, **kwargs) as conn:
            yield conn
        if kwargs.get("write") and kwargs.get("write_scope") == "company" and not lost:
            lost = True
            raise ConnectionError("injected commit acknowledgement loss")

    monkeypatch.setattr(c.db, "transaction", lose_one_ack)
    with pytest.raises(ConnectionError, match="acknowledgement"):
        attach(c, actor, upload_id, event)
    committed = state(c, actor, upload_id)
    assert committed["state"] == "consumed"
    assert c.store.backend.read(committed["storage_key"], committed["size_bytes"])
    retry = attach(c, actor, upload_id, event)
    assert len(retry["attachments"]) == 1 and retry["version"] == event["version"] + 1


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


@pytest.mark.parametrize(
    "field",
    [
        "company_id",
        "target_id",
        "event_id",
        "attachment_id",
        "attachment_table",
        "source_id",
        "storage",
    ],
)
def test_native_guard_rejects_forged_reference_and_rolls_back_original(uploads, field):
    c, actor = uploads, uploads.actors["employee"]
    event, upload_id = create(c), stored(c, actor)

    def forge(conn, metadata):
        result = c.service.add_attachment(actor, event["id"], event["version"], metadata, conn=conn)
        row = conn.execute("SELECT * FROM upload_intents WHERE id=?", (upload_id,)).fetchone()
        binding = reference(conn, actor, row, event["id"])
        binding[field] = (
            {"storage_key": "other/key", "sha256": "f" * 64, "size_bytes": 10}
            if field == "storage"
            else "forged"
        )
        conn.execute(
            "UPDATE upload_intents SET state='consumed',consumed_ref=?,binding_json=? WHERE id=?",
            (f"business_attachments:{upload_id}", json.dumps(binding), upload_id),
        )
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
            conn.execute(
                "UPDATE upload_intents SET consumed_ref='attachment' WHERE id=?", (upload_id,)
            )


def test_new_insert_cannot_create_a_fake_legacy_consumed_row(uploads):
    c, actor = uploads, uploads.actors["employee"]
    upload_id = stored(c, actor)
    with pytest.raises(sqlite3.IntegrityError, match="creation"):
        with c.db.transaction(write=True, actor=actor) as conn:
            conn.execute(
                "INSERT INTO upload_intents(id,firm_id,user_id,purpose,filename,media_type,"
                "size_bytes,sha256,storage_key,state,created_at,expires_at,consumed_ref,binding_json) "
                "SELECT 'ffffffffffffffffffffffffffffffff',firm_id,user_id,purpose,filename,media_type,"
                "size_bytes,sha256,'ff/ffffffffffffffffffffffffffffffff','consumed',created_at,expires_at,'attachment',NULL "
                "FROM upload_intents WHERE id=?",
                (upload_id,),
            )


def test_original_identity_cannot_change_after_consumption(uploads):
    c, actor = uploads, uploads.actors["employee"]
    event, upload_id = create(c), stored(c, actor)
    attach(c, actor, upload_id, event)
    for field, value in [
        ("sha256", "f" * 64),
        ("storage_key", "ff/" + "f" * 32),
        ("user_id", "peer"),
        ("firm_id", "other-firm"),
        ("purpose", "case_attachment"),
        ("size_bytes", 1),
    ]:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with c.db.transaction(write=True, actor=actor) as conn:
                conn.execute(f"UPDATE upload_intents SET {field}=? WHERE id=?", (value, upload_id))


def test_migration_preserves_legacy_reference_without_fabricating_verified_binding(
    tmp_path, monkeypatch
):
    import app.accounting.database as database_module

    real_files = database_module.migration_files
    # Build an actual pre-088 database, including its historically weak generic ref.
    with monkeypatch.context() as patch:
        patch.setattr(
            database_module,
            "migration_files",
            lambda dialect: [
                path
                for path in real_files(dialect)
                if not (dialect == "sqlite" and path.name.startswith("088_"))
            ],
        )
        c = business.__wrapped__(tmp_path)
        c.store = AttachmentStore(tmp_path / "legacy-originals")
        c.uploads = UploadIntentService(c.db, c.store)
        actor, event = c.actors["employee"], create(c)
        upload_id = stored(c, actor)
        metadata = c.uploads.take(actor, upload_id, "event_attachment")
        c.service.add_attachment(actor, event["id"], event["version"], metadata)
        with c.db.transaction(write=True, actor=actor) as conn:
            conn.execute(
                "UPDATE upload_intents SET state='consumed',consumed_ref='attachment' WHERE id=?",
                (upload_id,),
            )
        c.db.close()
    upgraded = Database(tmp_path / "business.db")
    c.db = upgraded
    c.uploads = UploadIntentService(upgraded, c.store)
    try:
        legacy = state(c, actor, upload_id)
        assert legacy["binding_json"] is None and legacy["consumed_ref"] == "attachment"
        with pytest.raises(AccountingError, match="历史消费记录"):
            c.uploads.consume(actor, upload_id, "event_attachment", event["id"], lambda *_: {})
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with upgraded.transaction(write=True, actor=actor) as conn:
                conn.execute("UPDATE upload_intents SET binding_json='{}' WHERE id=?", (upload_id,))
    finally:
        upgraded.close()
