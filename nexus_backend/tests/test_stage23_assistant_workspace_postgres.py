"""Exercise the aggregated read through native FORCE RLS and revoked grants."""

import pytest

from app.accounting.domain import AccountingError
from app.assistant.actions import AssistantActionService
from app.jobs.service import QueueService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres  # noqa: F401
from test_intelligence_workflow_postgres import pg_ai  # noqa: F401
from test_stage20_assistant_reads import service_and_thread
from test_stage23_assistant_workspace import workspace_parity


pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员及受限运行测试连接"
)


def test_pg_workspace_matches_all_legacy_reads_with_fewer_round_trips(pg_ai):
    workspace_parity(pg_ai)


@pytest.mark.parametrize("change", ["book", "company", "membership", "user"])
def test_pg_workspace_checks_live_scope_after_native_revocation(pg_ai, change):
    c = pg_ai
    service, thread = service_and_thread(c, books=[c.book["id"]])
    actions = AssistantActionService(c.db, service, QueueService(c.db, c.intelligence), c.ledger)
    actor = c.actors["accountant"]
    assert service.workspace(actor, thread["id"], actions)["detail"]["context"]["books"]
    with pytest.raises(AccountingError) as other_user:
        service.workspace(c.manager, thread["id"], actions)
    assert other_user.value.status == 404
    sql, args = {
        "book": (
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.book["id"], actor.user_id),
        ),
        "company": (
            "UPDATE accounting.firm_clients SET status='suspended' WHERE firm_id=%s AND company_id=%s",
            (actor.firm_id, c.company["id"]),
        ),
        "membership": (
            "DELETE FROM accounting.memberships WHERE firm_id=%s AND user_id=%s",
            (actor.firm_id, actor.user_id),
        ),
        "user": ("UPDATE accounting.users SET active=0 WHERE id=%s", (actor.user_id,)),
    }[change]
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(sql, args)
    with pytest.raises(AccountingError) as denied:
        service.workspace(actor, thread["id"], actions)
    assert denied.value.status in (403, 404)
