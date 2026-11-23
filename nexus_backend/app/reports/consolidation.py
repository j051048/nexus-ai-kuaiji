"""Group management consolidation trial worksheet (Batch 10-1.2).

This module deliberately stays outside the statutory ledger:

* it only reads posted vouchers and voucher lines;
* related-party matching is driven by an explicit firm-level mapping, never by
  guessing from account names or counterparty text;
* manual eliminations live in their own append-only table with an independent
  reviewer, so no single-company voucher, book or period is ever rewritten.

Every amount is an integer number of cents.
"""

import json
from datetime import datetime, timezone
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError

STATEMENT = "集团管理合并试算（非经审计法定合并报表）"

KINDS = (
    "internal_receivable",
    "internal_payable",
    "internal_revenue",
    "internal_cost",
    "internal_funding",
)
KIND_LABELS = {
    "internal_receivable": "内部应收",
    "internal_payable": "内部应付",
    "internal_revenue": "内部收入",
    "internal_cost": "内部成本",
    "internal_funding": "内部资金往来",
}

# Account-code prefix -> elimination dimension. Codes are the four-digit chart
# used by every book in this product; unknown prefixes are not guessed.
CODE_KINDS = {
    "1122": "internal_receivable",
    "1221": "internal_receivable",
    "1231": "internal_receivable",
    "2202": "internal_payable",
    "2203": "internal_payable",
    "2241": "internal_payable",
    "6001": "internal_revenue",
    "6051": "internal_revenue",
    "6301": "internal_revenue",
    "6401": "internal_cost",
    "6402": "internal_cost",
    "1001": "internal_funding",
    "1002": "internal_funding",
    "1012": "internal_funding",
}

# Debit-positive dimensions fund the debit side of the pair.
DEBIT_KINDS = frozenset({"internal_receivable", "internal_cost", "internal_funding"})

# Which two dimensions should offset each other inside one group pair.
COUNTERPARTS = {
    "internal_receivable": "internal_payable",
    "internal_payable": "internal_receivable",
    "internal_revenue": "internal_cost",
    "internal_cost": "internal_revenue",
    "internal_funding": "internal_funding",
}

STANDARD_LABELS = {
    "small_enterprise": "小企业会计准则",
    "enterprise": "企业会计准则",
    "unclassified": "未设置准则口径",
    "mixed": "多准则并行（不合并）",
}


def money(cents):
    whole, cents = divmod(int(cents), 100)
    return f"{whole:,}.{cents:02d}"


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def line_kind(code):
    return CODE_KINDS.get(str(code or "")[:4])


def line_amount(kind, debit_cents, credit_cents):
    debit = int(debit_cents or 0)
    credit = int(credit_cents or 0)
    return debit - credit if kind in DEBIT_KINDS else credit - debit


def classify_lines(rows):
    """Aggregate normalized voucher lines into internal balances per pair."""

    totals = {}
    skipped = []
    for row in rows:
        kind = line_kind(row.get("code"))
        if kind is None:
            continue
        counterparty = row.get("counterparty_company_id") or ""
        company = row.get("company_id") or ""
        if not counterparty or not company:
            skipped.append(
                {
                    "company_id": company,
                    "code": row.get("code") or "",
                    "reason": "unmapped_party",
                    "label": "该凭证未标注集团关联方标识",
                }
            )
            continue
        amount = line_amount(kind, row.get("debit_cents"), row.get("credit_cents"))
        if amount <= 0:
            skipped.append(
                {
                    "company_id": company,
                    "code": row.get("code") or "",
                    "reason": "direction",
                    "label": "科目方向与内部往来方向不一致，暂不纳入对平",
                }
            )
            continue
        key = (company, counterparty, kind)
        totals[key] = totals.get(key, 0) + amount
    balances = [
        {
            "company_id": company,
            "counterparty_company_id": counterparty,
            "kind": kind,
            "kind_label": KIND_LABELS[kind],
            "amount_cents": amount,
            "amount": money(amount),
        }
        for (company, counterparty, kind), amount in sorted(totals.items())
    ]
    return balances, skipped


def pair_up(balances):
    """Pair internal balances of every group pair and surface open differences."""

    index = {}
    for row in balances:
        index[(row["company_id"], row["counterparty_company_id"], row["kind"])] = row[
            "amount_cents"
        ]

    pairs = []
    seen = set()
    for row in balances:
        company = row["company_id"]
        counterparty = row["counterparty_company_id"]
        kind = row["kind"]
        opposite = COUNTERPARTS.get(kind)
        if opposite is None:
            continue
        if kind == opposite:
            # Funding balances are comparable in both directions; keep one entry
            # per unordered pair so the top and bottom side are not counted twice.
            if (counterparty, company, kind) in index and company > counterparty:
                continue
        else:
            if counterparty < company:
                # Report each pair from the smaller company id so a receivable and
                # its payable side collapse into one line.
                company, counterparty = counterparty, company
                kind, opposite = opposite, kind
        signature = (company, counterparty, kind)
        if signature in seen:
            continue
        seen.add(signature)
        left = index.get((company, counterparty, kind), 0)
        right = index.get((counterparty, company, opposite), 0)
        if not left and not right:
            continue
        if left == right:
            status = "balanced"
        elif not left or not right:
            status = "one_sided"
        else:
            status = "difference"
        pairs.append(
            {
                "company_id": company,
                "counterparty_company_id": counterparty,
                "kind": kind,
                "kind_label": KIND_LABELS[kind],
                "counterpart_kind": opposite,
                "counterpart_kind_label": KIND_LABELS[opposite],
                "left_cents": left,
                "right_cents": right,
                "left": money(left),
                "right": money(right),
                "matched_cents": min(left, right),
                "matched": money(min(left, right)),
                "difference_cents": abs(left - right),
                "difference": money(abs(left - right)),
                "status": status,
            }
        )
    pairs.sort(key=lambda row: (row["company_id"], row["counterparty_company_id"], row["kind"]))
    return pairs


def standards_by_company(policy_rows):
    buckets = {}
    for row in policy_rows:
        buckets.setdefault(row["company_id"], set()).add(row.get("standard_kind") or "")
    result = {}
    for company, values in buckets.items():
        values.discard("")
        if not values:
            result[company] = "unclassified"
        elif len(values) == 1:
            result[company] = next(iter(values))
        else:
            result[company] = "mixed"
    return result


def build_worksheet(*, requested, companies, standards, pairs, eliminations):
    """Assemble the worksheet. Approved eliminations only; pending ones are named."""

    names = {row["company_id"]: row.get("name") or row["company_id"] for row in companies}
    included = [cid for cid in requested if cid in names]
    missing = [cid for cid in requested if cid not in names]
    standard_groups = {}
    for cid in included:
        standard = standards.get(cid, "unclassified")
        standard_groups.setdefault(standard, []).append({"company_id": cid, "name": names[cid]})

    approved = [row for row in eliminations if row["status"] == "approved"]
    pending = [row for row in eliminations if row["status"] == "pending"]

    # Pairs only merge inside one accounting-standard scope: mixing standards
    # would silently add differently measured balances together.
    scoped_pairs = []
    not_mixed = []
    for pair in pairs:
        left = standards.get(pair["company_id"], "unclassified")
        right = standards.get(pair["counterparty_company_id"], "unclassified")
        if left == right and left not in {"mixed"}:
            scoped_pairs.append({**pair, "standard": left})
        else:
            not_mixed.append(
                {
                    **pair,
                    "reason": "standard_mismatch",
                    "label": "两家法人准则口径不同，未合并试算",
                    "standards": [
                        STANDARD_LABELS.get(left, left),
                        STANDARD_LABELS.get(right, right),
                    ],
                }
            )

    totals = {}
    for row in approved:
        bucket = totals.setdefault(
            (row["company_id"], row["kind"]),
            {"entered_cents": 0, "entered": 0},
        )
        bucket["entered_cents"] += int(row["amount_cents"])
    elimination_rows = [
        {
            "company_id": company,
            "kind": kind,
            "kind_label": KIND_LABELS.get(kind, kind),
            "entered_cents": value["entered_cents"],
            "entered": money(value["entered_cents"]),
        }
        for (company, kind), value in sorted(totals.items())
    ]

    open_differences = [row for row in scoped_pairs if row["status"] != "balanced"]
    return {
        "statement": STATEMENT,
        "statement_note": "集团管理合并试算，非经审计法定合并报表；仅供内部管理参考，不能对外报送。",
        "requested_companies": list(requested),
        "included_companies": [{"company_id": cid, "name": names[cid]} for cid in included],
        "missing_companies": missing,
        "standard_groups": [
            {
                "standard": standard,
                "standard_label": STANDARD_LABELS.get(standard, standard),
                "companies": rows,
                "merged": standard not in {"mixed"},
            }
            for standard, rows in sorted(standard_groups.items())
        ],
        "pairs": scoped_pairs,
        "not_merged_pairs": not_mixed,
        "open_differences": open_differences,
        "eliminations": elimination_rows,
        "elimination_totals": {
            "approved_cents": sum(row["entered_cents"] for row in elimination_rows),
            "approved": money(sum(row["entered_cents"] for row in elimination_rows)),
            "approved_count": len(approved),
            "pending_count": len(pending),
        },
        "pending_eliminations": [
            {
                "id": row["id"],
                "company_id": row["company_id"],
                "kind": row["kind"],
                "amount_cents": int(row["amount_cents"]),
                "amount": money(int(row["amount_cents"])),
                "label": "尚未独立复核，未进入试算",
            }
            for row in pending
        ],
    }


class ConsolidationService:
    def __init__(self, db, accounting=None):
        self.db = db
        self.access = getattr(accounting, "access", None) or AccessControl()

    # ---------------------------------------------------------------- mapping
    def parties(self, actor, company_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id)
            rows = conn.execute(
                "SELECT * FROM group_related_parties WHERE firm_id=? AND company_id=? "
                "ORDER BY party_key",
                (actor.firm_id, company_id),
            ).fetchall()
        return {"company_id": company_id, "items": [self._party(row) for row in rows]}

    def _party(self, row):
        return {
            "id": row["id"],
            "company_id": row["company_id"],
            "party_key": row["party_key"],
            "party_name": row["party_name"],
            "counterparty_company_id": row["counterparty_company_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_party(
        self,
        actor,
        company_id,
        party_key,
        party_name,
        counterparty_company_id,
        status="active",
        *,
        now=None,
    ):
        party_key = (party_key or "").strip()
        party_name = (party_name or "").strip()
        if not 2 <= len(party_key) <= 120:
            raise AccountingError("关联方标识需 2-120 字", 422)
        if not 1 <= len(party_name) <= 120:
            raise AccountingError("关联方名称需 1-120 字", 422)
        if status not in {"active", "inactive"}:
            raise AccountingError("未知的启用状态", 422)
        if counterparty_company_id == company_id:
            raise AccountingError("关联方不能是本企业自己", 422)
        stamp = now or self._now()
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            self.access.company(conn, actor, counterparty_company_id)
            existing = conn.execute(
                "SELECT * FROM group_related_parties "
                "WHERE firm_id=? AND company_id=? AND party_key=?",
                (actor.firm_id, company_id, party_key),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE group_related_parties SET party_name=?,counterparty_company_id=?,"
                    "status=?,updated_at=? WHERE id=?",
                    (party_name, counterparty_company_id, status, stamp, existing["id"]),
                )
                row_id = existing["id"]
            else:
                row_id = uuid4().hex
                conn.execute(
                    "INSERT INTO group_related_parties(id,firm_id,company_id,party_key,"
                    "party_name,counterparty_company_id,status,created_by,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        row_id,
                        actor.firm_id,
                        company_id,
                        party_key,
                        party_name,
                        counterparty_company_id,
                        status,
                        actor.user_id,
                        stamp,
                        stamp,
                    ),
                )
            self._audit(conn, actor, "consolidation.party.save", row_id, {"party_key": party_key})
            row = conn.execute(
                "SELECT * FROM group_related_parties WHERE id=?", (row_id,)
            ).fetchone()
        return self._party(row)

    # ------------------------------------------------------------ eliminations
    def eliminations(self, actor, period, company_ids):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            rows = self._eliminations(conn, actor, period, company_ids)
        return {
            "period": period,
            "items": [self._elimination(row) for row in rows],
            "statement": STATEMENT,
        }

    def _elimination(self, row):
        return {
            "id": row["id"],
            "company_id": row["company_id"],
            "counterparty_company_id": row["counterparty_company_id"],
            "period": row["period"],
            "kind": row["kind"],
            "kind_label": KIND_LABELS.get(row["kind"], row["kind"]),
            "amount_cents": int(row["amount_cents"]),
            "amount": money(int(row["amount_cents"])),
            "note": row["note"],
            "status": row["status"],
            "state_version": int(row["state_version"]),
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "reviewed_by": row["reviewed_by"],
            "reviewed_at": row["reviewed_at"],
            "review_note": row["review_note"],
        }

    def _eliminations(self, conn, actor, period, company_ids):
        if not company_ids:
            return []
        marks = ",".join("?" for _ in company_ids)
        return conn.execute(
            "SELECT * FROM consolidation_eliminations WHERE firm_id=? AND period=? "
            f"AND company_id IN ({marks}) ORDER BY created_at,id",
            (actor.firm_id, period, *company_ids),
        ).fetchall()

    def create_elimination(
        self,
        actor,
        company_id,
        counterparty_company_id,
        period,
        kind,
        amount_cents,
        note="",
        *,
        now=None,
    ):
        if kind not in KINDS:
            raise AccountingError("未知的抵消分录类型", 422)
        if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
            raise AccountingError("抵消金额必须是正整数分", 422)
        self._check_period(period)
        if counterparty_company_id == company_id:
            raise AccountingError("抵消分录必须跨法人", 422)
        if len(note or "") > 500:
            raise AccountingError("备注不能超过 500 字", 422)
        stamp = now or self._now()
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            self.access.company(conn, actor, counterparty_company_id)
            row_id = uuid4().hex
            conn.execute(
                "INSERT INTO consolidation_eliminations(id,firm_id,company_id,"
                "counterparty_company_id,period,kind,amount_cents,note,status,state_version,"
                "created_by,created_at) VALUES(?,?,?,?,?,?,?,?,'pending',1,?,?)",
                (
                    row_id,
                    actor.firm_id,
                    company_id,
                    counterparty_company_id,
                    period,
                    kind,
                    int(amount_cents),
                    note or "",
                    actor.user_id,
                    stamp,
                ),
            )
            self._audit(
                conn,
                actor,
                "consolidation.elimination.create",
                row_id,
                {"kind": kind, "amount_cents": int(amount_cents)},
            )
            row = conn.execute(
                "SELECT * FROM consolidation_eliminations WHERE id=?", (row_id,)
            ).fetchone()
        return self._elimination(row)

    def review_elimination(self, actor, elimination_id, approve, note="", *, now=None):
        if len(note or "") > 500:
            raise AccountingError("复核意见不能超过 500 字", 422)
        with self.db.transaction(actor=actor) as conn:
            head = conn.execute(
                "SELECT * FROM consolidation_eliminations WHERE id=?", (elimination_id,)
            ).fetchone()
            if not head:
                raise AccountingError("抵消分录不存在或无权访问", 404)
            self.access.company(conn, actor, head["company_id"])
            company_id = head["company_id"]
        stamp = now or self._now()
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            row = conn.execute(
                "SELECT * FROM consolidation_eliminations WHERE id=?", (elimination_id,)
            ).fetchone()
            if not row:
                raise AccountingError("抵消分录不存在或无权访问", 404)
            self.access.company(conn, actor, row["counterparty_company_id"])
            if row["status"] != "pending":
                raise AccountingError("该抵消分录已经复核，不能重复处理", 409)
            if row["created_by"] == actor.user_id:
                raise AccountingError("不能复核自己录入的抵消分录", 403)
            conn.execute(
                "UPDATE consolidation_eliminations SET status=?,reviewed_by=?,reviewed_at=?,"
                "review_note=?,state_version=state_version+1 WHERE id=?",
                (
                    "approved" if approve else "rejected",
                    actor.user_id,
                    stamp,
                    note or "",
                    elimination_id,
                ),
            )
            self._audit(
                conn,
                actor,
                "consolidation.elimination.review",
                elimination_id,
                {"approved": bool(approve)},
            )
            row = conn.execute(
                "SELECT * FROM consolidation_eliminations WHERE id=?", (elimination_id,)
            ).fetchone()
        return self._elimination(row)

    # ---------------------------------------------------------------- worksheet
    def worksheet(self, actor, period, company_ids):
        """Read-only: never opens a write transaction and never touches a ledger."""

        self._check_period(period)
        company_ids = [cid for cid in dict.fromkeys(company_ids or []) if cid]
        if not company_ids:
            raise AccountingError("请至少选择一家法人", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            companies = []
            denied = []
            for company_id in company_ids:
                try:
                    row = self.access.company(conn, actor, company_id)
                except AccountingError:
                    # A company the reader cannot see is reported as missing from
                    # the consolidation scope; it is never silently dropped.
                    denied.append(company_id)
                    continue
                companies.append({"company_id": company_id, "name": row["name"]})
            if not companies:
                raise AccountingError("所选法人中没有可访问的账套范围", 404)
            visible_ids = [row["company_id"] for row in companies]
            marks = ",".join("?" for _ in visible_ids)
            policy_rows = conn.execute(
                "SELECT company_id,standard_kind FROM accounting_policies "
                f"WHERE firm_id=? AND company_id IN ({marks}) AND status='active'",
                (actor.firm_id, *visible_ids),
            ).fetchall()
            standards = standards_by_company([dict(row) for row in policy_rows])
            parties = self._party_index(conn, actor, visible_ids)
            rows = self._internal_lines(conn, actor, period, visible_ids, parties)
            eliminations = [
                dict(row) for row in self._eliminations(conn, actor, period, visible_ids)
            ]
        balances, skipped = classify_lines(rows)
        pairs = pair_up(balances)
        sheet = build_worksheet(
            requested=company_ids,
            companies=companies,
            standards=standards,
            pairs=pairs,
            eliminations=eliminations,
        )
        sheet["period"] = period
        sheet["denied_companies"] = denied
        sheet["balances"] = balances
        sheet["unmapped_lines"] = skipped
        sheet["standards"] = [
            {
                "company_id": cid,
                "standard": standards.get(cid, "unclassified"),
                "standard_label": STANDARD_LABELS.get(standards.get(cid, "unclassified"), ""),
            }
            for cid in company_ids
        ]
        sheet["totals"] = {
            "matched_cents": sum(row["matched_cents"] for row in pairs),
            "matched": money(sum(row["matched_cents"] for row in pairs)),
            "difference_cents": sum(row["difference_cents"] for row in pairs),
            "difference": money(sum(row["difference_cents"] for row in pairs)),
            "open_pairs": len([row for row in pairs if row["status"] != "balanced"]),
        }
        return sheet

    def _party_index(self, conn, actor, company_ids):
        marks = ",".join("?" for _ in company_ids)
        rows = conn.execute(
            "SELECT company_id,party_key,counterparty_company_id FROM group_related_parties "
            f"WHERE firm_id=? AND status='active' AND company_id IN ({marks})",
            (actor.firm_id, *company_ids),
        ).fetchall()
        return {
            (row["company_id"], row["party_key"]): row["counterparty_company_id"] for row in rows
        }

    def _internal_lines(self, conn, actor, period, company_ids, parties):
        marks = ",".join("?" for _ in company_ids)
        rows = conn.execute(
            "SELECT v.company_id,v.book_id,v.period,l.code_snapshot AS code,"
            "l.debit_cents,l.credit_cents,l.aux_json "
            "FROM vouchers v JOIN voucher_lines l ON l.voucher_id=v.id "
            f"WHERE v.firm_id=? AND v.period=? AND v.company_id IN ({marks})",
            (actor.firm_id, period, *company_ids),
        ).fetchall()
        result = []
        for row in rows:
            data = _json(row["aux_json"], {})
            key = str(data.get("related_party_key") or "").strip()
            result.append(
                {
                    "company_id": row["company_id"],
                    "book_id": row["book_id"],
                    "period": row["period"],
                    "code": row["code"],
                    "debit_cents": int(row["debit_cents"]),
                    "credit_cents": int(row["credit_cents"]),
                    "counterparty_company_id": parties.get((row["company_id"], key), ""),
                }
            )
        return result

    # ------------------------------------------------------------------ helpers
    def _check_period(self, period):
        if not isinstance(period, str) or len(period) != 7 or period[4] != "-":
            raise AccountingError("期间须为 YYYY-MM", 422)
        if not period[:4].isdigit() or not period[5:].isdigit() or not 1 <= int(period[5:]) <= 12:
            raise AccountingError("期间须为 YYYY-MM", 422)

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def _audit(self, conn, actor, action, object_id, details):
        conn.execute(
            "INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                actor.firm_id,
                actor.user_id,
                action,
                object_id,
                json.dumps(details, ensure_ascii=False),
                self._now(),
            ),
        )
