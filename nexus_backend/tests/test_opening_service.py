"""B02: accounting policy, stable ledger accounts and opening packages (SQLite)."""

import sqlite3
from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError, Actor
from app.accounting.models import FirmMemberGrant
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.opening.models import (
    AccountCreate,
    AccountRevision,
    MappingCreate,
    OpeningCommand,
    OpeningImport,
    OpeningLineInput,
    OpeningOpenItemInput,
    OpeningPackageCreate,
    PolicyCreate,
)
from app.opening.service import OpeningService
from test_business import business


def actor_admin(case):
    identity = SimpleNamespace(
        user_id="opening-admin", display_name="期初复核人", email="opening-admin@example.test"
    )
    case.directory.register_identity(identity)
    case.directory.grant_firm_member(
        case.manager, FirmMemberGrant(email=identity.email, role="admin")
    )
    return Actor(identity.user_id, case.manager.firm_id)


def ledger_of(case, book_id):
    with case.db.transaction() as conn:
        return conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book_id,)
        ).fetchone()["ledger_id"]


def policy(service, actor, ledger_id):
    return service.create_policy(
        actor,
        PolicyCreate(
            ledger_id=ledger_id,
            standard_kind="small_enterprise",
            taxpayer_kind="general",
            effective_from="2026-01",
            effective_to="2026-12",
            reason="B02 隔离测试政策",
        ),
    )


def account(service, actor, ledger_id, code="1002", name="银行存款", **changes):
    return service.create_account(
        actor,
        AccountCreate(
            ledger_id=ledger_id,
            code=code,
            name=name,
            effective_from="2026-01",
            effective_to="2026-12",
            reason="B02 隔离测试科目",
            **changes,
        ),
    )


def opening_import(key="opening-001"):
    return OpeningImport(
        idempotency_key=key,
        reason="虚构期初导入",
        lines=[
            OpeningLineInput(
                account_id="bank-account",
                account_version=1,
                year_opening="0.00",
                ytd_debit="10000.00",
                ytd_credit="0.00",
                opening="10000.00",
                source_row={"source": "synthetic"},
            ),
            OpeningLineInput(
                account_id="payable-account",
                account_version=1,
                year_opening="0.00",
                ytd_debit="0.00",
                ytd_credit="10000.00",
                opening="-10000.00",
                source_row={"source": "synthetic"},
            ),
        ],
    )


def test_opening_package_validates_submits_approves_and_freezes(business):
    case = business
    ledger_id = ledger_of(case, case.book["id"])
    service = OpeningService(case.db)
    created_policy = policy(service, case.manager, ledger_id)
    bank = account(service, case.manager, ledger_id, "1002", "银行存款")
    payable = account(service, case.manager, ledger_id, "2202", "应付账款")
    request = opening_import().model_copy(
        update={
            "lines": [
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
            ]
        }
    )
    package = service.create_package(
        case.manager,
        OpeningPackageCreate(
            ledger_id=ledger_id, target_period="2026-10", policy_id=created_policy["id"]
        ),
    )
    version = service.import_version(case.manager, package["package"]["id"], request)
    assert version["version"]["status"] == "draft"
    validated = service.validate_version(case.manager, version["version"]["id"])
    assert validated["version"]["status"] == "validated"
    assert all(check["status"] == "pass" for check in validated["checks"])
    submitted = service.submit_version(
        case.manager,
        version["version"]["id"],
        OpeningCommand(expected_version=1, reason="提交期初准备包"),
    )
    assert submitted["version"]["status"] == "submitted"
    with pytest.raises(AccountingError, match="分离"):
        service.approve_version(
            case.manager,
            version["version"]["id"],
            OpeningCommand(expected_version=1, reason="同一人不能核准"),
        )
    admin = actor_admin(case)
    approved = service.approve_version(
        admin,
        version["version"]["id"],
        OpeningCommand(expected_version=1, reason="独立核准期初准备包"),
    )
    assert approved["version"]["status"] == "approved"
    frozen = service.freeze_version(
        admin,
        version["version"]["id"],
        OpeningCommand(expected_version=1, reason="冻结期初准备包"),
    )
    assert frozen["version"]["status"] == "frozen"
    assert frozen["posting"] is None
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM exports").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT status FROM opening_packages WHERE id=?", (package["package"]["id"],)
            ).fetchone()[0]
            == "frozen"
        )
    with pytest.raises(sqlite3.IntegrityError, match="frozen|Frozen"):
        with case.db.transaction(write=True, actor=case.manager) as conn:
            conn.execute(
                "UPDATE opening_lines SET opening_cents=0 WHERE package_version_id=?",
                (version["version"]["id"],),
            )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with case.db.transaction(write=True, actor=case.manager) as conn:
            conn.execute(
                "UPDATE ledger_account_versions SET name='篡改历史' WHERE account_id=?",
                (bank["id"],),
            )


def test_opening_idempotency_and_config_change_block_submit(business):
    case = business
    ledger_id = ledger_of(case, case.book["id"])
    service = OpeningService(case.db)
    created_policy = policy(service, case.manager, ledger_id)
    bank = account(service, case.manager, ledger_id, "1002", "银行存款")
    payable = account(service, case.manager, ledger_id, "2202", "应付账款")
    request = opening_import("opening-idem").model_copy(
        update={
            "lines": [
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
            ]
        }
    )
    package = service.create_package(
        case.manager,
        OpeningPackageCreate(
            ledger_id=ledger_id, target_period="2026-10", policy_id=created_policy["id"]
        ),
    )
    first = service.import_version(case.manager, package["package"]["id"], request)
    replay = service.import_version(case.manager, package["package"]["id"], request)
    assert replay["replayed"] is True and replay["version"]["id"] == first["version"]["id"]
    different = request.model_copy(
        update={
            "lines": [
                request.lines[0].model_copy(update={"opening": "9999.00"}),
                request.lines[1],
            ]
        }
    )
    with pytest.raises(AccountingError, match="不同内容"):
        service.import_version(case.manager, package["package"]["id"], different)
    validated = service.validate_version(case.manager, first["version"]["id"])
    assert validated["version"]["status"] == "validated"
    service.revise_account(
        case.manager,
        bank["id"],
        AccountRevision(
            ledger_id=ledger_id,
            code="1002",
            name="银行存款（新版本）",
            effective_from="2026-01",
            effective_to="2026-12",
            expected_version=1,
            reason="测试配置变化",
        ),
    )
    with pytest.raises(AccountingError, match="核验未通过|配置已变化"):
        service.submit_version(
            case.manager,
            first["version"]["id"],
            OpeningCommand(expected_version=1, reason="配置变化后提交"),
        )


def test_account_hierarchy_cross_ledger_and_cycle_are_rejected(business):
    case = business
    ledger_id = ledger_of(case, case.book["id"])
    other_ledger = ledger_of(case, case.other_book["id"])
    service = OpeningService(case.db)
    parent = account(service, case.manager, ledger_id, "6602", "管理费用", is_leaf=True)
    child = account(
        service,
        case.manager,
        ledger_id,
        "6602.01",
        "管理费用—办公费",
        parent_account_id=parent["id"],
        is_leaf=True,
    )
    current_parent = service.list_accounts(case.manager, ledger_id)
    parent_view = next(row for row in current_parent if row["id"] == parent["id"])
    assert parent_view["is_leaf"] == 0
    assert child["parent_account_id"] == parent["id"]
    other_policy = policy(service, case.manager, other_ledger)
    other_parent = account(service, case.manager, other_ledger, "6602", "其他账套管理费用")
    with pytest.raises(AccountingError):
        account(
            service,
            case.manager,
            ledger_id,
            "6602.02",
            "跨账套父级",
            parent_account_id=other_parent["id"],
        )
    with pytest.raises(AccountingError, match="成环"):
        service.revise_account(
            case.manager,
            parent["id"],
            AccountRevision(
                ledger_id=ledger_id,
                code="6602",
                name="管理费用",
                parent_account_id=child["id"],
                expected_version=2,
                effective_from="2026-01",
                effective_to="2026-12",
                reason="测试成环",
            ),
        )
    assert other_policy["ledger_id"] == other_ledger


def test_opening_open_item_requires_current_counterparty(business):
    case = business
    ledger_id = ledger_of(case, case.book["id"])
    service = OpeningService(case.db)
    created_policy = policy(service, case.manager, ledger_id)
    bank = account(service, case.manager, ledger_id, "1002", "银行存款")
    payable = account(service, case.manager, ledger_id, "2202", "应付账款")
    counterparty = LedgersService(case.db).create_entity(
        case.manager,
        case.book["id"],
        EntityCreate(kind="counterparty", code="C001", name="虚构客户"),
    )
    package = service.create_package(
        case.manager,
        OpeningPackageCreate(
            ledger_id=ledger_id, target_period="2026-10", policy_id=created_policy["id"]
        ),
    )
    request = OpeningImport(
        idempotency_key="opening-open-item",
        reason="含未结事项的虚构期初",
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
        open_items=[
            OpeningOpenItemInput(
                kind="receivable",
                counterparty_id=counterparty["id"],
                original_date="2026-09-01",
                remaining="500.00",
            )
        ],
    )
    version = service.import_version(case.manager, package["package"]["id"], request)
    assert len(version["open_items"]) == 1
    assert version["open_items"][0]["counterparty_id"] == counterparty["id"]
    with pytest.raises(AccountingError, match="往来方"):
        service.import_version(
            case.manager,
            package["package"]["id"],
            request.model_copy(
                update={
                    "idempotency_key": "opening-open-item-bad",
                    "open_items": [
                        request.open_items[0].model_copy(
                            update={"counterparty_id": "not-a-counterparty"}
                        )
                    ],
                }
            ),
        )


def test_mapping_and_cross_scope_permissions(business):
    case = business
    ledger_id = ledger_of(case, case.book["id"])
    service = OpeningService(case.db)
    policy(service, case.manager, ledger_id)
    account_row = account(service, case.manager, ledger_id, "1002", "银行存款")
    mapped = service.create_mapping(
        case.manager,
        MappingCreate(
            book_id=case.book["id"],
            book_account_code="1002",
            account_id=account_row["id"],
            account_version=1,
            reason="旧账套科目映射",
        ),
    )
    assert mapped["account_id"] == account_row["id"]
    assert service.list_mappings(case.manager, ledger_id, case.book["id"])[0][
        "book_account_code"
    ] == "1002"
    finance = case.actors["finance"]
    with pytest.raises(AccountingError) as denied:
        service.list_accounts(finance, ledger_id)
    assert denied.value.status == 403
    with pytest.raises(AccountingError):
        service.create_mapping(
            case.manager,
            MappingCreate(
                book_id=case.other_book["id"],
                book_account_code="1002",
                account_id=account_row["id"],
                account_version=1,
                reason="跨账套映射",
            ),
        )
