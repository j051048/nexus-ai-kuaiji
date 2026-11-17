"""FSC13 closing authority: external evidence never becomes a second closing."""

from __future__ import annotations

import pytest

from app.accounting.domain import AccountingError
from app.periodic.authority import ClosingAuthorityService
from app.periodic.models import ClosingEvidenceWrite, ClosingGenerate, DraftCommand
from app.periodic.service import PeriodicService
from app.tax.pilot import PilotBoundaryService
from test_b04_pilot import setup_b03
from test_business import business  # noqa: F401
from test_tax01_pilot import CONFIG, TP_CONFIG, deps
from review_policy_fixtures import independent_reviewer
from test_tax02_closing import _account, _approve_and_post, _closing, _line, _post


def _confirm(case, mode, config=None, expected_version=0):
    service = PilotBoundaryService(case.db)
    service.update(
        case.manager,
        case.company["id"],
        expected_version,
        config or {**CONFIG, "authority_mode": mode},
        deps(status="not_applicable", evidence="FSC13 合成客户确认"),
    )
    return service.confirm(case.manager, case.company["id"], expected_version + 1)


def _evidence(case, amount="100.00", source_id="CLOSE-1", version="1", details=None):
    return ClosingEvidenceWrite(
        book_id=case.book["id"],
        source_system="tplus",
        source_object="closing_voucher",
        source_id=source_id,
        source_version=version,
        amount=amount,
        reason="客户外部结转回执",
        details=details or {"voucher": "记-001"},
    )


def test_external_evidence_is_idempotent_and_conflicts_on_changed_content(business):
    case = business
    _confirm(case, "internal")
    service = ClosingAuthorityService(case.db)
    first = service.bind(case.manager, _evidence(case))
    assert first["status"] == "evidence_only"
    assert first["amount_cents"] == 10000

    replay = service.bind(case.manager, _evidence(case))
    assert replay["replayed"] is True
    assert replay["id"] == first["id"]

    with pytest.raises(AccountingError) as caught:
        service.bind(case.manager, _evidence(case, amount="101.00"))
    assert caught.value.status == 409
    assert "内容不同" in caught.value.detail

    rows = service.evidence(case.manager, case.book["id"])
    assert len(rows) == 1
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM vouchers").fetchone()[0] == 0


def test_external_authority_blocks_internal_closing_generation(business):
    case = business
    _confirm(case, "tplus_voucher", TP_CONFIG)
    profit = _account(case, "3103", "本年利润", "equity", "credit")
    with pytest.raises(AccountingError) as caught:
        PeriodicService(case.db).generate_closing(
            case.manager,
            ClosingGenerate(
                book_id=case.book["id"],
                period=case.book["period"],
                profit_account_id=profit["id"],
                profit_account_version=profit["version"],
                idempotency_key="fsc13-external-block",
                reason="FSC13 外部权威模式",
            ),
        )
    assert caught.value.status == 409
    assert "不能生成内部结转" in caught.value.detail


def test_posted_internal_closing_marks_external_evidence_conflict(business):
    case = business
    _confirm(case, "internal")
    opening, posting = setup_b03(case)
    income = _account(case, "500105", "主营收入", "income", "credit")
    profit = _account(case, "3103", "本年利润", "equity", "credit")
    _post(
        case,
        posting,
        [_line(income, credit="100.00"), _line(opening["bank"], debit="100.00")],
        "fsc13-post-income",
    )
    _approve_and_post(case, _closing(case, income, opening["expense"], profit))

    bound = ClosingAuthorityService(case.db).bind(case.manager, _evidence(case))
    assert bound["status"] == "conflict"
    with case.db.transaction(actor=case.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM periodic_runs WHERE book_id=? AND kind='closing' "
                "AND status='posted'",
                (case.book["id"],),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM vouchers WHERE book_id=?", (case.book["id"],)
            ).fetchone()[0]
            == 2
        )


def test_authority_mode_change_invalidates_existing_closing_draft(business):
    case = business
    _confirm(case, "internal")
    opening, posting = setup_b03(case)
    income = _account(case, "500105", "主营收入", "income", "credit")
    profit = _account(case, "3103", "本年利润", "equity", "credit")
    _post(
        case,
        posting,
        [_line(income, credit="100.00"), _line(opening["bank"], debit="100.00")],
        "fsc13-mode-income",
    )
    draft = _closing(case, income, opening["expense"], profit)
    _confirm(case, "tplus_voucher", TP_CONFIG, expected_version=1)
    reviewer = independent_reviewer(case.db, case.manager, case.book["id"])
    with pytest.raises(AccountingError) as caught:
        PeriodicService(case.db).approve(
            reviewer,
            draft["draft"]["id"],
            DraftCommand(expected_version=draft["draft"]["version"], reason="权威模式变化后复核"),
        )
    assert caught.value.status == 409
    assert "权威模式" in caught.value.detail


def test_unconfirmed_authority_cannot_accept_external_evidence(business):
    case = business
    with pytest.raises(AccountingError) as caught:
        ClosingAuthorityService(case.db).bind(case.manager, _evidence(case))
    assert caught.value.status == 409
    assert "尚未确认" in caught.value.detail
