"""P02-R1: real signed actors, nonzero accounting, isolated PostgreSQL only."""

import os
from pathlib import Path

import pytest

from app.accounting.domain import Actor
from p02_business_fixture import complete_close_delivery, seed_nonzero_flow
from p02_session_fixture import create_session


@pytest.fixture
def session(tmp_path):
    config = os.environ.get("P02_POSTGRES_CONFIG")
    if not config:
        pytest.skip("P02_POSTGRES_CONFIG must identify a local isolated PG16/17 configuration")
    with create_session(Path(config), tmp_path) as value:
        yield value


def test_nonzero_employee_bank_subledger_to_exact_customer_ack(session):
    state = seed_nonzero_flow(session.client, session.headers, session.ids)
    owner = Actor(session.ids["users"]["owner"], session.ids["firm"])
    with session.db.transaction(actor=owner) as connection:
        assert (
            connection.execute(
                "SELECT role FROM memberships WHERE firm_id=? AND user_id=?",
                (session.ids["firm"], session.ids["users"]["accountant"]),
            ).fetchone()["role"]
            == "reviewer"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM ledger_managers WHERE user_id=?",
                (session.ids["users"]["accountant"],),
            ).fetchone()[0]
            == 0
        ), "ordinary accounting must not be repaired by manager elevation"
    assert set(state["initial_blockers"]) >= {"bank_reconciliation", "subledger_control"}
    assert state["bank_closing_cents"] == 973190
    assert state["payable_remaining_cents"] == 1000000
    complete_close_delivery(session.client, session.headers, session.ids, state)
    assert state["close_status"] == "locked"
    assert state["report_status"] == "published"
    assert state["customer_acknowledged"] is True
    assert state["package_version"] == 1
