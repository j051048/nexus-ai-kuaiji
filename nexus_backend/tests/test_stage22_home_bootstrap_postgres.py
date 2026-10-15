"""Home bootstrap preserves native RLS, live grants and human export gates."""

import json
from contextlib import closing, contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.directory import DirectoryService
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import ExportRequest
from app.accounting.service import AccountingService
from app.auth import Identity
from app.workspace.bootstrap import home_bootstrap
from test_batch_review_postgres import bank_draft
from test_business_postgres import (
    ADMIN_URL,
    APP_URL,
    business_case,
    business_postgres,
    insert_event,
)
from test_stage12_workspace_postgres import workspace


pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="需要独立本机 PostgreSQL 管理与受限运行测试连接"
)


def identity_for(case, actor):
    """A provider-verified fixture whose profile matches the current DB row."""
    with case.psycopg.connect(ADMIN_URL) as conn:
        name, email = conn.execute(
            "SELECT name,email FROM accounting.users WHERE id=%s", (actor.user_id,)
        ).fetchone()
    return Identity(actor.user_id, email or None, name)


def bootstrap(case, actor, *, directory=None, service=None, firm_id=None, mode="local"):
    return home_bootstrap(
        directory or DirectoryService(case.db),
        service or workspace(case),
        identity_for(case, actor),
        firm_id,
        mode,
    )


def test_pg_bootstrap_matches_separate_contracts_for_seven_roles(business_case):
    case, service = business_case, workspace(business_case)
    directory = DirectoryService(case.db)
    own = insert_event(case)
    hidden = insert_event(case, case.colleague)
    for actor in (
        case.owner,
        case.employee,
        case.approver,
        case.finance,
        case.reviewer,
        case.viewer,
        case.unassigned,
    ):
        expected = {
            "me": directory.me(actor.user_id, mode="local"),
            "profile": service.profile(actor),
            "tasks": service.list(actor),
        }
        value = bootstrap(case, actor, directory=directory, service=service)
        assert value == expected
        if actor == case.employee:
            assert own in json.dumps(value, ensure_ascii=False)
            assert hidden not in json.dumps(value, ensure_ascii=False)
            assert case.other_company not in json.dumps(value, ensure_ascii=False)


def test_pg_bootstrap_uses_one_app_read_snapshot_and_binds_selected_firm(
    business_case, monkeypatch
):
    case, service = business_case, workspace(business_case)
    directory = DirectoryService(case.db)
    identity = identity_for(case, case.reviewer)
    transactions, states = [], []
    original_transaction = case.db.transaction

    @contextmanager
    def transaction(*args, **kwargs):
        transactions.append(kwargs)
        with original_transaction(*args, **kwargs) as conn:
            yield conn

    def checked(method, *, initial=False):
        def call(conn, *args, **kwargs):
            state = conn.execute(
                "SELECT current_user,current_setting('transaction_read_only'),"
                "current_setting('transaction_isolation'),"
                "current_setting('app.user_id',true),current_setting('app.firm_id',true),"
                "pg_current_snapshot()::text"
            ).fetchone()
            states.append((id(conn), tuple(state[index] for index in range(6))))
            assert state[0] == "accounting_app"
            assert state[1:4] == ("on", "repeatable read", case.reviewer.user_id)
            assert state[4] == ("" if initial else case.reviewer.firm_id)
            return method(conn, *args, **kwargs)

        return call

    monkeypatch.setattr(case.db, "transaction", transaction)
    monkeypatch.setattr(
        directory, "me_in_connection", checked(directory.me_in_connection, initial=True)
    )
    monkeypatch.setattr(service, "profile_in_connection", checked(service.profile_in_connection))
    monkeypatch.setattr(service, "list_in_connection", checked(service.list_in_connection))
    result = home_bootstrap(directory, service, identity, mode="local")
    assert result["me"]["current_firm_id"] == case.reviewer.firm_id
    assert len(transactions) == 1
    assert transactions[0] == {"actor": Actor(case.reviewer.user_id, "")}
    assert len(states) == 3
    assert len({connection for connection, _ in states}) == 1
    assert len({state[5] for _, state in states}) == 1


@pytest.mark.parametrize("change", ["membership", "book", "client", "company", "user"])
def test_pg_bootstrap_next_request_observes_revocation(business_case, change):
    case = business_case
    actor = case.reviewer if change == "book" else case.finance
    assert bootstrap(case, actor)["profile"]["can_accounting"]
    with case.psycopg.connect(ADMIN_URL) as conn:
        if change == "membership":
            conn.execute(
                "DELETE FROM accounting.company_memberships WHERE user_id=%s", (actor.user_id,)
            )
        elif change == "book":
            conn.execute("DELETE FROM accounting.book_access WHERE user_id=%s", (actor.user_id,))
        elif change == "client":
            conn.execute(
                "UPDATE accounting.firm_clients SET status='suspended' WHERE company_id=%s",
                (case.company,),
            )
        elif change == "company":
            conn.execute(
                "UPDATE accounting.companies SET status='archived',version=version+1 WHERE id=%s",
                (case.company,),
            )
        else:
            conn.execute("UPDATE accounting.users SET active=0 WHERE id=%s", (actor.user_id,))
    if change == "user":
        with pytest.raises(AccountingError) as error:
            bootstrap(case, actor)
        assert error.value.status == 403
    else:
        value = bootstrap(case, actor)
        assert case.company not in json.dumps(value, ensure_ascii=False)
        if change == "book":
            assert value["profile"]["companies"] == []
            assert value["profile"]["can_accounting"] is False
            assert value["tasks"]["tasks"] == []
        else:
            assert value["me"]["current_firm_id"] is None
            assert value["profile"] is None and value["tasks"] is None
            with pytest.raises(AccountingError) as error:
                bootstrap(case, actor, firm_id=actor.firm_id)
            assert error.value.status == 403


def test_pg_bootstrap_no_firm_and_unassigned_firm_have_no_fallback(business_case):
    case = business_case
    outsider = case.person()
    value = bootstrap(case, outsider)
    assert value["me"]["firms"] == []
    assert value["me"]["current_firm_id"] is None
    assert value["profile"] is None and value["tasks"] is None
    for firm_id in (case.owner.firm_id, "missing-" + uuid4().hex):
        with pytest.raises(AccountingError) as error:
            bootstrap(case, outsider, firm_id=firm_id)
        assert error.value.status == 403


def test_pg_bootstrap_default_and_explicit_firm_keep_existing_workspace_order(business_case):
    case = business_case
    own = insert_event(case)
    staff_firm = "bootstrap-staff-firm-" + uuid4().hex
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,'另一事务所授权',%s)",
            (staff_firm, case.employee.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships VALUES(%s,%s,'owner')",
            (staff_firm, case.employee.user_id),
        )
    # Firm membership precedes customer-company access, just as in /me.
    default = bootstrap(case, case.employee)
    assert default["me"]["current_firm_id"] == staff_firm
    assert default["profile"]["companies"] == [] and default["tasks"]["tasks"] == []
    assert own not in json.dumps(default, ensure_ascii=False)
    selected = bootstrap(case, case.employee, firm_id=case.employee.firm_id)
    assert selected["me"]["current_firm_id"] == case.employee.firm_id
    assert selected["profile"]["role"] == "employee"
    assert own in json.dumps(selected, ensure_ascii=False)


def test_pg_bootstrap_pool_reuse_after_failure_does_not_inherit_scope(
    business_case, monkeypatch
):
    case = business_case
    private_event = insert_event(case)
    suffix = uuid4().hex
    other = Actor("bootstrap-other-user-" + suffix, "bootstrap-other-firm-" + suffix)
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name) VALUES(%s,'另一个事务所测试用户')",
            (other.user_id,),
        )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,'另一个事务所',%s)",
            (other.firm_id, other.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships VALUES(%s,%s,'owner')",
            (other.firm_id, other.user_id),
        )
    monkeypatch.setenv("ACCOUNTING_DB_POOL_MAX", "1")
    monkeypatch.setenv("ACCOUNTING_DB_POOL_MIN", "0")
    database = Database(APP_URL)
    scoped_case = SimpleNamespace(**{**vars(case), "db": database, "service": AccountingService(database)})
    service, directory = workspace(scoped_case), DirectoryService(database)
    borrowed = []
    original_profile = service.profile_in_connection

    def profile(conn, actor):
        borrowed.append(id(conn.raw))
        return original_profile(conn, actor)

    monkeypatch.setattr(service, "profile_in_connection", profile)
    try:
        first = bootstrap(scoped_case, case.employee, service=service, directory=directory)
        assert private_event in json.dumps(first, ensure_ascii=False)
        with pytest.raises(AccountingError) as error:
            bootstrap(
                scoped_case, case.employee, service=service, directory=directory, firm_id=other.firm_id
            )
        assert error.value.status == 403
        second = bootstrap(scoped_case, other, service=service, directory=directory)
        assert second["me"]["current_firm_id"] == other.firm_id
        assert second["profile"]["companies"] == [] and second["tasks"]["tasks"] == []
        assert case.company not in json.dumps(second, ensure_ascii=False)
        assert private_event not in json.dumps(second, ensure_ascii=False)
        assert len(borrowed) == 2 and borrowed[0] == borrowed[1]
        with closing(database.connect()) as conn:
            row = conn.execute(
                "SELECT current_user,current_setting('app.user_id',true),"
                "current_setting('app.firm_id',true)"
            ).fetchone()
            assert row[0] != "accounting_app"
            assert row[1] in (None, "") and row[2] in (None, "")
    finally:
        database.close()


def test_pg_bootstrap_verified_stable_identity_does_not_use_system_role(
    business_case, monkeypatch
):
    case, service = business_case, workspace(business_case)
    directory = DirectoryService(case.db)

    def unexpected_register(_identity):
        pytest.fail("稳定身份不得执行 system 身份写入或注册")

    monkeypatch.setattr(directory, "register_identity", unexpected_register)
    value = bootstrap(case, case.employee, directory=directory, service=service, mode="supabase")
    assert value["me"] == directory.me(case.employee.user_id, mode="supabase")
    assert value["profile"] == service.profile(case.employee)
    with case.db.transaction(actor=Actor(case.employee.user_id, "")) as conn:
        with pytest.raises(ValueError, match="身份"):
            directory.me_in_connection(
                conn,
                case.employee.user_id,
                identity=Identity(case.reviewer.user_id, None, "不能冒充其他身份"),
            )


def test_pg_bootstrap_changed_verified_profile_registers_once_then_reads_fresh(
    business_case, monkeypatch
):
    case = business_case
    directory, service = DirectoryService(case.db), workspace(case)
    previous = identity_for(case, case.employee)
    changed = Identity(previous.user_id, previous.email, "经身份服务确认的新姓名")
    registrations = []
    original_register = directory.register_identity

    def register(identity):
        registrations.append(identity)
        original_register(identity)

    monkeypatch.setattr(directory, "register_identity", register)
    value = home_bootstrap(directory, service, changed)
    assert registrations == [changed]
    assert value["me"]["user"]["name"] == changed.display_name
    assert value["profile"] == service.profile(case.employee)


def test_pg_bootstrap_profile_sync_never_reactivates_concurrently_disabled_user(
    business_case, monkeypatch
):
    case = business_case
    directory, service = DirectoryService(case.db), workspace(case)
    previous = identity_for(case, case.employee)
    changed = Identity(previous.user_id, previous.email, "不能借资料同步恢复已停用账号")
    original_register = directory.register_identity

    def disabled_before_sync(identity):
        with case.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("UPDATE accounting.users SET active=0 WHERE id=%s", (identity.user_id,))
        original_register(identity)

    monkeypatch.setattr(directory, "register_identity", disabled_before_sync)
    with pytest.raises(AccountingError) as error:
        home_bootstrap(directory, service, changed)
    assert error.value.status == 403
    with case.psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute(
            "SELECT active,name FROM accounting.users WHERE id=%s", (previous.user_id,)
        ).fetchone() == (0, previous.display_name)


def test_pg_bootstrap_does_not_approve_or_export_unreviewed_drafts(business_case):
    case = business_case
    item = bank_draft(case, case.book, "bootstrap-unreviewed-" + uuid4().hex)

    def accounting_state():
        with case.psycopg.connect(ADMIN_URL) as conn:
            return conn.execute(
                "SELECT (SELECT count(*) FROM accounting.exports WHERE book_id=%s),"
                "(SELECT status FROM accounting.drafts WHERE id=%s),"
                "(SELECT count(*) FROM accounting.audit_events WHERE book_id=%s)",
                (case.book, item["draft_id"], case.book),
            ).fetchone()

    before = accounting_state()
    value = bootstrap(case, case.reviewer)
    assert before[0] == 0 and before[1] == "draft"
    assert accounting_state() == before
    assert not any(task["kind"] == "export" for task in value["tasks"]["tasks"])
    with pytest.raises(AccountingError) as error:
        case.service.export(
            case.reviewer,
            case.book,
            ExportRequest(draft_ids=[item["draft_id"]], idempotency_key=uuid4().hex),
        )
    assert error.value.status == 409
    assert accounting_state() == before
