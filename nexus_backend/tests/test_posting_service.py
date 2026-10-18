"""B03: formal voucher posting, numbering, reversal and ledger read models."""

import sqlite3
from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError, Actor
from app.accounting.models import ApprovalRequest, FirmMemberGrant, ImportRequest, SourceRow
from app.opening.models import (
    AccountCreate,
    OpeningCommand,
    OpeningImport,
    OpeningLineInput,
    OpeningPackageCreate,
    PolicyCreate,
)
from app.opening.service import OpeningService
from app.posting.models import ManualLine, ManualVoucher, OpeningBind, PostDraft, ReverseVoucher
from app.posting.service import PostingService
from period_fixtures import restore_period_state
from test_business import business


def admin(case):
    identity = SimpleNamespace(
        user_id="posting-admin", display_name="记账复核人", email="posting-admin@example.test"
    )
    case.directory.register_identity(identity)
    case.directory.grant_firm_member(
        case.manager, FirmMemberGrant(email=identity.email, role="admin")
    )
    return Actor(identity.user_id, case.manager.firm_id)


def ledger_of(case):
    with case.db.transaction() as conn:
        return conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.book["id"],)
        ).fetchone()["ledger_id"]


def frozen_opening(case):
    ledger_id = ledger_of(case)
    opening = OpeningService(case.db)
    policy = opening.create_policy(
        case.manager,
        PolicyCreate(
            ledger_id=ledger_id,
            standard_kind="small_enterprise",
            taxpayer_kind="general",
            effective_from="2026-01",
            effective_to="2026-12",
            reason="B03 期初政策",
        ),
    )

    def account(code, name, category="asset", normal="debit"):
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
                reason="B03 期初科目",
            ),
        )

    bank = account("1002", "银行存款")
    payable = account("2202", "应付账款", "liability", "credit")
    expense = account("6602.02", "管理费用—办公费", "expense")
    package = opening.create_package(
        case.manager,
        OpeningPackageCreate(
            ledger_id=ledger_id, target_period="2026-10", policy_id=policy["id"]
        ),
    )
    version = opening.import_version(
        case.manager,
        package["package"]["id"],
        OpeningImport(
            idempotency_key="b03-opening-001",
            reason="B03 冻结期初",
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
    opening.validate_version(case.manager, version["version"]["id"])
    opening.submit_version(
        case.manager,
        version["version"]["id"],
        OpeningCommand(expected_version=1, reason="提交 B03 期初"),
    )
    opening.approve_version(
        admin(case),
        version["version"]["id"],
        OpeningCommand(expected_version=1, reason="独立核准 B03 期初"),
    )
    opening.freeze_version(
        admin(case),
        version["version"]["id"],
        OpeningCommand(expected_version=1, reason="冻结 B03 期初"),
    )
    for code, row in (("1002", bank), ("2202", payable), ("6602.02", expense)):
        opening.create_mapping(
            case.manager,
            __import__("app.opening.models", fromlist=["MappingCreate"]).MappingCreate(
                book_id=case.book["id"],
                book_account_code=code,
                account_id=row["id"],
                account_version=1,
                reason="B03 旧账套映射",
            ),
        )
    return {
        "ledger_id": ledger_id,
        "version_id": version["version"]["id"],
        "bank": bank,
        "payable": payable,
        "expense": expense,
    }


def approved_draft(case):
    case.ledger.import_sources(
        case.manager,
        case.book["id"],
        ImportRequest(
            source_name="B03 虚构银行流水",
            rows=[
                SourceRow(
                    external_id="b03-bank-001",
                    date="2026-10-15",
                    description="办公用品",
                    counterparty="虚构供方",
                    amount="0.30",
                    direction="expense",
                    evidence_ref="B03 发票",
                )
            ],
        ),
    )
    case.ledger.suggestions(case.manager, case.book["id"])
    draft = case.ledger.get_book(case.manager, case.book["id"])["drafts"][0]
    case.ledger.approve(
        case.manager,
        case.book["id"],
        draft["id"],
        ApprovalRequest(expected_version=draft["version"]),
    )
    return case.ledger.get_draft(case.manager, case.book["id"], draft["id"])


def test_post_draft_idempotency_numbering_balance_and_no_status_change(business):
    case = business
    opening = frozen_opening(case)
    posting = PostingService(case.db)
    binding = posting.bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"],
            package_version_id=opening["version_id"],
            reason="绑定冻结期初",
        ),
    )
    assert binding["binding"]["status"] == "active"
    draft = approved_draft(case)
    request = PostDraft(
        command_key="post-draft-001",
        expected_version=draft["version"],
        voucher_date="2026-10-15",
    )
    first = posting.post_draft(case.manager, case.book["id"], draft["id"], request)
    assert first["voucher"]["voucher_no"] == 1
    assert first["voucher"]["voucher_type"] == "记"
    assert first["posting"] == "posted"
    replay = posting.post_draft(case.manager, case.book["id"], draft["id"], request)
    assert replay["replayed"] is True
    assert replay["voucher"]["id"] == first["voucher"]["id"]
    with pytest.raises(AccountingError, match="不同内容"):
        posting.post_draft(
            case.manager,
            case.book["id"],
            draft["id"],
            request.model_copy(update={"voucher_date": "2026-10-16"}),
        )
    with pytest.raises(AccountingError, match="已经生成过"):
        posting.post_draft(
            case.manager,
            case.book["id"],
            draft["id"],
            request.model_copy(update={"command_key": "post-draft-002"}),
        )
    with case.db.transaction() as conn:
        assert conn.execute("SELECT status FROM drafts WHERE id=?", (draft["id"],)).fetchone()[0] == "approved"
        assert conn.execute("SELECT COUNT(*) FROM exports").fetchone()[0] == 0
    balance = posting.trial_balance(case.manager, case.book["id"])
    rows = {row["code"]: row for row in balance["rows"]}
    assert rows["1002"]["closing_cents"] == 999970
    assert rows["6602.02"]["closing_cents"] == 30
    assert rows["2202"]["closing_cents"] == -1000000
    assert balance["totals"]["closing_cents"] == 0


def test_manual_voucher_and_reversal_preserve_original(business):
    case = business
    opening = frozen_opening(case)
    posting = PostingService(case.db)
    posting.bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"], package_version_id=opening["version_id"], reason="绑定期初"
        ),
    )
    manual = posting.post_manual(
        case.manager,
        ManualVoucher(
            command_key="manual-001",
            book_id=case.book["id"],
            voucher_date="2026-10-16",
            description="手工计提",
            lines=[
                ManualLine(
                    account_id=opening["expense"]["id"],
                    account_version=1,
                    debit="100.00",
                    credit="0.00",
                ),
                ManualLine(
                    account_id=opening["bank"]["id"],
                    account_version=1,
                    debit="0.00",
                    credit="100.00",
                ),
            ],
        ),
    )
    assert manual["voucher"]["voucher_no"] == 1
    reversed_voucher = posting.reverse_voucher(
        case.manager,
        manual["voucher"]["id"],
        ReverseVoucher(
            command_key="reverse-001",
            voucher_date="2026-10-17",
            reason="全额冲销手工计提",
        ),
    )
    assert reversed_voucher["voucher"]["voucher_no"] == 2
    assert reversed_voucher["voucher"]["reversal_of"] == manual["voucher"]["id"]
    assert reversed_voucher["relations"][0]["kind"] == "reversal"
    original = posting.voucher_detail(case.manager, manual["voucher"]["id"])
    assert original["voucher"]["voucher_no"] == 1
    assert len(original["lines"]) == 2
    with pytest.raises(AccountingError, match="已经全额冲销"):
        posting.reverse_voucher(
            case.manager,
            manual["voucher"]["id"],
            ReverseVoucher(
                command_key="reverse-002",
                voucher_date="2026-10-17",
                reason="重复冲销",
            ),
        )
    balance = posting.trial_balance(case.manager, case.book["id"])
    rows = {row["code"]: row for row in balance["rows"]}
    assert rows["6602.02"]["closing_cents"] == 0
    assert rows["1002"]["closing_cents"] == 1000000
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with case.db.transaction(write=True, actor=case.manager) as conn:
            conn.execute(
                "UPDATE vouchers SET description='篡改' WHERE id=?",
                (manual["voucher"]["id"],),
            )


def test_manual_adjustment_links_original_without_rewriting_it(business):
    case = business
    opening = frozen_opening(case)
    posting = PostingService(case.db)
    posting.bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"], package_version_id=opening["version_id"], reason="绑定期初"
        ),
    )
    original = posting.post_manual(
        case.manager,
        ManualVoucher(
            command_key="adjust-original",
            book_id=case.book["id"],
            voucher_date="2026-10-15",
            description="原始手工凭证",
            lines=[
                ManualLine(
                    account_id=opening["expense"]["id"],
                    account_version=1,
                    debit="100.00",
                    credit="0.00",
                ),
                ManualLine(
                    account_id=opening["bank"]["id"],
                    account_version=1,
                    debit="0.00",
                    credit="100.00",
                ),
            ],
        ),
    )
    adjustment = posting.post_manual(
        case.manager,
        ManualVoucher(
            command_key="adjust-followup",
            book_id=case.book["id"],
            voucher_date="2026-10-16",
            description="补记调整",
            relation_kind="adjustment",
            original_voucher_id=original["voucher"]["id"],
            reason="补记 20 元",
            lines=[
                ManualLine(
                    account_id=opening["expense"]["id"],
                    account_version=1,
                    debit="20.00",
                    credit="0.00",
                ),
                ManualLine(
                    account_id=opening["bank"]["id"],
                    account_version=1,
                    debit="0.00",
                    credit="20.00",
                ),
            ],
        ),
    )
    assert adjustment["voucher"]["voucher_no"] == 2
    assert adjustment["relations"][0]["kind"] == "adjustment"
    assert adjustment["relations"][0]["original_voucher_id"] == original["voucher"]["id"]
    assert posting.voucher_detail(case.manager, original["voucher"]["id"])["voucher"][
        "voucher_no"
    ] == 1
    balance = posting.trial_balance(case.manager, case.book["id"])
    rows = {row["code"]: row for row in balance["rows"]}
    assert rows["6602.02"]["closing_cents"] == 12000
    assert rows["1002"]["closing_cents"] == 988000


def test_unapproved_wrong_scope_closed_period_and_revoke_are_rejected(business):
    case = business
    opening = frozen_opening(case)
    posting = PostingService(case.db)
    posting.bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"], package_version_id=opening["version_id"], reason="绑定期初"
        ),
    )
    case.ledger.import_sources(
        case.manager,
        case.book["id"],
        ImportRequest(
            source_name="B03 未审流水",
            rows=[
                SourceRow(
                    external_id="b03-unapproved",
                    date="2026-10-15",
                    description="未审办公费",
                    counterparty="虚构供方",
                    amount="0.30",
                    direction="expense",
                    evidence_ref="B03 发票",
                )
            ],
        ),
    )
    case.ledger.suggestions(case.manager, case.book["id"])
    unapproved = case.ledger.get_book(case.manager, case.book["id"])["drafts"][0]
    with pytest.raises(AccountingError, match="已审核"):
        posting.post_draft(
            case.manager,
            case.book["id"],
            unapproved["id"],
            PostDraft(
                command_key="post-unapproved-001",
                expected_version=unapproved["version"],
                voucher_date="2026-10-15",
            ),
        )
    finance = case.actors["finance"]
    with pytest.raises(AccountingError):
        posting.post_manual(
            finance,
            ManualVoucher(
                command_key="manual-denied",
                book_id=case.book["id"],
                voucher_date="2026-10-15",
                description="无权凭证",
                lines=[
                    ManualLine(
                        account_id=opening["expense"]["id"],
                        account_version=1,
                        debit="1.00",
                        credit="0.00",
                    ),
                    ManualLine(
                        account_id=opening["bank"]["id"],
                        account_version=1,
                        debit="0.00",
                        credit="1.00",
                    ),
                ],
            ),
        )
    restore_period_state(case.db, case.book["id"], status="closed")
    with pytest.raises(AccountingError, match="关闭"):
        posting.post_manual(
            case.manager,
            ManualVoucher(
                command_key="manual-closed",
                book_id=case.book["id"],
                voucher_date="2026-10-15",
                description="关闭期间凭证",
                lines=[
                    ManualLine(
                        account_id=opening["expense"]["id"],
                        account_version=1,
                        debit="1.00",
                        credit="0.00",
                    ),
                    ManualLine(
                        account_id=opening["bank"]["id"],
                        account_version=1,
                        debit="0.00",
                        credit="1.00",
                    ),
                ],
            ),
        )
    restore_period_state(case.db, case.book["id"], status="open")


def test_rejected_posting_does_not_consume_number(business):
    case = business
    opening = frozen_opening(case)
    posting = PostingService(case.db)
    posting.bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"], package_version_id=opening["version_id"], reason="绑定期初"
        ),
    )
    base = {
        "book_id": case.book["id"],
        "voucher_date": "2026-10-15",
        "description": "失败不占号",
    }
    with pytest.raises(AccountingError, match="不平衡"):
        posting.post_manual(
            case.manager,
            ManualVoucher(
                command_key="manual-bad-balance",
                lines=[
                    ManualLine(
                        account_id=opening["expense"]["id"],
                        account_version=1,
                        debit="100.00",
                        credit="0.00",
                    ),
                    ManualLine(
                        account_id=opening["bank"]["id"],
                        account_version=1,
                        debit="0.00",
                        credit="99.00",
                    ),
                ],
                **base,
            ),
        )
    with pytest.raises(AccountingError, match="期间"):
        posting.post_manual(
            case.manager,
            ManualVoucher(
                command_key="manual-bad-date",
                voucher_date="2026-11-01",
                book_id=case.book["id"],
                description="错误期间",
                lines=[
                    ManualLine(
                        account_id=opening["expense"]["id"],
                        account_version=1,
                        debit="100.00",
                        credit="0.00",
                    ),
                    ManualLine(
                        account_id=opening["bank"]["id"],
                        account_version=1,
                        debit="0.00",
                        credit="100.00",
                    ),
                ],
            ),
        )
    valid = posting.post_manual(
        case.manager,
        ManualVoucher(
            command_key="manual-valid-after-failure",
            lines=[
                ManualLine(
                    account_id=opening["expense"]["id"],
                    account_version=1,
                    debit="100.00",
                    credit="0.00",
                ),
                ManualLine(
                    account_id=opening["bank"]["id"],
                    account_version=1,
                    debit="0.00",
                    credit="100.00",
                ),
            ],
            **base,
        ),
    )
    assert valid["voucher"]["voucher_no"] == 1
    ledger = posting.account_ledger(
        case.manager, case.book["id"], opening["bank"]["id"]
    )
    assert ledger["opening_cents"] == 1000000
    assert ledger["closing_cents"] == 990000
    journal = posting.daily_journal(case.manager, case.book["id"])
    assert journal[-1]["code_snapshot"] == "1002"
    assert journal[-1]["balance_cents"] == 990000
