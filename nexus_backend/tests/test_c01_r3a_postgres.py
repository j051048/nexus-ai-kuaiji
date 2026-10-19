"""C01-R3A PostgreSQL-only identity guard.

The locked-period coverage guard is present in migration 133 but its dedicated
two-session proof is tracked as remaining C01-R3A work; it is not claimed here.
"""

import pytest

from test_postgres_integration import ADMIN_URL, case, postgres  # noqa: F401


def test_runtime_can_configure_control_account(case):
    from app.opening.models import AccountCreate
    from app.opening.service import OpeningService
    from app.subledger.models import ControlAccountCreate
    from app.subledger.service import SubledgerService

    with case.db.transaction(actor=case.actor) as conn:
        ledger_id = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.book,)
        ).fetchone()["ledger_id"]
    account = OpeningService(case.db).create_account(
        case.actor,
        AccountCreate(
            ledger_id=ledger_id,
            code="C01AP",
            name="C01 应付控制",
            category="liability",
            normal_balance="credit",
            effective_from="2026-01",
            reason="C01-R3A 正常路径",
        ),
    )
    config = SubledgerService(case.db).configure_control_account(
        case.actor,
        ControlAccountCreate(
            book_id=case.book,
            kind="payable",
            control_account_id=account["id"],
            control_account_version=1,
            effective_from="2026-01",
            reason="C01-R3A 正常配置",
        ),
    )
    assert config["control_account_id"] == account["id"]
    from app.ledgers.models import EntityCreate
    from app.ledgers.service import LedgersService
    from app.subledger.models import OpenItemCreate

    party = LedgersService(case.db).create_entity(
        case.actor,
        case.book,
        EntityCreate(kind="counterparty", code="C01SUP", name="C01 供应商"),
    )
    item = SubledgerService(case.db).create_item(
        case.actor,
        OpenItemCreate(
            book_id=case.book,
            kind="payable",
            counterparty_id=party["id"],
            control_account_id=account["id"],
            control_account_version=1,
            original_date="2026-10-01",
            amount="10.00",
            source_kind="manual",
            reason="C01-R3A 子账事项",
        ),
    )
    assert item["aux"] == {}


def test_bank_coverage_rejects_forged_verifier(case):
    from uuid import uuid4

    bank_id = "c01-bank-" + uuid4().hex
    coverage_id = "c01-cov-" + uuid4().hex
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("SELECT set_config('app.user_id',%s,true)", (case.actor.user_id,))
        conn.execute(
            "INSERT INTO accounting.bank_accounts(id,firm_id,company_id,book_id,name,currency,"
            "created_by,created_at) VALUES(%s,%s,%s,%s,'C01 基本户','CNY',%s,%s)",
            (
                bank_id,
                case.actor.firm_id,
                case.company,
                case.book,
                case.actor.user_id,
                "2026-10-01T00:00:00+00:00",
            ),
        )
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.actor) as conn:
            conn.execute(
                "INSERT INTO bank_statement_coverages(id,account_id,book_id,period,opening_cents,"
                "closing_cents,source_ref,verified_by,verified_at,created_at) "
                "VALUES(? ,? ,?,'2026-10',0,0,'synthetic','forged-user',?,?)",
                (
                    coverage_id,
                    bank_id,
                    case.book,
                    "2026-10-01T00:00:00+00:00",
                    "2026-10-01T00:00:00+00:00",
                ),
            )
