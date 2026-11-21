"""Batch 04: four-tax period results are versioned and reviewed by a second person."""

import pytest

from app.accounting.seed import LOCAL_ACTOR
from app.tax.periods import AccountingError, TaxPeriodService
from review_policy_fixtures import independent_reviewer
from test_accounting import book, service  # noqa: F401 - fixtures


def payload(total=12_345):
    return {"total_cents": total, "lines": [{"code": "vat", "cents": total}]}


def reviewer(service, book):
    return independent_reviewer(service.db, LOCAL_ACTOR, book)


def test_draft_is_stored_verbatim_as_version_one(service, book):
    svc = TaxPeriodService(service.db)
    assert svc.list(LOCAL_ACTOR, book, period="2026-10") == []
    run = svc.record(
        LOCAL_ACTOR,
        book,
        period="2026-10",
        kind="vat",
        payload=payload(),
        source_digest="d" * 16,
    )
    assert run["status"] == "draft"
    assert run["version"] == 1
    assert run["payload"]["total_cents"] == 12_345


def test_second_record_increments_version(service, book):
    svc = TaxPeriodService(service.db)
    first = svc.record(
        LOCAL_ACTOR,
        book,
        period="2026-10",
        kind="vat",
        payload=payload(100),
        source_digest="a" * 16,
    )
    second = svc.record(
        LOCAL_ACTOR,
        book,
        period="2026-10",
        kind="vat",
        payload=payload(200),
        source_digest="b" * 16,
    )
    assert (first["version"], second["version"]) == (1, 2)


def test_author_cannot_review_own_version(service, book):
    svc = TaxPeriodService(service.db)
    run = svc.record(
        LOCAL_ACTOR, book, period="2026-10", kind="vat", payload=payload(), source_digest="d" * 16
    )
    with pytest.raises(AccountingError) as error:
        svc.review(LOCAL_ACTOR, book, run["id"], note="ok", expected_version=1)
    assert error.value.status == 409
    assert "制单责任人不能复核本人" in error.value.detail


def test_reviewer_marks_reviewed_with_note(service, book):
    svc = TaxPeriodService(service.db)
    run = svc.record(
        LOCAL_ACTOR,
        book,
        period="2026-10",
        kind="surtax",
        payload=payload(),
        source_digest="c" * 16,
    )
    done = svc.review(
        reviewer(service, book), book, run["id"], note="与申报表一致", expected_version=1
    )
    assert done["status"] == "reviewed"
    assert done["review_note"] == "与申报表一致"
    assert done["reviewed_by"]


def test_stale_expected_version_is_refused(service, book):
    svc = TaxPeriodService(service.db)
    run = svc.record(
        LOCAL_ACTOR,
        book,
        period="2026-10",
        kind="cit_prepayment",
        payload=payload(),
        source_digest="e" * 16,
    )
    with pytest.raises(AccountingError) as error:
        svc.review(reviewer(service, book), book, run["id"], note="x", expected_version=9)
    assert error.value.status == 409


def test_unknown_kind_and_float_amount_are_refused(service, book):
    svc = TaxPeriodService(service.db)
    with pytest.raises(AccountingError):
        svc.record(
            LOCAL_ACTOR,
            book,
            period="2026-10",
            kind="property_tax",
            payload=payload(),
            source_digest="f" * 16,
        )
    with pytest.raises(AccountingError):
        svc.record(
            LOCAL_ACTOR,
            book,
            period="2026-10",
            kind="vat",
            payload={"total_cents": 1.5},
            source_digest="f" * 16,
        )
