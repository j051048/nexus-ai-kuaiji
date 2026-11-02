"""P01 latest-business restore proof must contain actual data, not empty tables."""

import pytest
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.accounting.domain import Actor, AccountingError
from app.business.storage import AttachmentStore
from test_business import business  # noqa: F401


@pytest.fixture
def latest_case(business, tmp_path):
    from p01_recovery_business import seed_latest_recovery_business

    c = business
    store = AttachmentStore(tmp_path / "p01-originals")
    result = seed_latest_recovery_business(
        c.db,
        c.db,
        store,
        owner=c.manager,
        reviewer=c.actors["accountant"],
        customer=c.actors["finance"],
        customer_email="finance@example.test",
    )
    return SimpleNamespace(case=c, evidence=result, store=store)


def test_latest_recovery_fixture_has_tax_delivery_and_three_notification_states(latest_case):
    c, result = latest_case.case, latest_case.evidence
    assert result["tax_paper"]["status"] == "draft"
    assert result["tax_paper"]["filing"] == "not_filed"
    assert len(result["packages"]) == 4
    with c.db.transaction(actor=c.manager) as conn:
        assert {
            r["state"]
            for r in conn.execute(
                "SELECT state FROM delivery_notifications WHERE book_id=?",
                (result["delivery_book"]["id"],),
            )
        } == {"pending", "sending", "unknown"}
        assert (
            conn.execute("SELECT count(*) FROM monthly_delivery_acknowledgements").fetchone()[0]
            == 1
        )
        assert conn.execute("SELECT count(*) FROM monthly_delivery_opens").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM tax_profiles").fetchone()[0] >= 1
        assert conn.execute("SELECT count(*) FROM invoice_import_batches").fetchone()[0] == 1


def test_latest_business_http_shapes_and_guard_assertions(latest_case):
    """Router-level shape smoke only; the full PG drill verifies signed identities."""
    from app.tax.router import build_tax_router
    from app.tax.workpaper import TaxWorkpaperService
    from app.delivery.router import build_delivery_router
    from app.delivery.monthly_package import MonthlyPackageService
    from app.invoices.router import build_invoice_imports_router
    from app.invoices.service import InvoiceImportService
    from app.uploads import UploadIntentService
    from p01_recovery_business import verify_latest_business_http

    c = latest_case.case
    actors = {
        "owner": c.manager,
        "reviewer": c.actors["accountant"],
        "customer": c.actors["finance"],
        "employee": c.actors["employee"],
        "outsider": Actor("unknown-user", c.manager.firm_id),
    }
    app = FastAPI()

    def actor(request: Request):
        return actors[request.headers["X-Synthetic-Actor"]]

    @app.exception_handler(AccountingError)
    async def error(_request, failure):
        return JSONResponse({"detail": failure.detail}, status_code=failure.status)

    app.state.tax = TaxWorkpaperService(c.db)
    app.state.monthly_packages = MonthlyPackageService(c.db)
    from app.delivery.followups import DeliveryFollowupService

    app.state.delivery_followups = DeliveryFollowupService(c.db)
    app.state.invoice_imports = InvoiceImportService(
        c.db, latest_case.store, UploadIntentService(c.db, latest_case.store)
    )
    app.include_router(build_tax_router(actor))
    app.include_router(build_delivery_router(actor))
    app.include_router(build_invoice_imports_router(actor))
    headers = {name: {"X-Synthetic-Actor": name} for name in actors}
    with TestClient(app) as client:
        proof = verify_latest_business_http(
            client, headers, latest_case.evidence, wrong_book_id=c.other_book["id"]
        )
    assert all(proof.values())


def test_empty_recovery_business_tables_cannot_pass_by_equal_hashes():
    from p01_recovery_business import require_latest_business_inventory

    with pytest.raises(AssertionError, match="tax_profiles"):
        require_latest_business_inventory({"tables": {}})


def test_latest_domain_restore_keeps_old_notifications_unsent(latest_case, tmp_path):
    """Local SQLite smoke; PG roles/restore/release are proved by the full drill."""
    from app.accounting.database import Database
    from app.delivery.followups import DeliveryFollowupService, LocalSinkChannel
    from scripts import backup_accounting
    from p01_recovery_business import assert_notifications_fenced

    c, evidence = latest_case.case, latest_case.evidence
    archive, target = tmp_path / "archive", tmp_path / "restored"
    backup_accounting.backup(c.db.path, latest_case.store.root, archive)
    receipt = backup_accounting.restore(archive, target)
    assert receipt["serving_enabled"] is False
    copied = Database(target / "database.sqlite")
    try:
        with pytest.raises(AccountingError) as blocked:
            DeliveryFollowupService(copied).dispatch(LocalSinkChannel())
        assert blocked.value.status == 409
        # Test-only local SQLite release. Real PG uses audited release_local_hold.
        with sqlite3.connect(copied.path) as conn:
            conn.execute(
                "UPDATE restore_state SET hold=0,reason='synthetic verification complete',updated_by='p01-test' WHERE instance_id='restored'"
            )
        (target / "restore-verification.json").rename(target / "released-test-marker.json")
        assert assert_notifications_fenced(copied, evidence)["channel_calls"] == 0
        with copied.transaction(actor=c.manager) as conn:
            rows = {
                r["id"]: r["state"]
                for r in conn.execute("SELECT id,state FROM delivery_notifications")
            }
        assert rows[evidence["notifications"]["unknown"]] == "unknown"
        assert rows[evidence["notifications"]["sending"]] == "unknown"
        assert rows[evidence["notifications"]["pending"]] == "cancelled"
    finally:
        copied.close()
