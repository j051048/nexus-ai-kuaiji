"""PLT08-B：三类高频问题的确定调查路径。

读取哪些来源由服务端固定，模型不能挑对象、不能写 SQL、不能给 URL。
每条路径都先重授权再读取；无权限或未加载的来源显式进入「未检查的范围」，
既不猜测也不拿别的企业的数据补洞。
"""

from __future__ import annotations

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from . import investigation
from .answer_contract import ABSTAIN, money_text

SETTLEMENT_KIND = "settlement_difference"
DEFAULT_LIMIT = 50
UNCHECKED_LABELS = {
    "fee_evidence": "手续费/扣款凭据（本期未提供）",
    "fee_evidence_coverage": "手续费凭据覆盖期间（现有凭据不在本期）",
    "fee_evidence_mismatch": "手续费凭据与差额匹配（金额不一致）",
    "verified_source_relation": "结算单、到账流水与手续费凭据的关系（未核对）",
    "verified_fee_relation": "手续费凭据与已核对来源关系的绑定（不匹配）",
}
OWNER_BY_CODE = (
    ("review", "reviewer"),
    ("review_", "reviewer"),
    ("manager", "manager"),
    ("cashier", "cashier"),
    ("payment", "cashier"),
)


def _owner_role(code):
    text = str(code or "")
    for token, role in OWNER_BY_CODE:
        if token in text:
            return role
    return "accountant"


def _href(book_id, period, **params):
    base = f"/workspace/accounting?book={book_id}&period={period}"
    for key, value in params.items():
        if value:
            base += f"&{key}={value}"
    return base


class BookReader:
    """真实账套读取：每条方法都重新校验账套权限与期间。"""

    def __init__(self, db, actor, book_id, period, *, now=None):
        self.db = db
        self.actor = actor
        self.book_id = book_id
        self.period = period
        self.now = now
        self.access = AccessControl()

    def book(self):
        with self.db.transaction(actor=self.actor) as conn:
            book = self.access.book(conn, self.actor, self.book_id, "read")
            if book["period"] != self.period:
                raise AccountingError("调查月份与账套不一致", 422)
            return dict(book)

    def close_blockers(self):
        with self.db.transaction(actor=self.actor) as conn:
            self.access.book(conn, self.actor, self.book_id, "read")
            return investigation.READERS["close_blockers"](conn, self.book_id, self.period)

    def delivery_status(self):
        with self.db.transaction(actor=self.actor) as conn:
            self.access.book(conn, self.actor, self.book_id, "read")
            return investigation.READERS["delivery_status"](conn, self.book_id, self.period)

    def settlement(self, settlement_source_id, bank_source_id, fee_source_id=None):
        from . import analysis

        return analysis.analyze(
            self.db,
            self.actor,
            self.book_id,
            self.period,
            SETTLEMENT_KIND,
            settlement_source_id=settlement_source_id,
            bank_source_id=bank_source_id,
            fee_source_id=fee_source_id,
        )

    def account_rows(self, account_code, limit=DEFAULT_LIMIT):
        with self.db.transaction(actor=self.actor) as conn:
            self.access.book(conn, self.actor, self.book_id, "read")
            rows = conn.execute(
                "SELECT v.id AS voucher_id,v.voucher_no,v.voucher_date,v.description,"
                "SUM(l.debit_cents-l.credit_cents) AS amount_cents,"
                "v.content_fingerprint FROM voucher_lines l "
                "JOIN vouchers v ON v.id=l.voucher_id "
                "WHERE v.book_id=? AND l.code_snapshot=? "
                "GROUP BY v.id,v.voucher_no,v.voucher_date,v.description,v.content_fingerprint "
                "ORDER BY v.voucher_date,v.voucher_no LIMIT ?",
                (self.book_id, account_code, limit + 1),
            ).fetchall()
            total = conn.execute(
                "SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) AS amount "
                "FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
                "WHERE v.book_id=? AND l.code_snapshot=?",
                (self.book_id, account_code),
            ).fetchone()
        return {
            "account_code": account_code,
            "total_cents": int(total["amount"]),
            "rows": [dict(row) for row in rows],
        }

    def effective_rules(self):
        from . import memory

        with self.db.transaction(actor=self.actor) as conn:
            book = self.access.book(conn, self.actor, self.book_id, "read")
            return memory.effective_rules(
                conn,
                self.actor,
                book["company_id"],
                now=self.now,
            )


class SampleReader:
    """评测用确定性读取端口：与真实路径共用同一套组装逻辑，不碰数据库。"""

    def __init__(self, sample):
        self.sample = sample
        self.book_id = sample.get("book_id", "sample-book")
        self.period = sample.get("period", "2026-10")

    def book(self):
        if not self.sample.get("allowed", True):
            raise AccountingError("当前岗位没有这项权限", 403)
        return {
            "id": self.book_id,
            "period": self.period,
            "company_id": self.sample.get("company_id", "sample-company"),
            "firm_id": self.sample.get("firm_id", "sample-firm"),
        }

    def close_blockers(self):
        self.book()
        value = self.sample.get("close") or {}
        return {
            "status": value.get("status", "no_run"),
            "blockers": [dict(row) for row in value.get("blockers") or []],
            "version": value.get("version"),
        }

    def delivery_status(self):
        self.book()
        value = self.sample.get("delivery") or {}
        return {
            "package": value.get("package"),
            "opened": bool(value.get("opened")),
            "notifications": dict(value.get("notifications") or {}),
        }

    def settlement(self, settlement_source_id, bank_source_id, fee_source_id=None):
        self.book()
        value = dict(self.sample.get("settlement") or {})
        if fee_source_id:
            fee = (self.sample.get("fees") or {}).get(fee_source_id)
            value["fee_source"] = fee
        return value

    def account_rows(self, account_code, limit=DEFAULT_LIMIT):
        self.book()
        value = dict(self.sample.get("account") or {})
        value.setdefault("account_code", account_code)
        return value

    def effective_rules(self):
        self.book()
        return [dict(row) for row in self.sample.get("rules") or []]


def _unchecked_for(missing):
    rows = []
    for code in missing or []:
        rows.append(
            {
                "label": UNCHECKED_LABELS.get(code, f"{code}（未核验）"),
                "detail": "未取得可核验来源" if code not in UNCHECKED_LABELS else "覆盖不足",
            }
        )
    return rows


def why_not_delivered(reader):
    """「本月为什么不能交付」：读月结预检、对外交付状态与权益规则。"""
    book = reader.book()
    close = reader.close_blockers()
    delivery = reader.delivery_status()
    rules = reader.effective_rules()
    blockers = [row for row in close.get("blockers") or [] if row.get("code")]
    evidence = []
    unchecked = []
    if close.get("version") is not None:
        evidence.append(
            {
                "object_type": "close_run",
                "object_id": f"{book['id']}:{book['period']}",
                "version": str(close["version"]),
                "label": f"月结预检结果 v{close['version']}",
            }
        )
    else:
        unchecked.append({"label": "月结预检结果", "detail": "本期间尚无预检记录，阻断项未知"})
    package = delivery.get("package")
    if package:
        version = package.get("version") if isinstance(package, dict) else None
        evidence.append(
            {
                "object_type": "delivery_package",
                "object_id": f"{book['id']}:{book['period']}",
                "version": str(version) if version is not None else None,
                "label": (
                    f"对外交付包 v{version}" if version is not None else "对外交付包（版本未知）"
                ),
            }
        )
        if not delivery.get("opened"):
            unchecked.append({"label": "客户打开回执", "detail": "交付包已生成但尚未被打开"})
    else:
        unchecked.append({"label": "对外交付包", "detail": "尚未生成，发送状态未检查"})
    for rule in rules:
        evidence.append(
            {
                "object_type": "rule",
                "object_id": rule["id"],
                "version": str(rule.get("version")),
                "label": f"企业规则（已批准生效）{rule.get('title') or rule['id']}",
            }
        )
    items = []
    for blocker in blockers:
        code = str(blocker.get("code"))
        items.append(
            {
                "code": code,
                "next_action": blocker.get("next_action") or "",
                "owner_role": _owner_role(code),
                "entry": _href(book["id"], book["period"], blocker=code),
            }
        )
    if items:
        conclusion = [f"本月不能交付：月结预检列出 {len(items)} 项阻断，需逐项处理。"]
        conclusion += [
            f"阻断项 {item['code']}：{item['next_action'] or '待补充处理方式'}" for item in items
        ]
        status = "blocked"
    elif close.get("version") is None:
        conclusion = ["本月能否交付尚未确认：本期间没有月结预检记录。"]
        status = "partial"
    else:
        conclusion = ["本月未发现阻断项，可按既有流程交付。"]
        status = "complete"
    return {
        "kind": "why_not_delivered",
        "book_id": book["id"],
        "company_id": book["company_id"],
        "period": book["period"],
        "status": status,
        "conclusion": conclusion,
        "evidence": evidence,
        "amounts": [],
        "unchecked": unchecked,
        "owner": (
            {
                "role": "accountant",
                "reason": f"共 {len(items)} 项阻断，按处理人分派后重新预检",
                "items": [
                    {
                        "role": item["owner_role"],
                        "reason": item["next_action"] or item["code"],
                    }
                    for item in items
                ],
            }
            if items
            else {}
        ),
        "entry": (
            {"label": "打开本月结账预检", "href": _href(book["id"], book["period"])}
            if items
            else {}
        ),
        "facts": {
            "blockers": items,
            "close_status": close.get("status"),
            "delivery_opened": bool(delivery.get("opened")),
            "rule_ids": [rule["id"] for rule in rules],
        },
        "reason": "blockers" if items else None,
    }


def revenue_mismatch(reader, settlement_source_id, bank_source_id, fee_source_id=None):
    """「这笔收入为什么与回款不一致」：读应收来源、到账流水与差异处理记录。"""
    book = reader.book()
    result = reader.settlement(settlement_source_id, bank_source_id, fee_source_id)
    facts = result.get("facts") or {}
    required = ("settlement_total_cents", "bank_total_cents", "difference_cents")
    if any(facts.get(key) is None for key in required):
        # 来源没读到就没有结论可言：宁可弃答，也不拿缺失数据凑一个差额。
        missing = list(result.get("missing") or ["settlement_source", "bank_source"])
        return {
            "kind": "revenue_mismatch",
            "book_id": book["id"],
            "company_id": book["company_id"],
            "period": book["period"],
            "status": "insufficient",
            "conclusion": [ABSTAIN],
            "conclusion_state": "abstained",
            "evidence": [],
            "amounts": [],
            "unchecked": [
                {"label": UNCHECKED_LABELS.get(code, code), "detail": "来源未提供"}
                for code in missing
            ],
            "owner": {},
            "entry": {},
            "facts": {"reason": None, "allowed_amounts": [], "allowed_ids": []},
            "conflicting": False,
            "reason": None,
        }
    evidence = []
    for row in (facts.get("sources") or []) + (
        [facts["fee_source"]] if facts.get("fee_source") else []
    ):
        evidence.append(
            {
                "object_type": "source",
                "object_id": row["id"],
                "version": row.get("source_version"),
                "label": f"{row.get('description') or row.get('source_name') or '来源'}"
                f"（{money_text(int(row['amount_cents']))} 元）",
                "href": row.get("evidence_href"),
            }
        )
    amounts = [
        f"结算单 {money_text(int(facts['settlement_total_cents']))} 元",
        f"银行到账 {money_text(int(facts['bank_total_cents']))} 元",
        f"差额 {money_text(int(facts['difference_cents']))} 元",
    ]
    if facts.get("fee_source"):
        amounts.append(f"手续费凭据 {money_text(int(facts['fee_total_cents']))} 元")
    handoff = result.get("handoff") or {}
    return {
        "kind": "revenue_mismatch",
        "book_id": book["id"],
        "company_id": book["company_id"],
        "period": book["period"],
        "status": result.get("status"),
        "conclusion": [result.get("summary") or ""],
        "evidence": evidence,
        "amounts": amounts,
        "unchecked": _unchecked_for(result.get("missing") or []),
        "owner": (
            {
                "role": handoff.get("owner_role") or "accountant",
                "reason": handoff.get("reason") or "原因待核对",
            }
            if handoff
            else {}
        ),
        "entry": (
            {"label": "打开银行到账原件", "href": handoff.get("action_target")}
            if handoff.get("action_target")
            else {}
        ),
        "facts": {
            **facts,
            "reason": facts.get("reason"),
            "allowed_amounts": [
                str(facts.get(key))
                for key in ("settlement_total", "bank_total", "difference", "fee_total")
                if facts.get(key) is not None
            ],
            "allowed_ids": [row["id"] for row in facts.get("sources") or []],
        },
        "conflicting": result.get("status") != "complete",
        "reason": facts.get("reason"),
        "analysis_fingerprint": result.get("fingerprint"),
        "analysis_source_version": result.get("source_version"),
    }


def number_origin(reader, account_code, limit=DEFAULT_LIMIT):
    """「这个数字来自哪里」：逐层列出来源凭证与合计校验。"""
    book = reader.book()
    value = reader.account_rows(account_code, limit)
    rows = list(value.get("rows") or [])
    total = int(value.get("total_cents") or 0)
    shown = rows[:limit]
    evidence = [
        {
            "object_type": "voucher",
            "object_id": row["voucher_id"],
            "version": row.get("content_fingerprint"),
            "label": f"凭证 {row.get('voucher_no')}（{row.get('voucher_date')}）",
            "href": _href(book["id"], book["period"], voucher=row["voucher_id"]),
        }
        for row in shown
    ]
    components = [int(row["amount_cents"]) for row in shown]
    amounts = [f"科目 {account_code} 合计 {money_text(total)} 元"]
    amounts += [
        f"凭证 {row.get('voucher_no')}：{money_text(int(row['amount_cents']))} 元" for row in shown
    ]
    unchecked = []
    off_period = [
        row for row in shown if str(row.get("voucher_date") or "")[:7] != str(book["period"])
    ]
    if off_period:
        unchecked.append(
            {
                "label": "跨期凭证",
                "detail": (
                    f"有 {len(off_period)} 张凭证的日期不在本期，本期结论需剔除这些凭证后重算"
                ),
            }
        )
    if len(rows) > limit:
        unchecked.append(
            {
                "label": "超出本次上限的明细",
                "detail": f"另有 {len(rows) - limit} 行未加载，合计未覆盖",
            }
        )
        conclusion = [f"该数字来自 {len(shown)} 张凭证，但明细未加载完，当前合计不是全量结论。"]
        status = "partial"
    elif off_period:
        conclusion = [
            f"该数字来自 {len(shown)} 张凭证，其中 {len(off_period)} 张不在本期，"
            "不能直接作为本期结论。"
        ]
        status = "partial"
    elif sum(components) != total:
        unchecked.append(
            {
                "label": "构成合计与科目合计的勾稽",
                "detail": "逐笔金额之和与科目合计不一致",
            }
        )
        conclusion = ["逐笔构成与科目合计不一致，需要会计核对原始凭证。"]
        status = "partial"
    else:
        conclusion = [f"该数字来自 {len(shown)} 张凭证的逐笔合计，共 {money_text(total)} 元。"]
        status = "complete"
    return {
        "kind": "number_origin",
        "book_id": book["id"],
        "company_id": book["company_id"],
        "period": book["period"],
        "status": status,
        "conclusion": conclusion,
        "evidence": evidence,
        "amounts": amounts,
        "unchecked": unchecked,
        "owner": (
            {"role": "accountant", "reason": "构成不勾稽，需核对原始凭证"}
            if status != "complete"
            else {}
        ),
        "entry": {"label": "打开科目明细", "href": _href(book["id"], book["period"])},
        "facts": {
            "account_code": account_code,
            "total_cents": total,
            "component_cents": components,
            "allowed_amounts": [money_text(total).replace(",", "")]
            + [money_text(value).replace(",", "") for value in components],
            "allowed_ids": [row["voucher_id"] for row in shown],
        },
        "reason": None if status == "complete" else "components_incomplete",
    }


def answer_why_not_delivered(reader, *, narrative=None):
    from .answer_contract import build_answer

    return build_answer(why_not_delivered(reader), narrative=narrative)


def answer_revenue_mismatch(
    reader,
    settlement_source_id,
    bank_source_id,
    fee_source_id=None,
    *,
    narrative=None,
):
    from .answer_contract import build_answer

    return build_answer(
        revenue_mismatch(reader, settlement_source_id, bank_source_id, fee_source_id),
        narrative=narrative,
    )


def answer_number_origin(reader, account_code, *, narrative=None):
    from .answer_contract import build_answer

    return build_answer(number_origin(reader, account_code), narrative=narrative)
