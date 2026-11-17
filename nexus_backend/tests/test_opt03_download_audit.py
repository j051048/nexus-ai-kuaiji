"""OPT03-C: successful attachment/export downloads leave audit rows."""

from __future__ import annotations

from uuid import uuid4

import pytest

from test_stage12_intake import ai_case, attach_intake, create_intake, intake  # noqa: F401


def _audit_rows(c, action):
    with c.db.transaction(actor=c.manager) as conn:
        return conn.execute(
            "SELECT object_id,details FROM audit_events WHERE action=? ORDER BY id", (action,)
        ).fetchall()


def test_intake_attachment_download_is_audited_and_denied_is_not(ai_case):
    c = ai_case
    service = intake(c)
    draft = attach_intake(c, service, create_intake(c, service))
    attachment_id = draft["attachments"][0]["id"]

    content, metadata = service.download(c.actors["employee"], attachment_id)
    assert content and metadata["sha256"]
    rows = _audit_rows(c, "attachment.download")
    assert len(rows) == 1
    assert rows[0]["object_id"] == attachment_id
    assert "sha256" in rows[0]["details"] and len(rows[0]["details"]) < 500

    from app.accounting.domain import AccountingError

    with pytest.raises(AccountingError) as denied:
        service.download(c.actors["peer"], attachment_id)
    assert denied.value.status == 404
    assert len(_audit_rows(c, "attachment.download")) == 1


def test_audit_write_failure_blocks_attachment_bytes(ai_case, monkeypatch):
    c = ai_case
    service = intake(c)
    draft = attach_intake(c, service, create_intake(c, service))
    attachment_id = draft["attachments"][0]["id"]

    def fail(*args, **kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr("app.intake.service.write_download_audit", fail)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        service.download(c.actors["employee"], attachment_id)
    assert _audit_rows(c, "attachment.download") == []
