"""Persist a manual voucher, review its exact content, then post that receipt."""

import hashlib
import json
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.review_policy import ReviewPolicyService, mode_label, stamp
from .models import ManualVoucher


def canonical_manual(request):
    return json.dumps(
        request.model_dump(mode="json", exclude={"review_request_id"}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class ManualReviewService:
    def __init__(self, db):
        self.db = db
        self.policy = ReviewPolicyService(db)
        self.access = self.policy.access

    def _write_scope(self, actor, book_id, capability="review"):
        with self.db.transaction(actor=actor) as conn:
            return self.access.book(conn, actor, book_id, capability)["company_id"]

    def _request(self, conn, actor, identifier, capability="read"):
        row = conn.execute(
            "SELECT * FROM manual_review_requests WHERE id=?", (identifier,)
        ).fetchone()
        if not row:
            raise AccountingError("手工待审凭证不存在或无权访问", 404)
        self.access.book(conn, actor, row["book_id"], capability)
        return row

    def _view(self, conn, actor, row):
        current = self.policy.policy_in_transaction(conn, row["book_id"])
        frozen = conn.execute(
            "SELECT mode FROM ledger_review_policies WHERE ledger_id=? AND version=?",
            (current["ledger_id"], row["policy_version"]),
        ).fetchone()
        mode = frozen["mode"] if frozen else "independent"
        voucher = conn.execute(
            "SELECT id FROM vouchers WHERE review_request_id=?", (row["id"],)
        ).fetchone()
        withdrawal = conn.execute(
            "SELECT * FROM manual_review_withdrawals WHERE request_id=? AND book_id=?",
            (row["id"], row["book_id"]),
        ).fetchone()
        blocked = None
        if row["status"] == "pending":
            if row["policy_version"] != current["version"]:
                blocked = "复核政策已变化，请按当前政策重新提交手工凭证"
            elif mode == "independent" and row["prepared_by"] == actor.user_id:
                blocked = "请另一位有权限的会计独立复核"
        if withdrawal:
            blocked = "该待审版本已由制单人撤回；原始内容和理由保留，不能审核或记账"
        return {
            "id": row["id"],
            "book_id": row["book_id"],
            "version": row["version"],
            "status": row["status"],
            "prepared_by": row["prepared_by"],
            "prepared_name": self.policy._name(conn, row["prepared_by"]),
            "policy_version": row["policy_version"],
            "mode": mode,
            "label": mode_label(mode),
            "independent": bool(row["independent"]) if row["status"] == "approved" else None,
            "content": json.loads(row["content_json"]),
            "reviewed_by": row["reviewed_by"],
            "reviewed_name": self.policy._name(conn, row["reviewed_by"]),
            "review_reason": row["review_reason"],
            "blocked_reason": blocked,
            "posted_voucher_id": voucher["id"] if voucher else None,
            "withdrawn": bool(withdrawal),
            "withdrawal_reason": withdrawal["reason"] if withdrawal else None,
        }

    def list_requests(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id)
            policy = self.policy.policy_in_transaction(conn, book_id)
            requests = conn.execute(
                "SELECT * FROM manual_review_requests WHERE book_id=? ORDER BY created_at DESC,id DESC LIMIT 100",
                (book_id,),
            ).fetchall()
            accounts = conn.execute(
                "SELECT a.id,v.version,v.code,v.name FROM ledger_accounts a "
                "JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=a.current_version "
                "WHERE a.ledger_id=? AND a.status='active' AND v.status='active' AND v.is_leaf=1 ORDER BY v.code",
                (policy["ledger_id"],),
            ).fetchall()
            return {
                "scope": {key: book[key] for key in ("company_id", "firm_id", "period")}
                | {"book_id": book_id},
                "requests": [self._view(conn, actor, row) for row in requests],
                "accounts": [dict(row) for row in accounts],
                "can_prepare": book["permissions"]["edit"],
                "can_review": book["permissions"]["review"],
            }

    def prepare(self, actor, request: ManualVoucher):
        if request.review_request_id:
            raise AccountingError("新待审请求不能指定既有复核凭据", 422)
        company_id = self._write_scope(actor, request.book_id, "edit")
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, request.book_id, "edit")
            if (
                book["period_status"] != "open"
                or conn.execute(
                    "SELECT 1 FROM internal_period_locks WHERE book_id=?", (book["id"],)
                ).fetchone()
            ):
                raise AccountingError("当前期间已关闭或已锁期，不能提交手工凭证", 403)
            if request.voucher_date[:7] != book["period"]:
                raise AccountingError("凭证日期须属于当前期间", 422)
            from .service import PostingService

            posting = PostingService(self.db)
            posting._binding(conn, book["id"])
            lines, _ = posting._validate_lines(conn, [line.model_dump() for line in request.lines])
            # Stable account IDs must belong to this ledger, not merely exist.
            policy = self.policy.policy_in_transaction(conn, book["id"])
            for line in lines:
                if not conn.execute(
                    "SELECT 1 FROM ledger_accounts WHERE id=? AND ledger_id=?",
                    (line["account_id"], policy["ledger_id"]),
                ).fetchone():
                    raise AccountingError("分录科目不属于当前长期账套", 409)
            if request.original_voucher_id or request.relation_kind:
                original = conn.execute(
                    "SELECT * FROM vouchers WHERE id=? AND book_id=?",
                    (request.original_voucher_id, book["id"]),
                ).fetchone()
                if not original or not request.relation_kind:
                    raise AccountingError("调整或冲销必须关联本账套原凭证", 409)
                if request.relation_kind == "reversal":
                    if conn.execute(
                        "SELECT 1 FROM voucher_relations WHERE original_voucher_id=? AND kind='reversal'",
                        (original["id"],),
                    ).fetchone():
                        raise AccountingError("该凭证已经全额冲销", 409)
                    originals = conn.execute(
                        "SELECT * FROM voucher_lines WHERE voucher_id=? ORDER BY line_no",
                        (original["id"],),
                    ).fetchall()
                    wanted = [
                        (
                            r["account_id"],
                            r["account_version"],
                            r["credit_cents"],
                            r["debit_cents"],
                            json.loads(r["aux_json"]),
                            r["source_ref"],
                        )
                        for r in originals
                    ]
                    actual = [
                        (
                            r["account_id"],
                            r["account_version"],
                            r["debit_cents"],
                            r["credit_cents"],
                            json.loads(r["aux_json"]),
                            r["source_ref"],
                        )
                        for r in lines
                    ]
                    if wanted != actual:
                        raise AccountingError("冲销分录必须与原凭证逐行相反", 409)
            payload = canonical_manual(request)
            digest = hashlib.sha256(payload.encode()).hexdigest()
            existing = conn.execute(
                "SELECT * FROM manual_review_requests WHERE book_id=? AND command_key=?",
                (book["id"], request.command_key),
            ).fetchone()
            if existing:
                if (
                    existing["content_fingerprint"] != digest
                    or existing["prepared_by"] != actor.user_id
                ):
                    raise AccountingError("手工请求幂等键已绑定其他制单人或内容", 409)
                return {"request": self._view(conn, actor, existing)}
            identifier = uuid4().hex
            conn.execute(
                "INSERT INTO manual_review_requests(id,book_id,version,prepared_by,status,policy_version,"
                "content_json,content_fingerprint,command_key,reason,independent,review_reason,created_at) "
                "VALUES(?,?,1,?,'pending',?,?,?,?,?,0,'',?)",
                (
                    identifier,
                    book["id"],
                    actor.user_id,
                    policy["version"],
                    payload,
                    digest,
                    request.command_key,
                    request.reason,
                    stamp(),
                ),
            )
            self.policy.audit(
                conn,
                actor,
                book["id"],
                "manual.prepare",
                identifier,
                {"content_fingerprint": digest, "policy_version": policy["version"]},
            )
            return {"request": self._view(conn, actor, self._request(conn, actor, identifier))}

    def approve(self, actor, identifier, request):
        with self.db.transaction(actor=actor) as conn:
            before = self._request(conn, actor, identifier, "review")
            company_id = self.access.book(conn, actor, before["book_id"], "review")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._request(conn, actor, identifier, "review")
            book = self.access.book(conn, actor, row["book_id"], "review")
            if (
                book["period_status"] != "open"
                or conn.execute(
                    "SELECT 1 FROM internal_period_locks WHERE book_id=?", (book["id"],)
                ).fetchone()
            ):
                raise AccountingError("当前期间已锁定，不能审核", 403)
            policy = self.policy.policy_in_transaction(conn, row["book_id"])
            if row["status"] != "pending" or row["version"] != request.expected_version:
                raise AccountingError("手工凭证状态或版本已变化", 409)
            if conn.execute(
                "SELECT 1 FROM manual_review_withdrawals WHERE request_id=?", (identifier,)
            ).fetchone():
                raise AccountingError("该手工待审版本已撤回，不能审核", 409)
            if (
                row["policy_version"] != policy["version"]
                or request.expected_policy_version != policy["version"]
            ):
                raise AccountingError("复核政策已变化，请按新政策重新提交", 409)
            if policy["mode"] == "independent" and row["prepared_by"] == actor.user_id:
                raise AccountingError("制单责任人不能独立复核本人手工凭证", 403)
            conn.execute(
                "UPDATE manual_review_requests SET status='approved',version=version+1,reviewed_by=?,reviewed_at=?,independent=?,review_reason=? WHERE id=? AND version=? AND status='pending'",
                (
                    actor.user_id,
                    stamp(),
                    int(policy["mode"] == "independent"),
                    request.reason,
                    identifier,
                    request.expected_version,
                ),
            )
            self.policy.audit(
                conn,
                actor,
                row["book_id"],
                "manual.review",
                identifier,
                {
                    "mode": policy["mode"],
                    "maker_user_id": row["prepared_by"],
                    "policy_version": policy["version"],
                    "review_reason": request.reason,
                },
            )
            return {"request": self._view(conn, actor, self._request(conn, actor, identifier))}

    def withdraw(self, actor, identifier, request):
        with self.db.transaction(actor=actor) as conn:
            row = self._request(conn, actor, identifier, "edit")
            company_id = self.access.book(conn, actor, row["book_id"], "edit")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._request(conn, actor, identifier, "edit")
            if row["prepared_by"] != actor.user_id:
                raise AccountingError("只有当前待审版本的制单责任人可以撤回", 403)
            if row["status"] != "pending" or row["version"] != request.expected_version:
                raise AccountingError("只能撤回未审核的当前版本，已审凭证请走冲销或调整", 409)
            previous = conn.execute(
                "SELECT * FROM manual_review_withdrawals WHERE request_id=?", (identifier,)
            ).fetchone()
            if previous:
                if (
                    previous["withdrawn_by"] != actor.user_id
                    or previous["reason"] != request.reason
                    or previous["request_version"] != request.expected_version
                ):
                    raise AccountingError("撤回已完成，不能变更原撤回理由或身份", 409)
            else:
                conn.execute(
                    "INSERT INTO manual_review_withdrawals(request_id,book_id,request_version,reason,withdrawn_by,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        identifier,
                        row["book_id"],
                        request.expected_version,
                        request.reason,
                        actor.user_id,
                        stamp(),
                    ),
                )
                self.policy.audit(
                    conn,
                    actor,
                    row["book_id"],
                    "manual.withdraw",
                    identifier,
                    {"version": request.expected_version, "reason": request.reason},
                )
            return {"request": self._view(conn, actor, row)}

    def receipt_in_transaction(self, conn, actor, request):
        if not request.review_request_id:
            raise AccountingError("手工凭证须先提交待审版本并完成复核，不能直接记账", 409)
        row = self._request(conn, actor, request.review_request_id, "review")
        payload = canonical_manual(request)
        if not conn.execute(
            "SELECT 1 FROM review_authority_snapshots WHERE subject_kind='manual' AND subject_id=? AND reviewed_version=? AND book_id=? AND proof_kind='review_time'",
            (row["id"], row["version"], row["book_id"]),
        ).fetchone():
            raise AccountingError("历史手工凭证缺少事务所复核依据，请提交新版本重新复核", 409)
        if (
            row["book_id"] != request.book_id
            or row["status"] != "approved"
            or row["content_json"] != payload
            or row["content_fingerprint"] != hashlib.sha256(payload.encode()).hexdigest()
        ):
            raise AccountingError("手工复核凭据与本次完整记账内容不一致", 409)
        view = self._view(conn, actor, row)
        return {
            "mode": view["mode"],
            "maker_user_id": row["prepared_by"],
            "reviewer_user_id": row["reviewed_by"],
            "policy_version": row["policy_version"],
        }

    def post(self, actor, identifier, request):
        with self.db.transaction(actor=actor) as conn:
            row = self._request(conn, actor, identifier, "review")
            if (
                row["version"] != request.expected_version
                or row["command_key"] != request.command_key
            ):
                raise AccountingError("手工凭证版本或命令编号与审核内容不一致", 409)
            payload = ManualVoucher(**json.loads(row["content_json"]), review_request_id=identifier)
        from .service import PostingService

        return PostingService(self.db).post_manual(actor, payload)
