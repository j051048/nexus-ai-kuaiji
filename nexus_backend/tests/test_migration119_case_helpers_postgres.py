"""Actual restricted PostgreSQL evidence acceptance and atomic failure checks."""

from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.cases.models import CaseRespond, CaseReviewResponse
from test_business_postgres import (
    ADMIN_URL,
    APP_URL,
    business_case,  # noqa: F401
    business_postgres,  # noqa: F401
)
from test_stage8_cases_postgres import command, prepare

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL,
    reason="需要独立 PostgreSQL 管理员及受限 API 测试 DSN",
)


def submitted_response(case):
    cases, event_id, task = prepare(case)
    with case.db.transaction(actor=case.reviewer) as conn:
        previous = dict(
            conn.execute(
                "SELECT fact_version,version,payload FROM business_events WHERE id=?", (event_id,)
            ).fetchone()
        )
        old_id = conn.execute(
            "SELECT id FROM business_attachments WHERE event_id=?", (event_id,)
        ).fetchone()[0]
    stored = {
        "id": uuid4().hex,
        "storage_key": "local119/" + uuid4().hex,
        "filename": "合成清晰原件.pdf",
        "media_type": "application/pdf",
        "sha256": "d" * 64,
        "size_bytes": 100,
    }
    task = cases.add_attachment(case.employee, task["id"], task["version"], uuid4().hex, stored)[
        "case"
    ]
    task = cases.command(
        case.employee,
        task["id"],
        "respond",
        command(task, CaseRespond, fields={"合同编号": "LOCAL-119"}),
    )
    return cases, event_id, task, previous, old_id, stored


def test_pg119_independent_acceptance_appends_evidence_and_verified_fact_atomically(
    business_case,
):
    case = business_case
    cases, event_id, task, previous, old_id, stored = submitted_response(case)
    request = command(
        task, CaseReviewResponse, decision="accept", supersedes_attachment_ids=[old_id]
    )
    accepted = cases.command(case.reviewer, task["id"], "review-response", request)
    assert accepted["state"] == "in_review"
    assert cases.command(case.reviewer, task["id"], "review-response", request) == accepted
    with case.db.transaction(actor=case.reviewer) as conn:
        event = conn.execute(
            "SELECT fact_version,version,payload FROM business_events WHERE id=?", (event_id,)
        ).fetchone()
        assert event["fact_version"] == previous["fact_version"] + 1
        assert event["version"] == previous["version"] + 1
        assert event["payload"] == previous["payload"]
        assert (
            conn.execute(
                "SELECT count(*) FROM business_attachments WHERE event_id=?", (event_id,)
            ).fetchone()[0]
            == 2
        )
        assert (
            conn.execute(
                "SELECT event_id FROM business_attachments WHERE id=?", (stored["id"],)
            ).fetchone()[0]
            == event_id
        )
        assert cases.superseded_attachment_ids_in_transaction(conn, case.reviewer, event_id) == {
            old_id
        }
        assert conn.execute(
            "SELECT fact_version,firm_id,company_id FROM queue_verified_evidence_versions "
            "WHERE event_id=?",
            (event_id,),
        ).fetchone()[:] == (previous["fact_version"] + 1, case.owner.firm_id, case.company)
    # Closing produces the normal company-scoped resume signal; it does not
    # approve a journal or make the superseded original disappear.
    closed = cases.command(case.reviewer, task["id"], "close", command(accepted))
    assert closed["state"] == "closed"
    with case.db.transaction(actor=case.reviewer) as conn:
        assert (
            conn.execute(
                "SELECT kind FROM queue_resume_signals WHERE event_id=?", (event_id,)
            ).fetchone()[0]
            == "case_closed"
        )
        assert (
            conn.execute(
                "SELECT d.status FROM drafts d JOIN business_mappings m ON m.draft_id=d.id "
                "WHERE m.event_id=?",
                (event_id,),
            ).fetchone()[0]
            == "draft"
        )


def test_pg119_acceptance_failure_rolls_back_response_original_and_fact(business_case, monkeypatch):
    case = business_case
    cases, event_id, task, previous, old_id, stored = submitted_response(case)
    accept = cases._accept_evidence

    def fail_after_accept(*args, **kwargs):
        accept(*args, **kwargs)
        raise AccountingError("合成故障：证据入链后中断，验证整体回滚", 409)

    monkeypatch.setattr(cases, "_accept_evidence", fail_after_accept)
    with pytest.raises(AccountingError, match="整体回滚"):
        cases.command(
            case.reviewer,
            task["id"],
            "review-response",
            command(
                task, CaseReviewResponse, decision="accept", supersedes_attachment_ids=[old_id]
            ),
        )
    assert cases.get(case.reviewer, task["id"])["state"] == "response_submitted"
    with case.db.transaction(actor=case.reviewer) as conn:
        assert (
            dict(
                conn.execute(
                    "SELECT fact_version,version,payload FROM business_events WHERE id=?",
                    (event_id,),
                ).fetchone()
            )
            == previous
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM business_attachments WHERE id=?", (stored["id"],)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM queue_verified_evidence_versions WHERE event_id=?",
                (event_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            cases.superseded_attachment_ids_in_transaction(conn, case.reviewer, event_id) == set()
        )


def test_pg119_applicant_cannot_self_accept_and_foreign_company_helper_is_denied(business_case):
    case = business_case
    cases, _, task, _, old_id, _ = submitted_response(case)
    with pytest.raises(AccountingError) as denied:
        cases.command(
            case.employee,
            task["id"],
            "review-response",
            command(
                task, CaseReviewResponse, decision="accept", supersedes_attachment_ids=[old_id]
            ),
        )
    assert denied.value.status in {403, 404}
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(
            write=True, actor=case.owner, company_id=case.other_company, write_scope="company"
        ) as conn:
            conn.execute(
                "SELECT accounting.case_accept_evidence(?,?)", (task["id"], task["version"])
            )
    assert cases.get(case.reviewer, task["id"])["state"] == "response_submitted"


def test_pg119_command_receipt_cannot_use_a_case_from_another_company(business_case):
    case = business_case
    _, _, task = prepare(case)
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(
            write=True, actor=case.owner, company_id=case.other_company, write_scope="company"
        ) as conn:
            conn.execute(
                "INSERT INTO case_commands(firm_id,user_id,idempotency_key,case_id,"
                "request_fingerprint,result_version,created_at) VALUES(?,?,?,?,?,1,?)",
                (
                    case.owner.firm_id,
                    case.owner.user_id,
                    uuid4().hex,
                    task["id"],
                    "a" * 64,
                    "2026-10-18T00:00:00+00:00",
                ),
            )


@pytest.mark.parametrize(
    "helper,args",
    [
        ("case_accept_evidence", (1,)),
        ("case_collection_update", (1, "open", "2026-10-18T00:00:00+00:00")),
    ],
)
def test_pg119_case_capabilities_remain_outside_dispatcher(business_case, helper, args):
    case = business_case
    cases, _, task = prepare(case)
    with pytest.raises(case.psycopg.errors.ObjectNotInPrerequisiteState):
        with case.db.transaction(actor=case.reviewer) as conn:
            conn.execute("SELECT set_config('app.write_dispatch','1',true)")
            conn.execute(
                f"SELECT accounting.{helper}({','.join('?' for _ in range(len(args) + 1))})",
                (task["id"], *args),
            )
    assert cases.get(case.reviewer, task["id"])["state"] == task["state"]
