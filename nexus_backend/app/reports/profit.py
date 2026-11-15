"""Profit-statement line mappings and voucher-level drilldown (TAX15).

The old statement reports account-category totals only. This service maps
accounts to versioned statement lines and returns the posted voucher lines
that contributed to each line, without rewriting or re-posting anything.
"""

import json
from uuid import uuid4

from pydantic import Field, StrictStr, field_validator

from ..accounting.domain import AccountingError
from ..accounting.invariants import cents_to_money, validate_period
from ..accounting.models import InputModel
from ..accounting.service import AccountingService, now
from ..reports.service import ReportService

DEFAULTS = (
    ("revenue", "营业收入", "revenue", ("6001", "6051", "6111")),
    ("cost", "营业成本", "cost", ("6401", "6402")),
    ("tax", "税金及附加", "tax", ("6403",)),
    ("selling", "销售费用", "selling", ("6601",)),
    ("admin", "管理费用", "admin", ("6602",)),
    ("finance", "财务费用", "finance", ("6603",)),
    ("nonoperating", "营业外收支", "nonoperating", ("6711", "6301")),
    ("income_tax", "所得税费用", "income_tax", ("6801",)),
)


class ProfitMappingItem(InputModel):
    line_code: StrictStr = Field(min_length=1, max_length=40)
    line_name: StrictStr = Field(min_length=1, max_length=120)
    line_kind: StrictStr
    account_prefix: StrictStr = Field(min_length=3, max_length=20)
    sign: int = Field(default=1, strict=True)

    @field_validator("line_kind")
    @classmethod
    def valid_kind(cls, value):
        if value not in {item[2] for item in DEFAULTS}:
            raise ValueError("利润项目类型无效")
        return value

    @field_validator("sign")
    @classmethod
    def valid_sign(cls, value):
        if value not in {-1, 1}:
            raise ValueError("方向只能是 1 或 -1")
        return value

    @field_validator("account_prefix")
    @classmethod
    def valid_prefix(cls, value):
        value = value.strip()
        if not value.isdigit() or len(value) < 3:
            raise ValueError("科目前缀必须是至少 3 位数字")
        return value


class ProfitMappingWrite(InputModel):
    items: list[ProfitMappingItem] = Field(min_length=1, max_length=200)


class ProfitDrilldownService:
    def __init__(self, db):
        self.db = db
        self.accounting = AccountingService(db)

    def _book(self, conn, actor, book_id):
        return self.accounting.access.book(conn, actor, book_id, "read")

    def _active(self, conn, actor):
        rows = conn.execute(
            "SELECT * FROM report_line_mappings WHERE firm_id=? AND template_id=? AND template_version=? "
            "AND active=TRUE ORDER BY version DESC,line_kind,line_code",
            (actor.firm_id, ReportService.TEMPLATE_ID, ReportService.TEMPLATE_VERSION),
        ).fetchall()
        if rows:
            return rows
        return [
            {
                "line_code": code, "line_name": name, "line_kind": kind,
                "account_prefix": prefix, "sign": 1, "version": 1,
            }
            for code, name, kind, prefixes in DEFAULTS
            for prefix in prefixes
        ]

    @staticmethod
    def _match(account_code, mappings):
        candidates = [m for m in mappings if account_code.startswith(str(m["account_prefix"]))]
        return max(candidates, key=lambda m: len(str(m["account_prefix"])), default=None)

    def mapping(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            rows = self._active(conn, actor)
            return {
                "template_id": ReportService.TEMPLATE_ID,
                "template_version": ReportService.TEMPLATE_VERSION,
                "version": int(rows[0]["version"]) if rows else 1,
                "items": [
                    {key: row[key] for key in ("line_code", "line_name", "line_kind", "account_prefix", "sign")}
                    for row in rows
                ],
            }

    def save_mapping(self, actor, book_id, request: ProfitMappingWrite):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self._book(conn, actor, book_id)
            self.accounting.access.company(conn, actor, book["company_id"], manage=True)
            seen = set()
            for item in request.items:
                key = (item.line_code, item.account_prefix)
                if key in seen:
                    raise AccountingError("同一利润项目与科目前缀不能重复", 422)
                seen.add(key)
            version = int(
                conn.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM report_line_mappings "
                    "WHERE firm_id=? AND template_id=? AND template_version=?",
                    (actor.firm_id, ReportService.TEMPLATE_ID, ReportService.TEMPLATE_VERSION),
                ).fetchone()[0]
            )
            conn.execute(
                "UPDATE report_line_mappings SET active=FALSE WHERE firm_id=? AND template_id=? AND template_version=?",
                (actor.firm_id, ReportService.TEMPLATE_ID, ReportService.TEMPLATE_VERSION),
            )
            stamp = now()
            for item in request.items:
                conn.execute(
                    "INSERT INTO report_line_mappings(id,firm_id,template_id,template_version,version,"
                    "line_code,line_name,line_kind,account_prefix,sign,active,created_by,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,TRUE,?,?)",
                    (
                        uuid4().hex, actor.firm_id, ReportService.TEMPLATE_ID,
                        ReportService.TEMPLATE_VERSION, version, item.line_code.strip(),
                        item.line_name.strip(), item.line_kind, item.account_prefix.strip(),
                        item.sign, actor.user_id, stamp,
                    ),
                )
        return self.mapping(actor, book_id)

    def _rows(self, conn, book, period):
        closing = [
            row[0]
            for row in conn.execute(
                "SELECT voucher_id FROM periodic_runs WHERE book_id=? AND kind='closing' "
                "AND status='posted' AND voucher_id IS NOT NULL",
                (book["id"],),
            )
        ]
        sql = (
            "SELECT v.id voucher_id,v.voucher_no,v.voucher_date,v.description,l.line_no,"
            "l.code_snapshot account_code,l.name_snapshot account_name,l.debit_cents,l.credit_cents,"
            "l.source_ref,COALESCE(s.evidence_ref,'') evidence_ref "
            "FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
            "LEFT JOIN sources s ON s.id=l.source_ref AND s.book_id=v.book_id "
            "WHERE v.book_id=? AND v.voucher_date LIKE ? "
        )
        params = [book["id"], period + "%"]
        if closing:
            sql += "AND v.id NOT IN (" + ",".join("?" * len(closing)) + ") "
            params.extend(closing)
        sql += "ORDER BY v.voucher_date,v.voucher_no,l.line_no"
        return conn.execute(sql, params).fetchall()

    def _ytd(self, conn, book, period, mappings):
        closing = [
            row[0]
            for row in conn.execute(
                "SELECT voucher_id FROM periodic_runs WHERE book_id=? AND kind='closing' "
                "AND status='posted' AND voucher_id IS NOT NULL",
                (book["id"],),
            )
        ]
        start = period[:4] + "-01"
        sql = (
            "SELECT l.code_snapshot account_code,l.debit_cents,l.credit_cents "
            "FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
            "WHERE v.book_id=? AND v.voucher_date>=? AND v.voucher_date<=? "
        )
        params = [book["id"], start + "-01", period + "-31"]
        if closing:
            sql += "AND v.id NOT IN (" + ",".join("?" * len(closing)) + ") "
            params.extend(closing)
        totals = {}
        for row in conn.execute(sql, params).fetchall():
            mapping = self._match(row["account_code"], mappings)
            if not mapping:
                continue
            sign = int(mapping["sign"])
            amount = (
                int(row["credit_cents"]) - int(row["debit_cents"])
                if mapping["line_kind"] == "revenue"
                else int(row["debit_cents"]) - int(row["credit_cents"])
            )
            totals[mapping["line_code"]] = totals.get(mapping["line_code"], 0) + sign * amount
        return totals

    def overview(self, actor, book_id, period, line_code=""):
        period = validate_period(period)
        with self.db.transaction(actor=actor) as conn:
            book = self._book(conn, actor, book_id)
            mappings = self._active(conn, actor)
            rows = self._rows(conn, book, period)
            ytd = self._ytd(conn, book, period, mappings)
            groups, details, unmapped = {}, [], 0
            for mapping in mappings:
                groups.setdefault(
                    mapping["line_code"],
                    {
                        "line_code": mapping["line_code"], "line_name": mapping["line_name"],
                        "line_kind": mapping["line_kind"], "amount_cents": 0,
                        "ytd_cents": int(ytd.get(mapping["line_code"], 0)),
                        "items_count": 0,
                    },
                )
            by_voucher = {}
            for row in rows:
                mapping = self._match(row["account_code"], mappings)
                if not mapping:
                    unmapped += 1
                    continue
                sign = int(mapping["sign"])
                amount = (
                    int(row["credit_cents"]) - int(row["debit_cents"])
                    if mapping["line_kind"] == "revenue"
                    else int(row["debit_cents"]) - int(row["credit_cents"])
                )
                amount *= sign
                group = groups[mapping["line_code"]]
                group["amount_cents"] += amount
                group["items_count"] += 1
                item = {
                    "voucher_id": row["voucher_id"], "voucher_no": int(row["voucher_no"]),
                    "voucher_date": row["voucher_date"], "description": row["description"],
                    "line_no": int(row["line_no"]), "account_code": row["account_code"],
                    "account_name": row["account_name"], "debit_cents": int(row["debit_cents"]),
                    "credit_cents": int(row["credit_cents"]), "net_cents": amount,
                    "source_ref": row["source_ref"], "evidence_ref": row["evidence_ref"],
                }
                if not line_code or line_code == mapping["line_code"]:
                    details.append(item)
                by_voucher.setdefault((row["voucher_id"], mapping["line_code"]), set()).add(row["line_no"])
            lines = sorted(groups.values(), key=lambda item: item["line_code"])
            revenue = sum(x["amount_cents"] for x in lines if x["line_kind"] == "revenue")
            cost = sum(x["amount_cents"] for x in lines if x["line_kind"] in {"cost", "tax", "selling", "admin", "finance"})
            nonoperating = sum(x["amount_cents"] for x in lines if x["line_kind"] == "nonoperating")
            income_tax_rows = [x for x in lines if x["line_kind"] == "income_tax" and x["items_count"] > 0]
            income_tax = sum(x["amount_cents"] for x in income_tax_rows) if income_tax_rows else None
            profit = revenue - cost + nonoperating
            net_profit = None if income_tax is None else profit - income_tax
            year_complete = period.endswith("-12")
            return {
                "book_id": book_id, "period": period,
                "template_id": ReportService.TEMPLATE_ID, "template_version": ReportService.TEMPLATE_VERSION,
                "mapping_version": int(mappings[0]["version"]),
                "lines": lines,
                "selected_line": line_code,
                "items": [
                    item for item in details
                    if not line_code or item["net_cents"] != 0
                ][:1000],
                "totals": {
                    "revenue_cents": revenue, "cost_and_expense_cents": cost,
                    "operating_profit_cents": profit,
                    "nonoperating_cents": nonoperating,
                    "income_tax_cents": income_tax,
                    "net_profit_cents": net_profit,
                    "year_to_date_known": year_complete,
                    "year_to_date_reason": "" if year_complete else "年度未结束，年累计仅显示已发生月份，不补零",
                },
                "checks": {
                    "unmapped_line_count": unmapped,
                    "mixed_voucher_contributions": sum(1 for count in by_voucher.values() if len(count) > 1),
                    "net_profit_known": income_tax is not None,
                },
            }
