"""Ledger review policy and immutable responsibility projection.

Capability is necessary but never substitutes for a version-bound human review.
The database repeats these guards for callers bypassing the HTTP service.
"""

import json
from datetime import datetime, timezone

from .access import AccessControl, FIRM_MANAGERS
from .domain import AccountingError


def stamp():
    return datetime.now(timezone.utc).isoformat()


def mode_label(mode):
    return "独立复核" if mode == "independent" else "单人办理，非独立复核"


class ReviewPolicyService:
    def __init__(self, db):
        self.db = db
        self.access = AccessControl()

    @staticmethod
    def policy_in_transaction(conn, book_id):
        binding = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book_id,)
        ).fetchone()
        if not binding:
            raise AccountingError("账套尚未关联长期账套，不能确定复核政策", 409)
        row = conn.execute(
            "SELECT * FROM ledger_review_policies WHERE ledger_id=? ORDER BY version DESC LIMIT 1",
            (binding["ledger_id"],),
        ).fetchone()
        policy = (
            dict(row)
            if row
            else {
                "ledger_id": binding["ledger_id"],
                "version": 0,
                "mode": "independent",
                "reason": "默认独立复核",
                "confirmed_by": None,
                "created_at": None,
            }
        )
        policy["label"] = mode_label(policy["mode"])
        return policy

    def _view(self, conn, actor, book):
        policy = self.policy_in_transaction(conn, book["id"])
        return {
            "book_id": book["id"],
            "ledger_id": policy["ledger_id"],
            "policy": policy,
            "can_manage": self.access.firm_role(conn, actor) in FIRM_MANAGERS
            and book["permissions"]["manage"],
        }

    def get(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            return self._view(conn, actor, self.access.book(conn, actor, book_id))

    def configure(self, actor, book_id, request):
        with self.db.transaction(actor=actor) as conn:
            company_id = self.access.book(conn, actor, book_id, "manage")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self.access.require_manager(conn, actor)
            book = self.access.book(conn, actor, book_id, "manage")
            current = self.policy_in_transaction(conn, book_id)
            if current["version"] != request.expected_version:
                raise AccountingError("复核政策版本已变化，请重新确认", 409)
            conn.execute(
                "INSERT INTO ledger_review_policies(ledger_id,version,firm_id,company_id,mode,"
                "reason,confirmed_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    current["ledger_id"],
                    current["version"] + 1,
                    book["firm_id"],
                    company_id,
                    request.mode,
                    request.reason,
                    actor.user_id,
                    stamp(),
                ),
            )
            self.audit(
                conn,
                actor,
                book_id,
                "review.policy.configure",
                current["ledger_id"],
                {
                    "previous_version": current["version"],
                    "version": current["version"] + 1,
                    "mode": request.mode,
                    "reason": request.reason,
                },
            )
            return self._view(conn, actor, book)

    @staticmethod
    def audit(conn, actor, book_id, action, entity_id, detail):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book_id,
                actor.user_id,
                action,
                entity_id,
                json.dumps(detail, ensure_ascii=False, sort_keys=True),
                stamp(),
            ),
        )

    @staticmethod
    def _name(conn, user_id):
        row = conn.execute("SELECT name FROM users WHERE id=?", (user_id,)).fetchone()
        return row["name"] if row else None

    def responsibility_in_transaction(self, conn, actor, draft):
        preparation = conn.execute(
            "SELECT * FROM draft_preparations WHERE draft_id=? AND book_id=?",
            (draft["id"], draft["book_id"]),
        ).fetchone()
        receipt = conn.execute(
            "SELECT * FROM draft_review_receipts WHERE draft_id=? AND book_id=? "
            "ORDER BY reviewed_version DESC LIMIT 1",
            (draft["id"], draft["book_id"]),
        ).fetchone()
        complete = draft["status"] in {"approved", "exported"}
        policy = self.policy_in_transaction(conn, draft["book_id"])
        maker = preparation["maker_user_id"] if preparation else None
        mode = receipt["mode"] if complete and receipt else policy["mode"]
        reason = None
        if complete:
            reason = (
                "该版本已完成审核"
                if receipt
                else "历史审核缺少版本责任凭据，请走事实修订后重新复核"
            )
        elif not maker or not preparation or preparation["version"] != draft["version"]:
            reason = "当前待审版本尚未确认制单责任人"
        elif not actor:
            reason = "请登录后确认复核权限"
        else:
            try:
                self.access.book(conn, actor, draft["book_id"], "review")
            except AccountingError:
                reason = "当前账号没有复核权限"
            if not reason and mode == "independent" and maker == actor.user_id:
                reason = "制单责任人不能独立复核本人待审版本，请交另一位有权限的会计"
            if (
                not reason
                and conn.execute(
                    "SELECT 1 FROM business_mappings m JOIN business_events e "
                    "ON e.id=m.event_id AND e.firm_id=m.firm_id AND e.company_id=m.company_id "
                    "WHERE m.draft_id=? AND m.book_id=? AND e.applicant_id=?",
                    (draft["id"], draft["book_id"], actor.user_id),
                ).fetchone()
            ):
                reason = "申请人不能审核本人业务事项的凭证"
        frozen_maker = receipt["maker_user_id"] if complete and receipt else maker
        reviewer = receipt["reviewer_user_id"] if complete and receipt else None
        return {
            "state": ("reviewed" if receipt else "legacy_unverified")
            if complete
            else ("awaiting_review" if maker else "unattributed"),
            "draft_version": draft["version"],
            "maker_user_id": frozen_maker,
            "maker_name": self._name(conn, frozen_maker),
            "content_revision": receipt["content_revision"]
            if complete and receipt
            else preparation["content_revision"]
            if preparation
            else None,
            "policy_version": receipt["policy_version"]
            if complete and receipt
            else policy["version"],
            "mode": mode,
            "label": "历史审核（独立性未核定）" if complete and not receipt else mode_label(mode),
            "independent": bool(receipt["independent"]) if complete and receipt else None,
            "can_review": reason is None,
            "blocked_reason": reason,
            "reviewer_user_id": reviewer,
            "reviewer_name": self._name(conn, reviewer),
            "reviewed_version": receipt["reviewed_version"] if complete and receipt else None,
        }

    def validate_review(self, conn, actor, draft, expected_policy_version=None, review_reason=""):
        value = self.responsibility_in_transaction(conn, actor, draft)
        if not value["can_review"]:
            raise AccountingError(value["blocked_reason"], 403 if value["maker_user_id"] else 409)
        version = value["policy_version"]
        if expected_policy_version != version and not (
            version == 0 and expected_policy_version is None
        ):
            raise AccountingError("复核政策版本已变化，请刷新后重新确认", 409)
        reason = review_reason.strip()
        if value["mode"] == "single_operator" and len(reason) < 3:
            raise AccountingError("单人办理须明确填写本次审核理由，不属于独立复核", 422)
        return version, reason or "独立复核确认"

    @staticmethod
    def approved_receipt(conn, draft):
        reviewed_version = draft["version"] - (2 if draft["status"] == "exported" else 1)
        row = conn.execute(
            "SELECT * FROM draft_review_receipts WHERE draft_id=? AND book_id=? AND reviewed_version=?",
            (draft["id"], draft["book_id"], reviewed_version),
        ).fetchone()
        if not row or draft["status"] not in {"approved", "exported"}:
            raise AccountingError(
                "缺少当前版本的审核责任凭据，请完成事实修订与复核后再记账或导出", 409
            )
        return dict(row)

    def claim(self, actor, book_id, draft_id, request):
        with self.db.transaction(actor=actor) as conn:
            company_id = self.access.book(conn, actor, book_id, "edit")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, book_id, "edit")
            draft = conn.execute(
                "SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)
            ).fetchone()
            preparation = conn.execute(
                "SELECT * FROM draft_preparations WHERE draft_id=? AND book_id=?",
                (draft_id, book_id),
            ).fetchone()
            if not draft:
                raise AccountingError("草稿不存在或无权访问", 404)
            if (
                book["period_status"] != "open"
                or draft["status"] != "draft"
                or draft["version"] != request.expected_version
            ):
                raise AccountingError("草稿或期间状态已变化，请刷新", 409)
            if preparation and preparation["maker_user_id"]:
                raise AccountingError("该版本已有制单责任人，不能通过认领改写责任", 409)
            conn.execute(
                "UPDATE drafts SET preparation_claim_reason=?,version=version+1 WHERE id=? AND book_id=? AND version=?",
                (request.reason, draft_id, book_id, request.expected_version),
            )
            self.audit(
                conn,
                actor,
                book_id,
                "draft.preparation.claim",
                draft_id,
                {"reason": request.reason, "previous_version": request.expected_version},
            )
            from .service import AccountingService

            return AccountingService(self.db)._draft_json(
                conn,
                conn.execute(
                    "SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)
                ).fetchone(),
                actor,
            )
