"""Human decisions and background jobs reference the final pending version."""

import pytest

from app.accounting.models import DraftUpdate
from app.intelligence.requests import ProposalRequest
from test_intelligence_workflow import ai_case, create_event, extract, handoff, draft  # noqa: F401


@pytest.mark.parametrize("decision", ["edit", "adopt"])
def test_human_decision_marker_binds_complete_pending_version(ai_case, decision):
    c = ai_case
    event = create_event(c)
    extract(c, event)
    mapped = handoff(c, event)
    before = draft(c, mapped["draft_id"])
    actor = c.actors["accountant"]
    if decision == "edit":
        result = c.ledger.update_draft(
            actor,
            c.book["id"],
            before["id"],
            DraftUpdate(
                expected_version=before["version"],
                description="会计完整核对并修改摘要",
                lines=before["lines"],
            ),
        )
    else:
        proposal = c.intelligence.propose(
            actor,
            c.book["id"],
            before["id"],
            ProposalRequest(
                expected_version=before["version"], idempotency_key="final-pending-version-test"
            ),
        )
        assert proposal["status"] == "succeeded"
        result = c.intelligence.apply(actor, proposal["id"], before["version"])
    with c.db.transaction(actor=actor) as conn:
        row = conn.execute(
            "SELECT version,last_manual_edit_version FROM drafts WHERE id=?", (before["id"],)
        ).fetchone()
    assert row["last_manual_edit_version"] == row["version"] == result["version"]
    assert result["review_responsibility"]["maker_user_id"] == actor.user_id
    assert result["status"] == "draft"
