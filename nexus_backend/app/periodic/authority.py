"""FSC13 closing authority: one formal closing contribution per logical source.

The external table is deliberately correspondence-only.  It never creates a
voucher, never contributes to a report total, and never overwrites an internal
closing that has already been posted.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.invariants import money_to_cents
from ..accounting.service import now
from .models import ClosingEvidenceWrite

AUTHORITY_MODES = ("internal", "tplus_voucher", "hybrid")


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ClosingAuthorityService:
    def __init__(self, db):
        self.db = db
        self.access = AccessControl()

    @staticmethod
    def _authority(conn, company_id):
        row = conn.execute(
            "SELECT id,version,authority_mode,fingerprint,config_json,confirmed_at "
            "FROM tax_pilot_profiles WHERE company_id=? AND status='confirmed' "
            "ORDER BY version DESC LIMIT 1",
            (company_id,),
        ).fetchone()
        if not row:
            return {
                "confirmed": False,
                "mode": "internal",
                "profile_id": None,
                "profile_version": None,
                "profile_fingerprint": None,
                "source_of_truth": "未确认（仅内部，不得作为对外权威）",
            }
        config = json.loads(row["config_json"]) if row["config_json"] else {}
        return {
            "confirmed": True,
            "mode": row["authority_mode"],
            "profile_id": row["id"],
            "profile_version": int(row["version"]),
            "profile_fingerprint": row["fingerprint"],
            "source_of_truth": config.get("source_of_truth") or "内部账簿"
            if row["authority_mode"] == "internal"
            else config.get("source_of_truth") or "外部凭证",
        }

    def get(self, actor, company_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id)
            return self._authority(conn, company_id)

    def summary(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            authority = self._authority(conn, book["company_id"])
            runs = conn.execute(
                "SELECT id,status,input_fingerprint FROM periodic_runs "
                "WHERE book_id=? AND kind='closing' AND period=? ORDER BY created_at DESC",
                (book_id, book["period"]),
            ).fetchall()
            evidence = conn.execute(
                "SELECT * FROM closing_external_evidence WHERE book_id=? AND period=? "
                "ORDER BY created_at DESC",
                (book_id, book["period"]),
            ).fetchall()
            counts = {}
            for row in evidence:
                counts[row["status"]] = counts.get(row["status"], 0) + 1
            return {
                "book_id": book_id,
                "period": book["period"],
                "authority": authority,
                "internal_runs": [dict(row) for row in runs],
                "evidence": [self._view(row) for row in evidence],
                "status_counts": counts,
                "double_count_blocked": authority["mode"] in ("tplus_voucher", "hybrid"),
                "next_action": (
                    "确认客户结转权威模式"
                    if not authority["confirmed"]
                    else (
                        "外部模式：内部结转不得生成，请导入外部证据"
                        if authority["mode"] in ("tplus_voucher", "hybrid")
                        else "内部模式：外部证据只作核对，不参与余额"
                    )
                ),
            }

    def evidence(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, "read")
            rows = conn.execute(
                "SELECT * FROM closing_external_evidence WHERE book_id=? ORDER BY created_at DESC",
                (book_id,),
            ).fetchall()
            return [self._view(row) for row in rows]

    def bind(self, actor, request: ClosingEvidenceWrite):
        amount_cents = money_to_cents(request.amount)
        content = _fingerprint(
            {
                "source_system": request.source_system,
                "source_object": request.source_object,
                "source_id": request.source_id,
                "source_version": request.source_version,
                "amount_cents": amount_cents,
                "details": request.details,
            }
        )
        with self.db.transaction(actor=actor) as conn:
            prebook = self.access.book(conn, actor, request.book_id, "review")
            company_id = prebook["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, request.book_id, "review")
            authority = self._authority(conn, book["company_id"])
            if not authority["confirmed"]:
                raise AccountingError("试点配置中的结转权威模式尚未确认", 409)
            existing = conn.execute(
                "SELECT * FROM closing_external_evidence WHERE book_id=? AND source_system=? "
                "AND source_object=? AND source_id=? AND source_version=?",
                (
                    request.book_id,
                    request.source_system,
                    request.source_object,
                    request.source_id,
                    request.source_version,
                ),
            ).fetchone()
            if existing:
                if existing["content_fingerprint"] != content:
                    raise AccountingError("同一外部结转编号已存在且内容不同", 409)
                return {**self._view(existing), "replayed": True}
            posted = conn.execute(
                "SELECT id FROM periodic_runs WHERE book_id=? AND period=? "
                "AND kind='closing' AND status='posted' LIMIT 1",
                (request.book_id, book["period"]),
            ).fetchone()
            if posted:
                status = "conflict"
            elif authority["mode"] == "tplus_voucher":
                status = "matched"
            elif authority["mode"] == "hybrid":
                status = "needs_review"
            else:
                status = "evidence_only"
            evidence_id = uuid4().hex
            created = now()
            conn.execute(
                "INSERT INTO closing_external_evidence(id,firm_id,company_id,book_id,period,"
                "authority_mode,authority_profile_id,source_system,source_object,source_id,"
                "source_version,content_fingerprint,amount_cents,status,details_json,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    evidence_id,
                    book["firm_id"],
                    book["company_id"],
                    request.book_id,
                    book["period"],
                    authority["mode"],
                    authority["profile_id"],
                    request.source_system,
                    request.source_object,
                    request.source_id,
                    request.source_version,
                    content,
                    amount_cents,
                    status,
                    json.dumps(request.details, ensure_ascii=False, sort_keys=True),
                    actor.user_id,
                    created,
                ),
            )
            conn.execute(
                "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    book["firm_id"],
                    request.book_id,
                    actor.user_id,
                    "closing.external_evidence",
                    evidence_id,
                    json.dumps(
                        {
                            "authority_mode": authority["mode"],
                            "status": status,
                            "source_id": request.source_id,
                            "reason": request.reason,
                            "content_fingerprint": content,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    created,
                ),
            )
            row = conn.execute(
                "SELECT * FROM closing_external_evidence WHERE id=?", (evidence_id,)
            ).fetchone()
            return self._view(row)

    @staticmethod
    def _view(row):
        return {
            "id": row["id"],
            "book_id": row["book_id"],
            "period": row["period"],
            "authority_mode": row["authority_mode"],
            "authority_profile_id": row["authority_profile_id"],
            "source_system": row["source_system"],
            "source_object": row["source_object"],
            "source_id": row["source_id"],
            "source_version": row["source_version"],
            "content_fingerprint": row["content_fingerprint"],
            "amount_cents": int(row["amount_cents"]),
            "status": row["status"],
            "details": (
                json.loads(row["details_json"])
                if isinstance(row["details_json"], str)
                else (row["details_json"] or {})
            ),
            "created_at": row["created_at"],
        }
