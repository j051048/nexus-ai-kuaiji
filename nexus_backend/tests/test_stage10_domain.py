import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.accounting.database import Database
from app.accounting.domain import Actor
from app.accounting.invariants import money_to_cents, validate_period
from app.accounting.models import BookCreate, DraftLine
from app.accounting.telemetry import TransactionMetrics

CASES = json.loads((Path(__file__).parent / "fixtures" / "domain_contract.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES["money"])
def test_domain_money_and_request_contract(case):
    if case["valid"]:
        assert money_to_cents(case["value"]) == case["cents"]
        assert money_to_cents(DraftLine(account_code="1002", debit=case["value"], credit="0").debit) == case["cents"]
    else:
        with pytest.raises(ValueError):
            money_to_cents(case["value"])
        with pytest.raises(ValidationError):
            DraftLine(account_code="1002", debit=case["value"], credit="0")


@pytest.mark.parametrize("case", CASES["period"])
def test_domain_period_and_request_contract(case):
    if case["valid"]:
        assert validate_period(case["value"]) == BookCreate(name="测试", period=case["value"]).period
    else:
        with pytest.raises(ValueError):
            validate_period(case["value"])
        with pytest.raises(ValidationError):
            BookCreate(name="测试", period=case["value"])


def test_metrics_bounded_tenant_scoped_and_rollback(tmp_path):
    metrics = TransactionMetrics(capacity=3)
    metrics.record(firm_id="other", transaction_ms=1000)
    metrics.record(firm_id="mine", company_id="a", lock_wait_ms=5, transaction_ms=7)
    metrics.record(firm_id="mine", company_id="b", transaction_ms=999)
    metrics.record(firm_id="mine", transaction_ms=20)
    assert metrics.snapshot(firm_id="other")["count"] == 0
    assert metrics.snapshot(firm_id="mine", company_id="a")["transaction"]["max_ms"] == 7
    assert metrics.snapshot(firm_id="mine", company_id="unknown")["transaction"]["max_ms"] is None
    db = Database(tmp_path / "measure.db")
    with pytest.raises(RuntimeError):
        with db.transaction(write=True, actor=Actor("u", "f"), company_id="c") as conn:
            conn.execute("CREATE TABLE must_rollback(id INTEGER)")
            raise RuntimeError("rollback")
    report = db.metrics.snapshot(firm_id="f", company_id="c")
    assert report["rollbacks"] == report["writes"] == 1
    assert report["lock_wait"]["max_ms"] >= 0
    with db.transaction() as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='must_rollback'").fetchone()
