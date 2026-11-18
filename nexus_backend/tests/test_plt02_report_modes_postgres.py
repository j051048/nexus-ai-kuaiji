"""PLT02-B: formal/trial report modes and the database publish guard on PostgreSQL."""

import psycopg
import pytest

from app.opening.models import PolicyCreate
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from policy_fixtures import approve_policy_independently
from test_n03_notification_postgres import p0_case, p0_pg  # noqa: F401
from test_p02_formal_read_postgres import formal_case  # noqa: F401


def test_formal_report_persists_policy_binding_and_blocks_trial_publish(formal_case):
    c = formal_case
    reports = ReportService(c.db)
    formal = reports.create(c.manager, c.book["id"], "2026-10")
    assert formal["report"]["mode"] == "formal", formal
    assert formal["report"]["standard_kind"] == "small_enterprise"
    assert formal["report"]["policy_version"] == c.policy["version"]
    assert formal["report"]["policy_approver"] == c.policy["approved_by"]
    assert formal["report"]["policy_approver"] != c.manager.user_id
    assert len(formal["report"]["mapping_fingerprint"]) == 64
    assert formal["report"]["trial_reasons"] == []

    synthetic = c.opening.create_policy(
        c.manager,
        PolicyCreate(
            ledger_id=c.ledger,
            standard_kind="small_enterprise",
            taxpayer_kind="small_scale",
            effective_from="2026-01",
            reason="PLT02-B 合成演练政策",
            is_synthetic=True,
        ),
    )
    approve_policy_independently(c.opening, c.db, c.manager, synthetic["id"])
    trial = reports.create(c.manager, c.book["id"], "2026-10")
    assert trial["report"]["mode"] == "trial"
    assert "合成演练" in "；".join(trial["report"]["trial_reasons"])
    with pytest.raises(Exception) as caught:
        reports.review(
            c.manager,
            trial["report"]["id"],
            ReportCommand(expected_version=1, reason="不得复核试算"),
        )
    assert "试算" in str(caught.value) or "不可" in str(caught.value)

    with pytest.raises(psycopg.errors.RaiseException):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "UPDATE report_runs SET mode='trial', status='published' WHERE id=?",
                (formal["report"]["id"],),
            )
