"""DCP09: the JIT switch is transaction-local and never changes semantics."""

import os

import pytest

from app.accounting.database import Database
from app.accounting.domain import Actor
from app.main import create_app
from fastapi.testclient import TestClient


def test_write_transaction_cannot_carry_a_plan_switch(tmp_path):
    database = Database(str(tmp_path / "jit.db"))
    with pytest.raises(ValueError):
        database.transaction(write=True, jit=False).__enter__()


def test_read_transaction_keeps_results_identical(tmp_path):
    database = Database(str(tmp_path / "jit.db"))
    actor = Actor("u1", "f1")
    with database.transaction(actor=actor) as conn:
        plain = conn.execute("SELECT 1 AS value").fetchone()["value"]
    with database.transaction(actor=actor, jit=False) as conn:
        switched = conn.execute("SELECT 1 AS value").fetchone()["value"]
    assert plain == switched == 1


def test_bootstrap_is_equivalent_and_repeats_without_leaking(tmp_path):
    app = create_app(tmp_path / "jit-boot.db", seed=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45697)) as client:
        token = client.get("/api/session").json()["token"]
        headers = {"Authorization": "Bearer " + token}
        first = client.get("/api/workspace/bootstrap", headers=headers)
        second = client.get("/api/workspace/bootstrap", headers=headers)
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert first.json()["me"]["current_firm_id"]


@pytest.mark.skipif(not os.getenv("ACCOUNTING_TEST_DATABASE_URL"), reason="needs isolated PostgreSQL")
def test_postgres_connection_does_not_keep_jit_off():
    database = Database(os.environ["ACCOUNTING_TEST_DATABASE_URL"])
    actor = Actor("p05-owner", "p05-firm")
    with database.transaction(actor=actor, jit=False) as conn:
        assert conn.execute("SHOW jit").fetchone()["jit"] == "off"
    # A later transaction on a pooled connection starts from the server default.
    with database.transaction(actor=actor) as conn:
        assert conn.execute("SHOW jit").fetchone()["jit"] == "on"
