"""Actual API/worker roles and transactions, with a mock-only model transport."""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import httpx
import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError
from app.accounting.models import ExportRequest
from app.accounting.service import AccountingService
from app.assistant.meter import AssistantMeter
from app.assistant.models import ConversationCreate, TurnCreate
from app.assistant.provider import AssistantProvider
from app.assistant.service import AssistantService
from app.assistant.worker import AssistantWorker
from app.business.models import BusinessEventCreate
from app.business.service import BusinessService
from app.intelligence.provider import AISettings, OpenAICompatibleProvider
from app.intelligence.service import IntelligenceService
from app.operations.meter import MeterService
from app.operations.pricing import Pricing, Rate
from app.operations.service import OperationsService
from app.service_plans.models import PlanSave
from app.service_plans.service import ServicePlansService
from test_business_postgres import ADMIN_URL, APP_URL, business_case
from test_business_postgres import business_postgres as existing_business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_intelligence_workflow import FakeProvider, create_event, extract, handoff
from test_model_cost_workflow import policy
from test_ak03_assistant_facts import amount_claim, reply
from tools.assistant_replay.baseline import DEFAULT_MANIFEST, load_dataset
from test_ak01_postgres import child, child_payload, safe_error
from test_stage15_conversations import SyntheticChatProvider
from test_monthly_delivery_postgres import p0_pg, p0_case, package_case

WORKER_URL = os.getenv("ACCOUNTING_TEST_WORKER_DATABASE_URL")
QUESTION = "为什么这家企业本月还不能交付？"


@pytest.mark.parametrize("outcome", ["success", "changed", "revoked", "foreign", "unknown"])
def test_pg_detail_pages_are_private_read_only_and_version_bound(pg_delivery, monkeypatch, outcome):
    from test_assistant_delivery_pages import bank_rows
    from app.subledger.models import BankAdjustment
    import test_posting_service

    c = pg_delivery

    def authorized_ledger(case):
        with case.db.transaction(actor=case.manager) as conn:
            return conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.book["id"],)
            ).fetchone()[0]

    with monkeypatch.context() as patch:
        patch.setattr(test_posting_service, "ledger_of", authorized_ledger)
        sub, bank = bank_rows(c, 22 if outcome == "success" else 2)
    actor, conv, turn = enqueue(c)
    if outcome == "unknown":
        c.mode = "timeout"
    c.chat_worker.run_once()
    original_turn = raw(c, turn["id"])
    original_costs = costs(c, turn["id"])
    assert original_costs
    read = lambda **kw: c.chat_service.delivery_details(
        actor, conv["id"], turn["id"], kind="bank", limit=10, **kw
    )
    if outcome == "unknown":
        with pytest.raises(AccountingError) as error:
            read()
        assert error.value.status == 409
    else:
        first = read()
        if outcome == "success":
            second = read(after=first["cursor"])
            third = read(after=second["cursor"])
            assert [len(p["rows"]) for p in (first, second, third)] == [10, 10, 2]
            assert len({r["key"] for p in (first, second, third) for r in p["rows"]}) == 22
            assert all(p["accounting_approved"] is False for p in (first, second, third))
            assert second == read(after=first["cursor"])
        else:
            if outcome == "changed":
                sub.add_bank_adjustment(
                    c.manager,
                    BankAdjustment(
                        book_id=c.book["id"],
                        bank_account_id=bank["id"],
                        period="2026-10",
                        kind="error",
                        amount="0.30",
                        explanation="读取两页之间变化",
                    ),
                )
            elif outcome == "revoked":
                with c.psycopg.connect(ADMIN_URL) as conn:
                    conn.execute(
                        "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
                        (c.book["id"], actor.user_id),
                    )
            else:
                # Same firm owner and foreign enterprise staff cannot read the
                # accountant's private conversation even with a valid cursor.
                for other in (c.manager, c.actors["outsider"]):
                    with pytest.raises(AccountingError) as error:
                        c.chat_service.delivery_details(
                            other, conv["id"], turn["id"], after=first["cursor"]
                        )
                    assert error.value.status == 404
            if outcome != "foreign":
                with pytest.raises(AccountingError) as error:
                    read(after=first["cursor"])
                assert error.value.status == (409 if outcome == "changed" else 404)
    c.chat_worker.run_once()
    assert len(c.calls) == 1
    assert costs(c, turn["id"]) == original_costs
    assert raw(c, turn["id"]) == original_turn


@pytest.mark.parametrize("outcome", ["success", "changed", "revoked", "unknown"])
def test_pg_account_detail_binding_preserves_single_send(pg_delivery, monkeypatch, outcome):
    from test_assistant_delivery_details import configured
    from app.subledger.models import BankAdjustment
    import test_posting_service

    c = pg_delivery

    def authorized_ledger(case):
        with case.db.transaction(actor=case.manager) as conn:
            return conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.book["id"],)
            ).fetchone()[0]

    # Reuse the business fixture through the restricted API role, never a schema
    # owner. The old SQLite fixture omits actor only in its ledger lookup.
    with monkeypatch.context() as patch:
        patch.setattr(test_posting_service, "ledger_of", authorized_ledger)
        sub, bank = configured(c)
    actor, conv, turn = enqueue(c)
    frozen = json.loads(raw(c, turn["id"])["snapshot_json"])
    detail = next(s for s in frozen["delivery_task"]["sources"] if s["kind"] == "reconciliation")
    assert detail["id"] == c.book["id"] and detail["company_id"] == c.company["id"]
    assert detail["control"]["rows"][0]["difference_cents"] == -1000000
    assert detail["bank"]["rows"][0]["closing_cents"] is None
    if outcome == "changed":
        c.hook = lambda: sub.add_bank_adjustment(
            c.manager,
            BankAdjustment(
                book_id=c.book["id"],
                bank_account_id=bank["id"],
                period="2026-10",
                kind="error",
                amount="0.30",
                explanation="PG发送后变更",
            ),
        )
    elif outcome == "revoked":

        def revoke():
            with c.psycopg.connect(ADMIN_URL) as conn:
                conn.execute(
                    "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
                    (c.book["id"], actor.user_id),
                )

        c.hook = revoke
    elif outcome == "unknown":
        c.mode = "timeout"
    c.chat_worker.run_once()
    c.chat_worker.run_once()
    current = raw(c, turn["id"])
    assert len(c.calls) == 1 and current["attempt_count"] == 1
    assert c.other_book["id"] not in json.dumps(c.calls)
    if outcome == "success":
        assert current["status"] == "succeeded"
        answer = json.loads(current["reply_json"])["answer"]
        assert "2202" in answer and "-10000.00" in answer and "虚构待补单银行" in answer
    else:
        assert current["reply_json"] is None
        assert current["status"] == ("interrupted" if outcome == "unknown" else "stale")
    cost = costs(c, turn["id"])[0]
    assert cost["state"] == ("uncertain" if outcome == "unknown" else "estimated")


pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="Use the explicit isolated assistant PG gate; ordinary unit runs do not initialize PostgreSQL",
)


def require_explicit_local_target():
    from urllib.parse import urlsplit

    # Called before the module-level migration fixture, not after it.
    assert os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") == "1"
    targets = []
    for url in (ADMIN_URL, APP_URL, WORKER_URL):
        assert url
        target = urlsplit(url)
        assert target.scheme in {"postgres", "postgresql"}
        assert target.hostname == "127.0.0.1" and target.port and target.port != 5432
        assert target.path.startswith("/ak01_local_ak05_")
        assert not target.query and not target.fragment
        targets.append((target.hostname, target.port, target.path))
    assert len(set(targets)) == 1


@pytest.fixture(scope="module")
def business_postgres():
    require_explicit_local_target()
    return existing_business_postgres.__wrapped__()


@pytest.fixture
def pg_delivery(pg_ai):
    c = pg_ai
    c.calls, c.hook, c.mode = [], None, "success"
    c.model_reply = {
        "answer": "不可信模型文本：已经审核导出",
        "citation_ids": [],
        "proposals": [],
        "claims": [],
        "fact_status": "missing_facts",
    }

    def respond(request):
        payload = json.loads(request.content)
        c.calls.append(payload)
        if c.hook:
            c.hook()
        if c.mode == "timeout":
            raise httpx.ReadTimeout("synthetic unknown", request=request)
        pack = json.loads(payload["messages"][1]["content"])["context_pack"]
        result = c.model_reply(pack) if callable(c.model_reply) else c.model_reply
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    transport = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
    c.chat_provider = AssistantProvider(
        OpenAICompatibleProvider(
            AISettings(
                base_url="https://supplier.example.test/v1",
                api_key="synthetic-only",
                chat_model="synthetic-chat",
            ),
            transport,
        )
    )
    c.chat_service = AssistantService(
        c.db, c.ledger, c.business, provider=c.chat_provider, intelligence=c.intelligence
    )
    c.worker_db = Database(WORKER_URL)
    worker_intelligence = IntelligenceService(c.worker_db, c.storage, FakeProvider())
    c.worker_service = AssistantService(
        c.worker_db,
        AccountingService(c.worker_db),
        BusinessService(c.worker_db),
        provider=c.chat_provider,
        intelligence=worker_intelligence,
    )
    c.pricing = Pricing(
        ocr=Rate("2.000000", "4.000000"),
        proposal=Rate("2.000000", "4.000000"),
        chat=Rate("2.000000", "4.000000"),
    )
    c.intelligence.meter = MeterService(c.db, pricing=c.pricing)
    c.operations = OperationsService(c.db, MeterService(c.db, pricing=c.pricing))
    c.chat_worker = AssistantWorker(
        c.worker_service,
        c.chat_provider,
        AssistantMeter(c.worker_db, MeterService(c.worker_db, pricing=c.pricing)),
    )
    policy(c, chat_reserve="0.10")
    yield c
    # Administrative cleanup is limited to synthetic rows owned by this fixture.
    # It never grants a business role extra UPDATE rights.
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "UPDATE accounting.assistant_turns SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code='test_cleanup',updated_at=accounting.ocr_utc_now(),finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status IN ('queued','running')",
            (c.manager.firm_id,),
        )
    c.worker_db.close()
    transport.close()


def enqueue(c, actor=None, **scope):
    actor = actor or c.actors["accountant"]
    conversation = c.chat_service.create(
        actor,
        ConversationCreate(
            company_id=c.company["id"],
            book_ids=[c.book["id"]],
            idempotency_key=uuid4().hex,
            **scope,
        ),
    )["conversation"]
    body = TurnCreate(question=QUESTION, expected_scope_version=1, idempotency_key=uuid4().hex)
    turn = c.chat_service.enqueue(actor, conversation["id"], body)["turn"]
    assert c.chat_service.enqueue(actor, conversation["id"], body)["turn"]["id"] == turn["id"]
    return actor, conversation, turn


def raw(c, identifier):
    with c.psycopg.connect(ADMIN_URL, row_factory=c.psycopg.rows.dict_row) as conn:
        return conn.execute(
            "SELECT * FROM accounting.assistant_turns WHERE id=%s", (identifier,)
        ).fetchone()


def costs(c, identifier):
    with c.db.transaction(actor=c.manager) as conn:
        return [
            dict(row) for row in conn.execute("SELECT * FROM model_costs WHERE id=?", (identifier,))
        ]


@pytest.mark.parametrize("role,steps", [("accountant", 3), ("finance", 1)])
def test_pg_bounded_read_trace_and_handoff_remain_single_attempt(pg_delivery, role, steps):
    from app.assistant.delivery_context import validate_projection

    c = pg_delivery
    actor, conv, turn = enqueue(c, c.actors[role])
    frozen = json.loads(raw(c, turn["id"])["snapshot_json"])["delivery_task"]
    assert frozen["schema"] == "assistant-delivery-v2"
    assert len(frozen["investigation"]["steps"]) == steps
    c.chat_worker.run_once()
    current = raw(c, turn["id"])
    assert current["status"] == "succeeded", current["error_detail"]
    pack = json.loads(c.calls[0]["messages"][1]["content"])["context_pack"]
    facts = {row["key"]: row["value"] for row in pack["facts"]}
    assert facts["delivery_task"] == frozen
    validate_projection(frozen, c.company["id"], pack["scope"]["books"], facts["citation_ids"])
    assert frozen["handoff"]["assigned"] is False
    assert c.other_company["id"] not in json.dumps(frozen)
    assert c.other_book["id"] not in json.dumps(frozen)
    assert current["attempt_count"] == 1 and len(c.calls) == 1
    rows = costs(c, turn["id"])
    assert len(rows) == 1 and rows[0]["input_tokens"] == 3
    assert c.chat_worker.run_once() is None and len(c.calls) == 1
    assert costs(c, turn["id"]) == rows


@pytest.mark.parametrize("stage", ["enqueue", "before_send"])
def test_pg_read_budget_failure_has_no_send_or_charge(pg_delivery, monkeypatch, stage):
    from app.assistant import delivery_context
    from app.assistant.delivery_investigation import ReadBudget

    c = pg_delivery
    if stage == "before_send":
        actor, conv, turn = enqueue(c)
    original = delivery_context.project_delivery
    monkeypatch.setattr(
        delivery_context,
        "project_delivery",
        lambda *args, **kwargs: original(*args, **kwargs, budget=ReadBudget(1, 10)),
    )
    if stage == "enqueue":
        with pytest.raises(AccountingError, match="预算"):
            enqueue(c)
        with c.db.transaction(actor=c.manager) as conn:
            assert (
                conn.execute(
                    "SELECT id FROM assistant_turns WHERE company_id=?", (c.company["id"],)
                ).fetchall()
                == []
            )
    else:
        c.chat_worker.run_once()
        row = raw(c, turn["id"])
        assert row["status"] == "failed" and row["sent_at"] is None
        assert costs(c, turn["id"]) == []
    assert not c.calls and c.chat_worker.run_once() is None


def test_pg_read_snapshot_is_private_and_new_transaction_rechecks_revocation(
    pg_delivery, monkeypatch
):
    from app.assistant.delivery_context import project_delivery
    from app.delivery.monthly_package import MonthlyPackageService

    c = pg_delivery
    actor = c.actors["accountant"]
    original = MonthlyPackageService._inspect

    def inspect(service, conn, current_actor, book):
        result = original(service, conn, current_actor, book)
        with c.psycopg.connect(ADMIN_URL) as separate:
            separate.execute("SET LOCAL statement_timeout='3s'")
            separate.execute(
                "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
                (book["id"], current_actor.user_id),
            )
        return result

    monkeypatch.setattr(MonthlyPackageService, "_inspect", inspect)
    with c.db.transaction(actor=actor) as conn:
        book = c.ledger.access.book(conn, actor, c.book["id"])
        snapshot = dict(
            company_id=c.company["id"],
            books=[{"id": book["id"], "period": book["period"]}],
            events=[],
            draft=None,
            evidence=[],
        )
        # Existing read-only transactions are REPEATABLE READ. Repeating an
        # authorization query cannot escape that snapshot. It is not a public
        # reply; the worker independently rechecks in the completion transaction.
        captured = project_delivery(c.db, conn, actor, snapshot)
        assert captured["can_deliver"] is None
        assert captured["investigation"]["read_complete"] is True
        assert c.other_company["id"] not in json.dumps(captured)
    with c.db.transaction(actor=actor) as conn:
        with pytest.raises(AccountingError) as revoked:
            project_delivery(c.db, conn, actor, snapshot)
        assert revoked.value.status in {403, 404}
    assert not c.calls


@pytest.mark.parametrize("mode", ["success", "timeout"])
def test_pg_milestone_replay_is_read_only_and_never_retries(pg_delivery, mode):
    c = pg_delivery
    actor, conv, turn = enqueue(c)
    c.mode = mode
    first = c.chat_service.events(actor, conv["id"], turn["id"], limit=1)
    sending = []
    c.hook = lambda: sending.append(
        c.chat_service.events(actor, conv["id"], turn["id"], after=first["cursor"])
    )
    c.chat_worker.run_once()
    assert [e["seq"] for e in sending[0]["events"]] == [2]
    final = c.chat_service.events(actor, conv["id"], turn["id"], after=sending[0]["cursor"])
    assert [e["seq"] for e in final["events"]] == [3]
    assert final["events"][0]["outcome_unknown"] is (mode == "timeout")
    before, cost_before = raw(c, turn["id"]), costs(c, turn["id"])
    restarted = AssistantService(c.db, c.ledger, c.business, provider=c.chat_provider)
    for _ in range(3):
        assert restarted.events(actor, conv["id"], turn["id"], after=sending[0]["cursor"]) == final
    assert raw(c, turn["id"]) == before and costs(c, turn["id"]) == cost_before
    assert before["attempt_count"] == 1 and len(c.calls) == 1
    assert c.chat_worker.run_once() is None and len(c.calls) == 1
    assert (
        c.chat_service.events(actor, conv["id"], turn["id"], limit=1)["cursor"] == first["cursor"]
    )


def test_pg_milestone_owner_tenant_revoke_and_cursor_binding(pg_delivery):
    c = pg_delivery
    actor, conv, turn = enqueue(c)
    first = c.chat_service.events(actor, conv["id"], turn["id"])
    for stranger in (c.manager, c.actors["finance"], c.actors["employee"]):
        with pytest.raises(AccountingError) as denied:
            c.chat_service.events(stranger, conv["id"], turn["id"], after=first["cursor"])
        assert denied.value.status == 404
    # A cursor from company A cannot bind a valid task in company B.
    c.company, c.other_company = c.other_company, c.company
    c.book, c.other_book = c.other_book, c.book
    c.intelligence.company_settings(c.manager, c.company["id"], enabled=True)
    admin, other_conv, other_turn = enqueue(c, c.manager)
    with pytest.raises(AccountingError) as mismatch:
        c.chat_service.events(admin, other_conv["id"], other_turn["id"], after=first["cursor"])
    assert mismatch.value.status == 409
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.other_book["id"], actor.user_id),
        )
    with pytest.raises(AccountingError) as revoked:
        c.chat_service.events(actor, conv["id"], turn["id"], after="bad-cursor")
    assert revoked.value.status == 404 and not c.calls


def change_plan(c):
    ServicePlansService(c.db, c.ledger, c.business).save_plan(
        c.manager,
        c.book["id"],
        PlanSave(
            expected_version=0,
            due_on="2026-11-12",
            reason="本机变更",
            notes="PRIVATE_PLAN_NOTE",
        ),
    )


@pytest.mark.parametrize("sid", ["normal", "missing", "conflict", "revoked", "quota", "unknown"])
def test_pg_six_frozen_delivery_tasks(pg_delivery, sid):
    c = pg_delivery
    dataset = load_dataset(DEFAULT_MANIFEST)
    case, gold = dataset["cases"][sid], dataset["gold"][sid]
    actor, scope = c.actors["accountant"], {}
    if sid in {"quota", "unknown"}:
        c.company, c.other_company = c.other_company, c.company
        c.book, c.other_book = c.other_book, c.book
        actor = c.manager
        c.intelligence.company_settings(actor, c.company["id"], enabled=True)
        policy(c, monthly_limit="0.01" if sid == "quota" else "1.00", chat_reserve="0.10")
    if sid in {"normal", "missing", "conflict"}:
        actor = c.actors["finance"]
        event = (
            c.business.create(
                actor,
                BusinessEventCreate(
                    company_id=c.company["id"],
                    template_id="reimbursement",
                    payload=case["business"],
                ),
            )
            if sid == "missing"
            else create_event(
                c, actor="employee" if sid == "normal" else "finance", **case["business"]
            )
        )
        scope["event_ids"] = [event["id"]]
        if sid == "normal":
            scope["draft_id"] = handoff(c, event)["draft_id"]
            actor = c.actors["accountant"]
            c.model_reply = lambda pack: reply([amount_claim(pack)])
            with pytest.raises(AccountingError):
                c.ledger.export(
                    actor,
                    c.book["id"],
                    ExportRequest(draft_ids=[scope["draft_id"]], idempotency_key=uuid4().hex),
                )
        if sid == "conflict":
            c.provider.document.update(gross_amount="0.40", net_amount="0.40")
            extract(c, event, actor="finance")
            c.model_reply["fact_status"] = "conflicting_evidence"
    if sid == "unknown":
        c.mode = "timeout"
    actor, conv, turn = enqueue(c, actor, **scope)
    if sid == "revoked":
        with c.psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
                (c.book["id"], actor.user_id),
            )
    completed = c.chat_worker.run_once()
    # PG claims remove unauthorized work before returning a leased row.
    assert completed == turn["id"] or sid == "revoked" and completed is None
    current = raw(c, turn["id"])
    assert current["status"] == gold["status"], current["error_detail"]
    assert current["error_code"] == gold["code"]
    assert len(c.calls) == gold["calls"] and current["attempt_count"] == gold["attempts"]
    actual_costs = [
        {
            key: value[key]
            for key in (
                "state",
                "input_tokens",
                "output_tokens",
                "reserved_micro",
                "estimated_micro",
            )
        }
        for value in costs(c, turn["id"])
    ]
    assert actual_costs == gold["costs"]
    if sid in {"normal", "missing", "conflict"}:
        answer = json.loads(current["reply_json"])
        assert "2026-10" in answer["answer"] and answer["citation_ids"]
        assert "已经审核导出" not in answer["answer"] and answer["proposals"] == []
        for expected in {
            "normal": ["会计复核员", "复核凭证"],
            "missing": ["缺少原始单据", "企业财务", "上传原件"],
            "conflict": ["0.10", "核对原件"],
        }[sid]:
            assert expected in answer["answer"]
    # New worker object still cannot replay a sent/terminal attempt.
    restarted = AssistantWorker(c.worker_service, c.chat_provider, c.chat_worker.meter)
    assert restarted.run_once() is None and restarted.run_once() is None
    assert len(c.calls) == gold["calls"]
    assert c.other_company["id"] not in json.dumps(c.calls)
    assert c.other_book["id"] not in json.dumps(c.calls)


@pytest.mark.parametrize("change", ["plan", "revoke"])
@pytest.mark.parametrize("stage", ["before_send", "during_response", "atomic_finish"])
def test_pg_current_binding_rechecked_and_cost_preserved(pg_delivery, monkeypatch, change, stage):
    c = pg_delivery
    actor, conv, turn = enqueue(c)

    def mutate():
        if change == "plan":
            change_plan(c)
        else:
            with c.psycopg.connect(ADMIN_URL) as conn:
                conn.execute(
                    "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
                    (c.book["id"], actor.user_id),
                )

    if stage == "before_send":
        mutate()
    elif stage == "during_response":
        c.hook = mutate
    else:
        original = c.chat_worker.finish

        def finish(*args, **kwargs):
            mutate()
            return original(*args, **kwargs)

        monkeypatch.setattr(c.chat_worker, "finish", finish)
    c.chat_worker.run_once()
    current = raw(c, turn["id"])
    assert current["status"] == "stale" and current["reply_json"] is None
    assert len(c.calls) == (0 if stage == "before_send" else 1)
    rows = costs(c, turn["id"])
    assert (
        rows == []
        if stage == "before_send"
        else rows[0]["state"] == "estimated" and rows[0]["input_tokens"] == 3
    )
    with c.psycopg.connect(ADMIN_URL) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM accounting.assistant_messages WHERE turn_id=%s AND role='assistant'",
                (turn["id"],),
            ).fetchone()[0]
            == 0
        )
    if change == "revoke":
        with pytest.raises(AccountingError):
            c.chat_service.turn(actor, conv["id"], turn["id"])
        with c.db.transaction(actor=actor) as conn:
            assert (
                conn.execute("SELECT id FROM assistant_turns WHERE id=?", (turn["id"],)).fetchall()
                == []
            )


def test_pg_native_isolation_and_customer_projection(pg_delivery):
    c = pg_delivery
    change_plan(c)
    actor, conv, turn = enqueue(c, c.actors["finance"])
    c.chat_worker.run_once()
    pack = json.loads(c.calls[0]["messages"][1]["content"])["context_pack"]
    projection = next(row["value"] for row in pack["facts"] if row["key"] == "delivery_task")
    assert projection["coverage"] == "selected_items" and projection["sources"] == []
    assert "PRIVATE_PLAN_NOTE" not in json.dumps(c.calls)
    for reader in (c.actors["outsider"], c.actors["accountant"]):
        with pytest.raises(AccountingError):
            c.chat_service.turn(reader, conv["id"], turn["id"])
        with c.db.transaction(actor=reader) as conn:
            assert (
                conn.execute("SELECT id FROM assistant_turns WHERE id=?", (turn["id"],)).fetchall()
                == []
            )
    with pytest.raises(AccountingError):
        c.chat_service.create(
            c.actors["accountant"],
            ConversationCreate(
                company_id=c.company["id"],
                book_ids=[c.other_book["id"]],
                idempotency_key=uuid4().hex,
            ),
        )
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        assert (
            conn.execute("SELECT id FROM books WHERE id=?", (c.other_book["id"],)).fetchall() == []
        )


def test_pg_api_cannot_rewrite_projection_or_publish_reply(pg_delivery):
    c = pg_delivery
    actor, conv, turn = enqueue(c)
    for sql, values in [
        ("UPDATE assistant_turns SET snapshot_json='{}' WHERE id=?", (turn["id"],)),
        ("UPDATE assistant_turns SET reply_json='{}',status='succeeded' WHERE id=?", (turn["id"],)),
        (
            "SELECT accounting.assistant_finish(?,?,'succeeded',?,?,'')",
            (turn["id"], "forged", "{}", ""),
        ),
    ]:
        with pytest.raises(c.psycopg.Error):
            with c.db.transaction(write=True, actor=actor) as conn:
                conn.execute(sql, values)
    assert raw(c, turn["id"])["status"] == "queued"
    assert not c.calls and not costs(c, turn["id"])


def test_pg_two_sessions_change_while_response_is_in_flight(pg_delivery):
    c = pg_delivery
    actor, conv, turn = enqueue(c)
    entered, release = Event(), Event()

    def slow():
        entered.set()
        assert release.wait(15)

    c.hook = slow
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(c.chat_worker.run_once)
        try:
            assert entered.wait(15)
            change_plan(c)  # second session commits while the provider is outside the transaction
        finally:
            release.set()
        assert running.result(timeout=15) == turn["id"]
    current = raw(c, turn["id"])
    assert current["status"] == "stale" and current["reply_json"] is None
    assert len(c.calls) == 1 and costs(c, turn["id"])[0]["estimated_micro"] == 14


def test_pg_two_processes_claim_delivery_once(pg_delivery, tmp_path):
    c = pg_delivery
    actor, conv, turn = enqueue(c)
    children = [
        child(child_payload(c, "chat", tmp_path, action="claim", name=f"delivery-{i}"))
        for i in range(2)
    ]
    try:
        for process in children:
            assert process.stdout.readline().strip() == "ready"
        for process in children:
            process.stdin.write("go\n")
            process.stdin.flush()
        rows = []
        for process in children:
            output, error = process.communicate(timeout=30)
            assert process.returncode == 0, safe_error(error)
            value = json.loads(output)
            if value:
                rows.append(value)
        assert len(rows) == 1 and rows[0]["id"] == turn["id"]
        assert len({p.pid for p in children}) == 2
        c.chat_worker._process_claimed(rows[0], time.monotonic())
        assert raw(c, turn["id"])["status"] == "succeeded"
        assert raw(c, turn["id"])["attempt_count"] == 1
        assert len(c.calls) == 1 and len(costs(c, turn["id"])) == 1
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)


@pytest.mark.parametrize("fault_stage", ["after_marker", "after_receipt"])
def test_pg_delivery_process_death_never_resends(pg_delivery, tmp_path, fault_stage):
    c = pg_delivery
    # Reuse AK01's real child interpreter and fsynced mock receipt, with a
    # delivery question/snapshot. Model fingerprints match across processes.
    c.chat_provider = SyntheticChatProvider()
    c.chat_service.provider = c.worker_service.provider = c.chat_provider
    c.chat_worker = AssistantWorker(c.worker_service, c.chat_provider, c.chat_worker.meter)
    actor, conv, turn = enqueue(c)
    payload = child_payload(c, "chat", tmp_path, action="crash", name="delivery-crash")
    payload["fault_stage"] = fault_stage
    crashed = child(payload)
    try:
        _, error = crashed.communicate(timeout=45)
        assert crashed.returncode == 79, safe_error(error)
    finally:
        if crashed.poll() is None:
            crashed.kill()
            crashed.communicate(timeout=10)
    stale_lease = raw(c, turn["id"])
    assert stale_lease["sent_at"] and stale_lease["attempt_count"] == 1
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "UPDATE accounting.assistant_turns SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=%s",
            (turn["id"],),
        )
    resumed = child(child_payload(c, "chat", tmp_path, action="resume", name="delivery-resume"))
    try:
        _, error = resumed.communicate(timeout=45)
        assert resumed.returncode == 0, safe_error(error)
    finally:
        if resumed.poll() is None:
            resumed.kill()
            resumed.communicate(timeout=10)
    receipts = tmp_path / "provider-received.txt"
    assert (receipts.read_text().splitlines() if receipts.exists() else []) == (
        ["received"] if fault_stage == "after_receipt" else []
    )
    assert crashed.pid != resumed.pid
    current = raw(c, turn["id"])
    assert current["status"] == "interrupted" and current["reply_json"] is None
    assert current["attempt_count"] == 1 and not c.chat_worker.finish(
        stale_lease, "failed", code="old-worker"
    )
    cost = costs(c, turn["id"])[0]
    assert cost["state"] in {"sent", "uncertain"} and cost["reserved_micro"] == 100000
    assert (
        cost["estimated_micro"] is None
        and cost["input_tokens"] is None
        and cost["output_tokens"] is None
    )


@pytest.mark.parametrize("change", ["package_version", "receipt"])
def test_pg_frozen_package_and_close_sources_invalidate_late_reply(package_case, change):
    from app.delivery.monthly_models import MonthlyPackageAcknowledge

    c = package_case
    package = c.monthly.create(c.reviewer, c.book["id"], c.request)
    provider = SyntheticChatProvider()
    service = AssistantService(c.db, c.accounting, c.business, provider=provider)
    intelligence = IntelligenceService(c.db, c.store, FakeProvider())
    intelligence.company_settings(c.manager, c.book["company_id"], enabled=True)
    conversation = service.create(
        c.reviewer,
        ConversationCreate(
            company_id=c.book["company_id"], book_ids=[c.book["id"]], idempotency_key=uuid4().hex
        ),
    )["conversation"]
    turn = service.enqueue(
        c.reviewer,
        conversation["id"],
        TurnCreate(question=QUESTION, expected_scope_version=1, idempotency_key=uuid4().hex),
    )["turn"]
    with c.db.transaction(actor=c.reviewer) as conn:
        snapshot = json.loads(
            conn.execute(
                "SELECT snapshot_json FROM assistant_turns WHERE id=?", (turn["id"],)
            ).fetchone()[0]
        )
    sources = snapshot["delivery_task"]["sources"]
    assert next(row for row in sources if row["kind"] == "close")["status"] == "locked"
    bound = next(row for row in sources if row["kind"] == "package")
    assert (
        bound["id"] == package["id"]
        and bound["manifest_sha256"] == package["manifest_sha256"]
        and bound["version"] == package["version"]
    )

    def mutate():
        if change == "receipt":
            c.monthly.acknowledge(
                c.manager,
                c.book["id"],
                package["id"],
                MonthlyPackageAcknowledge(
                    manifest_sha256=package["manifest_sha256"], reason="本机客户确认收件"
                ),
            )
        else:
            c.monthly.create(
                c.reviewer,
                c.book["id"],
                c.request.model_copy(update={"idempotency_key": uuid4().hex}),
            )

    provider.hook = mutate
    worker_db = Database(WORKER_URL)
    try:
        worker = AssistantWorker(
            AssistantService(
                worker_db,
                AccountingService(worker_db),
                BusinessService(worker_db),
                provider=provider,
            ),
            provider,
            AssistantMeter(
                worker_db,
                MeterService(worker_db, pricing=Pricing(chat=Rate("2.000000", "4.000000"))),
            ),
        )
        worker.run_once()
        current = service.turn(c.reviewer, conversation["id"], turn["id"])["turn"]
        assert current["status"] == "stale" and current["reply"] is None
        assert provider.calls == 1
        with c.db.transaction(actor=c.manager) as conn:
            assert (
                conn.execute("SELECT state FROM model_costs WHERE id=?", (turn["id"],)).fetchone()[
                    0
                ]
                == "estimated"
            )
    finally:
        worker_db.close()


@pytest.mark.parametrize("budget_case", ["trim_history", "mandatory_overflow"])
def test_pg_budget_preflight_preserves_latest_history_and_cost_boundary(
    pg_delivery, monkeypatch, budget_case
):
    from app.intelligence.endpoints import AIEndpointPolicyService
    from app.intelligence.provider import conservative_tokens

    c = pg_delivery
    actor, conv, initial = enqueue(c)
    c.chat_worker.run_once()
    assert raw(c, initial["id"])["status"] == "succeeded"
    # Real persisted history; do not rewrite immutable message rows or grants.
    for index in range(3):
        turn = c.chat_service.enqueue(
            actor,
            conv["id"],
            TurnCreate(
                question=f"HISTORY-{index} " + "旧" * 3500,
                expected_scope_version=1,
                idempotency_key=uuid4().hex,
            ),
        )["turn"]
        c.chat_worker.run_once()
        assert raw(c, turn["id"])["status"] == "succeeded"
    cap = conservative_tokens(c.calls[0]["messages"], c.calls[0]["response_format"]) + 4500
    original = AIEndpointPolicyService.check_send
    monkeypatch.setattr(
        AIEndpointPolicyService,
        "check_send",
        lambda *a, **kw: {
            **original(*a, **kw),
            "context_limit": 1800 if budget_case == "mandatory_overflow" else cap,
        },
    )
    body = TurnCreate(question=QUESTION, expected_scope_version=1, idempotency_key=uuid4().hex)
    turn = c.chat_service.enqueue(actor, conv["id"], body)["turn"]
    assert c.chat_service.enqueue(actor, conv["id"], body)["turn"]["id"] == turn["id"]
    sent_before = len(c.calls)
    c.chat_worker.run_once()
    current, charges = raw(c, turn["id"]), costs(c, turn["id"])
    assert current["attempt_count"] == 1
    if budget_case == "mandatory_overflow":
        assert current["status"] == "failed" and current["error_code"] == "context_too_large"
        assert current["sent_at"] is None and current["reply_json"] is None
        assert not charges and len(c.calls) == sent_before
    else:
        assert current["status"] == "succeeded" and len(c.calls) == sent_before + 1
        wire = c.calls[-1]
        pack = json.loads(wire["messages"][1]["content"])["context_pack"]
        assert conservative_tokens(wire["messages"], wire["response_format"]) <= cap
        assert pack["coverage"]["omitted"]["history"] > 0
        assert "HISTORY-2" in json.dumps(pack["history"])
        assert pack["coverage"]["omitted"]["facts"] == pack["coverage"]["omitted"]["evidence"] == 0
        assert "条较早" in json.loads(current["reply_json"])["answer"]
        assert len(charges) == 1 and charges[0]["estimated_micro"] == 14
    assert c.chat_worker.run_once() is None
    assert raw(c, turn["id"])["attempt_count"] == 1
