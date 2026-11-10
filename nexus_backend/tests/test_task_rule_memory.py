from copy import deepcopy

import pytest

from app.accounting.domain import AccountingError
from test_rsi_rules import business, candidate  # noqa: F401


def memory(c, learning, artifact, **overrides):
    rule = learning.get_rule(c.manager, c.book["id"], artifact["source_rule_id"])
    return learning.rule_memory(
        c.manager,
        c.book["id"],
        rule["id"],
        expected_version=overrides.get("version", rule["current_version"]),
        expected_state_version=rule["state_version"],
    )


def test_memory_is_scoped_reviewed_template_not_historical_money(candidate):
    c, lab, artifact, learning, *_ = candidate
    value = memory(c, learning, artifact)
    assert value["state"] == "candidate"
    assert value["scope"]["company_id"] == c.company["id"]
    assert value["scope"]["period"] == "2026-10"
    assert value["auto_apply"] is False
    assert value["sources"][0]["reviewed_version"] > 0
    assert "amount" not in value["sources"][0]
    assert lab.shadow(c.manager, c.book["id"], artifact, "120.00")["lines"][0][1] == 12000
    assert learning.get_rule(c.manager, c.book["id"], value["rule_id"])["status"] == "draft"


def test_memory_rejects_wrong_version_and_foreign_book(candidate):
    c, _, artifact, learning, *_ = candidate
    with pytest.raises(AccountingError):
        memory(c, learning, artifact, version=999)
    with pytest.raises(AccountingError):
        learning.rule_memory(
            c.manager,
            c.other_book["id"],
            artifact["source_rule_id"],
            expected_version=1,
            expected_state_version=1,
        )


def test_memory_rechecks_revocation_and_same_version_source_changes(candidate, monkeypatch):
    c, _, artifact, learning, *_ = candidate
    from app.learning import memory as module

    original = module.snapshot
    calls = 0

    def change(*args, **kwargs):
        nonlocal calls
        calls += 1
        value = deepcopy(original(*args, **kwargs))
        if calls == 2:
            value["policy_fingerprint"] = "changed"
        return value

    monkeypatch.setattr(module, "snapshot", change)
    with pytest.raises(AccountingError) as error:
        memory(c, learning, artifact)
    assert error.value.status == 409


def test_inactive_counterparty_blocks_memory_without_publishing(candidate):
    from app.ledgers.models import EntityUpdate

    c, _, artifact, learning, master, entity = candidate
    master.update_entity(
        c.manager,
        c.book["id"],
        entity["id"],
        EntityUpdate(
            expected_version=entity["version"],
            name=entity["name"],
            active=False,
            reason="合成停用验证",
        ),
    )
    value = memory(c, learning, artifact)
    assert value["state"] == "blocked" and value["unavailable"]
    assert value["auto_apply"] is False
    assert learning.get_rule(c.manager, c.book["id"], value["rule_id"])["status"] == "draft"
