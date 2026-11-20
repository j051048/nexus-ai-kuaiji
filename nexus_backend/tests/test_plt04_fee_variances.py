"""PLT04-B: explicit fee variance register, no automatic cause inference."""

import json

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest
from app.b04_close.service import CloseService
from app.tax.variances import FeeVarianceService
from test_business import business  # noqa: F401
from test_plt02_report_modes import _prepared


def _configure_reason(case, code="bank_fee"):
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "UPDATE accounting_policies SET fee_variance_reasons_json=? "
            "WHERE ledger_id=(SELECT ledger_id FROM ledger_periods WHERE book_id=?) AND status='active'",
            (
                json.dumps(
                    [
                        {
                            "code": code,
                            "label": "银行手续费",
                            "blocks_close": True,
                            "debit_account": "1002",
                            "credit_account": "6602.02",
                        }
                    ]
                ),
                case.book["id"],
            ),
        )


def test_variance_defaults_to_unresolved_and_blocks_close(business):
    case = business
    _prepared(case)
    service = FeeVarianceService(case.db)
    item = service.record(
        case.manager,
        case.book["id"],
        kind="underpaid",
        expected_cents=10_000,
        actual_cents=9_000,
        evidence_ref="synthetic://variance/under-1",
        idempotency_key="variance-under-1",
    )
    assert item["status"] == "pending"
    assert item["reason_code"] == "unresolved"
    assert item["difference_cents"] == -1_000
    assert service.list(case.manager, case.book["id"])["items"][0]["id"] == item["id"]
    close = CloseService(case.db)
    with case.db.transaction() as conn:
        blockers = service.close_blockers(conn, case.book["id"], case.book["period"])
        check = close._fee_variance_check(conn, case.book["id"], case.book["period"])
        assert check["code"] == "fee_variance_pending"
        assert check["status"] == "blocked"
        assert check["pending_count"] == 1
        with pytest.raises(AccountingError) as blocked:
            close._assert_no_open_fee_variances(conn, case.book["id"], case.book["period"])
        assert blocked.value.status == 409
    assert blockers == [
        {
            "code": "fee_variance_pending",
            "message": "规费差异仍待查或待处理",
            "variance_id": item["id"],
            "reason_code": "unresolved",
        }
    ]
    started = service.start(case.manager, item["id"])
    assert started["status"] == "pending"
    assert started["state"] == "processing"
    assert started["processing_started_by"] == case.manager.user_id
    again = service.start(case.manager, item["id"])
    assert again["state"] == "processing"
    with case.db.transaction() as conn:
        assert (
            service.close_blockers(conn, case.book["id"], case.book["period"])[0]["variance_id"]
            == item["id"]
        )


def test_configured_reason_handles_and_reverses_append_only(business):
    case = business
    _prepared(case)
    _configure_reason(case)
    service = FeeVarianceService(case.db)
    item = service.record(
        case.manager,
        case.book["id"],
        kind="overpaid",
        expected_cents=10_000,
        actual_cents=10_100,
        evidence_ref="synthetic://variance/over-1",
        idempotency_key="variance-over-1",
    )
    with pytest.raises(AccountingError) as missing:
        service.handle(case.manager, item["id"], reason_code="unresolved")
    assert missing.value.status == 422
    handled = service.handle(
        case.manager,
        item["id"],
        reason_code="bank_fee",
        evidence_ref="synthetic://policy/bank-fee",
    )
    assert handled["status"] == "handled"
    assert handled["handled_by"] == case.manager.user_id
    assert handled["draft_id"]
    draft = case.ledger.get_draft(case.manager, case.book["id"], handled["draft_id"])
    assert [(line["account_code"], line["debit"], line["credit"]) for line in draft["lines"]] == [
        ("1002", "1.00", "0.00"),
        ("6602.02", "0.00", "1.00"),
    ]
    with pytest.raises(AccountingError) as own_review:
        case.ledger.approve(
            case.manager,
            case.book["id"],
            handled["draft_id"],
            ApprovalRequest(expected_version=1, review_reason="处理人自审"),
        )
    assert own_review.value.status == 403
    approved = case.ledger.approve(
        case.actors["accountant"],
        case.book["id"],
        handled["draft_id"],
        ApprovalRequest(expected_version=1, review_reason="独立复核差异凭证"),
    )
    assert approved["status"] == "approved"
    with pytest.raises(AccountingError) as unknown:
        service.handle(case.manager, item["id"], reason_code="not_configured")
    assert unknown.value.status == 409
    with pytest.raises(AccountingError) as posted:
        service.reverse(case.manager, item["id"], reason="重复登记")
    assert posted.value.status == 409
    pending = service.record(
        case.manager,
        case.book["id"],
        kind="underpaid",
        expected_cents=2_000,
        actual_cents=1_900,
        evidence_ref="synthetic://variance/pending-reverse",
        idempotency_key="variance-pending-reverse",
    )
    service.handle(
        case.manager,
        pending["id"],
        reason_code="unresolved",
        evidence_ref="synthetic://variance/evidence",
    )
    reversed_item = service.reverse(case.manager, pending["id"], reason="重复登记")
    assert reversed_item["status"] == "reversed"
    assert reversed_item["reversal_reason"] == "重复登记"
    with case.db.transaction() as conn:
        assert service.close_blockers(conn, case.book["id"], case.book["period"]) == []
        check = CloseService(case.db)._fee_variance_check(
            conn, case.book["id"], case.book["period"]
        )
        assert check["status"] == "pass" and check["pending_count"] == 0
    with pytest.raises(AccountingError) as duplicate:
        service.reverse(case.manager, pending["id"], reason="再次撤销")
    assert duplicate.value.status == 409


@pytest.mark.parametrize(
    ("kind", "expected", "actual"),
    [
        ("underpaid", 10_000, 9_000),
        ("overpaid", 10_000, 10_100),
        ("unmatched", 0, 500),
        ("cross_period", 10_000, 9_000),
        ("many_to_one", 20_000, 10_000),
        ("one_to_many", 10_000, 20_000),
    ],
)
def test_each_variance_kind_generates_independently_reviewed_draft(
    business, kind, expected, actual
):
    case = business
    _prepared(case)
    _configure_reason(case)
    service = FeeVarianceService(case.db)
    item = service.record(
        case.manager,
        case.book["id"],
        kind=kind,
        expected_cents=expected,
        actual_cents=actual,
        evidence_ref=f"synthetic://variance/{kind}",
        idempotency_key=f"variance-{kind}",
    )
    handled = service.handle(
        case.manager,
        item["id"],
        reason_code="bank_fee",
        evidence_ref=f"synthetic://policy/{kind}",
    )
    assert handled["draft_id"]
    with pytest.raises(AccountingError) as own_review:
        case.ledger.approve(
            case.manager,
            case.book["id"],
            handled["draft_id"],
            ApprovalRequest(expected_version=1, review_reason="处理人自审"),
        )
    assert own_review.value.status == 403
    approved = case.ledger.approve(
        case.actors["accountant"],
        case.book["id"],
        handled["draft_id"],
        ApprovalRequest(expected_version=1, review_reason=f"独立复核 {kind} 差异凭证"),
    )
    assert approved["status"] == "approved"


def test_rate_equal_difference_is_not_auto_classified_and_cannot_handle_twice(business):
    case = business
    _prepared(case)
    _configure_reason(case)
    service = FeeVarianceService(case.db)
    item = service.record(
        case.manager,
        case.book["id"],
        kind="underpaid",
        expected_cents=10_500,
        actual_cents=10_000,
        evidence_ref="synthetic://variance/rate-equal-500",
        idempotency_key="variance-rate-equal-500",
    )
    assert item["difference_cents"] == -500
    assert item["reason_code"] == "unresolved"
    assert item["draft_id"] is None
    handled = service.handle(
        case.manager,
        item["id"],
        reason_code="bank_fee",
        evidence_ref="synthetic://policy/rate-equal-500",
    )
    assert handled["status"] == "handled"
    assert handled["draft_id"]
    with pytest.raises(AccountingError) as duplicate:
        service.handle(
            case.manager,
            item["id"],
            reason_code="bank_fee",
            evidence_ref="synthetic://policy/rate-equal-500-again",
        )
    assert duplicate.value.status == 409


def test_cross_period_variance_respects_closed_period_guard(business, monkeypatch):
    case = business
    _prepared(case)
    service = FeeVarianceService(case.db)
    # The DB trigger only permits a real reviewed close receipt; this test targets the
    # service-layer guard that reads the authoritative period status.
    closed_book = dict(case.book, period_status="closed")
    monkeypatch.setattr(service.ledgers.access, "book", lambda *args, **kwargs: closed_book)
    with pytest.raises(AccountingError) as locked:
        service.record(
            case.manager,
            case.book["id"],
            kind="cross_period",
            expected_cents=10_000,
            actual_cents=9_500,
            evidence_ref="synthetic://variance/locked-period",
            idempotency_key="variance-locked-period",
        )
    assert locked.value.status == 403
    assert "已关闭" in str(locked.value)


def test_policy_unresolved_reason_can_make_pending_advisory(business):
    case = business
    _prepared(case)
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "UPDATE accounting_policies SET fee_variance_reasons_json=? "
            "WHERE ledger_id=(SELECT ledger_id FROM ledger_periods WHERE book_id=?) AND status='active'",
            (
                json.dumps(
                    [{"code": "unresolved", "label": "待查", "blocks_close": False}]
                ),
                case.book["id"],
            ),
        )
    item = FeeVarianceService(case.db).record(
        case.manager,
        case.book["id"],
        kind="unmatched",
        expected_cents=0,
        actual_cents=500,
        evidence_ref="synthetic://variance/advisory",
        idempotency_key="variance-advisory",
    )
    close = CloseService(case.db)
    with case.db.transaction() as conn:
        check = close._fee_variance_check(conn, case.book["id"], case.book["period"])
        assert check["status"] == "pass"
        assert check["advisory"] is True
        assert check["pending_count"] == 1
        close._assert_no_open_fee_variances(conn, case.book["id"], case.book["period"])
    assert item["status"] == "pending"


def test_fee_variance_is_actionable_workspace_task_until_handled(business):
    from app.accounting.review import ReviewService
    from app.cases.service import CaseService
    from app.workspace.service import WorkspaceService

    case = business
    _prepared(case)
    _configure_reason(case)
    service = FeeVarianceService(case.db)
    item = service.record(
        case.manager,
        case.book["id"],
        kind="unmatched",
        expected_cents=0,
        actual_cents=500,
        evidence_ref="synthetic://variance/task-1",
        idempotency_key="variance-task-1",
    )
    workspace = WorkspaceService(
        case.db,
        case.ledger,
        case.service,
        ReviewService(case.db, case.ledger),
        CaseService(case.db, case.service),
    )
    tasks = workspace.list(case.manager, bucket="all", kind="fee_variance")
    assert len(tasks["tasks"]) == 1
    assert workspace.list(case.actors["accountant"], bucket="all", kind="fee_variance")["tasks"] == []
    task = tasks["tasks"][0]
    assert task["ref"] == item["id"]
    assert task["title"].startswith("核对到账差额")
    assert task["action_label"] == "核对到账差额"
    assert task["responsible"] == "未分配"
    assert task["period"] == "2026-10"
    assert task["available_action"] == "can_act"

    service.start(case.manager, item["id"])
    service.handle(
        case.manager,
        item["id"],
        reason_code="bank_fee",
        evidence_ref="synthetic://variance/task-1/fee",
    )
    assert workspace.list(case.manager, bucket="all", kind="fee_variance")["tasks"] == []


def test_manager_can_assign_variance_to_qualified_editor_only(business):
    case = business
    _prepared(case)
    service = FeeVarianceService(case.db)
    item = service.record(
        case.manager,
        case.book["id"],
        kind="unmatched",
        expected_cents=0,
        actual_cents=500,
        evidence_ref="synthetic://variance/assign",
        idempotency_key="variance-assign-1",
    )
    finance = case.actors["finance"]
    assigned = service.assign(case.manager, item["id"], finance.user_id, "转交财务核对")
    assert assigned["processing_started_by"] == finance.user_id
    assert assigned["state"] == "processing"

    from app.accounting.review import ReviewService
    from app.cases.service import CaseService
    from app.workspace.service import WorkspaceService

    workspace = WorkspaceService(
        case.db,
        case.ledger,
        case.service,
        ReviewService(case.db, case.ledger),
        CaseService(case.db, case.service),
    )
    tasks = workspace.list(finance, bucket="all", kind="fee_variance")
    assert [task["ref"] for task in tasks["tasks"]] == [item["id"]]

    with pytest.raises(AccountingError) as forbidden:
        service.assign(case.actors["accountant"], item["id"], finance.user_id, "越权转交")
    assert forbidden.value.status == 403
    with pytest.raises(AccountingError) as same:
        service.assign(case.manager, item["id"], finance.user_id, "重复转交")
    assert same.value.status == 409
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE book_id=? AND user_id=?",
            (case.book["id"], finance.user_id),
        )
    with pytest.raises(AccountingError) as revoked:
        service.assign(case.manager, item["id"], finance.user_id, "已撤权")
    assert revoked.value.status == 409
    with pytest.raises(AccountingError) as missing:
        service.assign(case.manager, item["id"], "missing-user", "无此人")
    assert missing.value.status == 409


def _bank_source(conn, book_id, source_id, amount, kind="bank", date="2026-10-18"):
    from uuid import uuid4

    conn.execute(
        "INSERT INTO sources(id,book_id,external_id,source_name,date,description,"
        "counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            source_id,
            book_id,
            f"ext-{source_id}",
            f"流水 {source_id}",
            date,
            "测试流水",
            "虚构平台",
            amount,
            "income",
            "银行回单",
            uuid4().hex,
            kind,
        ),
    )


def test_variance_actual_source_must_be_a_matching_bank_flow(business):
    case = business
    _prepared(case)
    service = FeeVarianceService(case.db)
    with case.db.transaction(write=True, actor=case.manager) as conn:
        _bank_source(conn, case.book["id"], "variance-business", 800, kind="business")
        _bank_source(conn, case.book["id"], "variance-bank", 700)

    with pytest.raises(AccountingError) as wrong_kind:
        service.record(
            case.manager,
            case.book["id"],
            kind="underpaid",
            expected_cents=1_000,
            actual_cents=800,
            bank_source_id="variance-business",
            idempotency_key="variance-wrong-kind",
        )
    assert wrong_kind.value.status == 422
    assert "不是银行流水" in str(wrong_kind.value)

    with pytest.raises(AccountingError) as wrong_amount:
        service.record(
            case.manager,
            case.book["id"],
            kind="underpaid",
            expected_cents=1_000,
            actual_cents=800,
            bank_source_id="variance-bank",
            idempotency_key="variance-wrong-amount",
        )
    assert wrong_amount.value.status == 422
    assert "金额与所选银行流水不一致" in str(wrong_amount.value)


def test_variance_relation_binds_settlement_bank_and_amount(business):
    case = business
    _prepared(case)
    service = FeeVarianceService(case.db)
    with case.db.transaction(write=True, actor=case.manager) as conn:
        _bank_source(conn, case.book["id"], "variance-settle", 1_100, kind="business")
        _bank_source(conn, case.book["id"], "variance-bank-2", 800)

    with pytest.raises(AccountingError) as mismatch:
        service.record(
            case.manager,
            case.book["id"],
            kind="overpaid",
            expected_cents=400,
            actual_cents=800,
            settlement_source_id="variance-settle",
            bank_source_id="variance-bank-2",
            idempotency_key="variance-relation-mismatch",
        )
    assert mismatch.value.status == 422
    assert "来源关系差额与差异登记金额不一致" in str(mismatch.value)

    item = service.record(
        case.manager,
        case.book["id"],
        kind="overpaid",
        expected_cents=500,
        actual_cents=800,
        settlement_source_id="variance-settle",
        bank_source_id="variance-bank-2",
        idempotency_key="variance-relation-ok",
    )
    assert item["relation_id"]
    assert item["bank_source_id"] == "variance-bank-2"


def test_stale_relation_blocks_difference_draft_with_409(business):
    """A relation pins its source versions; re-importing a source must stop the draft."""
    case = business
    _prepared(case)
    _configure_reason(case)
    service = FeeVarianceService(case.db)
    with case.db.transaction(write=True, actor=case.manager) as conn:
        _bank_source(conn, case.book["id"], "relation-stale-settle", 1_100, kind="business")
        _bank_source(conn, case.book["id"], "relation-stale-bank", 800)
    item = service.record(
        case.manager,
        case.book["id"],
        kind="overpaid",
        expected_cents=500,
        actual_cents=800,
        settlement_source_id="relation-stale-settle",
        bank_source_id="relation-stale-bank",
        idempotency_key="relation-stale",
    )
    assert item["relation_id"]
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "UPDATE sources SET fingerprint=? WHERE id=?",
            ("reimported-fingerprint", "relation-stale-bank"),
        )
    with pytest.raises(AccountingError) as stale:
        service.handle(
            case.manager,
            item["id"],
            reason_code="bank_fee",
            evidence_ref="synthetic://policy/stale",
        )
    assert stale.value.status == 409
    assert "已换版" in str(stale.value)


def test_same_month_same_amount_rows_keep_separate_relations(business):
    """Two equal differences in one month must not share a source relation."""
    case = business
    _prepared(case)
    service = FeeVarianceService(case.db)
    with case.db.transaction(write=True, actor=case.manager) as conn:
        _bank_source(conn, case.book["id"], "same-settle-a", 1_100, kind="business")
        _bank_source(conn, case.book["id"], "same-bank-a", 800)
        _bank_source(conn, case.book["id"], "same-settle-b", 1_100, kind="business")
        _bank_source(conn, case.book["id"], "same-bank-b", 800)
    first = service.record(
        case.manager,
        case.book["id"],
        kind="overpaid",
        expected_cents=500,
        actual_cents=800,
        settlement_source_id="same-settle-a",
        bank_source_id="same-bank-a",
        idempotency_key="same-row-a",
    )
    second = service.record(
        case.manager,
        case.book["id"],
        kind="overpaid",
        expected_cents=500,
        actual_cents=800,
        settlement_source_id="same-settle-b",
        bank_source_id="same-bank-b",
        idempotency_key="same-row-b",
    )
    assert first["id"] != second["id"]
    assert first["relation_id"] and second["relation_id"]
    assert first["relation_id"] != second["relation_id"]
    assert first["bank_source_id"] == "same-bank-a"
    assert second["bank_source_id"] == "same-bank-b"
