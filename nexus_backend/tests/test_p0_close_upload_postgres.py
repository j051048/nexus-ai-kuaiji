"""P0 real PostgreSQL boundaries; explicit new loopback test_ database only.

Use the ignored disposable-cluster runner, never a deployment database.  These
tests deliberately use separate libpq sessions and restricted runtime roles.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from threading import Barrier
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService
from app.b04_close.models import CloseCommand, ClosePreflight
from app.b04_close.service import CloseService
from app.business.models import BusinessEventCreate
from app.business.service import BusinessService
from app.business.storage import AttachmentStore
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.opening.models import AccountCreate, OpeningCommand, OpeningImport, OpeningLineInput, OpeningPackageCreate, PolicyCreate
from app.opening.service import OpeningService
from app.posting.models import OpeningBind
from app.posting.service import PostingService
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from app.uploads import UploadCreate, UploadIntentService
from app.upload_bindings import reference


@pytest.fixture(scope="module")
def p0_pg():
    admin = os.getenv("ACCOUNTING_P0_TEST_ADMIN_URL")
    runtime = os.getenv("ACCOUNTING_P0_TEST_APP_URL")
    if not admin or not runtime:
        pytest.skip("需显式配置新建的本机 P0 test_ PostgreSQL 管理和受限账号")
    targets = [urlsplit(value) for value in (admin, runtime)]
    for target in targets:
        if target.scheme not in {"postgres", "postgresql"} or target.hostname != "127.0.0.1" or target.port == 5432 or not target.path.startswith("/test_p0_") or target.query or target.fragment:
            pytest.fail("只允许独立非 5432 loopback test_p0_ 数据库")
    assert (targets[0].hostname, targets[0].port, targets[0].path) == (targets[1].hostname, targets[1].port, targets[1].path)
    import psycopg
    with psycopg.connect(admin) as conn:
        assert conn.execute("SHOW data_directory").fetchone()[0].startswith("/tmp/test_nexus_p0_")
    db = Database(runtime)
    yield SimpleNamespace(admin=admin, runtime=runtime, db=db, psycopg=psycopg)
    db.close()


@pytest.fixture
def p0_case(p0_pg, tmp_path):
    c = p0_pg
    suffix = uuid4().hex
    manager, reviewer, foreign = (Actor("p0-" + kind + "-" + suffix, "p0-firm-" + suffix) for kind in ("manager", "reviewer", "foreign"))
    foreign = Actor(foreign.user_id, "p0-foreign-firm-" + suffix)
    with c.psycopg.connect(c.admin) as conn:
        for actor in (manager, reviewer, foreign):
            conn.execute("INSERT INTO accounting.users(id,name,email) VALUES(%s,'P0合成测试人员',%s)", (actor.user_id, actor.user_id + "@example.test"))
        for actor in (manager, foreign):
            conn.execute("INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,'P0合成事务所',%s)", (actor.firm_id, actor.user_id))
        for actor, role in ((manager, "owner"), (reviewer, "admin"), (foreign, "owner")):
            conn.execute("INSERT INTO accounting.memberships VALUES(%s,%s,%s)", (actor.firm_id, actor.user_id, role))
    accounting = AccountingService(c.db)
    book = accounting.create_book(manager, BookCreate(name="P0合成客户", period="2026-10"))
    other_book = accounting.create_book(manager, BookCreate(name="P0同所隔离客户", period="2026-10"))
    foreign_book = accounting.create_book(foreign, BookCreate(name="P0其他事务所客户", period="2026-10"))
    with c.psycopg.connect(c.admin) as conn:
        for actor, current in ((manager, book), (manager, other_book), (foreign, foreign_book)):
            conn.execute("INSERT INTO accounting.company_memberships(company_id,user_id,role) VALUES(%s,%s,'admin')", (current["company_id"], actor.user_id))
    store = AttachmentStore(tmp_path / "originals")
    yield SimpleNamespace(**vars(c), manager=manager, reviewer=reviewer, foreign=foreign, book=book, other_book=other_book, foreign_book=foreign_book,
                          accounting=accounting, business=BusinessService(c.db), store=store, uploads=UploadIntentService(c.db, store))


def _reviewed_close(c):
    opening = OpeningService(c.db)
    with c.db.transaction(actor=c.manager) as conn:
        ledger_id = conn.execute("SELECT ledger_id FROM ledger_periods WHERE book_id=?", (c.book["id"],)).fetchone()[0]
    policy = opening.create_policy(c.manager, PolicyCreate(ledger_id=ledger_id, standard_kind="small_enterprise", taxpayer_kind="general", effective_from="2026-01", effective_to="2026-12", reason="P0合成政策"))
    accounts = {}
    for code, name, category, normal in (("1002", "银行存款", "asset", "debit"), ("2202", "应付账款", "liability", "credit")):
        accounts[code] = opening.create_account(c.manager, AccountCreate(ledger_id=ledger_id, code=code, name=name, category=category, normal_balance=normal, effective_from="2026-01", effective_to="2026-12", reason="P0合成科目"))
    package = opening.create_package(c.manager, OpeningPackageCreate(ledger_id=ledger_id, target_period="2026-10", policy_id=policy["id"]))
    version = opening.import_version(c.manager, package["package"]["id"], OpeningImport(idempotency_key="p0-opening-" + uuid4().hex, reason="P0零余额期初", lines=[OpeningLineInput(account_id=row["id"], account_version=1, year_opening="0.00", ytd_debit="0.00", ytd_credit="0.00", opening="0.00") for row in accounts.values()]))
    version_id = version["version"]["id"]
    opening.validate_version(c.manager, version_id)
    opening.submit_version(c.manager, version_id, OpeningCommand(expected_version=1, reason="P0提交期初"))
    opening.approve_version(c.reviewer, version_id, OpeningCommand(expected_version=1, reason="P0独立复核"))
    opening.freeze_version(c.reviewer, version_id, OpeningCommand(expected_version=1, reason="P0冻结期初"))
    PostingService(c.db).bind_opening(c.manager, OpeningBind(book_id=c.book["id"], package_version_id=version_id, reason="P0绑定期初"))
    party = LedgersService(c.db).create_entity(c.manager, c.book["id"], EntityCreate(kind="counterparty", code="P0-PARTY", name="P0合成往来单位"))
    reports = ReportService(c.db)
    report = reports.create(c.manager, c.book["id"], "2026-10")
    report_id = report["report"]["id"]
    reports.validate(c.manager, report_id)
    reports.review(c.reviewer, report_id, ReportCommand(expected_version=1, reason="P0独立报表复核"))
    close = CloseService(c.db)
    run = close.preflight(c.manager, ClosePreflight(book_id=c.book["id"], period="2026-10", report_id=report_id, idempotency_key="p0-close-" + uuid4().hex, reason="P0月结预检"))
    assert run["close"]["status"] == "preflight_passed", run
    run = close.review(c.reviewer, run["close"]["id"], CloseCommand(expected_version=run["close"]["version"], reason="P0独立月结复核"))
    return SimpleNamespace(close=close, run=run["close"], ledger_id=ledger_id, accounts=accounts, party=party)


@contextmanager
def _runtime_transaction(c, actor):
    with c.psycopg.connect(c.runtime) as conn:
        conn.execute("SET LOCAL ROLE accounting_app")
        conn.execute("SET LOCAL search_path TO accounting,pg_catalog")
        conn.execute("SELECT set_config('app.user_id',%s,true),set_config('app.firm_id',%s,true)", (actor.user_id, actor.firm_id))
        conn.execute("SET LOCAL statement_timeout='12s'")
        yield conn


def _await_blocked(c, blocker_pid, future):
    deadline = time.monotonic() + 8
    with c.psycopg.connect(c.admin, autocommit=True) as observer:
        while time.monotonic() < deadline:
            rows = observer.execute("SELECT pid FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid))", (blocker_pid,)).fetchall()
            if rows:
                return
            if future.done():
                raise AssertionError("Competitor completed before the real PG lock was released")
            time.sleep(0.025)
    raise AssertionError("Expected separate PostgreSQL session did not block")


def _lock(c, prepared):
    try:
        return prepared.close.lock(c.manager, prepared.run["id"], CloseCommand(expected_version=prepared.run["version"], reason="P0并发最终锁期"))
    except AccountingError as exc:
        return exc


def test_real_pg_new_account_version_exact_scope_and_current_manager_policy(p0_case):
    c = p0_case
    with c.db.transaction(actor=c.manager) as conn:
        ledger_id = conn.execute("SELECT ledger_id FROM ledger_periods WHERE book_id=?", (c.book["id"],)).fetchone()[0]
        other_ledger = conn.execute("SELECT ledger_id FROM ledger_periods WHERE book_id=?", (c.other_book["id"],)).fetchone()[0]
    opening = OpeningService(c.db)
    created = opening.create_account(c.manager, AccountCreate(ledger_id=ledger_id, code="1002", name="P0授权科目", category="asset", normal_balance="debit", effective_from="2026-01", reason="P0真实PG新科目"))
    assert created["id"] == created["account_id"]
    other = opening.create_account(c.manager, AccountCreate(ledger_id=other_ledger, code="1002", name="P0另一企业科目", category="asset", normal_balance="debit", effective_from="2026-01", reason="P0真实PG新科目"))
    peer = Actor("p0-account-peer-" + uuid4().hex, c.manager.firm_id)
    with c.psycopg.connect(c.admin) as conn:
        conn.execute("INSERT INTO accounting.users(id,name) VALUES(%s,'P0仅账套授权测试人员')", (peer.user_id,))
        conn.execute("INSERT INTO accounting.memberships VALUES(%s,%s,'reviewer')", (peer.firm_id, peer.user_id))
        conn.execute("INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'reviewer')", (c.book["id"], peer.firm_id, peer.user_id))
    with c.db.transaction(actor=peer) as conn:
        assert conn.execute("SELECT * FROM ledger_account_versions WHERE account_id=?", (created["id"],)).fetchall() == []
    with c.psycopg.connect(c.admin) as conn:
        conn.execute("INSERT INTO accounting.ledger_managers(ledger_id,user_id,granted_by,reason,created_at) VALUES(%s,%s,%s,'P0仅当前账套管理',CURRENT_TIMESTAMP::text)", (ledger_id, peer.user_id, c.manager.user_id))
    with c.db.transaction(actor=peer) as conn:
        assert conn.execute("SELECT * FROM ledger_account_versions WHERE account_id=?", (created["id"],)).fetchone()["ledger_id"] == ledger_id
        assert conn.execute("SELECT * FROM ledger_account_versions WHERE account_id=?", (other["id"],)).fetchall() == []
    with c.db.transaction(actor=c.foreign) as conn:
        assert conn.execute("SELECT * FROM ledger_account_versions WHERE account_id=?", (created["id"],)).fetchall() == []
    with pytest.raises(c.psycopg.Error) as forged:
        with _runtime_transaction(c, peer) as conn:
            conn.execute("INSERT INTO ledger_account_versions(id,account_id,ledger_id,firm_id,company_id,version,code,name,level,is_leaf,category,normal_balance,effective_from,reason,created_by,created_at) VALUES(%s,%s,%s,%s,%s,2,'1002','P0伪造企业绑定',1,1,'asset','debit','2026-01','P0应被数据库拒绝',%s,CURRENT_TIMESTAMP::text)", (uuid4().hex, created["id"], ledger_id, peer.firm_id, c.other_book["company_id"], peer.user_id))
    assert forged.value.sqlstate in {"42501", "23503", "23514"}
    with c.psycopg.connect(c.admin) as conn:
        conn.execute("UPDATE accounting.users SET active=0 WHERE id=%s", (peer.user_id,))
    with c.db.transaction(actor=peer) as conn:
        assert conn.execute("SELECT * FROM ledger_account_versions WHERE account_id=?", (created["id"],)).fetchall() == []


@pytest.mark.parametrize("dependency", ["bank", "subledger", "account", "policy", "template"])
def test_real_pg_dependency_write_committed_before_waiting_close_invalidates_review(p0_case, dependency):
    c = p0_case
    prepared = _reviewed_close(c)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with _runtime_transaction(c, c.manager) as writer:
            blocker = writer.execute("SELECT pg_backend_pid()").fetchone()[0]
            if dependency == "bank":
                writer.execute("INSERT INTO bank_statement_lines(id,book_id,period,line_date,description,amount_cents,status,created_by,created_at) VALUES(%s,%s,'2026-10','2026-10-03','P0并发银行资料',0,'unmatched',%s,CURRENT_TIMESTAMP::text)", (uuid4().hex, c.book["id"], c.manager.user_id))
            elif dependency == "subledger":
                writer.execute("INSERT INTO subledger_items(id,firm_id,company_id,ledger_id,book_id,period,kind,counterparty_id,control_account_id,control_account_version,original_date,amount_cents,source_kind,created_by,created_at) VALUES(%s,%s,%s,%s,%s,'2026-10','payable',%s,%s,1,'2026-10-03',1,'manual',%s,CURRENT_TIMESTAMP::text)", (uuid4().hex, c.manager.firm_id, c.book["company_id"], prepared.ledger_id, c.book["id"], prepared.party["id"], prepared.accounts["2202"]["id"], c.manager.user_id))
            elif dependency == "account":
                writer.execute("INSERT INTO ledger_accounts(id,firm_id,company_id,ledger_id,current_version,status,created_by,created_at) VALUES(%s,%s,%s,%s,1,'active',%s,CURRENT_TIMESTAMP::text)", (uuid4().hex, c.manager.firm_id, c.book["company_id"], prepared.ledger_id, c.manager.user_id))
            elif dependency == "policy":
                writer.execute("UPDATE accounting_policies SET industry='P0并发政策变更' WHERE ledger_id=%s", (prepared.ledger_id,))
            else:
                writer.execute("UPDATE report_templates SET name=name||'修订' WHERE id='small_enterprise_basic' AND version=1")
            pending = pool.submit(_lock, c, prepared)
            _await_blocked(c, blocker, pending)
        outcome = pending.result(timeout=15)
    assert isinstance(outcome, AccountingError), outcome
    assert outcome.status == 409
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM internal_period_locks WHERE book_id=?", (c.book["id"],)).fetchone()[0] == 0


def _event(c, company_id=None):
    return c.business.create(c.manager, BusinessEventCreate(company_id=company_id or c.book["company_id"], template_id="reimbursement", payload={"occurred_on":"2026-10-03", "description":"P0虚构办公支出", "counterparty":"P0虚构供应商", "amount":"0.30", "direction":"expense", "payment_status":"unpaid", "category":"office"}))


def _stored(c):
    data = b"%PDF-1.4\nP0 synthetic original\n%%EOF"
    created = c.uploads.create(c.manager, UploadCreate(purpose="event_attachment", filename="P0合成票据.pdf", media_type="application/pdf", size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    c.uploads.store_content(c.manager, created["id"], data)
    return created["id"]


def test_real_pg_upload_scope_never_reveals_or_consumes_other_firm(p0_case):
    c = p0_case
    upload_id = _stored(c)
    with pytest.raises(AccountingError) as rejected:
        c.uploads.peek(c.foreign, upload_id, "event_attachment")
    assert rejected.value.status == 404
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute("SELECT state FROM upload_intents WHERE id=?", (upload_id,)).fetchone()[0] == "stored"


def _consume_event(c, upload_id, event):
    return c.uploads.consume(c.manager, upload_id, "event_attachment", event["id"], lambda conn, metadata: c.business.add_attachment(c.manager, event["id"], event["version"], metadata, conn=conn))


@pytest.mark.parametrize("same_target", [False, True])
def test_real_pg_parallel_consumption_is_one_binding_or_exact_replay(p0_case, same_target):
    c = p0_case
    first = _event(c)
    second = first if same_target else _event(c, c.other_book["company_id"])
    upload_id = _stored(c)
    start = Barrier(2)
    def contender(event):
        start.wait(timeout=5)
        try:
            return _consume_event(c, upload_id, event)
        except AccountingError as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contender, event) for event in (first, second)]
        outcomes = [future.result(timeout=20) for future in futures]
    accepted = [value for value in outcomes if not isinstance(value, AccountingError)]
    refused = [value for value in outcomes if isinstance(value, AccountingError)]
    assert len(accepted) == (2 if same_target else 1)
    assert all(value.status == 409 for value in refused)
    with c.db.transaction(actor=c.manager) as conn:
        row = conn.execute("SELECT state,binding_json,consumed_ref FROM upload_intents WHERE id=?", (upload_id,)).fetchone()
        attachments = conn.execute("SELECT * FROM business_attachments WHERE id=?", (upload_id,)).fetchall()
    assert row["state"] == "consumed"
    binding = json.loads(row["binding_json"])
    assert len(attachments) == 1
    assert binding["target_id"] == attachments[0]["event_id"]
    assert binding["company_id"] == attachments[0]["company_id"]
    assert binding["firm_id"] == c.manager.firm_id
    assert row["consumed_ref"] == "business_attachments:" + upload_id
    if same_target:
        assert accepted[0]["version"] == accepted[1]["version"] == first["version"] + 1
    else:
        assert accepted[0]["id"] in {first["id"], second["id"]}


def test_real_pg_attachment_insert_and_consumption_rollback_together(p0_case):
    c = p0_case
    event, upload_id = _event(c), _stored(c)
    def failed_registration(conn, metadata):
        c.business.add_attachment(c.manager, event["id"], event["version"], metadata, conn=conn)
        # Simulate a failure after the real SQL insert and fact-version update.
        raise RuntimeError("P0 deliberate post-insert transaction failure")
    with pytest.raises(RuntimeError, match="deliberate post-insert"):
        c.uploads.consume(c.manager, upload_id, "event_attachment", event["id"], failed_registration)
    with c.db.transaction(actor=c.manager) as conn:
        row = conn.execute("SELECT state,binding_json,consumed_ref FROM upload_intents WHERE id=?", (upload_id,)).fetchone()
        assert (row["state"], row["binding_json"], row["consumed_ref"]) == ("stored", None, None)
        assert conn.execute("SELECT COUNT(*) FROM business_attachments WHERE id=?", (upload_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT version FROM business_events WHERE id=?", (event["id"],)).fetchone()[0] == event["version"]
    assert _consume_event(c, upload_id, event)["version"] == event["version"] + 1


def test_real_pg_binding_rejects_forgery_without_actual_original_and_is_immutable(p0_case):
    c = p0_case
    event, upload_id = _event(c), _stored(c)
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with _runtime_transaction(c, c.manager) as writer:
            writer.execute("UPDATE upload_intents SET state='consuming' WHERE id=%s", (upload_id,))
            writer.execute("UPDATE upload_intents SET state='consumed',consumed_ref=%s,binding_json=%s WHERE id=%s", ("business_attachments:" + upload_id, json.dumps({"version":1,"target_id":event["id"],"company_id":c.book["company_id"]}), upload_id))
    _consume_event(c, upload_id, event)
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with _runtime_transaction(c, c.manager) as writer:
            writer.execute("UPDATE upload_intents SET consumed_ref='business_attachments:forged' WHERE id=%s", (upload_id,))
    other = _event(c, c.other_book["company_id"])
    with pytest.raises(AccountingError) as mismatch:
        _consume_event(c, upload_id, other)
    assert mismatch.value.status == 409
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM business_attachments WHERE event_id=?", (other["id"],)).fetchone()[0] == 0


@pytest.mark.parametrize("field", ["firm_id", "company_id", "event_id", "target_id", "sha256", "size_bytes"])
def test_real_pg_binding_tamper_with_real_attachment_rolls_back_both_rows(p0_case, field):
    c = p0_case
    event, upload_id = _event(c), _stored(c)
    def forge_after_insert(conn, metadata):
        result = c.business.add_attachment(c.manager, event["id"], event["version"], metadata, conn=conn)
        row = conn.execute("SELECT * FROM upload_intents WHERE id=?", (upload_id,)).fetchone()
        binding = reference(conn, c.manager, row, event["id"])
        if field in {"sha256", "size_bytes"}:
            binding["storage"][field] = "a" * 64 if field == "sha256" else binding["storage"][field] + 1
        else:
            binding[field] = {"firm_id": c.foreign.firm_id, "company_id": c.other_book["company_id"], "event_id": "different-event", "target_id": "different-target"}[field]
        conn.execute("UPDATE upload_intents SET state='consumed',consumed_ref=?,binding_json=? WHERE id=?", ("business_attachments:" + upload_id, json.dumps(binding), upload_id))
        return result
    with pytest.raises(c.psycopg.errors.CheckViolation):
        c.uploads.consume(c.manager, upload_id, "event_attachment", event["id"], forge_after_insert)
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute("SELECT state FROM upload_intents WHERE id=?", (upload_id,)).fetchone()[0] == "stored"
        assert conn.execute("SELECT COUNT(*) FROM business_attachments WHERE id=?", (upload_id,)).fetchone()[0] == 0


def test_real_pg_cannot_insert_fabricated_legacy_consumption(p0_case):
    c = p0_case
    upload_id = _stored(c)
    with pytest.raises(c.psycopg.Error) as refused:
        with _runtime_transaction(c, c.manager) as writer:
            writer.execute("INSERT INTO upload_intents(id,firm_id,user_id,purpose,filename,media_type,size_bytes,sha256,storage_key,state,created_at,expires_at,consumed_ref,binding_json) SELECT %s,firm_id,user_id,purpose,filename,media_type,size_bytes,sha256,storage_key||'_forged','consumed',created_at,expires_at,NULL,NULL FROM upload_intents WHERE id=%s", (uuid4().hex, upload_id))
    assert refused.value.sqlstate in {"42501", "23514"}


@pytest.mark.parametrize("field", ["storage_key", "sha256", "user_id"])
def test_real_pg_consumed_object_identity_cannot_be_relabelled(p0_case, field):
    c = p0_case
    event, upload_id = _event(c), _stored(c)
    _consume_event(c, upload_id, event)
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with _runtime_transaction(c, c.manager) as writer:
            value = {"storage_key": "aa/" + uuid4().hex, "sha256": "a" * 64, "user_id": c.reviewer.user_id}[field]
            writer.execute(c.psycopg.sql.SQL("UPDATE upload_intents SET {}=%s WHERE id=%s").format(c.psycopg.sql.Identifier(field)), (value, upload_id))


def test_real_pg_committed_revocation_wins_against_waiting_period_lock(p0_case):
    c = p0_case
    prepared = _reviewed_close(c)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with c.psycopg.connect(c.admin) as revoker:
            # The same legacy compatibility gate used by identity mutations.
            revoker.execute("SELECT accounting.lock_legacy_write()")
            revoker.execute("UPDATE accounting.users SET active=0 WHERE id=%s", (c.manager.user_id,))
            blocker = revoker.execute("SELECT pg_backend_pid()").fetchone()[0]
            pending = pool.submit(_lock, c, prepared)
            _await_blocked(c, blocker, pending)
        outcome = pending.result(timeout=15)
    assert isinstance(outcome, AccountingError), outcome
    assert outcome.status in {403, 404}
    with c.psycopg.connect(c.admin) as conn:
        assert conn.execute("SELECT COUNT(*) FROM accounting.internal_period_locks WHERE book_id=%s", (c.book["id"],)).fetchone()[0] == 0


def test_real_pg_period_lock_wins_then_waiting_bank_write_is_rejected(p0_case):
    c = p0_case
    prepared = _reviewed_close(c)
    token = "p0_barrier_" + uuid4().hex[:10]
    barrier = 7_010_190_001
    # A test-only database trigger pauses the real service immediately before
    # its insert, while its real company transaction lock remains held.
    with c.psycopg.connect(c.admin) as admin:
        admin.execute(c.psycopg.sql.SQL("CREATE FUNCTION accounting.{}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.book_id={} THEN PERFORM pg_advisory_xact_lock({}); END IF; RETURN NEW; END $$").format(c.psycopg.sql.Identifier(token), c.psycopg.sql.Literal(c.book["id"]), c.psycopg.sql.Literal(barrier)))
        admin.execute(c.psycopg.sql.SQL("CREATE TRIGGER {} BEFORE INSERT ON accounting.internal_period_locks FOR EACH ROW EXECUTE FUNCTION accounting.{}()").format(c.psycopg.sql.Identifier(token), c.psycopg.sql.Identifier(token)))

    def bank_write():
        try:
            with _runtime_transaction(c, c.manager) as writer:
                writer.execute("INSERT INTO bank_statement_lines(id,book_id,period,line_date,description,amount_cents,status,created_by,created_at) VALUES(%s,%s,'2026-10','2026-10-03','P0锁期后并发银行写入',0,'unmatched',%s,CURRENT_TIMESTAMP::text)", (uuid4().hex, c.book["id"], c.manager.user_id))
            return "unexpected-write"
        except c.psycopg.Error as exc:
            return exc.sqlstate

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            with c.psycopg.connect(c.admin) as latch:
                latch.execute("SELECT pg_advisory_xact_lock(%s)", (barrier,))
                blocker = latch.execute("SELECT pg_backend_pid()").fetchone()[0]
                close_future = pool.submit(_lock, c, prepared)
                _await_blocked(c, blocker, close_future)
                write_future = pool.submit(bank_write)
                with c.psycopg.connect(c.admin, autocommit=True) as observer:
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        if observer.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid))>0 AND datname=current_database()").fetchone()[0] >= 2:
                            break
                        if write_future.done():
                            raise AssertionError("Bank write did not wait for the in-progress close")
                        time.sleep(0.025)
                    else:
                        raise AssertionError("Both independent PostgreSQL competitors were not blocked")
            outcome = close_future.result(timeout=15)
            rejected = write_future.result(timeout=15)
        assert not isinstance(outcome, AccountingError), outcome
        assert outcome["close"]["status"] == "locked"
        assert rejected in {"23514", "42501"}, rejected
    finally:
        with c.psycopg.connect(c.admin) as admin:
            admin.execute(c.psycopg.sql.SQL("DROP TRIGGER IF EXISTS {} ON accounting.internal_period_locks").format(c.psycopg.sql.Identifier(token)))
            admin.execute(c.psycopg.sql.SQL("DROP FUNCTION IF EXISTS accounting.{}()").format(c.psycopg.sql.Identifier(token)))
