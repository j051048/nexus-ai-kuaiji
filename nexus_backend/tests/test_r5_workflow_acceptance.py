"""R5 local acceptance: real services and SQLite, synthetic identities only.

These tests deliberately reuse the established review and directory fixtures.
No provider/model request, email, production service or PostgreSQL is involved.
"""

import json

import pytest

from app.accounting.admin_identity import AdminIdentityService
from app.accounting.admin_identity_models import AccountRequestCreate
from app.accounting.database import Database
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import (
    CompanyCreate,
    DepartmentCreate,
    DraftUpdate,
    EvidenceRequest,
    ExportRequest,
)
from app.accounting.review import ReviewService
from app.accounting.seed import LOCAL_ACTOR, bootstrap
from app.accounting.service import AccountingService
from app.business.models import BusinessEventCreate
from app.business.service import BusinessService
from test_admin_password_accounts import PasswordProvider
from test_batch_review import confirm, draft, preview, reviewer, status
from test_business import payload
from test_directory import case


@pytest.fixture
def review_case(tmp_path):
    accounting = AccountingService(Database(tmp_path / "r5-review.db"))
    bootstrap(accounting, seed_examples=False)
    return accounting, ReviewService(accounting.db, accounting)


def approval_count(review_case, draft_id):
    with review_case[0].db.transaction() as conn:
        return conn.execute(
            "SELECT count(*) FROM audit_events WHERE action='draft.approve' AND object_id=?",
            (draft_id,),
        ).fetchone()[0]


def test_r5_partial_review_retries_only_failed_book_and_never_replays_success(review_case):
    good = draft(review_case, external_id="r5-good")
    blocked = draft(review_case, external_id="r5-blocked", evidence="")
    old_preview = preview(review_case, [good, blocked])
    receipt = confirm(review_case, old_preview, key="r5-partial-original")
    assert receipt["status"] == "partial"
    assert receipt["approved_count"] == 1 and receipt["failed_book_count"] == 1
    good_version = status(review_case, good)["version"]
    assert status(review_case, blocked)["status"] == "draft"
    assert approval_count(review_case, good["draft_id"]) == 1

    review_case[0].add_evidence(
        LOCAL_ACTOR,
        blocked["book_id"],
        status(review_case, blocked)["source_id"],
        EvidenceRequest(evidence_ref="R5 合成补件，已核对原件"),
    )
    # A repaired fact cannot change the receipt of an already completed command.
    assert confirm(review_case, old_preview, key="r5-partial-original") == receipt
    assert status(review_case, blocked)["status"] == "draft"
    blocked["expected_version"] = status(review_case, blocked)["version"]
    retry_preview = preview(review_case, [blocked])
    retried = confirm(review_case, retry_preview, key="r5-partial-failed-only")
    assert retried["approved_count"] == 1
    assert [item["book_id"] for item in retried["results"]] == [blocked["book_id"]]
    assert confirm(review_case, retry_preview, key="r5-partial-failed-only") == retried
    assert status(review_case, good)["version"] == good_version
    assert approval_count(review_case, good["draft_id"]) == 1
    assert approval_count(review_case, blocked["draft_id"]) == 1
    with review_case[0].db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM exports").fetchone()[0] == 0


def test_r5_stale_preview_and_revoked_reviewer_cannot_write_or_repeat(review_case):
    item = draft(review_case, external_id="r5-stale")
    actor = reviewer(review_case, [item["book_id"]], user="r5-reviewer")
    old_preview = preview(review_case, [item], actor)
    current = review_case[0].get_draft(LOCAL_ACTOR, item["book_id"], item["draft_id"])
    review_case[0].update_draft(
        LOCAL_ACTOR,
        item["book_id"],
        item["draft_id"],
        DraftUpdate(
            expected_version=item["expected_version"],
            description="R5 会计更正后的当前事实",
            lines=current["lines"],
        ),
    )
    refused = confirm(review_case, old_preview, key="r5-stale-confirm", actor=actor)
    assert refused["status"] == "failed"
    assert approval_count(review_case, item["draft_id"]) == 0
    item["expected_version"] = status(review_case, item)["version"]
    current_preview = preview(review_case, [item], actor)
    with review_case[0].db.transaction(write=True) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE book_id=? AND user_id=?",
            (item["book_id"], actor.user_id),
        )
    for _ in range(2):
        with pytest.raises(AccountingError) as caught:
            confirm(review_case, current_preview, key="r5-revoked-confirm", actor=actor)
        assert caught.value.status in (403, 404)
    assert status(review_case, item)["status"] == "draft"
    assert approval_count(review_case, item["draft_id"]) == 0


def test_r5_human_review_required_export_exact_and_cross_company_or_stale_denied(review_case):
    accounting = review_case[0]
    first = draft(review_case, external_id="r5-export")
    other = draft(review_case, external_id="r5-other")
    actor = reviewer(review_case, [first["book_id"]], user="r5-independent-accountant")
    export_request = ExportRequest(
        draft_ids=[first["draft_id"]], idempotency_key="r5-export-reviewed-only"
    )
    with pytest.raises(AccountingError, match="已审核") as caught:
        accounting.export(actor, first["book_id"], export_request)
    assert caught.value.status == 409
    decision = confirm(
        review_case, preview(review_case, [first], actor), key="r5-review-before-export", actor=actor
    )
    assert decision["approved_count"] == 1
    exported = accounting.export(actor, first["book_id"], export_request)
    assert exported["count"] == 1 and "100.00" in exported["csv"]
    assert accounting.export(actor, first["book_id"], export_request) == exported
    with pytest.raises(AccountingError) as cross_company:
        accounting.export(
            actor,
            other["book_id"],
            ExportRequest(draft_ids=[other["draft_id"]], idempotency_key="r5-foreign-export"),
        )
    assert cross_company.value.status in (403, 404)
    with pytest.raises(AccountingError, match="已审核资料已冻结"):
        accounting.add_evidence(
            LOCAL_ACTOR,
            first["book_id"],
            status(review_case, first)["source_id"],
            EvidenceRequest(evidence_ref="不可覆盖已导出冻结事实"),
        )
    stale = draft(review_case, book=first["book_id"], external_id="r5-pending-stale")
    old_preview = preview(review_case, [stale], actor)
    accounting.add_evidence(
        LOCAL_ACTOR,
        first["book_id"],
        status(review_case, stale)["source_id"],
        EvidenceRequest(evidence_ref="预览后新增事实证据"),
    )
    assert confirm(
        review_case, old_preview, key="r5-stale-before-export", actor=actor
    )["status"] == "failed"
    with pytest.raises(AccountingError, match="已审核"):
        accounting.export(
            actor,
            first["book_id"],
            ExportRequest(draft_ids=[stale["draft_id"]], idempotency_key="r5-stale-export"),
        )
    assert accounting.get_export(actor, first["book_id"], exported["id"])["csv"] == exported["csv"]


@pytest.mark.parametrize("role", ["employee", "finance", "admin"])
def test_r5_password_accounts_persist_real_company_role_and_never_regrant_on_replay(case, role):
    directory = case.directory
    company_b = directory.create_company(case.actor, CompanyCreate(name="R5 第二家虚构企业"))
    department_a = directory.create_department(
        case.actor, case.company["id"], DepartmentCreate(name="R5 甲部门")
    )
    department_b = directory.create_department(
        case.actor, company_b["id"], DepartmentCreate(name="R5 乙部门")
    )
    provider = PasswordProvider()
    service = AdminIdentityService(case.db, provider)
    synthetic_password = "R5 synthetic initial password only"
    created = []
    for suffix, company, department in [
        ("a", case.company, department_a),
        ("b", company_b, department_b),
    ]:
        request = AccountRequestCreate(
            idempotency_key=f"r5-account-{role}-{suffix}",
            name=f"R5 {role} {suffix}",
            email=f"r5-{role}-{suffix}@example.invalid",
            company_id=company["id"],
            company_role=role,
            department_id=department["id"],
            provisioning_mode="password",
            initial_password=synthetic_password,
        )
        result = service.create(case.actor, request)
        assert result["request"]["status"] == "completed"
        uid = provider.users[request.email]["user_id"]
        actor = Actor(uid, case.actor.firm_id)
        with case.db.transaction(actor=case.actor) as conn:
            grants = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM company_memberships WHERE user_id=?", (uid,)
                )
            ]
            assert len(grants) == 1
            assert (grants[0]["company_id"], grants[0]["department_id"], grants[0]["role"]) == (
                company["id"], department["id"], role
            )
            assert not conn.execute("SELECT 1 FROM memberships WHERE user_id=?", (uid,)).fetchone()
            persisted = json.dumps([
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM admin_account_requests WHERE id=?", (result["request"]["id"],)
                )
            ])
            assert synthetic_password not in persisted and "initial_password" not in persisted
        assert directory.me(uid)["current_firm_id"] == case.actor.firm_id
        context = BusinessService(case.db).context(actor)
        assert {row["id"] for row in context["companies"]} == {company["id"]}
        other_company = company_b if suffix == "a" else case.company
        with pytest.raises(AccountingError) as denied:
            BusinessService(case.db).create(
                actor,
                BusinessEventCreate(
                    company_id=other_company["id"],
                    template_id="reimbursement",
                    payload=payload(description="不得写入另一企业"),
                ),
            )
        assert denied.value.status in (403, 404)
        created.append((request, result, uid))

    request, original, uid = created[0]
    directory.revoke_company_member(case.actor, case.company["id"], uid)
    replay = service.create(case.actor, request)
    assert replay["request"]["id"] == original["request"]["id"]
    assert len(provider.calls) == 2
    with case.db.transaction(actor=case.actor) as conn:
        assert not conn.execute(
            "SELECT 1 FROM company_memberships WHERE user_id=? AND company_id=?",
            (uid, case.company["id"]),
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM company_memberships WHERE user_id=? AND company_id=?",
            (created[1][2], company_b["id"]),
        ).fetchone()
