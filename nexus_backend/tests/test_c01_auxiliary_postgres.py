"""Restricted PostgreSQL API and native-SQL checks for C01 auxiliary facts."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.directory import DirectoryService
from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate, CompanyMemberGrant, DepartmentCreate
from app.ledgers.models import EntityCreate, EntityUpdate
from app.ledgers.service import LedgersService
from app.opening.models import AccountCreate
from app.opening.service import OpeningService
from app.subledger.models import ControlAccountCreate, OpenItemCreate
from app.subledger.service import SubledgerService
from test_postgres_integration import APP_URL, ADMIN_URL, case, postgres  # noqa: F401

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="Requires isolated PostgreSQL DSNs"
)


@pytest.fixture
def aux_case(case):
    master = LedgersService(case.db)
    directory = DirectoryService(case.db)
    with case.db.transaction(actor=case.actor) as conn:
        ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.book,)
        ).fetchone()[0]
    account = OpeningService(case.db).create_account(
        case.actor,
        AccountCreate(
            ledger_id=ledger,
            code="C01AUX",
            name="Synthetic control",
            category="liability",
            normal_balance="credit",
            effective_from="2026-01",
            reason="C01 entity test",
        ),
    )
    party = master.create_entity(
        case.actor,
        case.book,
        EntityCreate(kind="counterparty", code="C01PARTY", name="Synthetic supplier"),
    )
    project = master.create_entity(
        case.actor,
        case.book,
        EntityCreate(kind="project", code="C01PROJECT", name="Synthetic project"),
    )
    department = directory.create_department(
        case.actor, case.company, DepartmentCreate(name="Synthetic department")
    )
    employee = "c01-employee-" + uuid4().hex
    email = employee + "@example.test"
    directory.register_identity(
        SimpleNamespace(user_id=employee, display_name="Synthetic employee", email=email)
    )
    directory.grant_company_member(
        case.actor,
        case.company,
        CompanyMemberGrant(email=email, role="employee", department_id=department["id"]),
    )
    other_book = case.service.create_book(
        case.actor, BookCreate(name="Other synthetic company", period="2026-10")
    )
    foreign = master.create_entity(
        case.actor,
        other_book["id"],
        EntityCreate(kind="project", code="C01FOREIGN", name="Foreign project"),
    )
    sub = SubledgerService(case.db)
    sub.configure_control_account(
        case.actor,
        ControlAccountCreate(
            book_id=case.book,
            kind="payable",
            control_account_id=account["id"],
            control_account_version=1,
            required_aux=["project", "employee", "department", "counterparty"],
            effective_from="2026-01",
            reason="C01 strict configuration",
        ),
    )
    aux = {
        "project": project["id"],
        "counterparty": party["id"],
        "employee": employee,
        "department": department["id"],
    }
    request = dict(
        book_id=case.book,
        kind="payable",
        counterparty_id=party["id"],
        control_account_id=account["id"],
        control_account_version=1,
        original_date="2026-10-01",
        amount="12.00",
        aux=aux,
        reason="C01 entity test",
    )
    return SimpleNamespace(
        case=case,
        sub=sub,
        request=request,
        party=party,
        project=project,
        foreign=foreign,
        ledger=ledger,
    )


def test_pg_runtime_accepts_all_four_owned_entities(aux_case):
    c = aux_case
    item = c.sub.create_item(c.case.actor, OpenItemCreate(**c.request))
    assert item["aux"] == c.request["aux"]
    assert c.sub.list_items(c.case.actor, c.case.book)[0]["id"] == item["id"]


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "unknown",
        "cross_company",
        "wrong_kind",
        "inactive",
        "counterparty_mismatch",
        "inactive_employee",
    ],
)
def test_pg_runtime_rejects_untrusted_aux_entities(aux_case, mode):
    c = aux_case
    values = dict(c.request["aux"])
    if mode == "missing":
        values.pop("project")
    if mode == "unknown":
        values["invented"] = "text"
    if mode == "cross_company":
        values["project"] = c.foreign["id"]
    if mode == "wrong_kind":
        values["project"] = c.party["id"]
    if mode == "counterparty_mismatch":
        values["counterparty"] = c.project["id"]
    if mode == "inactive":
        LedgersService(c.case.db).update_entity(
            c.case.actor,
            c.case.book,
            c.project["id"],
            EntityUpdate(
                expected_version=1,
                name="Inactive project",
                active=False,
                reason="C01 deactivate entity",
            ),
        )
    if mode == "inactive_employee":
        with c.case.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("UPDATE accounting.users SET active=0 WHERE id=%s", (values["employee"],))
    with pytest.raises(AccountingError):
        c.sub.create_item(c.case.actor, OpenItemCreate(**(c.request | {"aux": values})))


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "unknown",
        "cross_company",
        "wrong_kind",
        "counterparty_mismatch",
        "boolean",
    ],
)
def test_pg_native_sql_cannot_bypass_entity_contract(aux_case, mode):
    c = aux_case
    item = c.sub.create_item(c.case.actor, OpenItemCreate(**c.request))
    values = dict(c.request["aux"])
    if mode == "missing":
        values.pop("project")
    if mode == "unknown":
        values["invented"] = "text"
    if mode == "cross_company":
        values["project"] = c.foreign["id"]
    if mode == "wrong_kind":
        values["project"] = c.party["id"]
    if mode == "counterparty_mismatch":
        values["counterparty"] = c.project["id"]
    if mode == "boolean":
        values["project"] = True
    with pytest.raises(c.case.psycopg.errors.CheckViolation, match="Subledger"):
        with c.case.db.transaction(write=True, actor=c.case.actor) as conn:
            row = dict(
                conn.execute("SELECT * FROM subledger_items WHERE id=?", (item["id"],)).fetchone()
            )
            row.update(id=uuid4().hex, aux_json=json.dumps(values))
            conn.execute(
                f"INSERT INTO subledger_items({','.join(row)}) VALUES({','.join('?' for _ in row)})",
                tuple(row.values()),
            )
    assert len(c.sub.list_items(c.case.actor, c.case.book)) == 1


def test_pg_native_config_cannot_omit_account_dimensions(aux_case):
    c = aux_case
    account = OpeningService(c.case.db).create_account(
        c.case.actor,
        AccountCreate(
            ledger_id=c.ledger,
            code="C01AUX2",
            name="Dimensional control",
            auxiliary_requirements=["project"],
            effective_from="2026-01",
            reason="C01 account requirements",
        ),
    )
    with pytest.raises(c.case.psycopg.errors.CheckViolation, match="omits account"):
        with c.case.db.transaction(write=True, actor=c.case.actor) as conn:
            row = dict(
                conn.execute(
                    "SELECT * FROM subledger_control_accounts WHERE ledger_id=? LIMIT 1",
                    (c.ledger,),
                ).fetchone()
            )
            row.update(
                id=uuid4().hex,
                kind="receivable",
                control_account_id=account["id"],
                required_aux_json="[]",
            )
            conn.execute(
                f"INSERT INTO subledger_control_accounts({','.join(row)}) VALUES({','.join('?' for _ in row)})",
                tuple(row.values()),
            )
