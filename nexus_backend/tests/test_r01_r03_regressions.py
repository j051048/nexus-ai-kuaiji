"""R01/R03 regressions for the 14-item review findings."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.posting.models import ManualLine, ManualVoucher
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from app.subledger.models import (
    AllocationCreate,
    AllocationPart,
    AllocationReverse,
    OpenItemCreate,
)
from app.subledger.service import SubledgerService
from test_b04_pilot import setup_b03
from test_business import business
from test_posting_service import admin
from test_p0_close_dependencies import close_case


def _case(case):
    opening, posting = setup_b03(case)
    return opening, posting, SubledgerService(case.db)


def _item(case, opening, sub, amount="5000.00"):
    from app.subledger.models import ControlAccountCreate

    if not sub.list_control_accounts(case.manager, case.book["id"]):
        sub.configure_control_account(
            case.manager,
            ControlAccountCreate(
                book_id=case.book["id"],
                kind="payable",
                control_account_id=opening["payable"]["id"],
                control_account_version=1,
                effective_from="2026-01",
                reason="R01 控制科目夹具",
            ),
        )
    party = LedgersService(case.db).create_entity(
        case.manager,
        case.book["id"],
        EntityCreate(kind="counterparty", code="R01-" + uuid4().hex[:8], name="虚构往来方"),
    )
    return sub.create_item(
        case.manager,
        OpenItemCreate(
            book_id=case.book["id"],
            kind="payable",
            counterparty_id=party["id"],
            control_account_id=opening["payable"]["id"],
            control_account_version=1,
            original_date="2026-10-01",
            amount=amount,
            source_kind="manual",
            reason="R01 隔离测试",
        ),
    )


def _payment(case, opening, posting, amount="3000.00", key="r01-payment"):
    return posting.post_manual(
        case.manager,
        ManualVoucher(
            command_key=key,
            book_id=case.book["id"],
            voucher_date="2026-10-20",
            description="R01 虚构付款",
            lines=[
                ManualLine(
                    account_id=opening["payable"]["id"],
                    account_version=1,
                    debit=amount,
                    credit="0.00",
                ),
                ManualLine(
                    account_id=opening["bank"]["id"],
                    account_version=1,
                    debit="0.00",
                    credit=amount,
                ),
            ],
        ),
    )


def test_f04_control_with_book_balance_and_no_items_fails(business):
    case = business
    opening, _, sub = _case(case)
    from app.subledger.models import ControlAccountCreate

    sub.configure_control_account(
        case.manager,
        ControlAccountCreate(
            book_id=case.book["id"],
            kind="payable",
            control_account_id=opening["payable"]["id"],
            control_account_version=1,
            effective_from="2026-01",
            reason="F04 配置应付控制科目",
        ),
    )
    result = sub.control_reconciliation(case.manager, case.book["id"])
    assert result["passed"] is False
    assert any(row["book_cents"] for row in result["rows"])


def test_f04_custom_control_account_is_not_code_based(business):
    case = business
    opening, _, sub = _case(case)
    from app.opening.models import AccountCreate
    from app.opening.service import OpeningService
    from app.subledger.models import ControlAccountCreate

    custom = OpeningService(case.db).create_account(
        case.manager,
        AccountCreate(
            ledger_id=opening["ledger_id"],
            code="9999",
            name="自定义应付控制",
            category="liability",
            normal_balance="credit",
            effective_from="2026-01",
            effective_to="2026-12",
            reason="F04 自定义控制科目",
        ),
    )
    sub.configure_control_account(
        case.manager,
        ControlAccountCreate(
            book_id=case.book["id"],
            kind="payable",
            control_account_id=custom["id"],
            control_account_version=1,
            effective_from="2026-01",
            reason="F04 自定义控制配置",
        ),
    )
    configs = sub.list_control_accounts(case.manager, case.book["id"])
    assert any(row["control_account_id"] == custom["id"] for row in configs)
    reconciled = sub.control_reconciliation(case.manager, case.book["id"])
    assert reconciled["status"] == "configured"
    assert any(row["control_account_id"] == custom["id"] for row in reconciled["rows"])
    party = LedgersService(case.db).create_entity(
        case.manager,
        case.book["id"],
        EntityCreate(kind="counterparty", code="F04-CUSTOM", name="自定义控制往来方"),
    )
    item = sub.create_item(
        case.manager,
        OpenItemCreate(
            book_id=case.book["id"],
            kind="payable",
            counterparty_id=party["id"],
            control_account_id=custom["id"],
            control_account_version=1,
            original_date="2026-10-01",
            amount="10.00",
            source_kind="manual",
            reason="F04 自定义控制事项",
        ),
    )
    assert item["control_account_id"] == custom["id"]


def test_f04_missing_config_does_not_silently_pass(business):
    case = business
    _, _, sub = _case(case)
    result = sub.control_reconciliation(case.manager, case.book["id"])
    assert result["status"] == "blocked"
    assert result["passed"] is False
    assert "配置" in result["reason"]


def test_f04_expired_config_blocks_and_required_aux_is_enforced(business):
    case = business
    opening, _, sub = _case(case)
    from app.ledgers.models import EntityCreate
    from app.ledgers.service import LedgersService
    from app.subledger.models import ControlAccountCreate, ControlApplicability

    sub.configure_control_account(
        case.manager,
        ControlAccountCreate(
            book_id=case.book["id"],
            kind="payable",
            control_account_id=opening["payable"]["id"],
            control_account_version=1,
            required_aux=["department"],
            effective_from="2020-01",
            effective_to="2020-02",
            reason="F04 过期配置",
        ),
    )
    expired = sub.control_reconciliation(case.manager, case.book["id"])
    assert expired["status"] == "blocked"
    sub.configure_control_account(
        case.manager,
        ControlAccountCreate(
            book_id=case.book["id"],
            kind="payable",
            control_account_id=opening["payable"]["id"],
            control_account_version=1,
            required_aux=["department"],
            effective_from="2026-01",
            reason="F04 辅助维度配置",
        ),
    )
    with pytest.raises(AccountingError, match="已有控制科目"):
        sub.set_control_applicability(
            case.manager,
            ControlApplicability(
                book_id=case.book["id"],
                status="not_applicable",
                effective_from="2026-11",
                reason="当前声明不能覆盖其他期间配置",
            ),
        )
    party = LedgersService(case.db).create_entity(
        case.manager,
        case.book["id"],
        EntityCreate(kind="counterparty", code="F04-AUX", name="辅助维度往来方"),
    )
    with pytest.raises(AccountingError, match="必要辅助"):
        sub.create_item(
            case.manager,
            OpenItemCreate(
                book_id=case.book["id"],
                kind="payable",
                counterparty_id=party["id"],
                control_account_id=opening["payable"]["id"],
                control_account_version=1,
                original_date="2026-10-01",
                amount="10.00",
                source_kind="manual",
                reason="F04 缺辅助",
            ),
        )


def test_c01_applicability_cannot_hide_payable_balance(business):
    case = business
    _, _, sub = _case(case)
    from app.subledger.models import ControlApplicability

    with pytest.raises(AccountingError, match="已有控制科目"):
        sub.set_control_applicability(
            case.manager,
            ControlApplicability(
                book_id=case.book["id"],
                status="not_applicable",
                effective_from="2026-10",
                reason="试图跳过应付子账",
            ),
        )
    with pytest.raises(AccountingError, match="当前登录主体"):
        sub.set_control_applicability(
            case.manager,
            ControlApplicability(
                book_id=case.book["id"],
                status="not_applicable",
                effective_from="2026-11",
                reason="伪造确认人",
                confirmed_by="another-user",
            ),
        )


def test_c01_applicability_period_must_be_yyyy_mm(business):
    case = business
    _, _, _ = _case(case)
    from pydantic import ValidationError
    from app.subledger.models import ControlApplicability

    with pytest.raises(ValidationError):
        ControlApplicability(
            book_id=case.book["id"],
            status="not_applicable",
            effective_from="2026-10-01",
            reason="错误期间",
        )


def test_f09_bank_without_statement_evidence_fails(business):
    case = business
    _, _, sub = _case(case)
    result = sub.bank_reconciliation(case.manager, case.book["id"])
    assert result["statement_line_count"] == 0
    assert result["coverage_ok"] is False
    assert result["passed"] is False


def test_f09_multi_bank_requires_each_account_coverage(business, tmp_path):
    case = business
    opening, _, sub = _case(case)
    from app.subledger.models import BankAccountCreate, BankCoverageCreate

    first = sub.create_bank_account(
        case.manager,
        BankAccountCreate(
            book_id=case.book["id"],
            name="基本户",
            ledger_account_id=opening["bank"]["id"],
            ledger_account_version=1,
            reason="F09 银行账户",
        ),
    )
    project = LedgersService(case.db).create_entity(
        case.manager,
        case.book["id"],
        EntityCreate(kind="project", code="BANK-SECOND", name="第二银行辅助桶"),
    )
    sub.create_bank_account(
        case.manager,
        BankAccountCreate(
            book_id=case.book["id"],
            name="一般户",
            ledger_account_id=opening["bank"]["id"],
            ledger_account_version=1,
            reason="F09 银行账户",
            aux={"project": project["id"]},
        ),
    )
    result = sub.bank_reconciliation(case.manager, case.book["id"])
    assert result["multi_account"] is True
    assert len(result["rows"]) == 2
    assert result["coverage_ok"] is False
    assert result["passed"] is False
    from app.business.storage import AttachmentStore
    from app.import_wizard.service import ImportWizardService
    from app.import_wizard.models import MappingSettings, PreviewRequest
    from app.subledger.models import BankCoverageReview

    sub.storage = AttachmentStore(tmp_path / "bank-originals")
    wizard = ImportWizardService(case.db, sub.storage)
    source = wizard.upload(
        case.manager,
        case.book["id"],
        "银行.csv",
        "日期,摘要,金额,方向\n2026-10-01,合成银行收入,0.30,收入\n".encode(),
    )
    preview = wizard.preview(
        case.manager,
        case.book["id"],
        PreviewRequest(
            file_id=source["file_id"],
            namespace="coverage",
            bank_account=first["id"],
            mapping=MappingSettings(
                columns={"date": 0, "description": 1, "amount": 2, "direction": 3}
            ),
        ),
    )
    wizard.confirm(case.manager, case.book["id"], preview["id"])
    declared = sub.declare_bank_coverage(
        case.manager,
        BankCoverageCreate(
            book_id=case.book["id"],
            account_id=first["id"],
            period="2026-10",
            opening="0.00",
            closing="0.30",
            source_file_id=source["file_id"],
            source_preview_id=preview["id"],
            source_sha256=source["sha256"],
            reason="F09 覆盖声明",
        ),
    )
    sub.review_bank_coverage(
        case.actors["accountant"],
        declared["id"],
        BankCoverageReview(expected_version=1, decision="verified", reason="独立核对银行原件"),
    )
    partial = sub.bank_reconciliation(case.manager, case.book["id"])
    assert partial["coverage_ok"] is False
    assert partial["passed"] is False


def test_f06_multi_item_allocation_and_f07_reversal_capacity(business):
    case = business
    opening, posting, sub = _case(case)
    first, second = _item(case, opening, sub), _item(case, opening, sub)
    voucher = _payment(case, opening, posting, "3000.00", "r01-multi-payment")
    allocated = sub.allocate(
        case.manager,
        AllocationCreate(
            book_id=case.book["id"],
            voucher_id=voucher["voucher"]["id"],
            parts=[
                AllocationPart(item_id=first["id"], amount="1500.00"),
                AllocationPart(item_id=second["id"], amount="1500.00"),
            ],
            idempotency_key="r01-multi-request",
            reason="一请求两项",
        ),
    )
    assert len(allocated["allocations"]) == 2
    sub.reverse_allocation(
        case.manager,
        allocated["allocations"][0]["id"],
        AllocationReverse(idempotency_key="r01-reverse", reason="撤销重配"),
    )
    repl = sub.allocate(
        case.manager,
        AllocationCreate(
            book_id=case.book["id"],
            voucher_id=voucher["voucher"]["id"],
            parts=[AllocationPart(item_id=first["id"], amount="1500.00")],
            idempotency_key="r01-reallocate",
            reason="撤销后同事项重配",
        ),
    )
    assert len(repl["allocations"]) == 3


def test_f02_periodic_post_rolls_back_voucher_and_draft_together(business, monkeypatch):
    case = business
    opening, posting, _ = _case(case)
    from app.periodic.models import AccrualRule, DraftCommand, DraftPost
    from app.periodic.service import PeriodicService

    periodic = PeriodicService(case.db)
    rule = periodic.create_accrual_rule(
        case.manager,
        AccrualRule(
            book_id=case.book["id"],
            name="R01 原子计提",
            amount="100.00",
            debit_account_id=opening["expense"]["id"],
            debit_account_version=1,
            credit_account_id=opening["payable"]["id"],
            credit_account_version=1,
            reason="R01 原子测试",
        ),
    )
    draft = periodic.generate_accrual(case.manager, rule["id"])
    periodic.approve(
        admin(case), draft["draft"]["id"], DraftCommand(expected_version=1, reason="复核计提")
    )

    def fail(*args, **kwargs):
        raise RuntimeError("R01 injected audit failure")

    monkeypatch.setattr(periodic, "_audit", fail)
    with pytest.raises(RuntimeError, match="injected"):
        periodic.post(
            case.manager,
            draft["draft"]["id"],
            DraftPost(
                expected_version=2,
                command_key="r01-atomic-post",
                voucher_date="2026-10-31",
                reason="原子记账",
            ),
        )
    with case.db.transaction(actor=case.manager) as conn:
        assert (
            conn.execute(
                "SELECT status FROM periodic_drafts WHERE id=?", (draft["draft"]["id"],)
            ).fetchone()["status"]
            == "approved"
        )
        assert conn.execute("SELECT COUNT(*) FROM vouchers").fetchone()[0] == 0


def test_f03_lock_rejects_pending_draft_after_preflight(close_case):
    case = close_case
    from app.accounting.models import ImportRequest, SourceRow
    from app.b04_close.models import CloseCommand
    from app.accounting.service import AccountingService

    close = case.close
    close.review(
        case.reviewer,
        case.run["close"]["id"],
        CloseCommand(expected_version=1, reason="F03 独立复核真实预检"),
    )
    accounting = AccountingService(case.db)
    accounting.import_sources(
        case.manager,
        case.book["id"],
        ImportRequest(
            source_name="F03 待审流水",
            rows=[
                SourceRow(
                    external_id="f03-source",
                    date="2026-10-05",
                    description="F03 待审草稿",
                    amount="10.00",
                    direction="expense",
                )
            ],
        ),
    )
    accounting.suggestions(case.manager, case.book["id"])
    with pytest.raises(AccountingError, match="预检已变化"):
        close.lock(
            case.manager,
            case.run["close"]["id"],
            CloseCommand(expected_version=1, reason="F03 锁期"),
        )


def test_f05_unclassified_cash_cannot_net_to_zero(business):
    case = business
    opening, posting, _ = _case(case)
    from app.opening.models import AccountCreate
    from app.opening.service import OpeningService

    service = OpeningService(case.db)
    asset = service.create_account(
        case.manager,
        AccountCreate(
            ledger_id=opening["ledger_id"],
            code="1501",
            name="虚构其他资产",
            category="asset",
            normal_balance="debit",
            effective_from="2026-01",
            effective_to="2026-12",
            reason="F05 科目",
        ),
    )
    liability = service.create_account(
        case.manager,
        AccountCreate(
            ledger_id=opening["ledger_id"],
            code="2101",
            name="虚构其他负债",
            category="liability",
            normal_balance="credit",
            effective_from="2026-01",
            effective_to="2026-12",
            reason="F05 科目",
        ),
    )
    for reverse, key in ((False, "f05-inflow"), (True, "f05-outflow")):
        values = [
            (opening["bank"]["id"], "100.00", "0.00"),
            (asset["id"], "0.00", "50.00"),
            (liability["id"], "0.00", "50.00"),
        ]
        lines = [
            ManualLine(
                account_id=account,
                account_version=1,
                debit=credit if reverse else debit,
                credit=debit if reverse else credit,
            )
            for account, debit, credit in values
        ]
        posting.post_manual(
            case.manager,
            ManualVoucher(
                book_id=case.book["id"],
                command_key=key,
                voucher_date="2026-10-20",
                description="F05 虚构未分类交易",
                lines=lines,
            ),
        )
    reports = ReportService(case.db)
    report = reports.create(case.manager, case.book["id"], "2026-10")
    assert report["content"]["checks"]["unclassified_cash_count"] == 2
    assert report["content"]["checks"]["unclassified_cash_absolute_cents"] == 20000
    with pytest.raises(AccountingError):
        reports.validate(case.manager, report["report"]["id"])


def test_f11_batch_worker_caps_general_exception_at_max_attempts():
    from app.batch_ops.worker import BatchWorker

    class Stub:
        def __init__(self):
            self.finished = None

        def execute(self, item):
            raise RuntimeError("persistent")

        def finish(self, item, status, **kwargs):
            self.finished = (status, kwargs)

    stub = Stub()
    BatchWorker(stub).process(
        {"item_id": "i", "batch_id": "b", "attempt": 3, "max_attempts": 3, "lease_token": "t"}
    )
    assert stub.finished[0] == "failed"
    assert "上限" in stub.finished[1]["error_detail"]


def test_f01_editor_cannot_review_report(business):
    case = business
    opening, posting, _ = _case(case)
    # Create a minimal report after binding; empty data still has a valid
    # balance/cash structure for this permission test.
    report = ReportService(case.db).create(case.manager, case.book["id"], "2026-10")
    report = ReportService(case.db).validate(case.manager, report["report"]["id"])
    with pytest.raises(AccountingError):
        ReportService(case.db).review(
            case.actors["finance"],
            report["report"]["id"],
            ReportCommand(expected_version=1, reason="编辑越权复核"),
        )
    with case.db.transaction(actor=case.manager) as conn:
        assert (
            conn.execute(
                "SELECT status FROM report_runs WHERE id=?", (report["report"]["id"],)
            ).fetchone()["status"]
            == "validated"
        )


def test_f08_close_get_requires_book_scope(close_case):
    case = close_case
    from app.b04_close.service import CloseService

    with pytest.raises(AccountingError) as error:
        CloseService(case.db).get(case.actors["employee"], case.run["close"]["id"])
    assert error.value.status == 404
