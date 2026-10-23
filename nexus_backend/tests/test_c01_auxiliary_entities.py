"""C01 F04 entity identities, isolated synthetic service and native SQL paths."""

import json
import sqlite3
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.ledgers.models import EntityCreate, EntityUpdate
from app.ledgers.service import LedgersService
from app.subledger.models import ControlAccountCreate, OpenItemCreate
from app.subledger.service import SubledgerService
from test_b04_pilot import setup_b03
from test_business import business  # noqa: F401


def _aux_case(case, required=()):
    opening, _ = setup_b03(case)
    sub = SubledgerService(case.db)
    master = LedgersService(case.db)
    party = master.create_entity(
        case.manager,
        case.book["id"],
        EntityCreate(kind="counterparty", code="AUX-PARTY", name="虚构辅助往来方"),
    )
    project = master.create_entity(
        case.manager,
        case.book["id"],
        EntityCreate(kind="project", code="AUX-PROJECT", name="虚构项目"),
    )
    foreign = master.create_entity(
        case.manager,
        case.other_book["id"],
        EntityCreate(kind="project", code="AUX-FOREIGN", name="其他企业项目"),
    )
    sub.configure_control_account(
        case.manager,
        ControlAccountCreate(
            book_id=case.book["id"],
            kind="payable",
            control_account_id=opening["payable"]["id"],
            control_account_version=1,
            required_aux=list(required),
            effective_from="2026-01",
            reason="辅助实体契约验证",
        ),
    )
    base = dict(
        book_id=case.book["id"],
        kind="payable",
        counterparty_id=party["id"],
        control_account_id=opening["payable"]["id"],
        control_account_version=1,
        original_date="2026-10-01",
        amount="10.00",
        reason="辅助实体隔离验证",
    )
    return sub, base, party, project, foreign


@pytest.mark.parametrize(
    "mode",
    [
        "unknown",
        "free_text",
        "cross_company",
        "wrong_kind",
        "inactive",
        "party_mismatch",
        "foreign_employee",
        "inactive_employee",
        "fake_department",
    ],
)
def test_new_subledger_item_rejects_untrusted_auxiliary_entities(business, mode):
    case = business
    sub, base, party, project, foreign = _aux_case(case)
    aux = {"project": project["id"]}
    if mode == "unknown":
        aux = {"unknown": "free text"}
    if mode == "free_text":
        aux = {"project": "虚构项目"}
    if mode == "cross_company":
        aux = {"project": foreign["id"]}
    if mode == "wrong_kind":
        aux = {"project": party["id"]}
    if mode == "inactive":
        LedgersService(case.db).update_entity(
            case.manager,
            case.book["id"],
            project["id"],
            EntityUpdate(
                expected_version=1,
                name="Stopped project",
                active=False,
                reason="Deactivate test entity",
            ),
        )
    if mode == "party_mismatch":
        aux = {"counterparty": project["id"]}
    if mode == "foreign_employee":
        aux = {"employee": case.manager.user_id}
    if mode == "inactive_employee":
        aux = {"employee": "employee"}
        with case.db.transaction(write=True, actor=case.manager) as conn:
            conn.execute("UPDATE users SET active=0 WHERE id='employee'")
    if mode == "fake_department":
        aux = {"department": "业务部"}
    with pytest.raises(AccountingError):
        sub.create_item(case.manager, OpenItemCreate(**base, aux=aux))
    with case.db.transaction(actor=case.manager) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM subledger_items WHERE book_id=?", (case.book["id"],)
            ).fetchone()[0]
            == 0
        )


def test_valid_entity_ids_are_preserved_and_required_dimensions_are_enforced(business):
    case = business
    sub, base, party, project, _ = _aux_case(
        case, ("counterparty", "project", "department", "employee")
    )
    aux = {
        "counterparty": party["id"],
        "project": project["id"],
        "department": case.department["id"],
        "employee": "employee",
    }
    result = sub.create_item(case.manager, OpenItemCreate(**base, aux=aux))
    assert result["aux"] == aux
    with pytest.raises(AccountingError, match="辅助"):
        sub.create_item(case.manager, OpenItemCreate(**base, aux={"counterparty": party["id"]}))


@pytest.mark.parametrize("bad", [["free_text"], ["project", "project"]])
def test_control_config_rejects_unknown_or_duplicate_auxiliary_kinds(business, bad):
    case = business
    opening, _ = setup_b03(case)
    with pytest.raises((AccountingError, ValueError)):
        SubledgerService(case.db).configure_control_account(
            case.manager,
            ControlAccountCreate(
                book_id=case.book["id"],
                kind="payable",
                control_account_id=opening["payable"]["id"],
                control_account_version=1,
                required_aux=bad,
                effective_from="2026-01",
                reason="拒绝未知辅助维度",
            ),
        )


@pytest.mark.parametrize("bad_aux", [{"project": "free_text"}, {"invented": "anything"}, {}])
def test_native_sql_cannot_bypass_entity_or_required_auxiliary_guard(business, bad_aux):
    case = business
    sub, base, party, project, _ = _aux_case(case, ("project",))
    item = sub.create_item(case.manager, OpenItemCreate(**base, aux={"project": project["id"]}))
    with pytest.raises(sqlite3.IntegrityError, match="Subledger"):
        with case.db.transaction(write=True, actor=case.manager) as conn:
            row = dict(
                conn.execute("SELECT * FROM subledger_items WHERE id=?", (item["id"],)).fetchone()
            )
            row.update(id=uuid4().hex, aux_json=json.dumps(bad_aux))
            columns = ",".join(row)
            conn.execute(
                f"INSERT INTO subledger_items({columns}) VALUES({','.join('?' for _ in row)})",
                tuple(row.values()),
            )
