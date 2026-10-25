"""SQLite fixture upgrade using actual immutable originals and independent review."""

from pathlib import Path
from decimal import Decimal
from uuid import uuid4
from app.business.storage import AttachmentStore
from app.import_wizard.models import MappingSettings, PreviewRequest
from app.import_wizard.service import ImportWizardService
from app.subledger.models import BankAccountCreate, BankCoverageCreate, BankCoverageReview
from test_posting_service import admin


def verified_bank_account(case, sub, ledger_account_id, opening, closing, rows):
    assert not case.db.is_postgres
    bank = sub.create_bank_account(
        case.manager,
        BankAccountCreate(
            book_id=case.book["id"],
            name="合成完整银行核验账户",
            ledger_account_id=ledger_account_id,
            ledger_account_version=1,
            reason="原件及逐笔匹配正式分录",
        ),
    )
    sub.storage = AttachmentStore(Path(case.db.path).parent / ("bank-originals-" + uuid4().hex))
    wizard = ImportWizardService(case.db, sub.storage)
    text = "日期,摘要,金额,方向,编号\n"
    for number, (date, amount) in enumerate(rows):
        value = Decimal(amount)
        text += f"{date},合成实际银行收付,{abs(value):.2f},{'收入' if value > 0 else '支出'},C01-{number}\n"
    source = wizard.upload(case.manager, case.book["id"], "真实合成银行.csv", text.encode())
    preview = wizard.preview(
        case.manager,
        case.book["id"],
        PreviewRequest(
            file_id=source["file_id"],
            namespace="c01-fixture-" + uuid4().hex,
            bank_account=bank["id"],
            mapping=MappingSettings(
                columns={"date": 0, "description": 1, "amount": 2, "direction": 3, "external_id": 4}
            ),
        ),
    )
    wizard.confirm(case.manager, case.book["id"], preview["id"])
    row = sub.declare_bank_coverage(
        case.manager,
        BankCoverageCreate(
            book_id=case.book["id"],
            account_id=bank["id"],
            period=case.book["period"],
            opening=opening,
            closing=closing,
            source_file_id=source["file_id"],
            source_preview_id=preview["id"],
            source_sha256=source["sha256"],
            reason="已确认原件覆盖完整本期",
        ),
    )
    sub.review_bank_coverage(
        admin(case),
        row["id"],
        BankCoverageReview(
            expected_version=1, decision="verified", reason="独立核对真实原件及期初期末"
        ),
    )
    return bank


def reviewed_export_draft(case):
    """Run the existing bounded suggestion pipeline only for the export fixture."""
    from app.accounting.models import ImportRequest, SourceRow, ApprovalRequest

    external_id = "c01-reviewed-export-" + uuid4().hex
    case.ledger.import_sources(
        case.manager,
        case.book["id"],
        ImportRequest(
            source_name="独立导出测试原始来源",
            rows=[
                SourceRow(
                    external_id=external_id,
                    date="2026-10-15",
                    description="办公用品",
                    counterparty="虚构供方",
                    amount="0.30",
                    direction="expense",
                    evidence_ref="真实导出测试发票",
                )
            ],
        ),
    )
    with case.db.transaction(
        write=True, actor=case.manager, write_scope="company", company_id=case.company["id"]
    ) as conn:
        source_id = conn.execute(
            "SELECT id FROM sources WHERE book_id=? AND external_id=?",
            (case.book["id"], external_id),
        ).fetchone()[0]
        generated = case.ledger.suggestions_in_transaction(
            conn, case.manager, case.book["id"], source_ids=[source_id]
        )
    assert generated["created"] == 1
    draft = case.ledger.get_draft(case.manager, case.book["id"], generated["draft_ids"][0])
    case.ledger.approve(
        case.reviewer,
        case.book["id"],
        draft["id"],
        ApprovalRequest(expected_version=draft["version"]),
    )
    return case.ledger.get_draft(case.reviewer, case.book["id"], draft["id"])
