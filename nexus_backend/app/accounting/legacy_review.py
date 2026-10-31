"""Present-day rechecks, never a backfill of historical approval authority."""

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .domain import AccountingError
from .review_policy import ReviewPolicyService

Kind = Literal["draft", "manual", "periodic"]


class RecheckPrepare(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)
    expected_version: int = Field(ge=1)
    content_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_policy_version: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=1000)


class RecheckConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)
    reason: str = Field(min_length=3, max_length=1000)


class LegacyReviewService:
    def __init__(self, db):
        self.db = db
        self.policy = ReviewPolicyService(db)
        self.access = self.policy.access

    def _inspect(self, conn, actor, book_id, kind, identifier):
        book = self.access.book(conn, actor, book_id)
        source = conn.execute(
            "SELECT * FROM legacy_review_sources WHERE kind=? AND subject_id=? AND book_id=?",
            (kind, identifier, book_id),
        ).fetchone()
        if not source:
            raise AccountingError("旧稿不存在、状态不适用或无权访问", 404)
        row = dict(source)
        policy = self.policy.policy_in_transaction(conn, book_id)
        row.update(
            policy_version=policy["version"],
            mode="independent" if kind == "periodic" else policy["mode"],
        )
        proof = conn.execute(
            "SELECT * FROM firm_review_evidence WHERE kind=? AND subject_id=? AND book_id=? AND reviewed_version=? AND content_fingerprint=?",
            (kind, identifier, book_id, row["reviewed_version"], row["content_fingerprint"]),
        ).fetchone()
        posted = conn.execute(
            "SELECT id FROM vouchers WHERE book_id=? AND ((?='draft' AND source_kind='draft' AND source_id=?) OR (?='manual' AND review_request_id=?) OR (?='periodic' AND periodic_review_id=?))",
            (book_id, kind, identifier, kind, identifier, kind, identifier),
        ).fetchone()
        row["proof"] = dict(proof) if proof else None
        row["blocked_reason"] = (
            "已正式记账，请通过冲销或调整更正"
            if posted
            else "该版本已有有效事务所复核依据，无需补核"
            if proof
            else "当前期间已关闭或已锁期，不能补核"
            if book["period_status"] != "open"
            or conn.execute(
                "SELECT 1 FROM internal_period_locks WHERE book_id=?", (book_id,)
            ).fetchone()
            else None
        )
        row["can_prepare"] = bool(book["permissions"]["edit"] and not row["blocked_reason"])
        preparation = conn.execute(
            "SELECT * FROM legacy_review_preparations WHERE kind=? AND subject_id=? AND source_version=? AND book_id=? AND policy_version=?",
            (kind, identifier, row["source_version"], book_id, policy["version"]),
        ).fetchone()
        row["preparation"] = dict(preparation) if preparation else None
        row["can_confirm"] = bool(
            preparation
            and not row["blocked_reason"]
            and book["permissions"]["review"]
            and (
                row["mode"] == "single_operator"
                or actor.user_id not in {preparation["maker_user_id"], row["known_maker"]}
            )
        )
        if kind == "draft":
            row["content"] = {
                "draft": dict(
                    conn.execute(
                        "SELECT * FROM drafts WHERE id=? AND book_id=?", (identifier, book_id)
                    ).fetchone()
                ),
                "lines": [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM draft_lines WHERE draft_id=? ORDER BY ordinal", (identifier,)
                    )
                ],
                "original_review_receipts": [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM draft_review_receipts WHERE draft_id=? ORDER BY reviewed_version",
                        (identifier,),
                    )
                ],
            }
        elif kind == "manual":
            import json

            original = dict(
                conn.execute(
                    "SELECT * FROM manual_review_requests WHERE id=?", (identifier,)
                ).fetchone()
            )
            row["content"] = {
                "original_request": original,
                "payload": json.loads(original["content_json"]),
            }
        else:
            row["content"] = {
                "draft": dict(
                    conn.execute(
                        "SELECT * FROM periodic_drafts WHERE id=?", (identifier,)
                    ).fetchone()
                ),
                "lines": [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM periodic_draft_lines WHERE draft_id=? ORDER BY line_no",
                        (identifier,),
                    )
                ],
            }
        return row

    def inspect(self, actor, book_id, kind: Kind, identifier):
        with self.db.transaction(actor=actor) as conn:
            return self._inspect(conn, actor, book_id, kind, identifier)

    def list(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id)
            rows = conn.execute(
                "SELECT s.kind,s.subject_id FROM legacy_review_sources s WHERE s.book_id=? AND NOT EXISTS(SELECT 1 FROM firm_review_evidence e WHERE e.kind=s.kind AND e.subject_id=s.subject_id AND e.book_id=s.book_id AND e.reviewed_version=s.reviewed_version) AND NOT EXISTS(SELECT 1 FROM vouchers v WHERE v.book_id=s.book_id AND ((s.kind='draft' AND v.source_kind='draft' AND v.source_id=s.subject_id) OR (s.kind='manual' AND v.review_request_id=s.subject_id) OR (s.kind='periodic' AND v.periodic_review_id=s.subject_id))) ORDER BY s.kind,s.subject_id LIMIT 100",
                (book_id,),
            ).fetchall()
            return {
                "book_id": book_id,
                "items": [
                    self._inspect(conn, actor, book_id, r["kind"], r["subject_id"]) for r in rows
                ],
            }

    def prepare(self, actor, book_id, kind: Kind, identifier, request: RecheckPrepare):
        with self.db.transaction(actor=actor) as conn:
            company = self.access.book(conn, actor, book_id, "edit")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company, write_scope="company"
        ) as conn:
            self.access.book(conn, actor, book_id, "edit")
            source = self._inspect(conn, actor, book_id, kind, identifier)
            if (
                source["source_version"],
                source["content_fingerprint"],
                source["policy_version"],
            ) != (
                request.expected_version,
                request.content_fingerprint,
                request.expected_policy_version,
            ):
                raise AccountingError("旧稿内容或复核政策已变化，请重新核对", 409)
            previous = source["preparation"]
            if previous:
                if (
                    previous["maker_user_id"] != actor.user_id
                    or previous["reason"] != request.reason
                    or previous["policy_version"] != request.expected_policy_version
                ):
                    raise AccountingError("该版本已有当前制单责任记录，不能改写", 409)
                return previous
            if source["blocked_reason"]:
                raise AccountingError(source["blocked_reason"], 409)
            identifier_new = uuid4().hex
            conn.execute(
                "INSERT INTO legacy_review_preparations(id,kind,subject_id,book_id,source_version,reviewed_version,content_revision,content_fingerprint,policy_version,mode,maker_user_id,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                (
                    identifier_new,
                    kind,
                    identifier,
                    book_id,
                    source["source_version"],
                    source["reviewed_version"],
                    source["content_revision"],
                    source["content_fingerprint"],
                    source["policy_version"],
                    source["mode"],
                    actor.user_id,
                    request.reason,
                ),
            )
            self.policy.audit(
                conn,
                actor,
                book_id,
                "legacy_review.prepare",
                identifier_new,
                {
                    "kind": kind,
                    "subject_id": identifier,
                    "version": source["source_version"],
                    "reason": request.reason,
                },
            )
            return dict(
                conn.execute(
                    "SELECT * FROM legacy_review_preparations WHERE id=?", (identifier_new,)
                ).fetchone()
            )

    def confirm(self, actor, identifier, request: RecheckConfirm):
        with self.db.transaction(actor=actor) as conn:
            row = conn.execute(
                "SELECT * FROM legacy_review_preparations WHERE id=?", (identifier,)
            ).fetchone()
            if not row:
                raise AccountingError("补核申请不存在或无权访问", 404)
            company = self.access.book(conn, actor, row["book_id"], "review")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company, write_scope="company"
        ) as conn:
            self.access.book(conn, actor, row["book_id"], "review")
            previous = conn.execute(
                "SELECT * FROM legacy_review_confirmations WHERE request_id=?", (identifier,)
            ).fetchone()
            if previous:
                if (
                    previous["reviewer_user_id"] != actor.user_id
                    or previous["reason"] != request.reason
                ):
                    raise AccountingError("补核已完成，不能替换复核人或理由", 409)
                return self._confirmation(conn, identifier)
            source = self._inspect(conn, actor, row["book_id"], row["kind"], row["subject_id"])
            if source["blocked_reason"] or (
                source["source_version"],
                source["content_fingerprint"],
                source["policy_version"],
            ) != (row["source_version"], row["content_fingerprint"], row["policy_version"]):
                raise AccountingError(
                    source["blocked_reason"] or "内容或政策已失效，请重新处理", 409
                )
            if not source["can_confirm"]:
                raise AccountingError("请交由另一位有权限的事务所会计复核；不能自认独立复核", 403)
            if conn.execute(
                "SELECT 1 FROM business_mappings bm JOIN business_events e ON e.id=bm.event_id WHERE ?='draft' AND bm.draft_id=? AND e.applicant_id=?",
                (row["kind"], row["subject_id"], actor.user_id),
            ).fetchone():
                raise AccountingError("申请人不能复核本人业务事项", 403)
            conn.execute(
                "INSERT INTO legacy_review_confirmations(request_id,book_id,reviewer_user_id,role_snapshot,reason,reviewed_at) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)",
                (
                    identifier,
                    row["book_id"],
                    actor.user_id,
                    self.access.firm_role(conn, actor),
                    request.reason,
                ),
            )
            self.policy.audit(
                conn,
                actor,
                row["book_id"],
                "legacy_review.confirm",
                identifier,
                {"proof_kind": "current_recheck", "reason": request.reason, "mode": row["mode"]},
            )
            return self._confirmation(conn, identifier)

    @staticmethod
    def _confirmation(conn, identifier):
        row = dict(
            conn.execute(
                "SELECT c.*,p.mode FROM legacy_review_confirmations c JOIN legacy_review_preparations p ON p.id=c.request_id WHERE c.request_id=?",
                (identifier,),
            ).fetchone()
        )
        return row | {"proof_kind": "current_recheck", "independent": row["mode"] == "independent"}
