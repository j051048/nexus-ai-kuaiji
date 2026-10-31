"""Populated scope grants and current-vs-historical review proof in a real dump."""

from uuid import uuid4

from app.accounting.access_policy import AccessPolicyService, StaffAssignment, ContactAccess
from app.accounting.legacy_review import LegacyReviewService, RecheckPrepare, RecheckConfirm
from app.accounting.models import ApprovalRequest, BookCreate, ImportRequest, SourceRow
from app.accounting.review_policy import ReviewPolicyService
from app.accounting.review_policy_models import ReviewPolicyChange
from app.accounting.service import AccountingService


from scripts.recovery_review_contract import TABLE_FIELDS


def seed_permission_recovery(
    db, *, owner, reviewer, observer, company_id, delivery_book, customer, strip_fixture
):
    accounting = AccountingService(db)
    book = accounting.create_book(
        owner, BookCreate(name="当前版本补核恢复合成账套", period="2026-10", company_id=company_id)
    )
    from app.accounting.directory import DirectoryService
    from app.accounting.models import BookMemberGrant

    DirectoryService(db).grant_book_member(
        owner, book["id"], reviewer.user_id, BookMemberGrant(role="reviewer")
    )
    ReviewPolicyService(db).configure(
        owner,
        book["id"],
        ReviewPolicyChange(
            expected_version=0, mode="independent", reason="当前独立复核政策，不能补造历史"
        ),
    )
    policy = AccessPolicyService(db)
    grant = policy.assign(
        owner,
        StaffAssignment(
            user_id=observer.user_id,
            template="auditor",
            company_id=company_id,
            reason="恢复验证指定企业只读",
        ),
    )
    expired = policy.assign(
        owner,
        StaffAssignment(
            user_id=observer.user_id,
            template="accountant",
            company_id=company_id,
            valid_to="2000-01-01T00:00:00Z",
            reason="恢复验证过期授权不得复活",
        ),
    )
    policy.contact_access(
        owner,
        delivery_book["id"],
        ContactAccess(user_id=customer.user_id, reason="独立客户签收范围恢复"),
    )
    # This is a separate recipient capability; remove the old ledger grant.
    from app.accounting.directory import DirectoryService

    DirectoryService(db).revoke_book_member(owner, delivery_book["id"], customer.user_id)
    svc = LegacyReviewService(db)
    requests = []
    for i in range(3):
        accounting.import_sources(
            owner,
            book["id"],
            ImportRequest(
                source_name="恢复合成旧稿",
                rows=[
                    SourceRow(
                        external_id=uuid4().hex,
                        date="2026-10-15",
                        description="办公用品",
                        counterparty="虚构供方",
                        amount="0.30",
                        direction="expense",
                        evidence_ref="仅本地原文",
                    )
                ],
            ),
        )
        accounting.suggestions(owner, book["id"])
        draft = next(
            d for d in accounting.get_book(owner, book["id"])["drafts"] if d["status"] == "draft"
        )
        accounting.approve(
            reviewer,
            book["id"],
            draft["id"],
            ApprovalRequest(
                expected_version=draft["version"],
                expected_policy_version=1,
                review_reason="本地真实人工复核",
            ),
        )
        # Keep one genuine review_time proof; reconstruct two unknown historical proofs.
        if i == 2:
            continue
        strip_fixture("draft", draft["id"])
        source = svc.inspect(owner, book["id"], "draft", draft["id"])
        request = svc.prepare(
            owner,
            book["id"],
            "draft",
            draft["id"],
            RecheckPrepare(
                expected_version=source["source_version"],
                content_fingerprint=source["content_fingerprint"],
                expected_policy_version=source["policy_version"],
                reason="今天承担该版本制单责任",
            ),
        )
        requests.append({"request": request, "source": source})
        if i == 0:
            svc.confirm(
                reviewer, request["id"], RecheckConfirm(reason="今天重新独立核对，不是历史补签")
            )
    return {"book_id": book["id"], "grant": grant, "expired": expired, "requests": requests}


def verify_permission_recovery_http(client, headers, evidence):
    from p01_recovery_business import _response

    book = evidence["book_id"]
    _response(client, "GET", f"/api/books/{book}", headers["editor"])
    _response(client, "GET", f"/api/books/{book}", headers["customer"], expected=404)
    sources = _response(client, "GET", f"/api/books/{book}/legacy-reviews", headers["owner"]).json()
    assert len(sources["items"]) == 1  # completed proof and genuine proof are absent
    pending = evidence["requests"][1]
    path = f"/api/legacy-reviews/{pending['request']['id']}/confirm"
    body = {"reason": "恢复后重新读取原件并独立确认当前版本"}
    _response(client, "POST", path, headers["owner"], body, expected=403)
    _response(client, "POST", path, headers["editor"], body, expected=403)
    _response(client, "POST", path, headers["reviewer"], body)
    for item in evidence["requests"]:
        _response(
            client,
            "POST",
            f"/api/books/{book}/exports",
            headers["reviewer"],
            {
                "draft_ids": [item["source"]["subject_id"]],
                "idempotency_key": "restored-current-review-" + item["source"]["subject_id"],
            },
            expected=200,
        )
    _response(
        client,
        "POST",
        f"/api/firm/access-assignments/{evidence['grant']['id']}/revoke",
        headers["owner"],
        {"reason": "恢复后立即撤销企业范围"},
    )
    _response(client, "GET", f"/api/books/{book}", headers["editor"], expected=404)
    return True
