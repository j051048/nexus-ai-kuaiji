import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.accounting.database import Database
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import ApprovalRequest, BookCreate, ExportRequest
from app.accounting.seed import LOCAL_ACTOR, bootstrap
from app.accounting.service import AccountingService
from app.monthly.models import (
    BaselineCreate,
    CheckCreate,
    MonthlyReason,
    PlanApproval,
    PlanCreate,
    PlanGenerate,
)
from app.monthly.router import build_monthly_router
from app.monthly.service import MonthlyService
from test_accounting import make_draft
from test_stage13_legacy_settlements import legacy_case
from test_business import business


@pytest.fixture
def monthly_case(tmp_path):
    db = Database(tmp_path / "monthly.db")
    accounting = AccountingService(db)
    bootstrap(accounting, seed_examples=False)
    book = accounting.create_book(LOCAL_ACTOR, BookCreate(name="虚构月度测试", period="2026-10"))
    reviewer = Actor("monthly-reviewer", LOCAL_ACTOR.firm_id)
    with db.transaction(write=True) as conn:
        conn.execute("INSERT INTO users(id,name) VALUES(?,'独立会计')", (reviewer.user_id,))
        conn.execute(
            "INSERT INTO memberships VALUES(?,?,'reviewer')", (reviewer.firm_id, reviewer.user_id)
        )
        conn.execute(
            "INSERT INTO book_access(book_id,firm_id,user_id,role) VALUES(?,?,?,'reviewer')",
            (book["id"], reviewer.firm_id, reviewer.user_id),
        )
    return SimpleNamespace(
        db=db,
        accounting=accounting,
        monthly=MonthlyService(db, accounting),
        owner=LOCAL_ACTOR,
        reviewer=reviewer,
        book=book["id"],
        company=book["company_id"],
    )


def baseline(kind="opening", balance="100.00", code="1002", **changes):
    return BaselineCreate(
        **{
            "kind": kind,
            "external_reference": "客户提供 T+ 单月已记账余额表",
            "evidence_ref": "私有原件:合成余额表",
            "reason": "仅核对提供的科目净借余额",
            "lines": [{"account_code": code, "balance": balance}],
            "idempotency_key": uuid4().hex,
            **changes,
        }
    )


def confirmed(case, kind, amount):
    row = case.monthly.create_baseline(case.owner, case.book, baseline(kind, amount))
    return case.monthly.confirm_baseline(
        case.reviewer, case.book, row["id"], MonthlyReason(reason="独立核对客户所提供余额")
    )


def plan(**changes):
    return PlanCreate(
        **{
            "name": "明确合同租金",
            "start_period": "2026-10",
            "description": "当月已确认合同租金",
            "evidence_ref": "私有原件:租赁合同",
            "reason": "每月金额由会计明确配置",
            "lines": [
                {"account_code": "6602.01", "debit": "0.30", "credit": "0.00"},
                {"account_code": "2241", "debit": "0.00", "credit": "0.30"},
            ],
            "idempotency_key": uuid4().hex,
            **changes,
        }
    )


def test_unknown_baseline_is_not_zero_and_only_listed_dimensions_known(monthly_case):
    case = monthly_case
    make_draft(case.accounting, case.book)
    overview = case.monthly.overview(case.owner, case.book)
    assert all(
        x["baseline"] is None and x["reference_closing"] is None
        for x in overview["reference"]["lines"]
    )
    confirmed(case, "opening", "0.00")
    lines = {
        x["account_code"]: x
        for x in case.monthly.overview(case.owner, case.book)["reference"]["lines"]
    }
    assert lines["1002"]["baseline"] == "0.00"
    assert lines["6602.02"]["baseline"] is None
    assert lines["1002"]["unreviewed"]["credit"] == "0.30"
    assert lines["1002"]["reference_closing"] == "0.00"


def test_baseline_version_independent_confirmation_and_scope(monthly_case):
    case = monthly_case
    request = baseline(balance="-10.30")
    row = case.monthly.create_baseline(case.owner, case.book, request)
    assert row["period"] == "2026-09" and row["lines"][0]["balance"] == "-10.30"
    assert case.monthly.create_baseline(case.owner, case.book, request)["id"] == row["id"]
    with pytest.raises(AccountingError, match="另一位"):
        case.monthly.confirm_baseline(
            case.owner, case.book, row["id"], MonthlyReason(reason="试图自行确认")
        )
    newer = case.monthly.create_baseline(case.owner, case.book, baseline(balance="-10.20"))
    with pytest.raises(AccountingError, match="替代"):
        case.monthly.confirm_baseline(
            case.reviewer, case.book, row["id"], MonthlyReason(reason="试图审核旧版")
        )
    case.monthly.confirm_baseline(
        case.reviewer, case.book, newer["id"], MonthlyReason(reason="独立确认新版本")
    )
    foreign = case.accounting.create_book(
        case.owner, BookCreate(name="另一虚构企业", period="2026-10")
    )["id"]
    with pytest.raises(AccountingError):
        case.monthly.confirm_baseline(
            case.reviewer, foreign, newer["id"], MonthlyReason(reason="无权其他企业")
        )


def test_approved_and_exported_pending_receipt_do_not_become_reference_movement(monthly_case):
    case = monthly_case
    confirmed(case, "opening", "100.00")
    draft = make_draft(case.accounting, case.book)
    draft = case.accounting.approve(
        case.reviewer, case.book, draft["id"], ApprovalRequest(expected_version=draft["version"])
    )
    lines = {
        x["account_code"]: x
        for x in case.monthly.overview(case.owner, case.book)["reference"]["lines"]
    }
    assert lines["1002"]["approved_pending_delivery"]["credit"] == "0.30"
    assert lines["1002"]["reference_closing"] == "100.00"
    case.accounting.export(
        case.owner, case.book, ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex)
    )
    lines = {
        x["account_code"]: x
        for x in case.monthly.overview(case.owner, case.book)["reference"]["lines"]
    }
    assert lines["1002"]["exported_pending_receipt"]["credit"] == "0.30"
    assert lines["1002"]["verified_delivery"]["credit"] == "0.00"
    assert lines["1002"]["reference_closing"] == "100.00"


def test_check_retains_differences_and_stales_when_draft_or_baseline_changes(monthly_case):
    case = monthly_case
    opening, closing = confirmed(case, "opening", "100.00"), confirmed(case, "closing", "98.00")
    request = CheckCreate(
        opening_id=opening["id"], closing_id=closing["id"], idempotency_key=uuid4().hex
    )
    check = case.monthly.create_check(case.owner, case.book, request)
    assert check["report"]["lines"][0]["difference"] == "-2.00"
    assert case.monthly.create_check(case.owner, case.book, request)["id"] == check["id"]
    closed = case.monthly.close_check(
        case.owner,
        case.book,
        check["id"],
        MonthlyReason(reason="客户只提供部分资料，差异保留后续追查"),
    )
    assert closed["state"] == "manually_checked" and closed["report"]["difference_count"] == 1
    make_draft(case.accounting, case.book)
    assert not case.monthly.overview(case.owner, case.book)["checks"][0]["current"]
    with pytest.raises(AccountingError, match="变化"):
        case.monthly.close_check(
            case.owner, case.book, check["id"], MonthlyReason(reason="试图覆盖历史关闭记录")
        )


def test_recurring_draft_independent_approval_month_idempotence_review_export(monthly_case):
    case = monthly_case
    row = case.monthly.create_plan(case.owner, case.book, plan())
    with pytest.raises(AccountingError):
        case.monthly.generate(
            case.owner, case.book, row["id"], PlanGenerate(version=1, idempotency_key=uuid4().hex)
        )
    with pytest.raises(AccountingError, match="另一位"):
        case.monthly.approve_plan(
            case.owner, case.book, row["id"], PlanApproval(version=1, reason="试图自行审批")
        )
    row = case.monthly.approve_plan(
        case.reviewer,
        case.book,
        row["id"],
        PlanApproval(version=1, reason="按合同独立核对金额与科目"),
    )
    request = PlanGenerate(version=1, idempotency_key=uuid4().hex)
    result = case.monthly.generate(case.owner, case.book, row["id"], request)
    assert result["created"]
    replay = case.monthly.generate(
        case.owner, case.book, row["id"], PlanGenerate(version=1, idempotency_key=uuid4().hex)
    )
    assert replay["draft_id"] == result["draft_id"] and not replay["created"]
    draft = case.accounting.get_draft(case.owner, case.book, result["draft_id"])
    assert draft["status"] == "draft" and not draft["issues"]
    with pytest.raises(AccountingError):
        case.accounting.export(
            case.owner,
            case.book,
            ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex),
        )
    case.accounting.approve(
        case.reviewer, case.book, draft["id"], ApprovalRequest(expected_version=draft["version"])
    )
    assert (
        case.accounting.export(
            case.reviewer,
            case.book,
            ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex),
        )["count"]
        == 1
    )


def test_recurring_new_version_does_not_duplicate_old_month_and_catalog_changes_block(monthly_case):
    from app.accounting.catalog_models import AccountUpdate

    case = monthly_case
    row = case.monthly.create_plan(case.owner, case.book, plan())
    case.monthly.approve_plan(
        case.reviewer, case.book, row["id"], PlanApproval(version=1, reason="独立确认固定支出")
    )
    case.monthly.generate(
        case.owner, case.book, row["id"], PlanGenerate(version=1, idempotency_key=uuid4().hex)
    )
    row = case.monthly.create_plan(
        case.owner, case.book, plan(plan_id=row["id"], expected_version=1)
    )
    case.monthly.approve_plan(
        case.reviewer, case.book, row["id"], PlanApproval(version=2, reason="审批明确的新版本")
    )
    with pytest.raises(AccountingError, match="重复"):
        case.monthly.generate(
            case.owner, case.book, row["id"], PlanGenerate(version=2, idempotency_key=uuid4().hex)
        )
    case.monthly.catalog.update_account(
        case.owner,
        case.book,
        "2241",
        AccountUpdate(
            code="2241",
            name="其他应付款新定义",
            category="liability",
            normal_balance="credit",
            expected_version=1,
        ),
    )
    assert case.monthly.overview(case.owner, case.book)["plans"][0]["issues"]


def test_exact_money_and_closed_period_native_guards(monthly_case):
    case = monthly_case
    for value in (0.1, "0.001", "-NaN", "1e2"):
        with pytest.raises(ValidationError):
            baseline(balance=value)
    row = case.monthly.create_baseline(case.owner, case.book, baseline())
    with pytest.raises(Exception):
        with case.db.transaction(write=True) as conn:
            conn.execute("UPDATE monthly_baselines SET lines_json='[]' WHERE id=?", (row["id"],))
    with pytest.raises(Exception):
        with case.db.transaction(write=True) as conn:
            conn.execute(
                "INSERT INTO monthly_baseline_confirmations VALUES(?,?,?,?,?,?,?)",
                (
                    row["id"],
                    case.book,
                    case.owner.firm_id,
                    case.company,
                    case.owner.user_id,
                    "伪造自审记录",
                    "2026-10-14",
                ),
            )
    from period_fixtures import restore_period_state

    restore_period_state(case.db, case.book)
    with pytest.raises(AccountingError, match="已关"):
        case.monthly.create_plan(case.owner, case.book, plan())


def test_monthly_http_routes_retain_review_gate(monthly_case):
    case = monthly_case
    app = FastAPI()
    app.state.monthly = case.monthly
    app.include_router(build_monthly_router(lambda: case.owner))
    with TestClient(app) as client:
        assert client.get(f"/api/books/{case.book}/monthly").json()["live_tplus_connected"] is False
        result = client.post(f"/api/books/{case.book}/monthly/plans", json=plan().model_dump())
        assert result.status_code == 201 and not result.json()["approved"]
        result = client.post(
            f"/api/books/{case.book}/monthly/baselines", json=baseline().model_dump()
        )
        assert result.status_code == 201 and not result.json()["confirmed"]


def test_import_preserves_original_utf8_and_rejects_wrong_period_or_scope(monthly_case):
    from app.monthly.models import BaselineImport

    case = monthly_case
    raw = (
        " \n"
        + json.dumps(
            {
                "period": "2026-09",
                "currency": "CNY",
                "scope": "posted",
                "lines": [{"account_code": "1002", "balance": "123.45"}],
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    request = BaselineImport(
        kind="opening",
        format="json",
        filename="客户合成余额.json",
        content=raw,
        external_reference="客户外部已记账余额文件",
        evidence_ref="客户合成文件",
        reason="提供仅已记账的期末余额",
        idempotency_key=uuid4().hex,
    )
    row = case.monthly.import_baseline(case.owner, case.book, request)
    assert case.monthly.original(case.reviewer, case.book, row["id"])["content"] == raw
    assert row["original"]["format"] == "json"
    assert case.monthly.import_baseline(case.owner, case.book, request)["id"] == row["id"]
    with pytest.raises(AccountingError):
        case.monthly.import_baseline(
            case.owner,
            case.book,
            request.model_copy(
                update={"content": raw.replace("posted", "all"), "idempotency_key": uuid4().hex}
            ),
        )
    with pytest.raises(AccountingError):
        case.monthly.import_baseline(
            case.owner,
            case.book,
            request.model_copy(
                update={
                    "content": raw.replace("2026-09", "2026-10"),
                    "idempotency_key": uuid4().hex,
                }
            ),
        )


def test_recurring_approved_plan_reuses_long_ledger_next_month_and_scope_revocation(monthly_case):
    from app.ledgers.models import PeriodCreate
    from app.ledgers.service import LedgersService

    case = monthly_case
    row = case.monthly.create_plan(case.owner, case.book, plan())
    case.monthly.approve_plan(
        case.reviewer,
        case.book,
        row["id"],
        PlanApproval(version=1, reason="核对真实科目与固定金额"),
    )
    new_book = LedgersService(case.db).create_period(
        case.owner,
        case.book,
        PeriodCreate(
            period="2026-11",
            name="测试次月",
            source_book_id=case.book,
            inherit_user_ids=[case.reviewer.user_id],
            idempotency_key=uuid4().hex,
        ),
    )
    new_book_id = new_book["book_id"]
    generated = case.monthly.generate(
        case.reviewer, new_book_id, row["id"], PlanGenerate(version=1, idempotency_key=uuid4().hex)
    )
    assert generated["book_id"] == new_book_id
    with case.db.transaction(write=True, actor=case.owner) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE book_id=? AND user_id=?",
            (new_book_id, case.reviewer.user_id),
        )
    with pytest.raises(AccountingError):
        case.monthly.overview(case.reviewer, new_book_id)


def test_monthly_changes_invalidate_closing_snapshot_and_block_unhandled_work(monthly_case):
    from app.closing.models import PreflightRequest
    from app.closing.service import ClosingService

    case = monthly_case
    closing = ClosingService(case.db)
    old = closing.preflight(case.owner, case.book, PreflightRequest(idempotency_key=uuid4().hex))
    assert old["report"]["passed"]
    row = case.monthly.create_plan(case.owner, case.book, plan())
    assert closing.get_snapshot(case.owner, case.book, old["id"])["stale"]
    case.monthly.approve_plan(
        case.reviewer, case.book, row["id"], PlanApproval(version=1, reason="独立确认固定支出")
    )
    fresh = closing.preflight(case.owner, case.book, PreflightRequest(idempotency_key=uuid4().hex))
    assert fresh["report"]["counts"]["recurring_pending"] == 1
    opening, ending = confirmed(case, "opening", "100.00"), confirmed(case, "closing", "99.00")
    check = case.monthly.create_check(
        case.owner,
        case.book,
        CheckCreate(opening_id=opening["id"], closing_id=ending["id"], idempotency_key=uuid4().hex),
    )
    report = closing.preflight(
        case.owner, case.book, PreflightRequest(idempotency_key=uuid4().hex)
    )["report"]
    assert report["counts"]["monthly_reference"] == 1
    case.monthly.close_check(
        case.reviewer, case.book, check["id"], MonthlyReason(reason="已保留差异并人工核对处理")
    )
    assert (
        "monthly_reference"
        not in closing.preflight(
            case.owner, case.book, PreflightRequest(idempotency_key=uuid4().hex)
        )["report"]["counts"]
    )


def test_reuses_independently_accepted_legacy_balances_without_creating_opening_sources(
    legacy_case,
):
    case = legacy_case
    monthly = MonthlyService(case.db, case.ledger)
    with case.db.transaction(actor=case.manager) as conn:
        cutover = conn.execute(
            "SELECT id FROM migration_cutovers WHERE project_id=?", (case.project,)
        ).fetchone()[0]
    request = baseline(source_kind="legacy_cutover", source_id=cutover, lines=[])
    row = monthly.create_baseline(case.manager, case.book["id"], request)
    assert row["original"]["format"] == "legacy_snapshot"
    assert len(row["lines"]) >= 2
    assert any(x["account_code"] == "2202.HIST" and x["auxiliary_values"] for x in row["lines"])
    monthly.confirm_baseline(
        case.actors["accountant"],
        case.book["id"],
        row["id"],
        MonthlyReason(reason="核对已验收旧账余额及科目映射"),
    )
    with case.db.transaction(actor=case.manager) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM sources WHERE book_id=?", (case.book["id"],)
            ).fetchone()[0]
            == 0
        )


def test_reference_only_counts_complete_exact_receipt_and_rechecks_newer_unknown(monthly_case):
    from app.delivery.models import ReceiptCreate
    from app.delivery.service import DeliveryService

    case = monthly_case
    confirmed(case, "opening", "100.00")
    draft = make_draft(case.accounting, case.book)
    case.accounting.approve(case.reviewer, case.book, draft["id"], ApprovalRequest(expected_version=draft["version"]))
    export = case.accounting.export(
        case.owner, case.book, ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex)
    )
    delivery = DeliveryService(case.db)
    body = json.loads(delivery.template(case.owner, case.book, export["id"]))
    for item in body["items"]:
        item.update(status="accepted", external_voucher_id="SYNTHETIC-" + item["draft_id"])
    delivery.create_receipt(
        case.owner,
        case.book,
        export["id"],
        ReceiptCreate(**body, reason="虚构精确回执，未连接厂商", idempotency_key=uuid4().hex),
    )
    lines = {
        x["account_code"]: x
        for x in case.monthly.overview(case.owner, case.book)["reference"]["lines"]
    }
    assert lines["1002"]["reference_closing"] == "99.70"
    assert lines["1002"]["verified_delivery"]["credit"] == "0.30"
    for item in body["items"]:
        item.update(status="unknown")
    delivery.create_receipt(
        case.owner,
        case.book,
        export["id"],
        ReceiptCreate(**body, reason="更新为外部结果未知，需重新查询", idempotency_key=uuid4().hex),
    )
    lines = {
        x["account_code"]: x
        for x in case.monthly.overview(case.owner, case.book)["reference"]["lines"]
    }
    assert lines["1002"]["reference_closing"] == "100.00"
    assert lines["1002"]["exported_pending_receipt"]["credit"] == "0.30"


def bridge_baseline(case):
    from app.integrations.models import BridgeConfiguration, BridgeImport, BridgeImportConfirm
    from app.integrations.service import FinancialBridgeService

    bridge = FinancialBridgeService(case.db)
    bridge.configure(
        case.owner,
        case.book,
        BridgeConfiguration(
            expected_version=0,
            external_book_id="SYNTHETIC-TPLUS",
            accounts=[{"local_code": "1002", "external_code": "BANK001"}],
            reason="未知版本只接文件映射",
            idempotency_key=uuid4().hex,
        ),
    )
    content = {
        "schema": "hengzhang.tplus.reference.v1",
        "book_id": case.book,
        "company_id": case.company,
        "external_book_id": "SYNTHETIC-TPLUS",
        "period": "2026-10",
        "records": [
            {
                "kind": "balance",
                "external_id": "SYNTHETIC-BALANCE",
                "code": "BANK001",
                "period": "2026-10",
                "debit": "99.70",
                "credit": "0.00",
                "source_ref": "虚构外部余额行",
            }
        ],
    }
    preview = bridge.preview_import(
        case.owner,
        case.book,
        BridgeImport(
            format="json",
            content=json.dumps(content),
            reason="明确的合成余额文件",
            idempotency_key=uuid4().hex,
        ),
    )
    with pytest.raises(AccountingError):
        case.monthly.create_baseline(
            case.owner,
            case.book,
            baseline("closing", source_kind="bridge_import", source_id=preview["id"], lines=[]),
        )
    bridge.confirm_import(
        case.owner,
        case.book,
        preview["id"],
        BridgeImportConfirm(fingerprint=preview["fingerprint"]),
    )
    return case.monthly.create_baseline(
        case.owner,
        case.book,
        baseline("closing", source_kind="bridge_import", source_id=preview["id"], lines=[]),
    )


def test_tplus_confirmed_file_balance_reuses_mapping_but_never_claims_live_connection(monthly_case):
    case = monthly_case
    row = bridge_baseline(case)
    assert row["lines"][0]["account_code"] == "1002" and row["lines"][0]["balance"] == "99.70"
    assert row["original"]["format"] == "bridge_snapshot"
    assert (
        not row["confirmed"]
        and not case.monthly.overview(case.owner, case.book)["live_tplus_connected"]
    )


def test_unapproved_plan_expiry_cannot_hide_previously_approved_due_obligation(monthly_case):
    from app.closing.models import PreflightRequest
    from app.closing.service import ClosingService

    case = monthly_case
    row = case.monthly.create_plan(case.owner, case.book, plan())
    case.monthly.approve_plan(
        case.reviewer, case.book, row["id"], PlanApproval(version=1, reason="独立确认本月合同义务")
    )
    revised = case.monthly.create_plan(
        case.owner,
        case.book,
        plan(
            plan_id=row["id"],
            expected_version=1,
            start_period="2026-09",
            end_period="2026-09",
            reason="提出提前结束的合同变更",
        ),
    )
    closing = ClosingService(case.db)
    assert (
        closing.preflight(case.owner, case.book, PreflightRequest(idempotency_key=uuid4().hex))[
            "report"
        ]["counts"]["recurring_pending"]
        == 1
    )
    case.monthly.approve_plan(
        case.reviewer,
        case.book,
        revised["id"],
        PlanApproval(version=2, reason="独立核对合同终止及月份范围"),
    )
    assert (
        "recurring_pending"
        not in closing.preflight(
            case.owner, case.book, PreflightRequest(idempotency_key=uuid4().hex)
        )["report"]["counts"]
    )


@pytest.mark.parametrize(
    "format,content",
    [
        (
            "json",
            '{"period":"2026-09","currency":"CNY","scope":"posted","lines":[{"account_code":"1002","balance":"1.00","balance":"999.00"}]}',
        ),
        (
            "json",
            '{"period":"2026-09","currency":"CNY","scope":"posted","lines":[{"account_code":"1002","balance":"1.00","auxiliary_values":{"project":"first","project":"second"}}]}',
        ),
        (
            "csv",
            "account_code,balance,balance,auxiliary_values,period,currency,scope\n1002,1.00,999.00,{},2026-09,CNY,posted\n",
        ),
        (
            "csv",
            'account_code,balance,auxiliary_values,period,currency,scope\n1002,1.00,"{""project"":""first"",""project"":""second""}",2026-09,CNY,posted\n',
        ),
    ],
)
def test_balance_import_rejects_ambiguous_json_or_csv_fields(monthly_case, format, content):
    from app.monthly.models import BaselineImport

    case = monthly_case
    request = BaselineImport(
        kind="opening",
        format=format,
        filename="合成余额." + format,
        content=content,
        external_reference="客户提供的合成余额表",
        evidence_ref="合成原件",
        reason="字段歧义必须人工处理",
        idempotency_key=uuid4().hex,
    )
    with pytest.raises(AccountingError):
        case.monthly.import_baseline(case.owner, case.book, request)
    assert not case.monthly.overview(case.owner, case.book)["baselines"]
