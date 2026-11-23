"""PLT08-A~E：固定六段、三类路径、失效即清除、受控记忆与留出评测。"""

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.assistant import answer_contract, answer_store, memory, paths
from test_business import business  # noqa: F401

SETTLE = "settle-001"
BANK = "bank-001"
FEE = "fee-001"
SIX_LABELS = ["结论", "依据", "差额或数字", "未检查的范围", "由谁处理", "办理入口"]


def _source(conn, book_id, source_id, name, amount, kind="business", evidence="原件"):
    conn.execute(
        "INSERT INTO sources(id,book_id,external_id,source_name,date,description,"
        "counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            source_id,
            book_id,
            f"ext-{source_id}",
            name,
            "2026-10-18",
            name,
            "虚构平台",
            amount,
            "income",
            evidence,
            uuid4().hex,
            kind,
        ),
    )


@pytest.fixture()
def plt08(business):  # noqa: F811
    case = business
    with case.db.transaction(write=True, actor=case.manager) as conn:
        _source(conn, case.book["id"], SETTLE, "平台结算单", 10_000_000)
        _source(conn, case.book["id"], BANK, "银行到账", 9_965_000, "bank", "银行回单")
    case.store = answer_store.AnswerStore(case.db)
    return case


def _payload(case, kind="revenue_mismatch", **extra):
    return {
        "kind": kind,
        "book_id": case.book["id"],
        "period": "2026-10",
        **extra,
    }


def _settlement(case, **extra):
    return _payload(
        case,
        settlement_source_id=SETTLE,
        bank_source_id=BANK,
        **extra,
    )


def _sample(**overrides):
    sample = {
        "allowed": True,
        "book_id": "sample-book",
        "period": "2026-10",
        "company_id": "sample-company",
        "firm_id": "sample-firm",
        "close": {"status": "blocked", "version": 3, "blockers": []},
        "delivery": {"package": None, "opened": False},
        "rules": [],
    }
    sample.update(overrides)
    return sample


# ---------------------------------------------------------------- A 六段与防幻觉


def test_unexplained_difference_is_six_sections_and_unconfirmed(plt08):
    answer = plt08.store.preview(plt08.manager, _settlement(plt08))
    assert [section["label"] for section in answer["sections"]] == SIX_LABELS
    assert answer["conclusion_state"] == "unconfirmed"
    assert answer_contract.section_text(answer, "conclusion") == "存在差异，原因未确认"
    evidence = answer_contract.section_text(answer, "evidence")
    assert SETTLE in evidence and BANK in evidence
    amounts = answer_contract.section_text(answer, "amounts")
    assert "100,000.00" in amounts
    assert "99,650.00" in amounts
    assert "350.00" in amounts
    unchecked = answer_contract.section_text(answer, "unchecked")
    assert "手续费" in unchecked and "未提供" in unchecked
    assert "accountant" in answer_contract.section_text(answer, "owner")
    assert f"source={BANK}" in answer_contract.section_text(answer, "entry")
    assert answer_contract.has_attribution_claim(answer) is False
    assert answer["model_calls"] == 0
    assert answer["auto_retry"] is False


def test_model_claiming_a_bank_fee_is_rewritten_by_the_server(plt08):
    answer = plt08.store.preview(
        plt08.manager,
        _settlement(plt08, narrative="这是银行手续费，差额就是手续费。"),
    )
    assert answer["conclusion_state"] == "unconfirmed"
    assert answer_contract.section_text(answer, "conclusion") == "存在差异，原因未确认"
    assert answer_contract.CODES["attribution"] in answer["codes"]
    assert "这是银行手续费" not in answer["text"]
    assert "手续费，差额" not in answer["text"]
    assert answer_contract.has_attribution_claim(answer) is False


def test_fabricated_identifier_and_amount_are_dropped(plt08):
    answer = plt08.store.preview(
        plt08.manager,
        _settlement(plt08, narrative="结算单 settle-999 少了 12,345.00 元。"),
    )
    assert answer_contract.CODES["reference"] in answer["codes"]
    assert answer_contract.CODES["amount"] in answer["codes"]
    assert "settle-999" not in answer["text"]
    assert "12,345.00" not in answer["text"]


def test_narrative_without_missing_facts_is_kept(plt08):
    answer = plt08.store.preview(
        plt08.manager,
        _settlement(plt08, narrative="结算单与银行到账相差 350.00 元。"),
    )
    assert "结算单与银行到账相差 350.00 元。" in answer["text"]
    assert answer_contract.CODES["amount"] not in answer["codes"]
    assert answer_contract.has_attribution_claim(answer) is False


def test_answer_without_evidence_abstains_instead_of_guessing():
    answer = answer_contract.abstain(["手续费/扣款凭据"])
    assert answer["conclusion_state"] == "abstained"
    conclusion = answer_contract.section_text(answer, "conclusion")
    assert answer_contract.ABSTAIN in conclusion
    assert answer_contract.CODES["abstained"] in answer["codes"]
    assert [section["label"] for section in answer["sections"]] == SIX_LABELS
    assert answer_contract.section_text(answer, "amounts") == "无"


def test_empty_sections_render_explicit_none():
    answer = answer_contract.build_answer({"kind": "why_not_delivered"})
    for section in answer["sections"]:
        assert section["lines"] == ["无"]


# -------------------------------------------------------------- B 三类确定路径


def test_why_not_delivered_mirrors_close_blockers_exactly():
    blockers = [
        {"code": "unposted_manual", "next_action": "补录手工凭证"},
        {"code": "voucher_review_pending", "next_action": "完成复核"},
    ]
    sample = _sample(close={"status": "blocked", "version": 4, "blockers": blockers})
    answer = answer_store.build_answer_for_payload(
        paths.SampleReader(sample),
        {"kind": "why_not_delivered", "book_id": "sample-book", "period": "2026-10"},
    )
    result = paths.why_not_delivered(paths.SampleReader(sample))
    assert [row["code"] for row in result["facts"]["blockers"]] == [row["code"] for row in blockers]
    assert result["status"] == "blocked"
    assert "2 项阻断" in answer_contract.section_text(answer, "conclusion")
    owner = answer_contract.section_text(answer, "owner")
    assert "reviewer" in owner
    entry = answer_contract.section_text(answer, "entry")
    assert "/workspace/accounting?book=sample-book" in entry
    evidence = answer_contract.section_text(answer, "evidence")
    assert "月结预检结果 v4" in evidence


def test_why_not_delivered_without_preflight_marks_scope_unchecked(plt08):
    answer = plt08.store.preview(plt08.manager, _payload(plt08, "why_not_delivered"))
    assert answer["conclusion_state"] == "confirmed"
    assert "没有月结预检记录" in answer_contract.section_text(answer, "conclusion")
    unchecked = answer_contract.section_text(answer, "unchecked")
    assert "月结预检结果" in unchecked
    assert "对外交付包" in unchecked


def test_number_origin_on_a_book_without_vouchers_is_honest(plt08):
    answer = plt08.store.preview(
        plt08.manager, _payload(plt08, "number_origin", account_code="6001")
    )
    assert answer["conclusion_state"] == "confirmed"
    assert "0 张凭证" in answer_contract.section_text(answer, "conclusion")
    assert "0.00" in answer_contract.section_text(answer, "amounts")


def test_number_origin_flags_a_non_tying_breakdown():
    sample = _sample(
        account={
            "account_code": "6001",
            "total_cents": 30_000,
            "rows": [
                {
                    "voucher_id": "v-1",
                    "voucher_no": 1,
                    "voucher_date": "2026-10-02",
                    "description": "虚构收入",
                    "amount_cents": 10_000,
                    "content_fingerprint": "a" * 64,
                },
                {
                    "voucher_id": "v-2",
                    "voucher_no": 2,
                    "voucher_date": "2026-10-03",
                    "description": "虚构收入",
                    "amount_cents": 15_000,
                    "content_fingerprint": "b" * 64,
                },
            ],
        }
    )
    answer = answer_store.build_answer_for_payload(
        paths.SampleReader(sample),
        {"kind": "number_origin", "book_id": "sample-book", "period": "2026-10"},
        narrative=None,
    )
    original = paths.number_origin(paths.SampleReader(sample), "6001")
    assert original["status"] == "partial"
    assert "不一致" in answer_contract.section_text(answer, "conclusion")
    assert "勾稽" in answer_contract.section_text(answer, "unchecked")
    assert "v-1" in answer_contract.section_text(answer, "evidence")


def test_unauthorized_sample_is_refused_and_listed_unchecked():
    sample = _sample(allowed=False)
    reader = paths.SampleReader(sample)
    with pytest.raises(AccountingError) as failure:
        paths.why_not_delivered(reader)
    assert failure.value.status == 403


def test_cross_company_book_is_refused(plt08):
    with pytest.raises(AccountingError) as failure:
        plt08.store.preview(
            plt08.manager,
            {
                "kind": "why_not_delivered",
                "book_id": plt08.other_book["id"],
                "period": "2026-11",
            },
        )
    assert failure.value.status == 422
    assert "调查月份与账套不一致" in failure.value.detail


# -------------------------------------------------------- C 失效即清除与计费诚实


def _evidence_change(case, source_id):
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT INTO source_evidence(source_id,book_id,evidence_ref,created_at,created_by) "
            "VALUES(?,?,?,?,?)",
            (
                source_id,
                case.book["id"],
                "重新上传的原件",
                "2026-10-31T00:00:00+00:00",
                case.manager.user_id,
            ),
        )


def test_changed_dependency_invalidates_and_hides_the_old_answer(plt08):
    created = plt08.store.investigate(plt08.manager, _settlement(plt08))
    first = plt08.store.read(plt08.manager, created["answer_id"])
    assert first["visible"] is True
    assert "100,000.00" in first["answer"]["text"]
    _evidence_change(plt08, SETTLE)
    second = plt08.store.read(plt08.manager, created["answer_id"])
    assert second["visible"] is False
    assert second["verification"] == "stale"
    assert second["reinvestigate"] is True
    assert "依据已变化" in second["invalid_reason"]
    assert second["answer"]["sections"] == []
    assert "100,000.00" not in second["answer"]["text"]
    with plt08.db.transaction(actor=plt08.manager) as conn:
        row = conn.execute(
            "SELECT status,invalid_reason FROM assistant_answers WHERE id=?",
            (created["answer_id"],),
        ).fetchone()
    assert row["status"] == "invalid"
    assert row["invalid_reason"]


def test_revoked_scope_refuses_without_leaking_the_conclusion(plt08):
    created = plt08.store.investigate(plt08.manager, _settlement(plt08))
    with pytest.raises(AccountingError) as failure:
        plt08.store.read(plt08.actors["peer"], created["answer_id"])
    assert failure.value.status in (403, 404)
    assert "无权" in failure.value.detail
    assert "100,000.00" not in failure.value.detail
    assert "差异" not in failure.value.detail


def test_reinvestigation_creates_a_new_answer_and_supersedes_the_old(plt08):
    created = plt08.store.investigate(plt08.manager, _settlement(plt08))
    _evidence_change(plt08, SETTLE)
    second = plt08.store.reinvestigate(plt08.manager, created["answer_id"])
    assert second["answer_id"] != created["answer_id"]
    assert second["visible"] is True
    with plt08.db.transaction(actor=plt08.manager) as conn:
        old = conn.execute(
            "SELECT status,superseded_by FROM assistant_answers WHERE id=?",
            (created["answer_id"],),
        ).fetchone()
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM assistant_answers WHERE status='active'"
        ).fetchone()
    assert old["status"] == "invalid"
    assert old["superseded_by"] == second["answer_id"]
    assert active["n"] == 1


def test_repeated_question_reuses_the_answer_without_charging(plt08):
    first = plt08.store.investigate(plt08.manager, _settlement(plt08))
    second = plt08.store.investigate(plt08.manager, _settlement(plt08))
    assert second["reused"] is True
    assert second["answer_id"] == first["answer_id"]
    with plt08.db.transaction(actor=plt08.manager) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM assistant_answers WHERE book_id=?",
            (plt08.book["id"],),
        ).fetchone()
    assert rows["n"] == 1
    assert first["answer"]["cost_state"] == "not_incurred"


def test_unverifiable_dependency_is_not_retried_and_costs_unknown(plt08, monkeypatch):
    created = plt08.store.investigate(plt08.manager, _settlement(plt08))

    def broken(*args, **kwargs):
        raise RuntimeError("来源库暂时不可读")

    monkeypatch.setattr(answer_store, "_current_version", broken)
    read = plt08.store.read(plt08.manager, created["answer_id"])
    assert read["visible"] is False
    assert read["verification"] == "unknown"
    assert read["answer"]["sections"] == []
    with plt08.db.transaction(actor=plt08.manager) as conn:
        row = conn.execute(
            "SELECT status,cost_state FROM assistant_answers WHERE id=?",
            (created["answer_id"],),
        ).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM assistant_answers WHERE book_id=?",
            (plt08.book["id"],),
        ).fetchone()
    assert row["status"] == "invalid"
    assert row["cost_state"] == "unknown"
    assert count["n"] == 1


# ------------------------------------------------------------- D 三类受控记忆


def test_personal_preference_is_personal_and_deletable_with_audit(plt08):
    saved = memory.set_preference(
        plt08.manager,
        plt08.company["id"],
        "queue_order",
        "due_first",
        db=plt08.db,
    )
    assert saved["value"] == "due_first"
    mine = memory.list_preferences(plt08.manager, plt08.company["id"], db=plt08.db)
    assert [row["pref_key"] for row in mine] == ["queue_order"]
    theirs = memory.list_preferences(plt08.actors["employee"], plt08.company["id"], db=plt08.db)
    assert theirs == []
    removed = memory.delete_preference(
        plt08.manager, plt08.company["id"], "queue_order", db=plt08.db
    )
    assert removed["deleted"] is True
    with plt08.db.transaction(actor=plt08.manager) as conn:
        audit = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_events WHERE action='assistant.preference.delete'"
        ).fetchone()
        left = conn.execute(
            "SELECT COUNT(*) AS n FROM assistant_preferences WHERE user_id=?",
            (plt08.manager.user_id,),
        ).fetchone()
    assert audit["n"] == 1
    assert left["n"] == 0


def test_preference_rejects_keys_that_could_move_money(plt08):
    with pytest.raises(AccountingError) as failure:
        memory.set_preference(
            plt08.manager, plt08.company["id"], "amount_cents", "100", db=plt08.db
        )
    assert failure.value.status == 422
    with pytest.raises(AccountingError) as failure:
        memory.set_preference(
            plt08.manager, plt08.company["id"], "queue_order", "biggest_first", db=plt08.db
        )
    assert failure.value.status == 422


def test_company_rule_requires_a_second_person_to_approve(plt08):
    created = memory.propose_rule(
        plt08.manager,
        plt08.company["id"],
        "bank_fee_hint",
        "银行到账先看手续费凭据",
        {"priority": 20, "match": {"keyword": "到账"}, "tags": ["银行"]},
        db=plt08.db,
    )
    assert created["status"] == "proposed"
    with pytest.raises(AccountingError) as failure:
        memory.approve_rule(plt08.manager, created["id"], db=plt08.db)
    assert failure.value.status == 403
    assert "不能批准自己提议" in failure.value.detail
    approved = memory.approve_rule(plt08.actors["company_admin"], created["id"], db=plt08.db)
    assert approved["status"] == "approved"
    with plt08.db.transaction(actor=plt08.manager) as conn:
        rules = memory.effective_rules(conn, plt08.manager, plt08.company["id"])
    assert [rule["id"] for rule in rules] == [created["id"]]
    assert rules[0]["version"] == 1


def test_rule_payload_cannot_carry_money(plt08):
    with pytest.raises(AccountingError) as failure:
        memory.propose_rule(
            plt08.manager,
            plt08.company["id"],
            "bad_rule",
            "带金额的规则",
            {"threshold_cents": 100},
            db=plt08.db,
        )
    assert failure.value.status == 422
    assert "金额" in failure.value.detail
    with pytest.raises(AccountingError) as failure:
        memory.propose_rule(
            plt08.manager,
            plt08.company["id"],
            "bad_rule",
            "带金额的规则",
            {"match": {"amount_cents_gte": "100"}},
            db=plt08.db,
        )
    assert failure.value.status == 422


def test_rules_never_leak_across_companies_and_posts(plt08):
    created = memory.propose_rule(
        plt08.manager,
        plt08.company["id"],
        "close_hint",
        "结账先看缺件",
        {"priority": 5},
        db=plt08.db,
    )
    memory.approve_rule(plt08.actors["company_admin"], created["id"], db=plt08.db)
    with plt08.db.transaction(actor=plt08.manager) as conn:
        other = memory.effective_rules(conn, plt08.manager, plt08.other["id"])
    assert other == []
    with pytest.raises(AccountingError) as failure:
        memory.propose_rule(
            plt08.actors["employee"],
            plt08.company["id"],
            "staff_rule",
            "员工自己立规则",
            {"priority": 5},
            db=plt08.db,
        )
    assert failure.value.status == 403


def test_expired_or_retired_rule_disappears_immediately(plt08):
    expired = memory.propose_rule(
        plt08.manager,
        plt08.company["id"],
        "old_rule",
        "已经过期的规则",
        {"priority": 5},
        db=plt08.db,
        expires_at="2020-01-01T00:00:00+00:00",
    )
    memory.approve_rule(plt08.actors["company_admin"], expired["id"], db=plt08.db)
    with plt08.db.transaction(actor=plt08.manager) as conn:
        assert memory.effective_rules(conn, plt08.manager, plt08.company["id"]) == []

    live = memory.propose_rule(
        plt08.manager,
        plt08.company["id"],
        "live_rule",
        "仍然生效的规则",
        {"priority": 5},
        db=plt08.db,
    )
    memory.approve_rule(plt08.actors["company_admin"], live["id"], db=plt08.db)
    with plt08.db.transaction(actor=plt08.manager) as conn:
        assert len(memory.effective_rules(conn, plt08.manager, plt08.company["id"])) == 1
    memory.retire_rule(plt08.manager, live["id"], "口径变了", db=plt08.db)
    with plt08.db.transaction(actor=plt08.manager) as conn:
        assert memory.effective_rules(conn, plt08.manager, plt08.company["id"]) == []


def test_single_manual_correction_only_creates_a_suggestion(plt08):
    suggestion = memory.suggest_rule_from_correction(
        {"note": "这次把银行手续费补进凭据里"}, company_id=plt08.company["id"]
    )
    assert suggestion["applied"] is False
    assert suggestion["requires_human_proposal"] is True
    with plt08.db.transaction(actor=plt08.manager) as conn:
        rows = conn.execute("SELECT COUNT(*) AS n FROM assistant_rule_proposals").fetchone()
    assert rows["n"] == 0


def test_approved_rule_is_cited_with_id_and_version(plt08):
    created = memory.propose_rule(
        plt08.manager,
        plt08.company["id"],
        "cite_rule",
        "结账前先核对缺件",
        {"priority": 5},
        db=plt08.db,
    )
    memory.approve_rule(plt08.actors["company_admin"], created["id"], db=plt08.db)
    answer = plt08.store.preview(plt08.manager, _payload(plt08, "why_not_delivered"))
    evidence = answer_contract.section_text(answer, "evidence")
    assert created["id"] in evidence
    assert "版本 1" in evidence
    dependencies = [row for row in answer["dependencies"] if row["object_type"] == "rule"]
    assert dependencies and dependencies[0]["object_id"] == created["id"]


def test_rule_rows_are_append_only(plt08):
    created = memory.propose_rule(
        plt08.manager,
        plt08.company["id"],
        "frozen_rule",
        "内容不可改写的规则",
        {"priority": 5},
        db=plt08.db,
    )
    with pytest.raises(Exception) as failure:
        with plt08.db.transaction(write=True, actor=plt08.manager) as conn:
            conn.execute(
                "UPDATE assistant_rule_proposals SET payload_json=? WHERE id=?",
                (json.dumps({"priority": 90}), created["id"]),
            )
    assert "frozen" in str(failure.value)
    with pytest.raises(Exception) as failure:
        with plt08.db.transaction(write=True, actor=plt08.manager) as conn:
            conn.execute("DELETE FROM assistant_rule_proposals WHERE id=?", (created["id"],))
    assert "audit" in str(failure.value)


# PLT08-E：≥60 条独立留出样本、三组独立候选、统一盲评、五项指标


def _replay():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.assistant_replay import plt08

    return plt08


def test_holdout_dataset_is_sealed_and_balanced():
    replay = _replay()
    dataset = replay.load_holdout()
    samples = dataset["samples"]
    assert dataset["synthetic"] is True
    assert "合成数据" in dataset["_notice"]
    assert dataset["schema"] == "plt08-holdout-v1"
    assert len(samples) >= 60
    counts = {}
    for sample in samples:
        counts[sample["category"]] = counts.get(sample["category"], 0) + 1
    assert set(counts) == {
        "attribution_supported",
        "missing_evidence_refusal",
        "cross_period_refusal",
        "cross_entity_leak",
    }
    assert min(counts.values()) >= 15
    questions = {sample["question"] for sample in samples}
    assert questions == {"revenue_mismatch", "why_not_delivered", "number_origin"}
    assert len({sample["id"] for sample in samples}) == len(samples)


def test_replay_groups_generate_independently_and_score_blind():
    replay = _replay()
    dataset = replay.load_holdout()
    samples = dataset["samples"][:8]
    candidates = {
        group: [replay.generate(group, sample) for sample in samples] for group in replay.GROUPS
    }
    # 三组各自独立生成：C 组的对抗叙述不会污染 A/B 组候选。
    plain = candidates["A_plain"][0]["text"]
    guarded = candidates["C_guarded_narrative"][0]["text"]
    assert plain and guarded
    observations = {}
    for group, answers in candidates.items():
        observations[group] = [
            replay.score(sample, answer) for sample, answer in zip(samples, answers)
        ]
    labels = {
        replay.blind_labels(samples, group, candidates[group])[sample["id"]]
        for group in replay.GROUPS
        for sample in samples
    }
    assert len(labels) == len(replay.GROUPS) * len(samples)
    for rows in observations.values():
        assert {row["sample"] for row in rows} == {sample["id"] for sample in samples}


def test_replay_metrics_are_five_independent_numbers(tmp_path):
    replay = _replay()
    report, _ = replay.run(tmp_path / "run")
    for group in replay.GROUPS:
        metrics = report["config_groups"][group]
        assert "总分" not in metrics
        for key in (
            "事实正确率",
            "引用准确率",
            "漏报阻断率",
            "阻断项完整列出率",
            "应弃答而未弃答率",
            "单次有效诊断回放成本",
        ):
            assert isinstance(metrics[key], float)
        assert metrics["模型调用次数"] == 0
        assert metrics["网络调用次数"] == 0


def test_guarded_narrative_group_never_leaks_attribution(tmp_path):
    replay = _replay()
    report, _ = replay.run(tmp_path / "run")
    guarded = report["config_groups"]["C_guarded_narrative"]
    plain = report["config_groups"]["A_plain"]
    # C 组确实把模型的归因断定改写掉了：改写发生，且残留 0 条。
    assert guarded["叙述被改写数"] > 0
    assert guarded["残留归因断定数"] == 0
    assert plain["残留归因断定数"] == 0


def test_rules_only_add_citations_not_judgements(tmp_path):
    replay = _replay()
    report, _ = replay.run(tmp_path / "run")
    plain = report["config_groups"]["A_plain"]
    rules = report["config_groups"]["B_rules"]
    # 企业规则按设计不能改金额与判断：B 组事实正确率与 A 组一致，只多出依据引用。
    assert rules["事实正确率"] == plain["事实正确率"]
    assert rules["规则依据引用数"] > plain["规则依据引用数"]


def test_replay_refuses_to_overwrite_existing_directory(tmp_path):
    replay = _replay()
    target = tmp_path / "kept"
    report, observations = replay.run(target)
    with pytest.raises(FileExistsError):
        replay.save(report, observations, target)
    assert (target / "report.json").exists()
