"""PLT05-C：返回文件差异进入统一待办（实时、投影、权限收口）。"""

from app.accounting.review import ReviewService
from app.accounting.seed import LOCAL_ACTOR, USER_NAME
from app.business.service import BusinessService
from app.cases.service import CaseService
from app.integrations.models import BridgeReturnImport, BridgeReturnRowHandle
from app.integrations.service import FinancialBridgeService
from app.workspace.projection import projection_clean, refresh_company
from app.workspace.service import WorkspaceService
from test_accounting import add_actor, book, service  # noqa: F401
from test_plt05_return_import import _artifact, _csv


def _workspace(service):
    business = BusinessService(service.db)
    return WorkspaceService(
        service.db,
        service,
        business,
        ReviewService(service.db, service),
        CaseService(service.db, business),
    )


def _company(service, book_id):
    with service.db.transaction() as conn:
        row = conn.execute(
            "SELECT firm_id,company_id FROM books WHERE id=?", (book_id,)
        ).fetchone()
    return row["firm_id"], row["company_id"]


def _mismatch(bridge, service, book_id):
    intent, artifact = _artifact(bridge, service, book_id)
    voucher = intent["package"]["vouchers"][0]
    payload = BridgeReturnImport(
        batch_code=intent["external_code"],
        content=_csv(
            intent["external_code"],
            f"T54-{voucher['external_code']}",
            0.01,
        ),
    )
    result = bridge.record_return(LOCAL_ACTOR, book_id, artifact["id"], payload)
    assert result["rows"][0]["status"] == "amount_mismatch"
    return intent, artifact, result


def _handle(bridge, book_id, artifact_id, result):
    row = result["rows"][0]
    return bridge.handle_return_row(
        LOCAL_ACTOR,
        book_id,
        artifact_id,
        result["id"],
        row["voucher_code"],
        BridgeReturnRowHandle(
            action="resolved",
            reason_code="amount_mismatch",
            reason="已与客户确认差额并留痕",
            evidence_ref="synthetic://return/workspace-handle-1",
        ),
    )


def test_return_discrepancy_is_workspace_action_and_disappears(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    _intent, artifact, result = _mismatch(bridge, service, book)
    row = result["rows"][0]
    object_id = f"{result['id']}:{row['voucher_code']}"
    expected_amount = f"{abs(row['actual_cents'] - row['expected_cents']) / 100:.2f}"

    workspace = _workspace(service)
    tasks = workspace.list(LOCAL_ACTOR, bucket="all", kind="bridge_return")["tasks"]
    assert [task["ref"] for task in tasks] == [object_id]
    task = tasks[0]
    assert task["object_id"] == object_id
    assert task["book_id"] == book
    assert task["state"] == "amount_mismatch"
    assert task["title"].startswith("处理返回差异 · ")
    assert "金额不一致" in task["title"]
    assert task["action_label"] == "处置返回差异"
    assert task["bucket"] == "action"
    assert task["available_action"] == "can_act"
    assert task["business_action"] == "处置返回差异"
    assert task["amount"] == expected_amount
    assert task["period"] == "2026-10"
    assert task["responsible"] == USER_NAME
    assert task["key"] == f"bridge_return:{object_id}"

    single = workspace.get(LOCAL_ACTOR, "bridge_return", object_id)
    assert single["object_id"] == object_id
    assert single["available_action"] == "can_act"

    handled = _handle(bridge, book, artifact["id"], result)
    assert handled["rows"][0]["handled"] is True
    assert workspace.list(LOCAL_ACTOR, bucket="all", kind="bridge_return")["tasks"] == []


def test_return_discrepancy_projection_matches_live_and_refreshes(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    _intent, artifact, result = _mismatch(bridge, service, book)
    object_id = f"{result['id']}:{result['rows'][0]['voucher_code']}"
    workspace = _workspace(service)
    firm_id, company_id = _company(service, book)

    with service.db.transaction(write=True, system=True) as conn:
        refresh_company(conn, service.db, firm_id, company_id)
    with service.db.transaction() as conn:
        assert projection_clean(conn, LOCAL_ACTOR)

    live = workspace.list(
        LOCAL_ACTOR, bucket="all", kind="bridge_return", use_projection=False
    )
    projected = workspace.list(LOCAL_ACTOR, bucket="all", kind="bridge_return")
    assert projected == live
    assert [task["object_id"] for task in projected["tasks"]] == [object_id]

    _handle(bridge, book, artifact["id"], result)
    with service.db.transaction() as conn:
        assert not projection_clean(conn, LOCAL_ACTOR), "处置写入应把投影置脏"
    assert workspace.list(LOCAL_ACTOR, bucket="all", kind="bridge_return")["tasks"] == []

    with service.db.transaction(write=True, system=True) as conn:
        refresh_company(conn, service.db, firm_id, company_id)
    with service.db.transaction() as conn:
        assert projection_clean(conn, LOCAL_ACTOR)

    live_after = workspace.list(
        LOCAL_ACTOR, bucket="all", kind="bridge_return", use_projection=False
    )
    assert live_after["tasks"] == []
    assert workspace.list(LOCAL_ACTOR, bucket="all", kind="bridge_return") == live_after


def test_return_discrepancy_hidden_without_review_capability(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    _intent, _artifact_row, _result = _mismatch(bridge, service, book)
    outsider = add_actor(
        service, "return-outsider", LOCAL_ACTOR.firm_id, "viewer", book
    )
    workspace = _workspace(service)
    assert workspace.list(outsider, bucket="all", kind="bridge_return")["tasks"] == []
