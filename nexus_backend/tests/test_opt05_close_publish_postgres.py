"""OPT05-E: real PostgreSQL evidence that close publish is retry-safe.

The fixture builds a zero-activity book through the real services, including the
current subledger applicability declaration.  Crossing the publish boundary is
done only through CloseService; no direct status UPDATE is used.
"""

import pytest

from app.b04_close.models import CloseCommand
from app.b04_close.service import CloseService
from app.reports.service import ReportService
from app.subledger.models import ControlApplicability
from app.subledger.service import SubledgerService
from test_monthly_delivery_postgres import p0_case, p0_pg  # noqa: F401
from test_p0_close_upload_postgres import _reviewed_close


@pytest.fixture
def reviewed_close(p0_case, monkeypatch):  # noqa: F811
    c = p0_case
    original_create = ReportService.create

    def create_after_applicability(service, actor, book_id, period):
        SubledgerService(c.db).set_control_applicability(
            c.manager,
            ControlApplicability(
                book_id=book_id,
                status="not_applicable",
                effective_from="2026-10",
                reason="本机隔离库零余额企业本期无应收应付控制科目发生",
            ),
        )
        return original_create(service, actor, book_id, period)

    with monkeypatch.context() as patch:
        patch.setattr(ReportService, "create", create_after_applicability)
        ready = _reviewed_close(c)
        yield ready


def test_close_publish_is_idempotent_after_lost_response(reviewed_close, p0_case):
    c = p0_case
    ready = reviewed_close
    close = CloseService(c.db)
    run_id = ready.run["id"]
    command = CloseCommand(expected_version=ready.run["version"], reason="本机独立锁期")

    locked = close.lock(c.reviewer, run_id, command)
    assert locked["close"]["status"] == "locked"

    first = close.publish(
        c.reviewer,
        run_id,
        CloseCommand(expected_version=locked["close"]["version"], reason="发布月结"),
    )
    assert first["close"]["status"] == "published"
    assert first["close"]["published_by"] == c.reviewer.user_id

    retried = close.publish(
        c.reviewer,
        run_id,
        CloseCommand(expected_version=locked["close"]["version"], reason="响应丢失后重试"),
    )
    assert retried["close"]["status"] == "published"
    assert retried["close"]["published_by"] == c.reviewer.user_id
    assert retried["close"]["version"] == first["close"]["version"]

    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='close.publish' AND object_id=%s",
                (run_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM report_runs WHERE id=%s AND status='published'",
                (ready.report_id,),
            ).fetchone()[0]
            == 1
        )
