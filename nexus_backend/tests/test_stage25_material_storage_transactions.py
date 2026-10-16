"""Slow original storage never holds a DB transaction or authorizes a later write."""

from contextlib import contextmanager

import pytest

from app.accounting.directory import DirectoryService
from app.accounting.domain import AccountingError
from app.business.storage import AttachmentStore
from app.service_plans.attachments import MaterialAttachmentService
from app.service_plans.models import MaterialReview, MaterialSubmit
from test_business import business  # noqa: F401
from test_business_postgres import business_case, business_postgres  # noqa: F401
from test_stage17_monthly_workbench import month_case  # noqa: F401
from test_stage25_service_plans import generated, no_activity, plans  # noqa: F401


def original_request(c, tmp_path):
    store = AttachmentStore(tmp_path / "material-originals")
    c.plans.attachments = MaterialAttachmentService(c.plans, store)
    _, request = generated(c)
    stored = store.put_original("statement.csv", "text/csv", b"date,amount\n2026-10-03,123.45\n")
    c.plans.attachments.add(c.employee, request["id"], 1, stored)
    return request, stored, store


def provided(identifier):
    return MaterialSubmit(
        expected_version=1,
        mode="provided",
        note="提交本月银行原件等待独立核验",
        attachment_ids=[identifier],
    )


def accepted():
    return MaterialReview(expected_version=2, action="accept", reason="独立核对原件和本期说明")


def action_count(c, request_id):
    with c.db.transaction(actor=c.owner) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM material_request_actions WHERE request_id=?", (request_id,)
        ).fetchone()[0]


def test_submit_and_accept_read_originals_only_after_database_transaction_is_released(
    plans, tmp_path, monkeypatch
):
    c = plans
    request, stored, store = original_request(c, tmp_path)
    transaction, read = c.db.transaction, store.read
    active_transactions, reads = [], []

    @contextmanager
    def tracked_transaction(*args, **kwargs):
        with transaction(*args, **kwargs) as conn:
            active_transactions.append(bool(kwargs.get("write", args[0] if args else False)))
            try:
                yield conn
            finally:
                active_transactions.pop()

    def checked_read(metadata):
        # This also catches a read-only connection retained over object I/O.
        # The subsequent write must obtain its own current ACL/CAS snapshot.
        assert active_transactions == []
        reads.append(metadata["id"])
        return read(metadata)

    monkeypatch.setattr(c.db, "transaction", tracked_transaction)
    monkeypatch.setattr(store, "read", checked_read)
    submitted = c.plans.submit(c.employee, request["id"], provided(stored["id"]))
    assert submitted["state"] == "submitted"
    result = c.plans.review(c.reviewer, request["id"], accepted())
    assert result["state"] == "accepted"
    assert reads == [stored["id"], stored["id"]]
    assert action_count(c, request["id"]) == 2


@pytest.mark.parametrize("phase", ["submit", "review"])
@pytest.mark.parametrize("change", ["membership", "version"])
def test_current_authorization_and_version_are_rechecked_after_original_verification(
    plans, tmp_path, monkeypatch, phase, change
):
    c = plans
    request, stored, _ = original_request(c, tmp_path)
    if phase == "review":
        c.plans.submit(c.employee, request["id"], provided(stored["id"]))
    verification = c.plans.attachments.verify
    directory = DirectoryService(c.db)
    changed, committed_count = [], []

    def verify_then_concurrent_change(*args, **kwargs):
        result = verification(*args, **kwargs)
        assert not changed
        changed.append(True)
        if change == "membership":
            if phase == "submit":
                directory.revoke_company_member(c.owner, c.company, c.employee.user_id)
            else:
                directory.revoke_book_member(c.owner, c.book, c.reviewer.user_id)
        elif phase == "submit":
            # A second tab submitted a different declaration while the first
            # tab was verifying its file. It legitimately owns version two.
            c.plans.submit(c.employee, request["id"], no_activity())
        else:
            # Another reviewer returned the current version during file I/O.
            c.plans.review(
                c.owner,
                request["id"],
                MaterialReview(
                    expected_version=2, action="return", reason="其他复核人已要求补充当期原件"
                ),
            )
        committed_count.append(action_count(c, request["id"]))
        return result

    monkeypatch.setattr(c.plans.attachments, "verify", verify_then_concurrent_change)
    with pytest.raises(AccountingError) as denied:
        if phase == "submit":
            c.plans.submit(c.employee, request["id"], provided(stored["id"]))
        else:
            c.plans.review(c.reviewer, request["id"], accepted())
    assert changed == [True]
    assert denied.value.status in ({403, 404} if change == "membership" else {409})
    # Only the intentional competing operation may persist; the stale or
    # newly unauthorized caller never appends an action or advances a version.
    assert action_count(c, request["id"]) == committed_count[0]
