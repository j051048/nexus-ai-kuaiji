"""One current-review/source binding for rule memory and the RSI adapter."""

import json

from app.accounting.domain import AccountingError
from app.accounting.review_policy import ReviewPolicyService


class RuleEvidenceError(AccountingError):
    def __init__(self, code):
        self.code = code
        super().__init__(code, 409)


def reviewed_source(service, conn, actor, book, sample):
    book_id = book["id"]
    draft = conn.execute(
        "SELECT * FROM drafts WHERE id=? AND book_id=?", (sample["draft_id"], book_id)
    ).fetchone()
    if not draft:
        raise RuleEvidenceError("sample_review_binding")
    receipt = ReviewPolicyService.approved_receipt(conn, draft)
    authority = ReviewPolicyService(service.db).responsibility_in_transaction(conn, actor, draft)
    if (
        receipt["reviewed_version"] != sample["reviewed_version"]
        or receipt["reviewer_user_id"] != sample["reviewed_by"]
        or authority["state"] != "reviewed"
    ):
        raise RuleEvidenceError("sample_review_binding")
    if not sample["ordinary_rule_eligible"] or sample["source_kind"] != "business":
        raise RuleEvidenceError("unsupported_sample_domain")
    source = conn.execute(
        "SELECT * FROM sources WHERE id=? AND book_id=?",
        (sample["snapshot"]["source"]["id"], book_id),
    ).fetchone()
    event = conn.execute(
        "SELECT e.* FROM business_events e JOIN business_mappings m ON m.event_id=e.id "
        "WHERE m.book_id=? AND m.draft_id=?",
        (book_id, sample["draft_id"]),
    ).fetchone()
    if (
        not source
        or not event
        or event["company_id"] != book["company_id"]
        or event["fact_version"] != sample["snapshot"]["fact_version"]
        or json.loads(event["payload"]) != sample["snapshot"]["payload"]
        or source["fingerprint"] != sample["snapshot"]["source"]["fingerprint"]
    ):
        raise RuleEvidenceError("sample_current_facts_stale")
    attachments = [
        dict(r)
        for r in conn.execute(
            "SELECT id,sha256 FROM business_attachments WHERE event_id=? ORDER BY id",
            (event["id"],),
        )
    ]
    return {
        "sample": sample,
        "source": dict(source),
        "event": dict(event),
        "attachments": attachments,
        "receipt": receipt,
    }
