"""Explicit human batch review: immutable preview, per-book atomic receipts.

The preview is evidence for a human decision. It is never an approval token
that bypasses current permission, amount, period or source checks.
"""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .domain import AccountingError
from .models import cents_to_money
from .queries import health_cte, page_bounds
from .review_models import ReviewConfirmRequest, ReviewPreviewRequest
from .service import AccountingService, fingerprint, now


_UNSET_ROLE = object()


class ReviewService:
    def __init__(self, db, accounting=None):
        self.db = db
        self.accounting = accounting or AccountingService(db)
        self.access = self.accounting.access

    def _authorized_books(self, conn, actor, items):
        # Deliberately precedes any snapshot, command or journal mutation.
        ids = sorted({item["book_id"] for item in items})
        scope, values, role = self._review_scope(conn, actor)
        rows = conn.execute(
            scope + " AND b.id IN (" + ",".join("?" for _ in ids) + ")", values + ids
        ).fetchall()
        if len(rows) != len(ids):
            raise AccountingError("所选账套不存在或当前没有完整复核权限", 404)
        return {
            row["id"]: {
                **dict(row),
                "firm_id": actor.firm_id,
                "permissions": self.access.permissions(role, row["book_role"], row["company_role"]),
            }
            for row in rows
        }

    def _review_scope(self, conn, actor, role=_UNSET_ROLE):
        # Callers that already resolved the live firm role in the same
        # transaction pass it in; no second identity round trip.
        if role is _UNSET_ROLE:
            role = self.access.firm_role(conn, actor)
        scope, values = self.accounting._summary_scope(actor)
        if role not in {"owner", "admin"}:
            scope += " AND a.role='reviewer'"
            if role is None:
                scope += " AND cm.role='finance'"
            elif role != "reviewer":
                scope += " AND 1=0"
        return scope, values, role

    def list_drafts(
        self,
        actor,
        company_id=None,
        period=None,
        template="all",
        risk="all",
        limit=20,
        offset=0,
        book_id=None,
    ):
        page_bounds(limit, offset)
        if template not in {
            "all",
            "bank",
            "reimbursement",
            "procurement_expense",
            "daily_cashflow",
        } or risk not in {"all", "basic_exception", "basic_clear"}:
            raise AccountingError("复核分组筛选参数无效")
        if period is not None and (
            len(period) != 7
            or not period[:4].isdigit()
            or not period[5:].isdigit()
            or period[4] != "-"
            or not 1 <= int(period[5:]) <= 12
        ):
            raise AccountingError("期间格式应为 YYYY-MM")
        with self.db.transaction(actor=actor) as conn:
            scope, values, _ = self._review_scope(conn, actor)
            cte = health_cte(scope, self.db.dialect)
            cte += """, pending AS (
                SELECT h.*,v.name AS book_name,v.company_id,v.company_name,v.period,v.period_status,
                    COALESCE(e.template_id,'bank') AS business_template,
                    CASE WHEN h.basic_exception=1 OR v.period_status<>'open'
                      OR e.applicant_id=? THEN 'basic_exception' ELSE 'basic_clear' END AS review_risk
                FROM health h JOIN visible_books v ON v.id=h.book_id
                LEFT JOIN business_mappings m ON m.draft_id=h.id AND m.book_id=h.book_id
                LEFT JOIN business_events e ON e.id=m.event_id AND e.firm_id=m.firm_id AND e.company_id=m.company_id
                WHERE h.status='draft' AND h.source_inactive=0
            ) """
            values.append(actor.user_id)
            predicate, filters = "1=1", []
            for column, value in (
                ("company_id", company_id),
                ("book_id", book_id),
                ("period", period),
                ("business_template", None if template == "all" else template),
                ("review_risk", None if risk == "all" else risk),
            ):
                if value is not None:
                    predicate += f" AND {column}=?"
                    filters.append(value)
            total = conn.execute(
                cte + "SELECT COUNT(*) FROM pending WHERE " + predicate, values + filters
            ).fetchone()[0]
            rows = conn.execute(
                cte
                + "SELECT * FROM pending WHERE "
                + predicate
                + " ORDER BY company_name,period,business_template,review_risk,book_id,id LIMIT ? OFFSET ?",
                values + filters + [limit, offset],
            ).fetchall()
            companies = [
                dict(row)
                for row in conn.execute(
                    cte
                    + "SELECT DISTINCT company_id AS id,company_name AS name FROM pending ORDER BY name,id",
                    values,
                )
            ]
            items = [
                {
                    "book_id": row["book_id"],
                    "draft_id": row["id"],
                    "expected_version": row["version"],
                    "company_id": row["company_id"],
                    "company_name": row["company_name"],
                    "book_name": row["book_name"],
                    "period": row["period"],
                    "template": row["business_template"],
                    "risk": row["review_risk"],
                    "description": row["description"],
                    "source_date": row["source_date"],
                    "source_amount": cents_to_money(row["amount_cents"]),
                }
                for row in rows
            ]
            return {
                "items": items,
                "companies": companies,
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "total": total,
                    "has_more": offset + len(rows) < total,
                },
                "risk_scope": "basic_checks_only_full_checks_at_preview_and_confirmation",
            }

    def _snapshot(self, conn, actor, item, book):
        draft = self.accounting._editable_draft(
            conn, item["book_id"], item["draft_id"], item["expected_version"]
        )
        value = self.accounting._draft_json(conn, draft, actor)
        issues = list(value["issues"])
        if book["period_status"] != "open":
            issues.append("该会计期间已关闭，不可审核")
        mapping = conn.execute(
            "SELECT e.* FROM business_mappings m JOIN business_events e ON e.id=m.event_id AND e.firm_id=m.firm_id AND e.company_id=m.company_id WHERE m.draft_id=? AND m.book_id=?",
            (item["draft_id"], item["book_id"]),
        ).fetchone()
        evidence_context = None
        if mapping:
            if mapping["applicant_id"] == actor.user_id:
                issues.append("申请人不能审核本人业务事项的凭证")
            assessment = self.accounting._intelligence_guard()._assessment(
                conn, actor, mapping, persist=False
            )
            evidence_context = {
                "fingerprint": assessment["fingerprint"],
                "references": assessment["references"],
                "resolutions": [
                    {
                        "code": f["code"],
                        "resolved": f["resolved"],
                        "reason": f.get("resolution_reason"),
                    }
                    for f in assessment["findings"]
                ],
            }
        cases = getattr(self.accounting, "cases", None)
        source = conn.execute(
            "SELECT * FROM sources WHERE id=? AND book_id=?", (draft["source_id"], draft["book_id"])
        ).fetchone()
        case_revision = (
            cases.evidence_revision_in_transaction(
                conn, actor, source=source, event_id=mapping["id"] if mapping else None
            )
            if cases
            else None
        )
        policy_fingerprint = self.accounting._learning_guard().policy_fingerprint(
            conn, actor, item["book_id"]
        )
        chart = [
            dict(row)
            for row in conn.execute(
                "SELECT code,version,active,auxiliary_requirements FROM accounts WHERE book_id=? ORDER BY code",
                (item["book_id"],),
            )
        ]
        templates = [
            dict(row)
            for row in conn.execute(
                "SELECT id,current_version,active FROM journal_templates WHERE book_id=? ORDER BY id",
                (item["book_id"],),
            )
        ]
        context = {
            "draft": value,
            "period_status": book["period_status"],
            "company_id": book["company_id"],
            "chart": chart,
            "templates": templates,
            "event_facts": {
                "id": mapping["id"],
                "fact_version": mapping["fact_version"],
                "payload": mapping["payload"],
                "status": mapping["status"],
            }
            if mapping
            else None,
            "evidence": evidence_context,
            "case_revision": case_revision,
            "learning_policy": policy_fingerprint,
            "master_policy": self.accounting._ledger_guard().policy_fingerprint_in_transaction(
                conn, actor, item["book_id"]
            ),
            "issues": sorted(set(issues)),
        }
        context["fact_revisions"] = (
            self.accounting._revision_guard().event_fingerprint_in_transaction(conn, actor, mapping)
            if mapping
            else None
        )
        context["settlements"] = (
            self.accounting._settlement_guard().policy_fingerprint_in_transaction(
                conn, actor, source
            )
        )
        legacy = getattr(self.accounting, "legacy_settlements", None)
        context["legacy_settlements"] = (
            legacy.policy_fingerprint_in_transaction(conn, actor, source) if legacy else None
        )
        return {
            **item,
            "company_id": book["company_id"],
            "company_name": book["company_name"],
            "book_name": book["name"],
            "period": book["period"],
            "template": mapping["template_id"] if mapping else "bank",
            "fact_version": mapping["fact_version"] if mapping else None,
            "source_amount": value["source_amount"],
            "source_date": value["source_date"],
            "description": value["description"],
            "evidence_ref": value["evidence_ref"],
            "lines": value["lines"],
            "issues": sorted(set(issues)),
            "ready": not issues,
            "review_fingerprint": fingerprint(context),
        }

    def preview(self, actor, request: ReviewPreviewRequest):
        items = [item.model_dump() for item in request.items]
        with self.db.transaction(write=True, actor=actor) as conn:
            books = self._authorized_books(conn, actor, items)
            snapshots = [
                self._snapshot(conn, actor, item, books[item["book_id"]]) for item in items
            ]
            preview_id = uuid4().hex
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
            conn.execute(
                "INSERT INTO review_previews(id,firm_id,created_by,request_fingerprint,snapshot_json,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                (
                    preview_id,
                    actor.firm_id,
                    actor.user_id,
                    fingerprint(items),
                    json.dumps(snapshots, ensure_ascii=False),
                    now(),
                    expires_at,
                ),
            )
            return self._public_preview(preview_id, expires_at, snapshots)

    @staticmethod
    def _public_preview(preview_id, expires_at, items):
        ready = sum(item["ready"] for item in items)
        return {
            "id": preview_id,
            "expires_at": expires_at,
            "items": items,
            "ready_count": ready,
            "blocked_count": len(items) - ready,
        }

    def _preview(self, conn, actor, preview_id):
        row = conn.execute(
            "SELECT * FROM review_previews WHERE id=? AND firm_id=? AND created_by=?",
            (preview_id, actor.firm_id, actor.user_id),
        ).fetchone()
        if row is None:
            raise AccountingError("复核预览不存在或当前无权访问", 404)
        items = json.loads(row["snapshot_json"])
        self._authorized_books(conn, actor, items)
        return row, items

    def get_preview(self, actor, preview_id):
        with self.db.transaction(actor=actor) as conn:
            row, items = self._preview(conn, actor, preview_id)
            return self._public_preview(preview_id, row["expires_at"], items)

    def _command(self, actor, preview_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            row, items = self._preview(conn, actor, preview_id)
            existing = conn.execute(
                "SELECT * FROM review_confirmations WHERE firm_id=? AND created_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if existing:
                if existing["preview_id"] != preview_id:
                    raise AccountingError("批量复核幂等键已用于其他预览", 409)
                return existing["id"], items
            if conn.execute(
                "SELECT 1 FROM review_confirmations WHERE preview_id=?", (preview_id,)
            ).fetchone():
                raise AccountingError(
                    "该预览已提交，请使用原幂等键读取回执，重新复核须创建新预览", 409
                )
            if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
                raise AccountingError("预览已过期，请重新预览当前凭证", 409)
            command_id = uuid4().hex
            conn.execute(
                "INSERT INTO review_confirmations(id,preview_id,firm_id,created_by,idempotency_key,created_at) VALUES(?,?,?,?,?,?)",
                (
                    command_id,
                    preview_id,
                    actor.firm_id,
                    actor.user_id,
                    request.idempotency_key,
                    now(),
                ),
            )
            return command_id, items

    @staticmethod
    def _receipt(conn, command_id, book_id):
        row = conn.execute(
            "SELECT result_json FROM review_book_receipts WHERE confirmation_id=? AND book_id=?",
            (command_id, book_id),
        ).fetchone()
        return json.loads(row["result_json"]) if row else None

    @staticmethod
    def _save_receipt(conn, command_id, book_id, result):
        conn.execute(
            "INSERT INTO review_book_receipts(confirmation_id,book_id,result_json,created_at) VALUES(?,?,?,?)",
            (command_id, book_id, json.dumps(result, ensure_ascii=False), now()),
        )

    def confirm(self, actor, preview_id, request: ReviewConfirmRequest):
        command_id, items = self._command(actor, preview_id, request)
        grouped = {}
        for item in items:
            grouped.setdefault(item["book_id"], []).append(item)
        results = []
        for book_id, selected in grouped.items():
            try:
                company_id = self.accounting._company_of_book(actor, book_id)
                with self.db.transaction(
                    write=True, actor=actor, company_id=company_id, write_scope="company"
                ) as conn:
                    preview, current_items = self._preview(conn, actor, preview_id)
                    receipt = self._receipt(conn, command_id, book_id)
                    if receipt:
                        results.append(receipt)
                        continue
                    if datetime.fromisoformat(preview["expires_at"]) <= datetime.now(timezone.utc):
                        raise AccountingError("预览已过期，未执行的账套须重新预览", 409)
                    book = self.accounting._book(conn, actor, book_id, reviewer=True)
                    # Validate the whole book before approving any draft. The
                    # shared write transaction also rolls back all later hooks.
                    for item in selected:
                        current = self._snapshot(conn, actor, item, book)
                        if current["review_fingerprint"] != item[
                            "review_fingerprint"
                        ] or fingerprint(current) != fingerprint(item):
                            raise AccountingError(
                                "凭证事实、科目、模板或证据已变化，请重新预览", 409
                            )
                        if not current["ready"]:
                            raise AccountingError(
                                "该账套存在阻断异常：" + "；".join(current["issues"]), 409
                            )
                    for item in selected:
                        self.accounting._approve_in_transaction(
                            conn, actor, book_id, item["draft_id"], item["expected_version"]
                        )
                    result = {
                        "book_id": book_id,
                        "company_name": selected[0]["company_name"],
                        "book_name": selected[0]["book_name"],
                        "status": "succeeded",
                        "approved_ids": [item["draft_id"] for item in selected],
                        "error": None,
                    }
                    self._save_receipt(conn, command_id, book_id, result)
                    self.accounting._audit(
                        conn,
                        actor,
                        book_id,
                        "review.batch.confirm",
                        command_id,
                        {"preview_id": preview_id, "draft_ids": result["approved_ids"]},
                    )
                    results.append(result)
            except AccountingError as error:
                company_id = self.accounting._company_of_book(actor, book_id)
                with self.db.transaction(
                    write=True, actor=actor, company_id=company_id, write_scope="company"
                ) as conn:
                    self._preview(conn, actor, preview_id)
                    existing = self._receipt(conn, command_id, book_id)
                    result = existing or {
                        "book_id": book_id,
                        "company_name": selected[0]["company_name"],
                        "book_name": selected[0]["book_name"],
                        "status": "failed",
                        "approved_ids": [],
                        "error": str(error),
                    }
                    if not existing:
                        self._save_receipt(conn, command_id, book_id, result)
                    results.append(result)
        with self.db.transaction(actor=actor) as conn:
            self._preview(conn, actor, preview_id)
        succeeded = sum(result["status"] == "succeeded" for result in results)
        return {
            "id": command_id,
            "preview_id": preview_id,
            "status": "completed"
            if succeeded == len(results)
            else "partial"
            if succeeded
            else "failed",
            "results": results,
            "approved_count": sum(len(result["approved_ids"]) for result in results),
            "failed_book_count": len(results) - succeeded,
        }
