"""Fixed domain readers, synthetic data and mock-only provider; no autonomous tools."""

import copy
import json

import pytest

from app.accounting.domain import AccountingError
from app.accounting.service import fingerprint
from app.assistant import delivery_context
from app.assistant.delivery_investigation import DeliveryReads, ReadBudget
from test_assistant_delivery_context import project, enqueue
from test_ak04_assistant_execution import execution_case
from test_intelligence_workflow import ai_case
from test_model_cost_workflow import cost_case, cost_rows
from test_business import business
from test_p0_close_dependencies import close_case
from test_monthly_delivery_package import ready_case


def test_fixed_steps_and_handoff_cover_every_blocker_once(execution_case):
    c = execution_case
    view = project(c)
    assert view["schema"] == "assistant-delivery-v2"
    trace = view["investigation"]
    assert [step["tool"] for step in trace["steps"]] == [
        "selected_materials",
        "month_close",
        "package_receipt",
    ]
    assert trace["read_complete"] is True and trace["model_calls"] == 0
    assert view == project(c)  # No wall-clock data in the frozen proof.
    handoff = view["handoff"]
    indices = [i for group in handoff["groups"] for i in group["issue_indices"]]
    assert sorted(indices) == list(range(len(view["issues"])))
    for group in handoff["groups"]:
        assert all(
            view["issues"][i]["owner_role"] == group["owner_role"] for i in group["issue_indices"]
        )
    assert handoff["source_fingerprint"] == fingerprint(
        {"sources": view["sources"], "issues": view["issues"]}
    )
    assert view["can_deliver"] is None and not handoff["assigned"]


def test_customer_has_no_internal_read_steps(ready_case, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Customer must not invoke staff reader")

    from app.delivery.monthly_package import MonthlyPackageService

    monkeypatch.setattr(MonthlyPackageService, "_inspect", forbidden)
    view = project(ready_case, ready_case.actors["finance"])
    assert [step["tool"] for step in view["investigation"]["steps"]] == ["selected_materials"]
    assert view["investigation"]["coverage"] == "selected_items" and not view["sources"]


@pytest.mark.parametrize("field", ["order", "duplicate", "digest", "company", "handoff", "missing"])
def test_rehashed_investigation_and_handoff_tampering_rejected(execution_case, field):
    c = execution_case
    value = project(c)
    trace = value["investigation"]
    if field == "order":
        trace["steps"].reverse()
    if field == "duplicate":
        trace["steps"].append(copy.deepcopy(trace["steps"][0]))
    if field == "digest":
        trace["steps"][0]["outputs_sha256"] = "0" * 64
    if field == "company":
        trace["company_id"] = c.other_company["id"]
    if field == "handoff":
        value["handoff"]["groups"][0]["issue_indices"] = []
    if field == "missing":
        del value["investigation"]
    value["fingerprint"] = fingerprint({k: v for k, v in value.items() if k != "fingerprint"})
    with pytest.raises(ValueError):
        delivery_context.validate_projection(
            value,
            c.company["id"],
            [{"book_id": c.book["id"], "period": "2026-10"}],
            ["book:" + c.book["id"]],
        )


def test_worker_uses_three_readers_but_sends_once_and_preserves_review(execution_case):
    c = execution_case
    actor, conv, turn = enqueue(c, book_ids=[c.book["id"]])
    c.chat_worker.run_once()
    row = c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]
    assert row["status"] == "succeeded"
    assert "取证范围" in row["reply"]["answer"] and "人工接手" in row["reply"]["answer"]
    assert "仍须会计复核" in row["reply"]["answer"]
    pack = json.loads(c.calls[0]["messages"][1]["content"])["context_pack"]
    value = next(f["value"] for f in pack["facts"] if f["key"] == "delivery_task")
    assert len(value["investigation"]["steps"]) == 3
    assert len(c.calls) == 1 and len(cost_rows(c)) == 1
    assert c.chat_worker.run_once() is None


def empty_view():
    return dict(
        company_id="company-a",
        book_id="book-a",
        period="2026-10",
        coverage="selected_book",
        sources=[],
        issues=[],
    )


@pytest.mark.parametrize(
    "steps,seconds",
    [
        (True, 1),
        (1.5, 1),
        (-1, 1),
        (4, 1),
        (3, True),
        (3, -1),
        (3, float("nan")),
        (3, float("inf")),
    ],
)
def test_invalid_read_budget_fails_closed(steps, seconds):
    with pytest.raises(ValueError):
        ReadBudget(steps, seconds)


@pytest.mark.parametrize("budget", [ReadBudget(0, 1), ReadBudget(3, 0)])
def test_zero_budget_never_reads(budget):
    called = []
    reads = DeliveryReads(empty_view(), lambda: None, budget=budget, clock=lambda: 0)
    with pytest.raises(AccountingError):
        reads.read("selected_materials", lambda: called.append("read"))
    assert not called and not reads.steps


@pytest.mark.parametrize("case", ["time", "revoke", "late_authorization"])
def test_results_rechecked_before_acceptance(case):
    clock = [0]
    revoked = [False]

    def authorize():
        if revoked[0]:
            if case == "late_authorization":
                clock[0] = 11
            else:
                raise AccountingError("synthetic revoke", 403)

    reads = DeliveryReads(empty_view(), authorize, clock=lambda: clock[0])

    def read():
        if case == "time":
            clock[0] = 11
        else:
            revoked[0] = True

    with pytest.raises(AccountingError):
        reads.read("selected_materials", read)
    assert not reads.steps


@pytest.mark.parametrize("tool", ["sql", "month_close", "selected_materials"])
def test_unknown_out_of_order_or_repeated_reader_never_executes(tool):
    reads = DeliveryReads(empty_view(), lambda: None, clock=lambda: 0)
    if tool == "selected_materials":
        reads.read(tool, lambda: None)
    called = []
    with pytest.raises(AccountingError):
        reads.read(tool, lambda: called.append(tool))
    assert not called


def test_step_limit_stops_before_later_read_and_cannot_finish():
    reads = DeliveryReads(empty_view(), lambda: None, budget=ReadBudget(1, 1), clock=lambda: 0)
    reads.read("selected_materials", lambda: None)
    with pytest.raises(AccountingError):
        reads.read("month_close", lambda: pytest.fail("over budget"))
    with pytest.raises(AccountingError):
        reads.finish()


def test_later_reader_cannot_rewrite_prior_fact_or_scope():
    view = empty_view()
    reads = DeliveryReads(view, lambda: None, clock=lambda: 0)
    reads.read("selected_materials", lambda: view["issues"].append({"reason": "original"}))
    with pytest.raises(AccountingError):
        reads.read("month_close", lambda: view["issues"][0].update(reason="rewritten"))


def test_budget_failure_before_enqueue_has_no_fee_or_send(execution_case, monkeypatch):
    c = execution_case
    original = delivery_context.project_delivery
    monkeypatch.setattr(
        delivery_context,
        "project_delivery",
        lambda *args, **kwargs: original(*args, **kwargs, budget=ReadBudget(1, 10)),
    )
    with pytest.raises(AccountingError, match="预算"):
        enqueue(c, book_ids=[c.book["id"]])
    assert not c.calls and not cost_rows(c)
    assert c.chat_worker.run_once() is None


def test_trace_rejects_uncovered_or_malformed_ranges(execution_case):
    c = execution_case
    source = project(c)
    for bad in ([0, True], [0, -1], [0, 999], [1, 1], "all"):
        value = copy.deepcopy(source)
        value["investigation"]["steps"][0]["issue_range"] = bad
        value["fingerprint"] = fingerprint({k: v for k, v in value.items() if k != "fingerprint"})
        with pytest.raises(ValueError):
            delivery_context.validate_projection(
                value,
                c.company["id"],
                [{"book_id": c.book["id"], "period": "2026-10"}],
                ["book:" + c.book["id"]],
            )
