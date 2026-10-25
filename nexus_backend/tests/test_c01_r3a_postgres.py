"""C01-R3A PostgreSQL-only identity guard.

The locked-period coverage guard is present in migration 133 but its dedicated
two-session proof is tracked as remaining C01-R3A work; it is not claimed here.
"""

import pytest

from test_postgres_integration import ADMIN_URL, case, postgres  # noqa: F401
from test_c01_bank_evidence_postgres import (  # noqa: F401
    business_case,
    business_postgres,
    coverage_pg,
)


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


def test_bank_coverage_rejects_forged_verifier(coverage_pg):
    from test_c01_bank_evidence import declaration, review

    c = coverage_pg
    # Real restricted-service preparation supplies the scoped ledger mapping,
    # confirmed original, frozen amounts and independent current reviewer.
    row = c.sub.declare_bank_coverage(c.c.manager, declaration(c))
    reviewer = c.pg.reviewer
    assert reviewer.user_id != c.c.manager.user_id
    with pytest.raises(c.pg.psycopg.errors.InsufficientPrivilege) as denied:
        with c.c.db.transaction(write=True, actor=reviewer) as conn:
            conn.execute(
                "UPDATE bank_statement_coverages SET state='verified',version=2,"
                "verified_by=?,verified_at='2026-10-25T00:00:00+00:00',"
                "review_reason='不得冒用另一真实用户的审核身份' WHERE id=?",
                (c.c.manager.user_id, row["id"]),
            )
    assert denied.value.sqlstate == "42501"
    # The very same evidence succeeds with the actual independent identity:
    # the negative test cannot pass because of unrelated malformed fixtures.
    done = review(c, reviewer, row)
    assert done["state"] == "verified" and done["verified_by"] == reviewer.user_id
