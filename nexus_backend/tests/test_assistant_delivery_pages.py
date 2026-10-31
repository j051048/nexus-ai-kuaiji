"""Version-bound read pages over the real assistant and synthetic accounting facts."""

import copy
import socket

import pytest

from app.accounting.domain import AccountingError
from app.assistant.service import AssistantService
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.subledger.models import BankAccountCreate, BankAdjustment
from test_assistant_delivery_details import configured
from test_assistant_delivery_context import enqueue
from test_ak04_assistant_execution import execution_case, api
from test_intelligence_workflow import ai_case
from test_model_cost_workflow import cost_case, cost_rows
from test_business import business
from test_assistant_task_workflow import financial_state


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    # Windows asyncio uses a loopback socket pair even for in-process ASGI.
    original = socket.socket.connect

    def connect(sock, address):
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original(sock, address)
        raise AssertionError("分页离线测试禁止外部网络")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(
        socket.socket,
        "connect_ex",
        lambda *args: (_ for _ in ()).throw(AssertionError("禁止外部网络")),
    )


def bank_rows(c, count=22):
    sub, first = configured(c)
    with c.db.transaction(actor=c.manager) as conn:
        account_id = conn.execute(
            "SELECT ledger_account_id FROM bank_accounts WHERE id=? AND book_id=?",
            (first["id"], c.book["id"]),
        ).fetchone()[0]
    for i in range(count - 1):
        entity = LedgersService(c.db).create_entity(
            c.manager,
            c.book["id"],
            EntityCreate(kind="project", code="PAGE-" + str(i), name="虚构核对项目-" + str(i)),
        )
        sub.create_bank_account(
            c.manager,
            BankAccountCreate(
                book_id=c.book["id"],
                name=f"分页银行-{i:02d}",
                ledger_account_id=account_id,
                ledger_account_version=1,
                aux={"project": entity["id"]},
                reason="独立辅助维度的合成账户",
            ),
        )
    return sub, first


def complete(c, count=2):
    sub, bank = bank_rows(c, count)
    actor, conv, turn = enqueue(c, c.actors["accountant"], book_ids=[c.book["id"]])
    c.chat_worker.run_once()
    assert c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]["status"] == "succeeded"
    return sub, bank, actor, conv, turn


def read(c, actor, conv, turn, **kw):
    return c.chat_service.delivery_details(
        actor, conv["id"], turn["id"], **{"kind": "bank", "limit": 10, **kw}
    )


def test_all_pages_replay_after_restart_without_writes_calls_or_costs(execution_case):
    c = execution_case
    _, _, actor, conv, turn = complete(c, 22)
    costs, state = copy.deepcopy(cost_rows(c)), financial_state(c)
    first = read(c, actor, conv, turn)
    assert first["total"] == 22 and first["start"] == 0 and first["end"] == 10
    assert first["has_more"] and first["accounting_approved"] is False
    restarted = AssistantService(c.db, c.ledger, c.business, provider=c.chat_provider)
    second = restarted.delivery_details(
        actor, conv["id"], turn["id"], kind="bank", limit=10, after=first["cursor"]
    )
    assert second == read(c, actor, conv, turn, after=first["cursor"])
    third = read(c, actor, conv, turn, after=second["cursor"])
    assert third["start"] == 20 and third["end"] == 22 and not third["has_more"]
    rows = first["rows"] + second["rows"] + third["rows"]
    assert len({r["key"] for r in rows}) == 22
    assert all(
        "独立核验" in r["issues"][0]["reason"] and "0.00" not in r["issues"][0]["reason"]
        for r in rows
    )
    assert len(c.calls) == 1 and costs == cost_rows(c) and state == financial_state(c)


@pytest.mark.parametrize("change", ["fact", "revoked", "owner", "kind", "size", "malformed"])
def test_stale_or_foreign_cursor_never_reuses_evidence(execution_case, change):
    c = execution_case
    sub, bank, actor, conv, turn = complete(c)
    page = read(c, actor, conv, turn, limit=1)
    kw = dict(limit=1, after=page["cursor"])
    expected = 409
    if change == "fact":
        sub.add_bank_adjustment(
            c.manager,
            BankAdjustment(
                book_id=c.book["id"],
                bank_account_id=bank["id"],
                period="2026-10",
                kind="error",
                amount="0.30",
                explanation="分页期间事实变化",
            ),
        )
    elif change == "revoked":
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "DELETE FROM book_access WHERE book_id=? AND user_id=?",
                (c.book["id"], actor.user_id),
            )
        expected = 404
    elif change == "owner":
        actor = c.manager  # Even firm owner cannot read another user's private conversation.
        expected = 404
    elif change == "kind":
        kw["kind"] = "control"
    elif change == "size":
        kw["limit"] = 2
    else:
        kw["after"] = "malformed"
    with pytest.raises(AccountingError) as error:
        read(c, actor, conv, turn, **kw)
    assert error.value.status == expected
    c.chat_worker.run_once()
    assert len(c.calls) == 1


def test_pending_or_customer_turn_cannot_read_internal_pages(execution_case):
    c = execution_case
    bank_rows(c)
    actor, conv, turn = enqueue(c, c.actors["accountant"], book_ids=[c.book["id"]])
    with pytest.raises(AccountingError) as error:
        read(c, actor, conv, turn)
    assert error.value.status == 409 and not c.calls
    c.chat_worker.run_once()
    actor, conv, turn = enqueue(c, c.actors["finance"], book_ids=[c.book["id"]])
    c.chat_worker.run_once()
    with pytest.raises(AccountingError) as error:
        read(c, actor, conv, turn)
    assert error.value.status in {403, 404}


def test_http_pages_no_store_and_parameters(execution_case):
    c = execution_case
    _, _, actor, conv, turn = complete(c)
    client = api(c)
    path = f"/api/assistant/conversations/{conv['id']}/turns/{turn['id']}/delivery-details"
    assert client.get(path).status_code == 401
    response = client.get(path + "?kind=bank&limit=1", headers={"X-User": actor.user_id})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["company_id"] == c.company["id"]
    for query in ("kind=sql", "kind=bank&limit=0", "kind=bank&limit=21"):
        assert client.get(path + "?" + query, headers={"X-User": actor.user_id}).status_code == 422
