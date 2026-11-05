"""Existing manual-review engine behind the new original-voucher correction entry."""

from uuid import uuid4
from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError
from app.accounting.directory import DirectoryService
from app.accounting.review_policy_models import ManualReviewPost
from test_business_postgres import business_case, business_postgres  # noqa: F401
from test_review_policy_postgres import _manual, _prepared_manual, _review_manual


@pytest.mark.parametrize("kind", ["adjustment", "reversal"])
def test_correction_preserves_original_and_requires_independent_review(business_case, kind):
    c = business_case
    service, request, prepared = _prepared_manual(c)
    approved = _review_manual(c, service, prepared)
    original = service.post(
        c.owner,
        prepared["id"],
        ManualReviewPost(expected_version=approved["version"], command_key=request.command_key),
    )
    lines = (
        [
            line.model_copy(update={"debit": line.credit, "credit": line.debit})
            for line in request.lines
        ]
        if kind == "reversal"
        else request.lines
    )
    correction = request.model_copy(
        update={
            "command_key": uuid4().hex,
            "original_voucher_id": original["voucher"]["id"],
            "relation_kind": kind,
            "lines": lines,
            "reason": "虚构更正独立核对",
        }
    )
    pending = service.prepare(c.owner, correction)["request"]
    with pytest.raises(AccountingError) as denied:
        _review_manual(c, service, pending, actor=c.owner)
    assert denied.value.status == 403
    with pytest.raises(AccountingError):
        service.post(
            c.owner,
            pending["id"],
            ManualReviewPost(
                expected_version=pending["version"], command_key=correction.command_key
            ),
        )
    approved = _review_manual(c, service, pending)
    command = ManualReviewPost(
        expected_version=approved["version"], command_key=correction.command_key
    )
    result = service.post(c.owner, pending["id"], command)
    assert service.post(c.owner, pending["id"], command)["voucher"]["id"] == result["voucher"]["id"]
    assert result["voucher"]["review_mode"] == "independent"
    with c.db.transaction(actor=c.owner) as conn:
        current = dict(
            conn.execute(
                "SELECT * FROM vouchers WHERE id=?", (original["voucher"]["id"],)
            ).fetchone()
        )
        assert current == original["voucher"]
        relations = conn.execute(
            "SELECT * FROM voucher_relations WHERE original_voucher_id=?",
            (original["voucher"]["id"],),
        ).fetchall()
        assert len(relations) == 1


def test_correction_cannot_use_another_book_or_revoked_reviewer(business_case):
    c = business_case
    service, request, prepared = _prepared_manual(c)
    approved = _review_manual(c, service, prepared)
    original = service.post(
        c.owner,
        prepared["id"],
        ManualReviewPost(expected_version=approved["version"], command_key=request.command_key),
    )
    other = _manual(SimpleNamespace(**{**vars(c), "book": c.other_book}))
    with pytest.raises(AccountingError) as cross_book:
        service.prepare(
            c.owner,
            other.model_copy(
                update={
                    "original_voucher_id": original["voucher"]["id"],
                    "relation_kind": "adjustment",
                }
            ),
        )
    assert cross_book.value.status == 409
    wrong = request.model_copy(
        update={
            "command_key": uuid4().hex,
            "original_voucher_id": str(uuid4()),
            "relation_kind": "adjustment",
        }
    )
    with pytest.raises(AccountingError):
        service.prepare(c.owner, wrong)
    pending = service.prepare(
        c.owner, wrong.model_copy(update={"original_voucher_id": original["voucher"]["id"]})
    )["request"]
    DirectoryService(c.db).revoke_book_member(c.owner, c.book, c.reviewer.user_id)
    with pytest.raises(AccountingError):
        _review_manual(c, service, pending)
