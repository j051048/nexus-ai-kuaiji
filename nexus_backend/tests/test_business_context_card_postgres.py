"""The same current-item card on restricted PG roles, not a mock permission check."""

import pytest

from app.accounting.domain import AccountingError
from test_business_postgres import (
    ADMIN_URL,
    APP_URL,
    business_case,
    business_postgres,
    attach,
    submit,
    approve,
)
from test_stage14_agent_workspace_postgres import services

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="requires isolated PG runtime roles"
)


def test_pg_card_scopes_versions_revocation_and_no_writes(business_case):
    c = business_case
    from app.business.models import BusinessEventCreate

    _, agent = services(c)
    event_id = agent.business.create(
        c.employee,
        BusinessEventCreate(
            company_id=c.company,
            template_id="reimbursement",
            payload={
                "occurred_on": "2026-10-03",
                "description": "虚构办公耗材",
                "counterparty": "虚构供方",
                "amount": "12.34",
                "direction": "expense",
                "payment_status": "unpaid",
                "category": "office",
            },
        ),
    )["id"]
    attach(c, event_id)
    submit(c, event_id)
    approve(c, event_id)
    _, agent = services(c)

    def read(actor=c.finance, **changes):
        return agent.business_context(
            actor,
            event_id,
            **(dict(company_id=c.company, period="2026-10", expected_version=3) | changes),
        )

    with c.db.transaction(actor=c.finance) as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM business_actions WHERE event_id=?", (event_id,)
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone()[0]
    card = read()
    assert card["scope"]["event_version"] == 3
    assert card["relationships"]["version"] == 1
    assert len(card["relationships"]["original_roots"]) == 1
    assert card["next_step"]["target"] == "actions" and len(card["evidence"]) == 1
    for actor, changes, status in [
        (c.colleague, {}, 404),
        (c.finance, {"company_id": c.other_company}, 404),
        (c.finance, {"period": "2026-11"}, 404),
        (c.finance, {"expected_version": 2}, 409),
    ]:
        with pytest.raises(AccountingError) as error:
            read(actor, **changes)
        assert error.value.status == status
    assert read(c.employee)["next_step"]["target"] is None
    with c.db.transaction(actor=c.finance) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM business_actions WHERE event_id=?", (event_id,)
            ).fetchone()[0]
            == before
        )
        assert (
            conn.execute("SELECT status FROM business_events WHERE id=?", (event_id,)).fetchone()[0]
            == "department_approved"
        )
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.company_memberships WHERE company_id=%s AND user_id=%s",
            (c.company, c.finance.user_id),
        )
    with pytest.raises(AccountingError) as error:
        read()
    assert error.value.status == 404


@pytest.mark.parametrize("change", ["version", "revocation"])
def test_pg_card_rechecks_after_evidence_read(business_case, monkeypatch, change):
    from app.business.models import BusinessEventCreate, BusinessEventUpdate

    c = business_case
    _, agent = services(c)
    event = agent.business.create(
        c.employee,
        BusinessEventCreate(
            company_id=c.company,
            template_id="reimbursement",
            payload={
                "occurred_on": "2026-10-03",
                "description": "并发资料读取",
                "counterparty": "虚构供方",
                "amount": "12.34",
                "direction": "expense",
                "payment_status": "unpaid",
                "category": "office",
            },
        ),
    )
    original = agent.business._event_json
    changed = False

    def between_reads(conn, actor, current):
        nonlocal changed
        value = original(conn, actor, current)
        if not changed:
            changed = True
            if change == "version":
                agent.business.update(
                    c.employee,
                    event["id"],
                    BusinessEventUpdate(
                        expected_version=event["version"],
                        payload={**event["payload"], "amount": "13.00"},
                    ),
                )
            else:
                with c.db.transaction(write=True, actor=c.owner) as other:
                    other.execute(
                        "DELETE FROM company_memberships WHERE company_id=? AND user_id=?",
                        (c.company, c.finance.user_id),
                    )
        return value

    monkeypatch.setattr(agent.business, "_event_json", between_reads)
    with pytest.raises(AccountingError) as error:
        agent.business_context(
            c.finance,
            event["id"],
            company_id=c.company,
            period="2026-10",
            expected_version=event["version"],
        )
    assert error.value.status == (409 if change == "version" else 404)


def test_pg_card_detects_concurrent_review_and_rereads_after_handling(
    business_case, tmp_path, monkeypatch
):
    from app.business.models import BusinessEventCreate
    from app.business.storage import AttachmentStore
    from app.intelligence.service import IntelligenceService
    from test_intelligence_workflow import FakeProvider
    from test_business_api import attachment_bytes

    c = business_case
    _, agent = services(c)
    storage = AttachmentStore(tmp_path / "originals")
    provider = FakeProvider()
    intelligence = IntelligenceService(c.db, storage, provider)
    agent.business.intelligence = intelligence
    intelligence.company_settings(c.owner, c.company, enabled=True)
    event = agent.business.create(
        c.employee,
        BusinessEventCreate(
            company_id=c.company,
            template_id="reimbursement",
            payload={
                "occurred_on": "2026-10-03",
                "description": "独立核对原件",
                "counterparty": "虚构供方",
                "amount": "12.34",
                "direction": "expense",
                "payment_status": "unpaid",
                "category": "office",
            },
        ),
    )
    event = agent.business.add_attachment(
        c.employee,
        event["id"],
        event["version"],
        storage.put("原件.png", "image/png", attachment_bytes()),
    )
    detail = intelligence.reconcile(c.finance, event["id"])
    finding = next(row for row in detail["findings"] if row["code"].startswith("ocr_missing_"))

    def read():
        return agent.business_context(
            c.finance,
            event["id"],
            company_id=c.company,
            period="2026-10",
            expected_version=event["version"],
        )

    before = read()
    assert any(row["id"] == finding["id"] for row in detail["findings"])
    original = intelligence._assessment
    changed = False

    def concurrent(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            intelligence.resolve(c.finance, finding["id"], "已独立核对本份原件，仅记录核对依据")
        return result

    monkeypatch.setattr(intelligence, "_assessment", concurrent)
    with pytest.raises(AccountingError) as caught:
        read()
    assert caught.value.status == 409
    monkeypatch.setattr(intelligence, "_assessment", original)
    after = read()
    assert after["scope"] == before["scope"]
    assert after["relationships"]["fingerprint"] != before["relationships"]["fingerprint"]
    assert any(row["resolved"] for row in after["relationships"]["issues"])
    assert not after["differences"]
    assert agent.business.get_event(c.finance, event["id"])["status"] == "draft"
    assert provider.extract_calls == provider.proposal_calls == 0
