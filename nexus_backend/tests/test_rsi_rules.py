"""Reviewed service examples, never fabricated approval SQL."""

from copy import deepcopy
import hashlib
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, DraftUpdate
from app.ledgers.models import EntityCreate, EntityUpdate
from app.ledgers.service import LedgersService
from app.learning.service import LearningService
from test_business import business, create, handoff
from tools.ai_inference_lab.rsi.rules import LocalRules
from tools.ai_inference_lab.rsi.snapshots import SnapshotError


@pytest.mark.parametrize(
    "path",
    [
        "postgresql://user@127.0.0.1:25616/ak01_local_ak05_test?host=remote.example",
        "postgresql://user@127.0.0.1:25616/ak01_local_ak05_test#fragment",
        "postgresql://user@remote.example:25616/ak01_local_ak05_test",
    ],
)
def test_local_guard_rejects_connection_overrides_without_connecting(path):
    from types import SimpleNamespace

    with pytest.raises(SnapshotError, match="local_database"):
        LocalRules(SimpleNamespace(is_postgres=True, path=path))


def test_local_guard_checks_resolved_sqlite_path(tmp_path):
    from types import SimpleNamespace

    outside = tmp_path.anchor + "tmp_test/../production/accounting.db"
    with pytest.raises(SnapshotError, match="isolated_sqlite"):
        LocalRules(SimpleNamespace(is_postgres=False, path=outside))


def reviewed_sample(c, *, counterparty="耗材商店", corrected=True):
    learning = LearningService(c.db)
    c.ledger.learning = learning
    event = create(
        c,
        template="procurement_expense",
        payment_status="paid",
        counterparty=counterparty,
        amount="100.00",
    )
    original = (event["id"] + " 合成票据").encode()
    event = c.service.add_attachment(
        c.actors["employee"],
        event["id"],
        event["version"],
        {
            "id": uuid4().hex,
            "storage_key": "rsi-" + event["id"],
            "filename": "synthetic.pdf",
            "media_type": "application/pdf",
            "sha256": hashlib.sha256(original).hexdigest(),
            "size_bytes": len(original),
        },
    )
    event = c.service.submit(c.actors["employee"], event["id"], event["version"])
    event = c.service.approve_department(c.actors["approver"], event["id"], event["version"])
    event = c.service.approve_finance(c.actors["finance"], event["id"], event["version"])
    mapped = handoff(c, event)
    draft = c.ledger.get_draft(c.manager, c.book["id"], mapped["draft_id"])
    if corrected:
        draft = c.ledger.update_draft(
            c.manager,
            c.book["id"],
            draft["id"],
            DraftUpdate(
                expected_version=draft["version"],
                description="核实办公用途",
                correction_reason="虚构合同逐项核对",
                lines=[
                    {"account_code": "6602.99", "debit": "100.00", "credit": "0.00"},
                    {"account_code": "1002", "debit": "0.00", "credit": "100.00"},
                ],
            ),
        )
    c.ledger.approve(
        c.actors["accountant"],
        c.book["id"],
        draft["id"],
        ApprovalRequest(expected_version=draft["version"]),
    )
    samples = learning.overview(c.manager, c.book["id"])["samples"]
    return next(s["id"] for s in samples if s["draft_id"] == draft["id"])


def setup_rules(c):
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "UPDATE ai_company_settings SET enabled=0 WHERE firm_id=? AND company_id=?",
            (c.manager.firm_id, c.company["id"]),
        )
    positive = reviewed_sample(c)
    negative = reviewed_sample(c, counterparty="其他商店", corrected=False)
    learning = LearningService(c.db)
    rule = learning.overview(c.manager, c.book["id"])["rules"][0]
    master = LedgersService(c.db)
    entity = master.create_entity(
        c.manager,
        c.book["id"],
        EntityCreate(kind="counterparty", code="RSI-VENDOR", name="耗材商店"),
    )
    master.create_entity(
        c.manager, c.book["id"], EntityCreate(kind="counterparty", code="RSI-NEG", name="其他商店")
    )
    lab = LocalRules(c.db)
    artifact = lab.prepare(c.manager, c.book["id"], rule["id"], [positive], [negative])
    return lab, artifact, learning, master, entity


@pytest.fixture
def candidate(business):
    return business, *setup_rules(business)


def test_stage_evaluate_only_draft_and_deduplicate(candidate):
    c, lab, artifact, learning, _, _ = candidate
    rule = lab.stage(c.manager, c.book["id"], artifact)
    assert rule["status"] == "draft"
    assert lab.stage(c.manager, c.book["id"], artifact)["id"] == rule["id"]
    evaluation = lab.evaluate(c.manager, c.book["id"], artifact, rule["id"])
    assert evaluation["passed"]
    assert learning.get_rule(c.manager, c.book["id"], rule["id"])["status"] == "draft"
    with c.db.transaction(actor=c.manager) as conn:
        snapshot = learning.get_sample(c.manager, c.book["id"], artifact["positive_ids"][0])[
            "snapshot"
        ]
        assert (
            learning.rule_generate_in_transaction(
                conn, c.manager, c.book["id"], "procurement_expense", snapshot["payload"], 10000
            )
            is None
        )
    rendered = lab.shadow(c.manager, c.book["id"], artifact, "120.00")
    assert rendered["lines"] == [("6602.99", 12000, 0), ("1002", 0, 12000)]


@pytest.mark.parametrize("actor", ["employee", "finance"])
def test_low_privilege_cannot_stage(candidate, actor):
    c, lab, artifact, *_ = candidate
    with pytest.raises(AccountingError):
        lab.stage(c.actors[actor], c.book["id"], artifact)


def test_foreign_scope_and_tamper_cannot_authorize(candidate):
    c, lab, artifact, *_ = candidate
    with pytest.raises((AccountingError, SnapshotError)):
        lab.stage(c.manager, c.other_book["id"], artifact)
    bad = deepcopy(artifact)
    bad["definition"]["lines"][0]["account_code"] = "1001"
    with pytest.raises(SnapshotError):
        lab.stage(c.manager, c.book["id"], bad)


def test_current_master_version_and_revoke_rechecked(candidate):
    c, lab, artifact, _, master, entity = candidate
    master.update_entity(
        c.manager,
        c.book["id"],
        entity["id"],
        EntityUpdate(expected_version=1, name="耗材商店", active=True, reason="重新核实"),
    )
    with pytest.raises(SnapshotError, match="stale"):
        lab.stage(c.manager, c.book["id"], artifact)
    c.directory.revoke_book_member(c.manager, c.book["id"], c.actors["accountant"].user_id)
    with pytest.raises(AccountingError):
        lab.stage(c.actors["accountant"], c.book["id"], artifact)


def test_positive_negative_and_holdout_overlap_rejected(candidate):
    c, lab, artifact, *_ = candidate
    with pytest.raises(SnapshotError):
        lab.prepare(
            c.manager,
            c.book["id"],
            artifact["source_rule_id"],
            artifact["positive_ids"],
            artifact["positive_ids"],
        )
    with pytest.raises(SnapshotError):
        lab.prepare(
            c.manager,
            c.book["id"],
            artifact["source_rule_id"],
            artifact["positive_ids"],
            artifact["negative_ids"],
            holdout_ids=artifact["positive_ids"],
        )


def test_latest_review_receipt_is_required_not_just_sample_flag(candidate, monkeypatch):
    from app.accounting.review_policy import ReviewPolicyService

    c, lab, artifact, *_ = candidate

    def absent(*args):
        raise AccountingError("缺少当前复核凭据", 409)

    monkeypatch.setattr(ReviewPolicyService, "approved_receipt", absent)
    with pytest.raises(AccountingError, match="复核凭据"):
        lab.stage(c.manager, c.book["id"], artifact)


def test_rehashed_candidate_cannot_replace_current_reviewed_version(candidate, monkeypatch):
    c, lab, artifact, *_ = candidate
    original = LearningService.get_sample

    def stale(self, *args):
        value = original(self, *args)
        value["reviewed_version"] -= 1
        return value

    monkeypatch.setattr(LearningService, "get_sample", stale)
    with pytest.raises(SnapshotError, match="review"):
        lab.prepare(
            c.manager,
            c.book["id"],
            artifact["source_rule_id"],
            artifact["positive_ids"],
            artifact["negative_ids"],
        )


def test_stage_and_evaluate_failures_rollback_individually(candidate, monkeypatch):
    c, lab, artifact, learning, *_ = candidate
    original = LearningService._audit

    def fail_create(conn, actor, book, action, *args):
        if action == "learning.rule.create":
            raise RuntimeError("injected save failure")
        return original(conn, actor, book, action, *args)

    before = learning.overview(c.manager, c.book["id"])["rules"]
    monkeypatch.setattr(LearningService, "_audit", staticmethod(fail_create))
    with pytest.raises(RuntimeError):
        lab.stage(c.manager, c.book["id"], artifact)
    assert learning.overview(c.manager, c.book["id"])["rules"] == before
    monkeypatch.setattr(LearningService, "_audit", staticmethod(original))
    staged = lab.stage(c.manager, c.book["id"], artifact)

    def fail_evaluation(conn, actor, book, action, *args):
        if action == "learning.rule.evaluate":
            raise RuntimeError("injected evaluation failure")
        return original(conn, actor, book, action, *args)

    monkeypatch.setattr(LearningService, "_audit", staticmethod(fail_evaluation))
    with pytest.raises(RuntimeError):
        lab.evaluate(c.manager, c.book["id"], artifact, staged["id"])
    assert learning.get_rule(c.manager, c.book["id"], staged["id"])["status"] == "draft"
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM learning_rule_evaluations WHERE rule_id=?", (staged["id"],)
            ).fetchone()[0]
            == 0
        )


def test_no_posting_model_or_export_side_effects(candidate, monkeypatch):
    c, lab, artifact, learning, *_ = candidate
    tables = [
        "sources",
        "drafts",
        "vouchers",
        "exports",
        "model_costs",
        "ai_runs",
        "ocr_jobs",
        "proposal_jobs",
    ]

    def counts():
        with c.db.transaction(actor=c.manager) as conn:
            return {
                name: conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in tables
            }

    before = counts()
    monkeypatch.setattr(LearningService, "publish", lambda *a: pytest.fail("automatic publication"))
    staged = lab.stage(c.manager, c.book["id"], artifact)
    lab.evaluate(c.manager, c.book["id"], artifact, staged["id"])
    assert counts() == before


def test_same_name_different_entity_and_holdout_original_rejected(candidate):
    c, lab, artifact, learning, master, _ = candidate
    with c.db.transaction(actor=c.manager) as conn:
        original_hash = conn.execute(
            "SELECT a.sha256 FROM business_attachments a JOIN business_mappings m ON m.event_id=a.event_id JOIN learning_samples s ON s.draft_id=m.draft_id WHERE s.id=?",
            (artifact["positive_ids"][0],),
        ).fetchone()[0]
    protected = LocalRules(c.db, holdout_source_hashes=(original_hash,))
    with pytest.raises(SnapshotError, match="source"):
        protected.prepare(
            c.manager,
            c.book["id"],
            artifact["source_rule_id"],
            artifact["positive_ids"],
            artifact["negative_ids"],
        )
    master.create_entity(
        c.manager,
        c.book["id"],
        EntityCreate(kind="counterparty", code="RSI-AMBIGUOUS", name="耗材商店"),
    )
    with pytest.raises(SnapshotError, match="stable_counterparty"):
        lab.stage(c.manager, c.book["id"], artifact)


@pytest.mark.parametrize("change", ["bank", "complex_tax", "unreviewed", "settlement"])
def test_unsupported_conditions_never_become_rule_candidates(candidate, monkeypatch, change):
    c, lab, artifact, *_ = candidate
    get_rule, get_sample = LearningService.get_rule, LearningService.get_sample

    def rule(self, *args):
        value = get_rule(self, *args)
        if change == "bank":
            value["definition"]["source_kind"] = "bank"
        if change == "complex_tax":
            value["definition"]["lines"][0]["amount_field"] = "tax"
        return value

    def sample(self, *args):
        value = get_sample(self, *args)
        if change == "unreviewed":
            value["corrected"] = False
        if change == "settlement":
            value["ordinary_rule_eligible"] = False
        return value

    monkeypatch.setattr(LearningService, "get_rule", rule)
    monkeypatch.setattr(LearningService, "get_sample", sample)
    with pytest.raises(SnapshotError):
        lab.prepare(
            c.manager,
            c.book["id"],
            artifact["source_rule_id"],
            artifact["positive_ids"],
            artifact["negative_ids"],
        )
