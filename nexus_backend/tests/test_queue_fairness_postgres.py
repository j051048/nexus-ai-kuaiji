"""Three companies share the real PostgreSQL dispatcher and keep private progress."""

import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.accounting.domain import Actor, AccountingError
from app.accounting.models import CompanyCreate, CompanyMemberGrant
from app.business.models import BusinessEventCreate
from app.jobs.requests import BatchRequest
from test_queue_worker_postgres import (
    ADMIN_URL, APP_URL, WORKER_URL, business_case, business_postgres, pg_ai, pg_queue,
)

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL,
                              reason="需要真实PG管理员、API和独立worker测试连接")


def new_company_input(case, name):
    company = case.directory.create_company(case.manager, CompanyCreate(name=name))
    person_id = "queue-client-" + uuid4().hex
    person = SimpleNamespace(user_id=person_id, display_name=name + "员工", email=person_id + "@example.test")
    case.directory.register_identity(person)
    case.directory.grant_company_member(case.manager, company["id"], CompanyMemberGrant(email=person.email, role="employee"))
    actor = Actor(person_id, case.manager.firm_id)
    case.intelligence.company_settings(case.manager, company["id"], enabled=True)
    event = case.business.create(actor, BusinessEventCreate(company_id=company["id"], template_id="procurement_expense", payload={
        "occurred_on": "2026-10-03", "description": name + "合成办公采购", "counterparty": "耗材商店",
        "amount": "0.30", "direction": "expense", "payment_status": "unpaid", "category": "office",
    }))
    original = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(original, "PNG")
    attachment = case.storage.put(name + "合成票据.png", "image/png", original.getvalue())
    event = case.business.add_attachment(actor, event["id"], event["version"], attachment)
    return company, actor, event


def batch_request(company_id, events):
    return BatchRequest(company_id=company_id, items=[{
        "event_id": event["id"], "attachment_id": event["attachments"][0]["id"], "expected_version": event["version"],
    } for event in events], idempotency_key="pg-fair-" + uuid4().hex)


def distinct_first_company_event(case, sequence):
    """Fair scheduling measures separate documents, independent of OCR caching."""
    actor = case.actors["employee"]
    event = case.business.create(actor, BusinessEventCreate(company_id=case.company["id"], template_id="reimbursement", payload={
        "occurred_on": "2026-10-03", "description": f"大批次第{sequence}张独立合成票据", "counterparty": "耗材商店",
        "amount": "0.30", "direction": "expense", "payment_status": "unpaid", "category": "office",
    }))
    original = io.BytesIO()
    picture = Image.new("RGB", (40, 20), "white")
    picture.putpixel((0, 0), (sequence, 0, 0))
    picture.save(original, "PNG")
    attachment = case.storage.put(f"独立合成票据-{sequence}.png", "image/png", original.getvalue())
    return case.business.add_attachment(actor, event["id"], event["version"], attachment)


def test_real_pg_three_companies_share_first_turns_and_keep_progress_private(pg_queue):
    case = pg_queue
    documents = [distinct_first_company_event(case, number) for number in range(1, 4)]
    assert len({event["attachments"][0]["sha256"] for event in documents}) == 3
    large = case.queue.enqueue(case.actors["employee"], batch_request(case.company["id"], documents))
    second_company, second_actor, second_event = new_company_input(case, "公平调度第二企业")
    second = case.queue.enqueue(second_actor, batch_request(second_company["id"], [second_event]))
    third_company, third_actor, third_event = new_company_input(case, "公平调度第三企业")
    third = case.queue.enqueue(third_actor, batch_request(third_company["id"], [third_event]))
    assert case.provider.extract_calls == 0

    for _ in range(3):
        assert case.worker.run_once()

    large_view = case.queue.get_batch(case.actors["employee"], large["id"])
    assert large_view["progress"]["succeeded"] == 1 and large_view["progress"]["pending"] == 2
    for actor, batch in ((second_actor, second), (third_actor, third)):
        view = case.queue.get_batch(actor, batch["id"])
        assert view["progress"]["total"] == 1 and view["progress"]["succeeded"] == 1
        assert view["status"] == "completed" and view["progress"]["percent"] == 100
        assert case.queue.list_batches(actor)["total"] == 1
        with pytest.raises(AccountingError) as invisible:
            case.queue.get_batch(actor, large["id"])
        assert invisible.value.status == 404
    assert case.provider.extract_calls == 3

    # Complete the large client's remaining work so no active jobs survive to
    # compete with another SQL test sharing this deliberately small test DB.
    assert case.worker.run_once() and case.worker.run_once()
    assert not case.worker.run_once()
    assert case.queue.get_batch(case.actors["employee"], large["id"])["progress"]["succeeded"] == 3
    assert case.provider.extract_calls == 5
