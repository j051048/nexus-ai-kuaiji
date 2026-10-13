"""Real SQLite commits with synthetic remote bytes and lost acknowledgements."""
from contextlib import contextmanager
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.seed import LOCAL_ACTOR
from app.business.models import BusinessEventCreate
from app.import_wizard.service import ImportWizardService
from app.intake.models import IntakeCreate
from app.intake.service import IntakeService
from app.legacy_migration.service import LegacyMigrationService
from test_accounting import service, book
from test_business import business, payload
from test_intelligence_workflow import ai_case
from test_shared_original_storage import SyntheticStorage, png
from test_stage12_import_wizard import CONTENT
from test_stage8_cases import setup as prepare_case


def lose_commit_ack(db, actor, table, row_id, monkeypatch):
    """Raise only after a real durable commit containing this original row."""
    original = db.transaction
    state = {"fired": False}

    @contextmanager
    def transaction(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            yield conn
        if kwargs.get("write") and not state["fired"]:
            with original(actor=actor) as conn:
                exists = conn.execute("SELECT 1 FROM " + table + " WHERE id=?", (row_id,)).fetchone()
            if exists:
                state["fired"] = True
                raise AccountingError("SYNTHETIC lost COMMIT acknowledgement", 503)

    monkeypatch.setattr(db, "transaction", transaction)
    return original, state


def test_bank_original_commit_lost_ack_does_not_delete_referenced_remote_bytes(service, book, monkeypatch):
    cloud = SyntheticStorage()
    store = cloud.store()
    wizard = ImportWizardService(service.db, store, service)
    original_transaction = service.db.transaction
    state = {"fired": False}

    @contextmanager
    def transaction(*args, **kwargs):
        with original_transaction(*args, **kwargs) as conn:
            yield conn
        if kwargs.get("write") and not state["fired"]:
            with original_transaction(actor=LOCAL_ACTOR) as conn:
                exists = conn.execute("SELECT 1 FROM bank_import_files WHERE book_id=?", (book,)).fetchone()
            if exists:
                state["fired"] = True
                raise AccountingError("SYNTHETIC lost COMMIT acknowledgement", 503)

    monkeypatch.setattr(service.db, "transaction", transaction)
    with pytest.raises(AccountingError):
        wizard.upload(LOCAL_ACTOR, book, "合成流水.csv", CONTENT.encode())
    assert state["fired"]
    with original_transaction(actor=LOCAL_ACTOR) as conn:
        metadata = dict(conn.execute("SELECT * FROM bank_import_files WHERE book_id=?", (book,)).fetchone())
    assert cloud.store().read(metadata) == CONTENT.encode()
    assert len(cloud.blobs) == 1 and not any(request.method == "DELETE" for request in cloud.requests)


def test_business_original_commit_lost_ack_and_revoke_are_safe(business, monkeypatch):
    case = business
    cloud, actor = SyntheticStorage(), case.actors["employee"]
    store = cloud.store()
    event = case.service.create(actor, BusinessEventCreate(company_id=case.company["id"], template_id="reimbursement", payload=payload()))
    item = store.put("合成.png", "image/png", png())
    original, state = lose_commit_ack(case.db, actor, "business_attachments", item["id"], monkeypatch)
    with pytest.raises(AccountingError):
        case.service.add_attachment(actor, event["id"], event["version"], item)
    # This is the exact cleanup performed by the upload route on domain errors.
    store.discard(item)
    assert state["fired"] and cloud.store().read(dict(item)) == png()
    assert not any(request.method == "DELETE" for request in cloud.requests)
    monkeypatch.setattr(case.db, "transaction", original)
    allowed = case.service.download_metadata(actor, item["id"])
    assert cloud.store().read(allowed) == png()
    case.directory.revoke_company_member(case.manager, case.company["id"], actor.user_id)
    before = len(cloud.requests)
    with pytest.raises(AccountingError):
        cloud.store().read(case.service.download_metadata(actor, item["id"]))
    assert len(cloud.requests) == before


def test_intake_original_commit_lost_ack_does_not_delete_and_other_employee_has_no_read(ai_case, monkeypatch):
    case, cloud = ai_case, SyntheticStorage()
    store, actor = cloud.store(), case.actors["employee"]
    intake = IntakeService(case.db, store, case.provider, business=case.business, intelligence=case.intelligence, meter=case.intelligence._meter())
    context = case.business.context(actor)
    department = next(item for item in context["companies"] if item["id"] == case.company["id"])["department_id"]
    draft = intake.create(actor, IntakeCreate(company_id=case.company["id"], department_id=department, idempotency_key=uuid4().hex))
    item = store.put("合成.png", "image/png", png())
    original, state = lose_commit_ack(case.db, actor, "intake_attachments", item["id"], monkeypatch)
    with pytest.raises(AccountingError):
        intake.add_attachment(actor, draft["id"], draft["version"], item)
    store.discard(item)
    assert state["fired"] and cloud.store().read(item) == png()
    monkeypatch.setattr(case.db, "transaction", original)
    assert intake.download(actor, item["id"])[0] == png()
    before = len(cloud.requests)
    with pytest.raises(AccountingError):
        intake.download(case.actors["peer"], item["id"])
    assert len(cloud.requests) == before


def test_supplement_original_commit_lost_ack_retains_evidence_and_private_scope(business, monkeypatch):
    case, cloud = business, SyntheticStorage()
    cases, _, _, task = prepare_case(case)
    store, actor = cloud.store(), case.actors["employee"]
    item = store.put("补充合成.png", "image/png", png())
    original, state = lose_commit_ack(case.db, actor, "case_attachments", item["id"], monkeypatch)
    with pytest.raises(AccountingError):
        cases.add_attachment(actor, task["id"], task["version"], uuid4().hex, item)
    store.discard(item)
    assert state["fired"] and cloud.store().read(item) == png()
    monkeypatch.setattr(case.db, "transaction", original)
    assert cloud.store().read(cases.download_metadata(actor, item["id"])) == png()
    before = len(cloud.requests)
    with pytest.raises(AccountingError):
        cloud.store().read(cases.download_metadata(case.actors["peer"], item["id"]))
    assert len(cloud.requests) == before


def test_legacy_http_upload_and_independent_worker_read_remote_originals(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.legacy_migration.worker import MigrationWorker
    from test_stage13_migration_api import prepare_http, upload, run_phase
    cloud = SyntheticStorage()
    app = create_app(tmp_path / "remote-legacy.db", seed=False, attachment_root=tmp_path / "unused-local", queue_worker_enabled=False)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 5512)) as client:
        case = prepare_http(client)
        api_store, worker_store = cloud.store(), cloud.store()
        app.state.legacy_migration.storage = api_store
        # These have distinct adapters and clients, with no shared directory.
        case.worker = MigrationWorker(LegacyMigrationService(case.db, worker_store))
        result = upload(case)
        run_phase(case, "parse")
        with case.db.transaction(actor=case.manager) as conn:
            metadata = dict(conn.execute("SELECT * FROM migration_files WHERE id=?", (result["file"]["id"],)).fetchone())
            assert conn.execute("SELECT count(*) FROM migration_records WHERE project_id=?", (case.project,)).fetchone()[0] > 0
        assert api_store.root is worker_store.root is None
        assert worker_store.read(metadata)
        before = len(cloud.requests)
        assert client.get(case.path + "/files/" + result["file"]["id"], headers=case.employee).status_code == 404
        assert len(cloud.requests) == before
        cloud.public = True
        assert client.get(case.path + "/files/" + result["file"]["id"], headers=case.owner).status_code == 503
