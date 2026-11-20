"""PLT04-B: explicit fee-receivable variance register.

The register never decides the cause of a difference.  A managed/approved
policy supplies the reason codes; until a reason is configured the row stays
``pending/unresolved`` and cannot be treated as a formal accounting decision.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from ..accounting.domain import AccountingError, Actor
from ..accounting.service import AccountingService, now
from ..ledgers.service import LedgersService
from .relations import SourceRelationService

KINDS = {"underpaid", "overpaid", "unmatched", "cross_period", "many_to_one", "one_to_many"}
UNRESOLVED = "unresolved"


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _reasons(policy) -> list[dict]:
    raw = policy["fee_variance_reasons_json"] or []
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return []
    else:
        value = raw
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if isinstance(item, dict)
        and isinstance(item.get("code"), str)
        and item["code"] != UNRESOLVED
    ]


class FeeVarianceService:
    def __init__(self, db):
        self.db = db
        self.ledgers = LedgersService(db)

    def _active_policy(self, conn, book_id):
        return conn.execute(
            "SELECT p.* FROM accounting_policies p "
            "JOIN ledger_periods lp ON lp.ledger_id=p.ledger_id "
            "WHERE lp.book_id=? AND p.status='active' ORDER BY p.version DESC LIMIT 1",
            (book_id,),
        ).fetchone()

    def _company_for_book(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            return self.ledgers.access.book(conn, actor, book_id, "edit")["company_id"]

    def _company_for_variance(self, actor, variance_id, capability):
        with self.db.transaction(actor=actor) as conn:
            row = self._row(conn, actor, variance_id, capability)
            return self.ledgers.access.book(conn, actor, row["book_id"], capability)["company_id"]

    def record(
        self,
        actor,
        book_id,
        *,
        kind,
        expected_cents,
        actual_cents,
        receivable_id=None,
        bank_source_id=None,
        settlement_source_id=None,
        fee_source_id=None,
        evidence_ref="",
        idempotency_key,
    ):
        company_id = self._company_for_book(actor, book_id)
        kind = str(kind or "").strip()
        if kind not in KINDS:
            raise AccountingError("规费差异类型无效", 422)
        expected_cents = int(expected_cents)
        actual_cents = int(actual_cents)
        if expected_cents < 0 or actual_cents < 0:
            raise AccountingError("差异金额不能为负", 422)
        difference = actual_cents - expected_cents
        if kind == "underpaid" and difference >= 0:
            raise AccountingError("少收差异必须实际到账小于应收", 422)
        if kind == "overpaid" and difference <= 0:
            raise AccountingError("多收差异必须实际到账大于应收", 422)
        if kind == "unmatched" and (expected_cents or not actual_cents):
            raise AccountingError("无对应应收必须应收为 0 且有实际到账", 422)
        idempotency_key = str(idempotency_key or "").strip()
        if not idempotency_key:
            raise AccountingError("差异登记必须提供幂等键", 422)
        fingerprint = _digest(
            {
                "kind": kind,
                "expected": expected_cents,
                "actual": actual_cents,
                "receivable_id": receivable_id,
                "bank_source_id": bank_source_id,
                "settlement_source_id": settlement_source_id,
                "fee_source_id": fee_source_id,
            }
        )
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "edit")
            AccountingService(self.db)._open(book)
            if conn.execute(
                "SELECT 1 FROM internal_period_locks WHERE book_id=? AND period=?",
                (book_id, book["period"]),
            ).fetchone():
                raise AccountingError("该会计期间已锁期，不可登记规费差异", 409)
            relation = None
            if bank_source_id:
                source = conn.execute(
                    "SELECT id,source_kind,amount_cents FROM sources WHERE id=? AND book_id=?",
                    (bank_source_id, book_id),
                ).fetchone()
                if not source:
                    raise AccountingError("实际到账来源不存在或不属于当前账套", 404)
                if source["source_kind"] != "bank":
                    raise AccountingError("实际到账来源不是银行流水", 422)
                if int(source["amount_cents"]) != actual_cents:
                    raise AccountingError("实际到账金额与所选银行流水不一致", 422)
            if settlement_source_id or fee_source_id:
                if not (settlement_source_id and bank_source_id):
                    raise AccountingError("登记来源关系必须同时提供结算单与实际到账流水", 422)
                relation = SourceRelationService(self.db).verify_in(
                    conn,
                    actor,
                    book,
                    settlement_source_id=settlement_source_id,
                    bank_source_id=bank_source_id,
                    fee_source_id=fee_source_id,
                    reason="规费差异登记来源核对",
                )
                if abs(int(relation["difference_cents"])) != abs(difference):
                    raise AccountingError("来源关系差额与差异登记金额不一致", 422)
            if receivable_id:
                row = conn.execute(
                    "SELECT * FROM fee_receivable_items WHERE id=? AND book_id=? AND firm_id=?",
                    (receivable_id, book_id, actor.firm_id),
                ).fetchone()
                if not row:
                    raise AccountingError("应收对象不存在或不属于当前账套", 404)
                if row["status"] != "open":
                    raise AccountingError("应收对象已结清或已停用", 409)
            existing = conn.execute(
                "SELECT * FROM fee_receivable_variances WHERE created_by=? AND idempotency_key=?",
                (actor.user_id, idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != fingerprint:
                    raise AccountingError("同一幂等键的差异内容不一致", 409)
                return self._view(existing)
            policy = self._active_policy(conn, book_id)
            version = policy["version"] if policy else None
            policy_fingerprint = _digest(_reasons(policy) if policy else [])
            stamp = now()
            variance_id = uuid4().hex
            conn.execute(
                "INSERT INTO fee_receivable_variances(id,firm_id,company_id,book_id,period,kind,"
                "receivable_id,bank_source_id,relation_id,expected_cents,actual_cents,difference_cents,status,"
                "reason_code,evidence_ref,policy_version,policy_fingerprint,created_by,created_at,"
                "idempotency_key,request_fingerprint) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'pending',?,?,?,?,?,?,?,?)",
                (
                    variance_id,
                    actor.firm_id,
                    book["company_id"],
                    book_id,
                    book["period"],
                    kind,
                    receivable_id,
                    bank_source_id,
                    relation["id"] if relation else None,
                    expected_cents,
                    actual_cents,
                    difference,
                    UNRESOLVED,
                    str(evidence_ref or "").strip(),
                    version,
                    policy_fingerprint,
                    actor.user_id,
                    stamp,
                    idempotency_key,
                    fingerprint,
                ),
            )
            self._audit(conn, actor, book_id, "fee.variance.record", variance_id, {"kind": kind})
            return self._view(
                conn.execute(
                    "SELECT * FROM fee_receivable_variances WHERE id=?", (variance_id,)
                ).fetchone()
            )

    def list(self, actor, book_id, period=None, *, status=None):
        with self.db.transaction(actor=actor) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "read")
            AccountingService(self.db)._open(book)
            clauses = []
            params = [book_id, actor.firm_id]
            if period:
                clauses.append("period=?")
                params.append(period)
            if status:
                if status not in {"pending", "handled", "reversed"}:
                    raise AccountingError("差异状态无效", 422)
                clauses.append("status=?")
                params.append(status)
            where = " AND ".join(clauses)
            rows = conn.execute(
                "SELECT * FROM fee_receivable_variances WHERE book_id=? AND firm_id=?"
                + (f" AND {where}" if where else "")
                + " ORDER BY created_at,id",
                params,
            ).fetchall()
            items = [self._view(row) for row in rows]
            for item in items:
                if item["bank_source_id"]:
                    source = conn.execute(
                        "SELECT date,description,counterparty,amount_cents FROM sources "
                        "WHERE id=? AND book_id=?",
                        (item["bank_source_id"], book_id),
                    ).fetchone()
                    if source:
                        item["bank_source"] = dict(source)
                if item["processing_started_by"]:
                    assignee = conn.execute(
                        "SELECT name FROM users WHERE id=?", (item["processing_started_by"],)
                    ).fetchone()
                    item["assignee_name"] = assignee["name"] if assignee else None
                item["candidates"] = [
                    {
                        "id": candidate["id"],
                        "amount_cents": candidate["amount_cents"],
                        "counterparty": f"{candidate['driver_name']} · {candidate['vehicle_name']}",
                    }
                    for candidate in conn.execute(
                        "SELECT id,amount_cents,driver_name,vehicle_name FROM fee_receivable_items "
                        "WHERE book_id=? AND period=? AND status='open' "
                        "ORDER BY created_at DESC,id DESC LIMIT 5",
                        (book_id, item["period"]),
                    ).fetchall()
                ]
            return {"items": items}

    def reasons(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "read")
            AccountingService(self.db)._open(book)
            policy = self._active_policy(conn, book_id)
            return {"items": _reasons(policy) if policy else [], "default": UNRESOLVED}

    def start(self, actor, variance_id):
        """Claim a pending variance for investigation.

        The accounting status stays ``pending`` so the variance continues to
        block close; ``processing`` is a workflow projection exposed by
        ``_view`` rather than a second accounting state.
        """
        company_id = self._company_for_variance(actor, variance_id, "edit")
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._row(conn, actor, variance_id, "edit")
            if row["status"] == "reversed":
                raise AccountingError("已撤销差异不能开始处理", 409)
            if row["status"] == "handled":
                raise AccountingError("差异已处理", 409)
            if row["processing_started_by"] and row["processing_started_by"] != actor.user_id:
                raise AccountingError("差异已由其他人员开始处理", 409)
            if not row["processing_started_by"]:
                stamp = now()
                conn.execute(
                    "UPDATE fee_receivable_variances SET processing_started_by=?,processing_started_at=? WHERE id=?",
                    (actor.user_id, stamp, variance_id),
                )
                self._audit(conn, actor, row["book_id"], "fee.variance.start", variance_id, {})
            return self._view(
                conn.execute(
                    "SELECT * FROM fee_receivable_variances WHERE id=?", (variance_id,)
                ).fetchone()
            )

    def handle(self, actor, variance_id, *, reason_code, evidence_ref=""):
        reason_code = str(reason_code or "").strip()
        evidence_ref = str(evidence_ref or "").strip()
        company_id = self._company_for_variance(actor, variance_id, "review")
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._row(conn, actor, variance_id, "review", for_update=True)
            if row["status"] == "reversed":
                raise AccountingError("已撤销差异不能处理", 409)
            if row["status"] == "handled":
                raise AccountingError("差异已处理，不能重复生成凭证", 409)
            relation_id = row["relation_id"] if "relation_id" in row.keys() else None
            if relation_id:
                relation = conn.execute(
                    "SELECT * FROM fee_source_relations WHERE id=? AND book_id=?",
                    (relation_id, row["book_id"]),
                ).fetchone()
                if not relation:
                    raise AccountingError("差异绑定的来源关系不存在或已失效", 409)
                stale = SourceRelationService(self.db).staleness(conn, dict(relation))
                if stale:
                    raise AccountingError(
                        f"差异来源已换版（{stale}），请重新核对来源关系后再生成差异草稿", 409
                    )
            policy = self._active_policy(conn, row["book_id"])
            reasons = _reasons(policy) if policy else []
            codes = {item["code"] for item in reasons}
            if reason_code != UNRESOLVED and reason_code not in codes:
                raise AccountingError("差异原因未在生效政策中配置", 409)
            if reason_code == UNRESOLVED and not evidence_ref:
                raise AccountingError("待查差异必须记录核查依据", 422)
            status = "pending" if reason_code == UNRESOLVED else "handled"
            stamp = now()
            draft_id = None
            if reason_code != UNRESOLVED:
                reason = next((item for item in reasons if item["code"] == reason_code), None)
                debit_account = (reason or {}).get("debit_account")
                credit_account = (reason or {}).get("credit_account")
                if not debit_account or not credit_account:
                    raise AccountingError("差异原因未配置会计科目映射", 409)
                if debit_account == credit_account:
                    raise AccountingError("差异原因的借贷科目不能相同", 409)
                amount = abs(int(row["difference_cents"]))
                if amount <= 0:
                    raise AccountingError("零差额不能生成差异凭证", 409)
                book = conn.execute(
                    "SELECT id AS book_id,period FROM books WHERE id=?", (row["book_id"],)
                ).fetchone()
                source_id, draft_id = uuid4().hex, uuid4().hex
                conn.execute(
                    "INSERT INTO sources(id,book_id,external_id,source_name,date,description,"
                    "counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        source_id,
                        row["book_id"],
                        f"fee-variance:{variance_id}",
                        "规费到账差异",
                        f"{book['period']}-01",
                        f"规费差异 {variance_id}",
                        "银行到账",
                        amount,
                        "income",
                        str(evidence_ref or f"fee-variance:{variance_id}"),
                        row["request_fingerprint"],
                        "bank",
                    ),
                )
                conn.execute(
                    "INSERT INTO drafts(id,book_id,source_id,description,explanation) VALUES(?,?,?,?,?)",
                    (
                        draft_id,
                        row["book_id"],
                        source_id,
                        f"规费到账差异（{reason['label']}）",
                        f"差异 {variance_id}；来源政策 v{policy['version'] if policy else '?'}；"
                        f"处理人 {actor.user_id}；须由另一名有复核权限的会计复核。",
                    ),
                )
                conn.execute(
                    "INSERT INTO draft_lines VALUES(?,?,?,?,?,?)",
                    (draft_id, row["book_id"], 0, debit_account, amount, 0),
                )
                conn.execute(
                    "INSERT INTO draft_lines VALUES(?,?,?,?,?,?)",
                    (draft_id, row["book_id"], 1, credit_account, 0, amount),
                )
                self._audit(
                    conn,
                    actor,
                    row["book_id"],
                    "fee.variance.draft",
                    variance_id,
                    {"draft_id": draft_id, "amount_cents": amount},
                )
            conn.execute(
                "UPDATE fee_receivable_variances SET status=?,reason_code=?,evidence_ref=?,"
                "handled_by=?,handled_at=?,draft_id=? WHERE id=?",
                (status, reason_code, evidence_ref, actor.user_id, stamp, draft_id, variance_id),
            )
            self._audit(
                conn,
                actor,
                row["book_id"],
                "fee.variance.handle",
                variance_id,
                {"reason": reason_code, "status": status},
            )
            return self._view(
                conn.execute(
                    "SELECT * FROM fee_receivable_variances WHERE id=?", (variance_id,)
                ).fetchone()
            )

    def reverse(self, actor, variance_id, *, reason):
        reason = str(reason or "").strip()
        if len(reason) < 3:
            raise AccountingError("撤销差异必须说明原因", 422)
        company_id = self._company_for_variance(actor, variance_id, "review")
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._row(conn, actor, variance_id, "review")
            if row["status"] == "reversed":
                raise AccountingError("差异已撤销", 409)
            if row["draft_id"]:
                raise AccountingError("已生成凭证的差异不能直接撤销，请走更正流程", 409)
            stamp = now()
            conn.execute(
                "UPDATE fee_receivable_variances SET status='reversed',reversed_by=?,reversed_at=?,"
                "reversal_reason=? WHERE id=?",
                (actor.user_id, stamp, reason, variance_id),
            )
            self._audit(
                conn,
                actor,
                row["book_id"],
                "fee.variance.reverse",
                variance_id,
                {"reason": reason},
            )
            return self._view(
                conn.execute(
                    "SELECT * FROM fee_receivable_variances WHERE id=?", (variance_id,)
                ).fetchone()
            )

    def close_blockers(self, conn, book_id, period):
        rows = conn.execute(
            "SELECT * FROM fee_receivable_variances WHERE book_id=? AND period=? AND status='pending'",
            (book_id, period),
        ).fetchall()
        return [
            {
                "code": "fee_variance_pending",
                "message": "规费差异仍待查或待处理",
                "variance_id": row["id"],
                "reason_code": row["reason_code"],
            }
            for row in rows
        ]

    def assignees(self, actor, book_id: str) -> list[dict]:
        with self.db.transaction() as conn:
            self.ledgers.access.book(conn, actor, book_id, "manage")
            rows = conn.execute(
                "SELECT u.id,u.name FROM book_access ba JOIN users u ON u.id=ba.user_id "
                "WHERE ba.book_id=? AND ba.role='editor' AND u.active=1 ORDER BY u.name,u.id",
                (book_id,),
            ).fetchall()
            return [{"id": row["id"], "name": row["name"]} for row in rows]

    def assign(self, actor, variance_id: str, assignee_id: str, reason: str) -> dict:
        company_id = self._company_for_variance(actor, variance_id, "manage")
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._row(conn, actor, variance_id, "manage", for_update=True)
            if row["status"] != "pending":
                raise AccountingError("只有待处理差异可以转交", 409)
            if row["processing_started_by"] == assignee_id:
                raise AccountingError("该差异已由这名人员处理", 409)
            target = conn.execute(
                "SELECT id,name FROM users WHERE id=? AND active=1", (assignee_id,)
            ).fetchone()
            if not target:
                raise AccountingError("没有可接手的有效人员", 409)
            try:
                self.ledgers.access.book(
                    conn, Actor(assignee_id, actor.firm_id), row["book_id"], "edit"
                )
            except AccountingError as error:
                raise AccountingError("目标人员没有该账套的编辑权限", 409) from error
            conn.execute(
                "UPDATE fee_receivable_variances SET processing_started_by=?,"
                " processing_started_at=? WHERE id=? AND firm_id=?",
                (assignee_id, now(), variance_id, actor.firm_id),
            )
            self._audit(
                conn,
                actor,
                row["book_id"],
                "fee.variance.assign",
                variance_id,
                {"assignee_id": assignee_id, "reason": reason},
            )
            updated = conn.execute(
                "SELECT * FROM fee_receivable_variances WHERE id=? AND firm_id=?",
                (variance_id, actor.firm_id),
            ).fetchone()
            return self._view(updated)

    def _row(self, conn, actor, variance_id, capability, *, for_update=False):
        sql = "SELECT * FROM fee_receivable_variances WHERE id=? AND firm_id=?"
        if for_update and self.db.dialect == "postgres":
            sql += " FOR UPDATE"
        row = conn.execute(sql, (variance_id, actor.firm_id)).fetchone()
        if not row:
            raise AccountingError("规费差异不存在或无权访问", 404)
        self.ledgers.access.book(conn, actor, row["book_id"], capability)
        return row

    @staticmethod
    def _view(row):
        view = {
            key: row[key]
            for key in row.keys()
            if key
            in {
                "id",
                "book_id",
                "period",
                "kind",
                "receivable_id",
                "bank_source_id",
                "relation_id",
                "expected_cents",
                "actual_cents",
                "difference_cents",
                "status",
                "state",
                "processing_started_by",
                "processing_started_at",
                "reason_code",
                "evidence_ref",
                "policy_version",
                "handled_by",
                "handled_at",
                "reversed_by",
                "reversed_at",
                "reversal_reason",
                "draft_id",
                "created_by",
                "created_at",
            }
        }
        view["state"] = (
            "processing"
            if row["status"] == "pending" and row["processing_started_by"]
            else row["status"]
        )
        return view

    @staticmethod
    def _audit(conn, actor, book_id, action, object_id, details):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book_id,
                actor.user_id,
                action,
                object_id,
                json.dumps(details, ensure_ascii=False, sort_keys=True),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
