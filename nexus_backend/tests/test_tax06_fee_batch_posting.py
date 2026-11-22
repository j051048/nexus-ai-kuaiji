"""Batch 06 B1：已独立复核的规费批次草稿走既有受控记账通道。

记账本身就是 ``POST /api/books/{book_id}/drafts/{draft_id}/post`` 背后的
``PostingService.post_draft``：这里不新开入口，只证明三件事——未复核时被拒、
复核后只生成一张正式凭证、锁期后再次被拒。
"""

from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import FirmMemberGrant
from app.ledgers.models import TransportAssignmentCreate
from app.ledgers.service import LedgersService
from app.opening.models import (
    AccountCreate,
    MappingCreate,
    OpeningCommand,
    OpeningImport,
    OpeningLineInput,
    OpeningPackageCreate,
    PolicyCreate,
)
from app.opening.service import OpeningService
from app.posting.models import OpeningBind, PostDraft
from app.posting.service import PostingService
from app.tax.fees import TaxFeeService
from policy_fixtures import approve_policy_independently
from period_fixtures import restore_period_state
from test_business import business  # noqa: F401  (pytest fixture)
from test_tax06_fees import POLICY, _masters, _source

PERIOD_END = "2026-10-31"


def _admin(case):
    from types import SimpleNamespace

    from app.accounting.domain import Actor

    identity = SimpleNamespace(
        user_id=f"fee-post-admin-{uuid4().hex[:8]}",
        display_name="规费记账独立核准人",
        email=f"fee-post-admin-{uuid4().hex[:8]}@example.test",
    )
    case.directory.register_identity(identity)
    case.directory.grant_firm_member(
        case.manager, FirmMemberGrant(email=identity.email, role="admin")
    )
    return Actor(identity.user_id, case.manager.firm_id)


def _frozen_opening(case):
    """Freeze an opening and map the two codes the fee batch draft uses."""
    with case.db.transaction() as conn:
        ledger_id = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.book["id"],)
        ).fetchone()["ledger_id"]
    opening = OpeningService(case.db)
    policy = opening.create_policy(
        case.manager,
        PolicyCreate(
            ledger_id=ledger_id,
            standard_kind="small_enterprise",
            taxpayer_kind="general",
            formal_book_system="local",
            effective_from="2026-01",
            effective_to="2026-12",
            reason="规费记账期初政策",
        ),
    )
    approve_policy_independently(opening, case.db, case.manager, policy["id"])

    def account(code, name, category, normal):
        return opening.create_account(
            case.manager,
            AccountCreate(
                ledger_id=ledger_id,
                code=code,
                name=name,
                category=category,
                normal_balance=normal,
                effective_from="2026-01",
                effective_to="2026-12",
                reason="规费记账科目",
            ),
        )

    bank = account("1002", "银行存款", "asset", "debit")
    payable = account("2202", "应付账款", "liability", "credit")
    receivable = account("1122", "应收账款", "asset", "debit")
    revenue = account("6001", "主营业务收入", "income", "credit")
    package = opening.create_package(
        case.manager,
        OpeningPackageCreate(
            ledger_id=ledger_id, target_period=case.book["period"], policy_id=policy["id"]
        ),
    )
    version = opening.import_version(
        case.manager,
        package["package"]["id"],
        OpeningImport(
            idempotency_key="fee-post-opening",
            reason="规费记账冻结期初",
            lines=[
                OpeningLineInput(
                    account_id=bank["id"],
                    account_version=1,
                    year_opening="0.00",
                    ytd_debit="10000.00",
                    ytd_credit="0.00",
                    opening="10000.00",
                ),
                OpeningLineInput(
                    account_id=payable["id"],
                    account_version=1,
                    year_opening="0.00",
                    ytd_debit="0.00",
                    ytd_credit="10000.00",
                    opening="-10000.00",
                ),
            ],
        ),
    )
    version_id = version["version"]["id"]
    opening.validate_version(case.manager, version_id)
    opening.submit_version(
        case.manager,
        version_id,
        OpeningCommand(expected_version=1, reason="提交规费记账期初"),
    )
    admin = _admin(case)
    opening.approve_version(
        admin, version_id, OpeningCommand(expected_version=1, reason="独立核准规费记账期初")
    )
    opening.freeze_version(
        admin, version_id, OpeningCommand(expected_version=1, reason="冻结规费记账期初")
    )
    for row in (receivable, revenue):
        opening.create_mapping(
            case.manager,
            MappingCreate(
                book_id=case.book["id"],
                book_account_code=row["code"],
                account_id=row["id"],
                account_version=1,
                reason="规费草稿科目映射",
            ),
        )
    PostingService(case.db).bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"],
            package_version_id=version_id,
            reason="绑定规费记账期初",
        ),
    )
    return {"ledger_id": ledger_id, "version_id": version_id}


def _reviewed_batch(case):
    driver, vehicle, driver2, vehicle2 = _masters(case)
    LedgersService(case.db).create_transport_assignment(
        case.manager,
        case.book["id"],
        TransportAssignmentCreate(
            vehicle_id=vehicle2["id"],
            driver_id=driver2["id"],
            shift="full",
            contract_external_id="C1",
            valid_from="2026-10-05",
            valid_to="2026-10-05",
        ),
    )
    source = _source(case)
    service = TaxFeeService(case.db)
    batch = service.generate(
        case.manager,
        case.book["id"],
        period="2026-10",
        source_file_ids=[source["id"]],
        policy=POLICY,
    )
    return service, batch


def test_unreviewed_fee_batch_draft_cannot_be_posted(business):
    case = business
    _frozen_opening(case)
    service, batch = _reviewed_batch(case)
    posting = PostingService(case.db)
    draft = case.ledger.get_draft(case.manager, case.book["id"], batch["draft_id"])
    assert draft["status"] == "draft"
    with pytest.raises(AccountingError, match="已审核|已导出"):
        posting.post_draft(
            case.manager,
            case.book["id"],
            batch["draft_id"],
            PostDraft(
                command_key="fee-batch-post-unreviewed",
                expected_version=draft["version"],
                voucher_date=PERIOD_END,
            ),
        )
    # 生成草稿这件事本身不该留下任何正式凭证。
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM vouchers").fetchone()[0] == 0


def test_reviewed_fee_batch_draft_posts_one_voucher_and_refuses_a_second(business):
    case = business
    _frozen_opening(case)
    service, batch = _reviewed_batch(case)
    reviewed = service.review(
        case.actors["accountant"],
        case.book["id"],
        batch["id"],
        expected_version=batch["version"],
        reason="独立复核规费批次",
    )
    assert reviewed["draft"]["status"] == "approved"
    with case.db.transaction(actor=case.manager) as conn:
        before = service._view(
            conn,
            conn.execute("SELECT * FROM tax_fee_batches WHERE id=?", (batch["id"],)).fetchone(),
        )
    assert before["draft"]["status"] == "approved"
    assert before["draft"]["posted_voucher_id"] is None
    posting = PostingService(case.db)
    command = PostDraft(
        command_key="fee-batch-post-reviewed",
        expected_version=before["draft"]["version"],
        voucher_date=PERIOD_END,
    )
    posted = posting.post_draft(case.manager, case.book["id"], batch["draft_id"], command)
    assert posted["posting"] == "posted"
    assert posted["voucher"]["voucher_no"] == 1
    debit = sum(int(line["debit_cents"]) for line in posted["lines"])
    credit = sum(int(line["credit_cents"]) for line in posted["lines"])
    assert debit == credit == reviewed["total_cents"] == 22_500
    # 幂等重放：同一个 command_key 返回原凭证，不新增第二张。
    replay = posting.post_draft(case.manager, case.book["id"], batch["draft_id"], command)
    assert replay["replayed"] is True and replay["voucher"]["id"] == posted["voucher"]["id"]
    # 换一个键再记一次必须被拒：一张草稿只能生成一张正式凭证。
    with pytest.raises(AccountingError, match="已经生成过"):
        posting.post_draft(
            case.manager,
            case.book["id"],
            batch["draft_id"],
            command.model_copy(update={"command_key": "fee-batch-post-again"}),
        )
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM vouchers").fetchone()[0] == 1
        view = service._view(
            conn,
            conn.execute("SELECT * FROM tax_fee_batches WHERE id=?", (batch["id"],)).fetchone(),
        )
    assert view["draft"]["posted_voucher_id"] == posted["voucher"]["id"]
    assert view["draft"]["voucher_no"] == 1


def test_closed_period_blocks_fee_batch_posting(business):
    case = business
    _frozen_opening(case)
    service, batch = _reviewed_batch(case)
    reviewed = service.review(
        case.actors["accountant"],
        case.book["id"],
        batch["id"],
        expected_version=batch["version"],
        reason="独立复核规费批次",
    )
    with case.db.transaction(actor=case.manager) as conn:
        draft_version = int(
            conn.execute("SELECT version FROM drafts WHERE id=?", (batch["draft_id"],)).fetchone()[
                "version"
            ]
        )
    assert reviewed["status"] == "reviewed"
    # 期间关闭与内部锁期走的是同一个 _book(write=True) 门禁。
    restore_period_state(case.db, case.book["id"], "closed")
    with pytest.raises(AccountingError, match="关闭|锁期"):
        PostingService(case.db).post_draft(
            case.manager,
            case.book["id"],
            batch["draft_id"],
            PostDraft(
                command_key="fee-batch-post-closed",
                expected_version=draft_version,
                voucher_date=PERIOD_END,
            ),
        )
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM vouchers").fetchone()[0] == 0
