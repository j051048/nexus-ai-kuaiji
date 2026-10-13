"""Comparable human review packets, pinned to immutable review evidence.

Packets never approve a draft. Every read rechecks the complete authorized
selection; the existing confirmation verifies each selected draft again.
"""
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .domain import AccountingError
from .models import cents_to_money, money_to_cents
from .review import ReviewService
from .review_models import ReviewConfirmRequest
from .service import fingerprint, now


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ReviewPackagesService:
    def __init__(self, db, review=None):
        self.db = db
        self.review = review or ReviewService(db)
        self.accounting = self.review.accounting

    def _request(self, conn, actor, preview_id):
        row = conn.execute("SELECT * FROM review_package_requests WHERE id=? AND firm_id=? AND created_by=?", (preview_id, actor.firm_id, actor.user_id)).fetchone()
        if row is None:
            raise AccountingError("复核包不存在或当前无权访问", 404)
        preview, items = self.review._preview(conn, actor, preview_id)
        return row, preview, items

    def _create(self, conn, actor, items, key, parent=None, snapshots=None):
        document = {"items": items, "parent_preview_id": parent}
        digest = fingerprint(document)
        existing = conn.execute("SELECT * FROM review_package_requests WHERE firm_id=? AND created_by=? AND idempotency_key=?", (actor.firm_id, actor.user_id, key)).fetchone()
        if existing:
            if existing["request_fingerprint"] != digest:
                raise AccountingError("同一复核包请求编号不能更改所选凭证或原复核包", 409)
            self._request(conn, actor, existing["id"])
            return existing["id"], None
        books = self.review._authorized_books(conn, actor, items)
        snapshots = snapshots or [self.review._snapshot(conn, actor, item, books[item["book_id"]]) for item in items]
        identifier, stamp = uuid4().hex, now()
        expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        conn.execute("INSERT INTO review_previews(id,firm_id,created_by,request_fingerprint,snapshot_json,created_at,expires_at) VALUES(?,?,?,?,?,?,?)", (identifier, actor.firm_id, actor.user_id, digest, encode(snapshots), stamp, expires))
        conn.execute("INSERT INTO review_package_requests(id,firm_id,created_by,idempotency_key,parent_preview_id,request_json,request_fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?)", (identifier, actor.firm_id, actor.user_id, key, parent, encode(document), digest, stamp))
        return identifier, snapshots

    def prepare(self, actor, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            items = [item.model_dump() for item in request.items]
            identifier, snapshots = self._create(conn, actor, items, request.idempotency_key)
            return self._project(conn, actor, identifier, snapshots)

    def get(self, actor, preview_id):
        with self.db.transaction(actor=actor) as conn:
            return self._project(conn, actor, preview_id)

    def _current(self, conn, actor, snapshot, book):
        try:
            current = self.review._snapshot(conn, actor, {key: snapshot[key] for key in ("book_id", "draft_id", "expected_version")}, book)
        except AccountingError as error:
            return None, str(error)
        # The digest alone is not enough: a raw SQL caller must not show fake
        # lines or readiness next to a genuine fingerprint obtained elsewhere.
        if fingerprint(current) != fingerprint(snapshot):
            return None, "事实、证据、科目、规则或期间已变化，请重新准备复核包"
        return current, None

    def _comparison(self, conn, actor, snapshot, identities):
        source = conn.execute("SELECT s.*,d.last_manual_edit_version FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE d.id=? AND s.book_id=?", (snapshot["draft_id"], snapshot["book_id"])).fetchone()
        if source is None:
            raise AccountingError("复核包来源已失效", 409)
        raw = source["counterparty"]
        learning = self.accounting._learning_guard()
        identity_key = (snapshot["book_id"], raw)
        if identity_key not in identities:
            identities[identity_key] = learning.resolve_counterparty_in_transaction(conn, actor, snapshot["book_id"], raw)
        resolved = identities[identity_key]
        party = resolved.get("entity_id") if resolved.get("status") == "resolved" else None
        event = conn.execute("SELECT e.* FROM business_mappings m JOIN business_events e ON e.id=m.event_id WHERE m.draft_id=? AND m.book_id=?", (snapshot["draft_id"], snapshot["book_id"])).fetchone()
        origin = conn.execute("SELECT details FROM audit_events WHERE book_id=? AND object_id=? AND action IN ('draft.company_rule.apply','draft.template.apply','draft.suggest') ORDER BY created_at DESC,id DESC LIMIT 1", (snapshot["book_id"], snapshot["draft_id"])).fetchone()
        details = json.loads(origin["details"]) if origin else {}
        rule_id = details.get("rule_id") or details.get("company_rule_id")
        rule_version = details.get("rule_version") or details.get("company_rule_version")
        template_id, template_version = details.get("template_id"), details.get("template_version")
        basis = {"kind": "manual" if source["last_manual_edit_version"] else "company_rule" if rule_id else "template" if template_id else "candidate",
                 "rule_id": rule_id, "rule_version": rule_version, "template_id": template_id, "template_version": template_version,
                 "manual_version": source["last_manual_edit_version"]}
        payload = json.loads(event["payload"]) if event else {}
        recognized = []
        if event:
            from ..intelligence.current_evidence import current_attachments
            attachments = current_attachments(self.db, conn, event, conn.execute("SELECT * FROM business_attachments WHERE event_id=? ORDER BY id", (event["id"],)).fetchall())
            latest = self.accounting._intelligence_guard()._latest(conn, event)
            for attachment in attachments:
                run = latest.get(attachment["id"])
                if not run or run["status"] != "succeeded":
                    continue
                value = json.loads(run["result_json"] or "{}")
                recognized.append({"run_id": run["id"], "tax_amount": value.get("tax_amount"), "total_amount": value.get("total_amount"), "seller": value.get("seller"), "buyer": value.get("buyer")})
        shape = sorted([{ "account_code": line["account_code"], "side": "debit" if money_to_cents(line["debit"]) else "credit", "auxiliary_values": line.get("auxiliary_values") or {} } for line in snapshot["lines"]], key=encode)
        # Unresolved identity is deliberately isolated per draft; matching text
        # alone cannot confer the assurance of an approved master identity.
        key = fingerprint({"company": snapshot["company_id"], "book": snapshot["book_id"], "period": snapshot["period"], "type": snapshot["template"],
                           "party": party or {"unresolved_draft": snapshot["draft_id"]}, "basis": basis, "shape": shape})
        return {"package_id": key, "counterparty_id": party, "counterparty": resolved.get("canonical_value") or raw,
                "party_status": resolved.get("status", "unresolved"), "original_counterparty": raw, "basis": basis, "line_shape": shape,
                "business_tax_amount": payload.get("tax_amount"), "recognized": recognized, "reference": snapshot["evidence_ref"]}

    def _project(self, conn, actor, preview_id, fresh=None):
        request, preview, items = self._request(conn, actor, preview_id)
        books = self.review._authorized_books(conn, actor, items)
        expired = datetime.fromisoformat(preview["expires_at"]) <= datetime.now(timezone.utc)
        confirmed = bool(conn.execute("SELECT 1 FROM review_confirmations WHERE preview_id=?", (preview_id,)).fetchone())
        groups, output, identities = {}, [], {}
        fresh_by_id = {row["draft_id"]: row for row in fresh or []}
        for stored in items:
            # Fresh snapshots were produced under this same write transaction;
            # all later requests, including idempotent replays, revalidate fully.
            current, stale_reason = (fresh_by_id[stored["draft_id"]], None) if stored["draft_id"] in fresh_by_id else self._current(conn, actor, stored, books[stored["book_id"]])
            snapshot = current or stored
            comparison = self._comparison(conn, actor, snapshot, identities)
            state = "stale" if stale_reason or expired or confirmed else "ready" if snapshot["ready"] else "blocked"
            item = {**snapshot, "check_state": state, "stale_reason": stale_reason or ("复核包已过期" if expired else "此预览已提交" if confirmed else None), "comparison": comparison}
            output.append(item)
            key = comparison["package_id"]
            if key not in groups:
                groups[key] = {"id": key, "company_id": snapshot["company_id"], "company_name": snapshot["company_name"], "book_id": snapshot["book_id"], "book_name": snapshot["book_name"], "period": snapshot["period"], "template": snapshot["template"],
                               "counterparty_id": comparison["counterparty_id"], "counterparty": comparison["counterparty"], "party_status": comparison["party_status"], "basis": comparison["basis"], "line_shape": comparison["line_shape"], "items": []}
            groups[key]["items"].append(item)
        packets = list(groups.values())
        for packet in packets:
            amounts = [money_to_cents(row["source_amount"]) for row in packet["items"]]
            total = sum(amounts)
            packet.update(count=len(amounts), total_amount=f"{total // 100}.{total % 100:02d}", min_amount=cents_to_money(min(amounts)), max_amount=cents_to_money(max(amounts)),
                          ready_count=sum(row["check_state"] == "ready" for row in packet["items"]), blocked_count=sum(row["check_state"] == "blocked" for row in packet["items"]), stale_count=sum(row["check_state"] == "stale" for row in packet["items"]))
        return {"id": preview_id, "parent_preview_id": request["parent_preview_id"], "expires_at": preview["expires_at"], "packages": packets, "items": output,
                "ready_count": sum(row["check_state"] == "ready" for row in output), "blocked_count": sum(row["check_state"] == "blocked" for row in output), "stale_count": sum(row["check_state"] == "stale" for row in output),
                "confirmed": confirmed, "scope": "selected_authorized_drafts_full_checks_human_confirmation_required"}

    def select(self, actor, parent_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            _, parent, snapshots = self._request(conn, actor, parent_id)
            if datetime.fromisoformat(parent["expires_at"]) <= datetime.now(timezone.utc):
                raise AccountingError("原复核包已过期，请重新准备", 409)
            chosen = {item["draft_id"]: item for item in snapshots if item["draft_id"] in request.draft_ids}
            if set(chosen) != set(request.draft_ids):
                raise AccountingError("所选凭证不属于原复核包", 409)
            books = self.review._authorized_books(conn, actor, list(chosen.values()))
            validated = []
            for item in chosen.values():
                current, reason = self._current(conn, actor, item, books[item["book_id"]])
                if not current or not current["ready"]:
                    raise AccountingError(reason or "所选复核包仍有阻断项，请先人工处理", 409)
                validated.append(current)
            items = [{key: item[key] for key in ("book_id", "draft_id", "expected_version")} for item in sorted(chosen.values(), key=lambda row: (row["book_id"], row["draft_id"]))]
            identifier, snapshots = self._create(conn, actor, items, request.idempotency_key, parent_id, validated)
            return self._project(conn, actor, identifier, snapshots)

    def confirm(self, actor, preview_id, request: ReviewConfirmRequest):
        # Confirmation preserves the existing per-book transactions and frozen
        # receipts. A parent may include blocked groups; selection creates an
        # independently pinned child so the accountant decides the exact set.
        with self.db.transaction(actor=actor) as conn:
            _, _, items = self._request(conn, actor, preview_id)
            receipt = conn.execute("SELECT 1 FROM review_confirmations WHERE preview_id=? AND firm_id=? AND created_by=? AND idempotency_key=?", (preview_id, actor.firm_id, actor.user_id, request.idempotency_key)).fetchone()
            if not receipt:
                books = self.review._authorized_books(conn, actor, items)
                for item in items:
                    current, reason = self._current(conn, actor, item, books[item["book_id"]])
                    if not current or not current["ready"]:
                        raise AccountingError(reason or "复核包有阻断项，请选择已完成检查的凭证重新预览", 409)
        return self.review.confirm(actor, preview_id, request)
