"""Suspension preserves queued work across every restricted SQL dispatcher."""

import pytest

from app.accounting.admin_models import AdminCompanyStatus
from app.accounting.admin_service import AdminService
from test_stage15_mixed_queue_postgres import (
    ADMIN_URL, APP_URL, WORKER_URL, business_case, business_postgres,
    chat_pg, mixed_pg, processing, claim_processing, finish_processing,
)

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL or not WORKER_URL,
    reason="需要专用 PostgreSQL 管理员、API 和受限 worker 测试连接",
)


@pytest.mark.parametrize("kind", ["ocr", "proposal"])
@pytest.mark.parametrize("entry", ["direct", "combined"])
def test_pg_suspended_queue_does_not_take_other_company_slots_and_can_resume(mixed_pg, kind, entry):
    case = mixed_pg
    paused, active, _ = case.companies
    paused_id = processing(case, paused, kind)
    active_id = processing(case, active, kind)
    admin = AdminService(case.raw.db)
    admin.change_status(case.raw.owner, paused.company, AdminCompanyStatus(
        status="suspended", expected_version=1, reason="合成企业暂停调度验证",
    ))
    claimed = claim_processing(case, kind, entry)
    assert claimed and claimed["id"] == active_id
    assert claim_processing(case, kind, entry) is None
    with case.raw.psycopg.connect(ADMIN_URL) as conn:
        # Both table names come from this test's fixed parameter list.
        queued = conn.execute(
            "SELECT status,attempt_count FROM accounting." + kind + "_jobs WHERE id=%s",
            (paused_id,),
        ).fetchone()
        assert queued == ("queued", 0)
    finish_processing(case, claimed)
    admin.change_status(case.raw.owner, paused.company, AdminCompanyStatus(
        status="active", expected_version=2, reason="合成企业恢复调度验证",
    ))
    resumed = claim_processing(case, kind, entry)
    assert resumed and resumed["id"] == paused_id
    finish_processing(case, resumed)
