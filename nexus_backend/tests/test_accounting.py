from period_fixtures import restore_period_state, complete_month_close
from review_policy_fixtures import independent_reviewer
import csv
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.accounting.database import Database
from app.accounting.invariants import signed_cents_to_money
from app.accounting.service import REVIEW_POLICY_VERSION
from app.accounting.models import ApprovalRequest, BookCreate, DraftUpdate, EvidenceRequest, ExportRequest, ImportRequest, SourceRow, cents_to_money, money_to_cents
from app.accounting.seed import LOCAL_ACTOR, bootstrap
from app.accounting.service import AccountingError, AccountingService, Actor
from app.main import create_app
from app.tools.accounting_tools import AccountingSuggestionTool


@pytest.fixture
def service(tmp_path):
    svc = AccountingService(Database(tmp_path / "accounting.db"))
    bootstrap(svc, seed_examples=False)
    return svc


@pytest.fixture
def book(service):
    identifier = service.create_book(LOCAL_ACTOR, BookCreate(name="测试企业", period="2026-10"))["id"]
    add_actor(service, "review-policy-peer", LOCAL_ACTOR.firm_id, "reviewer", identifier)
    return identifier


def row(**changes):
    value = {"external_id": "bank-001:0001", "date": "2026-10-01", "description": "办公用品", "counterparty": "测试供应商", "amount": "0.30", "direction": "expense", "evidence_ref": "测试发票001"}
    return SourceRow(**{**value, **changes})


def import_rows(service, book, rows=None, actor=LOCAL_ACTOR):
    return service.import_sources(actor, book, ImportRequest(source_name="测试文件", rows=rows or [row()]))


def make_draft(service, book, **changes):
    import_rows(service, book, [row(**changes)])
    service.suggestions(LOCAL_ACTOR, book)
    return service.get_book(LOCAL_ACTOR, book)["drafts"][0]


def approve(service, book, draft):
    actor = independent_reviewer(service.db, LOCAL_ACTOR, book)
    return service.approve(actor, book, draft["id"], ApprovalRequest(expected_version=draft["version"]))


def add_actor(service, user_id="other", firm_id="other-firm", role="reviewer", book_id=None):
    # A book grant cannot make a client operator/viewer a formal reviewer.
    # Keep the effective-intersection fixtures valid by granting the strongest
    # non-reviewer book role when the firm membership is not a reviewer.
    book_role = "reviewer" if role in {"reviewer", "admin", "owner"} else "editor"
    with service.db.transaction(write=True) as conn:
        conn.execute("INSERT OR IGNORE INTO firms(id,name) VALUES(?,?)", (firm_id, firm_id))
        conn.execute("INSERT OR IGNORE INTO users(id,name) VALUES(?,?)", (user_id, user_id))
        conn.execute("INSERT INTO memberships VALUES(?,?,?)", (firm_id, user_id, role))
        if book_id:
            conn.execute("INSERT INTO book_access(book_id,firm_id,user_id,role) VALUES(?,?,?,?)", (book_id, firm_id, user_id, book_role))
    return Actor(user_id=user_id, firm_id=firm_id)


@pytest.mark.parametrize("bad", [0.1, 10, -1, "-1", "1e2", "1.001", ".20", "01.20", "NaN", "Infinity", "1,000", "", "9999999999999.99"])
def test_money_rejects_noncanonical_and_inexact_input(bad):
    with pytest.raises((ValueError, ValidationError)):
        row(amount=bad)


def test_precision_roundtrip_and_split_amounts(service, book):
    assert money_to_cents("0.10") + money_to_cents("0.20") == money_to_cents("0.30")
    assert cents_to_money(money_to_cents("999999999999.99")) == "999999999999.99"
    assert signed_cents_to_money(0) == "0.00"
    assert signed_cents_to_money(-1) == "-0.01"
    assert signed_cents_to_money(money_to_cents("999999999999.99")) == "999999999999.99"
    with pytest.raises(ValueError):
        signed_cents_to_money(-(10**15))
    draft = make_draft(service, book)
    updated = service.update_draft(LOCAL_ACTOR, book, draft["id"], DraftUpdate(expected_version=draft["version"], description="分摊办公费", lines=[
        {"account_code": "6602.02", "debit": "0.10", "credit": "0"},
        {"account_code": "6602.99", "debit": "0.20", "credit": "0"},
        {"account_code": "1002", "debit": "0", "credit": "0.30"},
    ]))
    assert updated["issues"] == []
    assert approve(service, book, updated)["status"] == "approved"


def test_import_dedup_normalizes_money_and_conflict_rolls_back_entire_batch(service, book):
    assert import_rows(service, book, [row(amount="1.0")]) == {"imported": 1, "duplicates": 0}
    assert import_rows(service, book, [row(amount="1.00")]) == {"imported": 0, "duplicates": 1}
    with pytest.raises(AccountingError, match="整批导入已回滚"):
        import_rows(service, book, [row(external_id="new-row"), row(amount="2.00")])
    assert len(service.get_book(LOCAL_ACTOR, book)["sources"]) == 1
    with service.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE action='source.import'").fetchone()[0] == 2


def test_concurrent_import_creates_one_source(service, book):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: import_rows(service, book), range(2)))
    assert sum(x["imported"] for x in results) == 1
    assert sum(x["duplicates"] for x in results) == 1


def test_import_date_outside_period_rolls_back(service, book):
    with pytest.raises(AccountingError, match="日期不属于"):
        import_rows(service, book, [row(), row(external_id="outside", date="2026-11-01")])
    assert service.get_book(LOCAL_ACTOR, book)["sources"] == []


def test_tenant_and_book_authorization_cover_reads_and_writes(service, book):
    stranger = add_actor(service)
    same_firm_no_access = add_actor(service, user_id="colleague", firm_id=LOCAL_ACTOR.firm_id)
    for actor in (stranger, same_firm_no_access):
        assert service.workbench(actor)["books"] == []
        for call in (
            lambda: service.get_book(actor, book),
            lambda: service.suggestions(actor, book),
            lambda: import_rows(service, book, actor=actor),
        ):
            with pytest.raises(AccountingError) as error:
                call()
            assert error.value.status == 404


def test_cross_book_source_and_account_references_are_rejected(service, book):
    draft = make_draft(service, book)
    other = service.create_book(LOCAL_ACTOR, BookCreate(name="另一账套", period="2026-10"))["id"]
    with pytest.raises(AccountingError) as error:
        service.approve(LOCAL_ACTOR, other, draft["id"], ApprovalRequest(expected_version=1))
    assert error.value.status == 404
    with service.db.transaction(write=True) as conn:
        conn.execute("INSERT INTO accounts(book_id,code,name) VALUES(?,?,?)", (other, "9999", "仅其他账套科目"))
    with pytest.raises(AccountingError, match="不属于当前账套"):
        service.update_draft(LOCAL_ACTOR, book, draft["id"], DraftUpdate(expected_version=draft["version"], description="非法科目", lines=[{"account_code": "9999", "debit": "0.30", "credit": "0.00"}]))
    with pytest.raises(sqlite3.IntegrityError):
        with service.db.transaction(write=True) as conn:
            conn.execute("INSERT INTO draft_lines VALUES(?,?,?,?,?,?)", (draft["id"], other, 3, "9999", 30, 0))


def test_optimistic_version_and_approved_freeze(service, book):
    draft = make_draft(service, book)
    update = DraftUpdate(expected_version=draft["version"], description="人工复核办公费", lines=draft["lines"])
    changed = service.update_draft(LOCAL_ACTOR, book, draft["id"], update)
    assert changed["version"] > draft["version"]
    for call in (lambda: service.update_draft(LOCAL_ACTOR, book, draft["id"], update), lambda: approve(service, book, draft)):
        with pytest.raises(AccountingError, match="版本已更新"):
            call()
    approved = approve(service, book, changed)
    assert approved["version"] == changed["version"] + 1
    with pytest.raises(AccountingError, match="已冻结"):
        service.update_draft(LOCAL_ACTOR, book, draft["id"], update.model_copy(update={"expected_version": approved["version"]}))
    with pytest.raises(AccountingError, match="已冻结"):
        service.add_evidence(LOCAL_ACTOR, book, draft["source_id"], EvidenceRequest(evidence_ref="替换证据"))


def test_failed_approval_preserves_lines_status_and_evidence(service, book):
    draft = make_draft(service, book, evidence_ref="")
    with service.db.transaction(actor=LOCAL_ACTOR) as conn:
        before = (
            conn.execute("SELECT status,version FROM drafts WHERE id=?", (draft["id"],)).fetchone(),
            conn.execute(
                "SELECT ordinal,account_code,debit_cents,credit_cents FROM draft_lines "
                "WHERE draft_id=? ORDER BY ordinal",
                (draft["id"],),
            ).fetchall(),
        )
    with pytest.raises(AccountingError, match="证据"):
        approve(service, book, draft)
    with service.db.transaction(actor=LOCAL_ACTOR) as conn:
        after = (
            conn.execute("SELECT status,version FROM drafts WHERE id=?", (draft["id"],)).fetchone(),
            conn.execute(
                "SELECT ordinal,account_code,debit_cents,credit_cents FROM draft_lines "
                "WHERE draft_id=? ORDER BY ordinal",
                (draft["id"],),
            ).fetchall(),
        )
    assert before[0]["status"] == after[0]["status"] == "draft"
    assert before[0]["version"] == after[0]["version"]
    assert [tuple(row) for row in before[1]] == [tuple(row) for row in after[1]]


def test_revoked_book_access_cannot_approve(service, book):
    draft = make_draft(service, book)
    reviewer = add_actor(
        service,
        user_id="revoked-reviewer",
        firm_id=LOCAL_ACTOR.firm_id,
        role="reviewer",
        book_id=book,
    )
    with service.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE book_id=? AND user_id=?",
            (book, reviewer.user_id),
        )
    with pytest.raises(AccountingError) as denied:
        service.approve(
            reviewer, book, draft["id"], ApprovalRequest(expected_version=draft["version"])
        )
    assert denied.value.status == 404
    with service.db.transaction(actor=LOCAL_ACTOR) as conn:
        assert (
            conn.execute("SELECT status FROM drafts WHERE id=?", (draft["id"],)).fetchone()[0]
            == "draft"
        )


def test_approval_audit_records_review_policy_version(service, book):
    draft = make_draft(service, book)
    approve(service, book, draft)
    with service.db.transaction(actor=LOCAL_ACTOR) as conn:
        row = conn.execute(
            "SELECT details FROM audit_events WHERE action='draft.approve' AND object_id=?",
            (draft["id"],),
        ).fetchone()
    assert row is not None
    assert json.loads(row["details"])["review_policy_version"] == REVIEW_POLICY_VERSION


def test_concurrent_approval_one_success(service, book):
    draft = make_draft(service, book)
    def attempt(_):
        try:
            return approve(service, book, draft)["status"]
        except AccountingError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(map(str, pool.map(attempt, range(2)))) == ["409", "approved"]
    with service.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE action='draft.approve'").fetchone()[0] == 1


@pytest.mark.parametrize("lines,issue", [
    ([{"account_code": "6602.02", "debit": "0.20", "credit": "0"}, {"account_code": "1002", "debit": "0", "credit": "0.30"}], "借贷不平衡"),
    ([{"account_code": "6602.02", "debit": "0.20", "credit": "0"}, {"account_code": "1002", "debit": "0", "credit": "0.20"}], "原始流水金额"),
    ([{"account_code": "6602.02", "debit": "0", "credit": "0.30"}, {"account_code": "1002", "debit": "0.30", "credit": "0"}], "方向"),
    ([{"account_code": "1002", "debit": "0.30", "credit": "0.30"}], "只能有一侧"),
])
def test_approval_validates_balance_amount_and_bank_direction(service, book, lines, issue):
    draft = make_draft(service, book)
    changed = service.update_draft(LOCAL_ACTOR, book, draft["id"], DraftUpdate(expected_version=draft["version"], description="异常分录", lines=lines))
    with pytest.raises(AccountingError, match=issue):
        approve(service, book, changed)
    assert service.get_book(LOCAL_ACTOR, book)["drafts"][0]["status"] == "draft"


def test_approval_rechecks_account_active_state(service, book):
    draft = make_draft(service, book)
    with service.db.transaction(write=True) as conn:
        conn.execute("UPDATE accounts SET active=0 WHERE book_id=? AND code='6602.02'", (book,))
    with pytest.raises(AccountingError, match="已停用"):
        approve(service, book, draft)


def test_evidence_add_is_append_only_and_invalidates_review_version(service, book):
    draft = make_draft(service, book, evidence_ref="")
    with pytest.raises(AccountingError, match="证据"):
        approve(service, book, draft)
    with service.db.transaction() as conn:
        original = dict(conn.execute("SELECT * FROM sources WHERE id=?", (draft["source_id"],)).fetchone())
    source = service.add_evidence(LOCAL_ACTOR, book, draft["source_id"], EvidenceRequest(evidence_ref="人工补充的资料编号"))
    assert source["evidence_ref"] == "人工补充的资料编号"
    assert source["original_evidence_ref"] == ""
    with service.db.transaction() as conn:
        assert dict(conn.execute("SELECT * FROM sources WHERE id=?", (draft["source_id"],)).fetchone()) == original
    assert import_rows(service, book, [row(evidence_ref="")])["duplicates"] == 1
    with pytest.raises(AccountingError, match="版本已更新"):
        approve(service, book, draft)
    refreshed = service.get_book(LOCAL_ACTOR, book)["drafts"][0]
    assert refreshed["issues"] == []
    assert approve(service, book, refreshed)["status"] == "approved"


def test_unmatched_rule_and_tool_cannot_approve(service, book):
    import_rows(service, book, [row(description="待确认款项")])
    tool = AccountingSuggestionTool(service)
    assert tool.policy.can_approve is False and tool.policy.can_export is False
    assert tool.execute(LOCAL_ACTOR, book) == {"created": 1, "exceptions": 1}
    assert tool.execute(LOCAL_ACTOR, book) == {"created": 0, "exceptions": 0}
    draft = service.get_book(LOCAL_ACTOR, book)["drafts"][0]
    assert draft["status"] == "draft" and "需会计选择对应科目" in draft["explanation"]
    assert draft["issues"]


def test_operator_cannot_review_export_or_close(service, book):
    draft = make_draft(service, book)
    operator = add_actor(service, user_id="operator", firm_id=LOCAL_ACTOR.firm_id, role="operator", book_id=book)
    assert service.get_book(operator, book)["book"]["id"] == book
    for call in (
        lambda: service.approve(operator, book, draft["id"], ApprovalRequest(expected_version=1)),
        lambda: service.export(operator, book, ExportRequest(draft_ids=[draft["id"]], idempotency_key="test-key")),
        lambda: service.close_period(operator, book, "2026-10"),
    ):
        with pytest.raises(AccountingError) as error:
            call()
        assert error.value.status == 403


def test_export_is_atomic_idempotent_and_formula_safe(service, book):
    draft = make_draft(service, book, description="  =1+1 办公用品", evidence_ref="@SUM(A1)")
    request = ExportRequest(draft_ids=[draft["id"]], idempotency_key="export-test-001")
    with pytest.raises(AccountingError, match="只能导出"):
        service.export(LOCAL_ACTOR, book, request)
    approved = approve(service, book, draft)
    bad_request = ExportRequest(draft_ids=[draft["id"], "nonexistent"], idempotency_key="export-test-002")
    with pytest.raises(AccountingError):
        service.export(LOCAL_ACTOR, book, bad_request)
    assert service.get_book(LOCAL_ACTOR, book)["drafts"][0]["status"] == "approved"
    result = service.export(LOCAL_ACTOR, book, request)
    assert result == service.export(LOCAL_ACTOR, book, request)
    csv_rows = list(csv.reader(io.StringIO(result["csv"].lstrip("\ufeff"))))
    assert result["count"] == 1 and len(csv_rows) == 3
    assert csv_rows[1][2].startswith("'=") and csv_rows[1][7].startswith("'@")
    with pytest.raises(AccountingError, match="不同凭证集合"):
        service.export(LOCAL_ACTOR, book, ExportRequest(draft_ids=["different"], idempotency_key=request.idempotency_key))
    with pytest.raises(AccountingError, match="只能导出"):
        service.export(LOCAL_ACTOR, book, request.model_copy(update={"idempotency_key": "new-key-0001"}))
    assert service.get_book(LOCAL_ACTOR, book)["drafts"][0]["version"] == approved["version"] + 1


def test_closed_period_blocks_every_write(service, book):
    draft = make_draft(service, book)
    restore_period_state(service.db, book)
    for call in (
        lambda: import_rows(service, book),
        lambda: service.suggestions(LOCAL_ACTOR, book),
        lambda: service.update_draft(LOCAL_ACTOR, book, draft["id"], DraftUpdate(expected_version=1, description="修改", lines=draft["lines"])),
        lambda: approve(service, book, draft),
        lambda: service.add_evidence(LOCAL_ACTOR, book, draft["source_id"], EvidenceRequest(evidence_ref="补证")),
        lambda: service.export(LOCAL_ACTOR, book, ExportRequest(draft_ids=[draft["id"]], idempotency_key="closed-export")),
    ):
        with pytest.raises(AccountingError) as error:
            call()
        assert error.value.status == 403 and "已关闭" in error.value.detail


def test_export_receipts_survive_close_and_remain_scoped(service, book):
    draft = make_draft(service, book)
    approve(service, book, draft)
    request = ExportRequest(draft_ids=[draft["id"]], idempotency_key="receipt-reload")
    receipt = service.export(LOCAL_ACTOR, book, request)
    complete_month_close(service, LOCAL_ACTOR, book)
    records = service.list_exports(LOCAL_ACTOR, book)["exports"]
    assert len(records) == 1 and records[0]["id"] == receipt["id"]
    assert service.get_export(LOCAL_ACTOR, book, receipt["id"]) == receipt
    assert service.export(LOCAL_ACTOR, book, request) == receipt
    other_book = service.create_book(LOCAL_ACTOR, BookCreate(name="其他企业", period="2026-10"))["id"]
    stranger = add_actor(service)
    for call in (lambda: service.list_exports(stranger, book), lambda: service.get_export(stranger, book, receipt["id"]), lambda: service.get_export(LOCAL_ACTOR, other_book, receipt["id"])):
        with pytest.raises(AccountingError) as error:
            call()
        assert error.value.status == 404


def test_export_audit_failure_rolls_back_receipt_and_status(service, book, monkeypatch):
    draft = make_draft(service, book)
    approved = approve(service, book, draft)
    def fail_audit(*args, **kwargs):
        raise RuntimeError("模拟审计落库失败")
    monkeypatch.setattr(service, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="模拟"):
        service.export(LOCAL_ACTOR, book, ExportRequest(draft_ids=[draft["id"]], idempotency_key="atomic-export"))
    assert service.list_exports(LOCAL_ACTOR, book) == {"exports": []}
    after = service.get_book(LOCAL_ACTOR, book)["drafts"][0]
    assert after["status"] == "approved" and after["version"] == approved["version"]


def test_concurrent_export_same_request_returns_same_receipt(service, book):
    draft = make_draft(service, book)
    approve(service, book, draft)
    request = ExportRequest(draft_ids=[draft["id"]], idempotency_key="concurrent-export")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.export(LOCAL_ACTOR, book, request), range(4)))
    assert all(result == results[0] for result in results)
    assert len(service.list_exports(LOCAL_ACTOR, book)["exports"]) == 1


def test_workbench_batched_stats_match_details(service, book):
    make_draft(service, book)
    for index in range(12):
        another = service.create_book(LOCAL_ACTOR, BookCreate(name=f"批次企业{index}", period="2026-10"))["id"]
        make_draft(service, another, evidence_ref="" if index % 2 else "有效引用")
    queries = []
    original_connect = service.db.connect
    def instrumented():
        conn = original_connect()
        conn.set_trace_callback(queries.append)
        return conn
    service.db.connect = instrumented
    result = service.workbench(LOCAL_ACTOR)
    service.db.connect = original_connect
    assert sum(x.lstrip().upper().startswith("SELECT") for x in queries) <= 6
    assert result["stats"] == {"book_count": 13, "pending_count": 13, "approved_count": 0, "exception_count": 6}
    for summary in result["books"]:
        assert summary == service.get_book(LOCAL_ACTOR, summary["id"])["book"]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "api.db", seed=False), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 45678)) as test_client:
        yield test_client


def test_http_auth_origin_and_loopback_boundaries(client, tmp_path):
    assert client.get("/api/workbench").status_code == 401
    assert client.get("/api/openapi.json").status_code == 401
    assert client.get("/api/session", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/session", headers={"Host": "evil.example"}).status_code == 400
    session = client.get("/api/session", headers={"Origin": "http://127.0.0.1:8080"})
    assert session.status_code == 200 and session.json()["mode"] == "local"
    client.headers["Authorization"] = "Bearer " + session.json()["token"]
    assert client.get("/api/workbench").status_code == 200
    with TestClient(create_app(tmp_path / "remote.db", seed=False), base_url="http://127.0.0.1", client=("192.0.2.1", 12345)) as remote:
        assert remote.get("/api/session").status_code == 403


def test_http_full_happy_path_and_scope_validation(client):
    client.headers["Authorization"] = "Bearer " + client.get("/api/session").json()["token"]
    book = client.post("/api/books", json={"name": "HTTP完整流程", "period": "2026-10"}).json()["id"]
    url = f"/api/books/{book}"
    raw = row().model_dump()
    assert client.post(url + "/imports", json={"source_name": "文件", "rows": [{**raw, "amount": 0.3}]}).status_code == 422
    assert client.post(url + "/imports", json={"source_name": "文件", "rows": [raw]}).json()["imported"] == 1
    assert client.post(url + "/suggestions", json={}).json()["created"] == 1
    draft = client.get(url).json()["drafts"][0]
    reviewer = http_independent_reviewer(client, book)
    assert client.post(url + f"/drafts/{draft['id']}/approve", headers=reviewer, json={"expected_version": draft["version"]}).status_code == 200
    export = client.post(url + "/exports", json={"draft_ids": [draft["id"]], "idempotency_key": "http-flow-001"})
    assert export.status_code == 200 and export.json()["count"] == 1
    assert client.get("/api/books/not-authorized").status_code == 404


def test_http_review_requires_current_book_permission(client):
    client.headers["Authorization"] = "Bearer " + client.get("/api/session").json()["token"]
    book = client.post("/api/books", json={"name": "HTTP权限复核", "period": "2026-10"}).json()["id"]
    url = f"/api/books/{book}"
    assert client.post(url + "/imports", json={"source_name": "文件", "rows": [row().model_dump()]}).json()["imported"] == 1
    assert client.post(url + "/suggestions", json={}).json()["created"] == 1
    draft = client.get(url).json()["drafts"][0]
    employee = client.get("/api/session", params={"persona": "employee"}).json()["token"]
    denied = client.post(
        url + f"/drafts/{draft['id']}/approve",
        headers={"Authorization": "Bearer " + employee},
        json={"expected_version": draft["version"]},
    )
    assert denied.status_code in {403, 404}
    with client.app.state.service.db.transaction() as conn:
        assert conn.execute("SELECT status FROM drafts WHERE id=?", (draft["id"],)).fetchone()[0] == "draft"
    reviewer = http_independent_reviewer(client, book)
    approved = client.post(
        url + f"/drafts/{draft['id']}/approve",
        headers=reviewer,
        json={"expected_version": draft["version"]},
    )
    assert approved.status_code == 200


def http_independent_reviewer(client, book_id):
    # seed=False intentionally creates no demo staff. Register a separate
    # synthetic identity as an invited firm reviewer before exercising the
    # public book-assignment/review APIs. A client finance role must not be
    # promoted to formal reviewer.
    from types import SimpleNamespace
    from app.accounting.directory import DirectoryService
    from app.accounting.seed import LOCAL_ACTOR
    reviewer_id = "local-reviewer"
    DirectoryService(client.app.state.service.db).register_identity(SimpleNamespace(
        user_id=reviewer_id, display_name="独立测试会计", email="reviewer@demo.example.invalid"
    ))
    with client.app.state.service.db.transaction(write=True, system=True) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO memberships(firm_id,user_id,role) VALUES(?,?,?)",
            (LOCAL_ACTOR.firm_id, reviewer_id, "reviewer"),
        )
    token = client.get("/api/session", params={"persona": "reviewer"}).json()["token"]
    assert client.put(f"/api/books/{book_id}/members/{reviewer_id}", json={"role": "reviewer"}).status_code == 200
    return {"Authorization": "Bearer " + token}


@pytest.mark.parametrize("key,value", [("ACCOUNTING_ENV", "production"), ("APP_ENV", "production"), ("ENVIRONMENT", "staging")])
def test_production_environment_fails_closed(monkeypatch, tmp_path, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="禁止"):
        create_app(tmp_path / "denied.db")


def test_seed_is_repeatable_and_only_fictional(tmp_path):
    service = AccountingService(Database(tmp_path / "seed.db"))
    bootstrap(service)
    bootstrap(service)
    result = service.workbench(LOCAL_ACTOR)
    assert result["stats"] == {"book_count": 3, "pending_count": 7, "exception_count": 2, "approved_count": 0}
    assert all("虚构示例" in book["name"] for book in result["books"])
