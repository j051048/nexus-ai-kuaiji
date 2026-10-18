"""会计工作流：精确整数金额、事务、授权和人工审核。

金额处理和审核规则均为确定性代码。此模块不创建正式总账分录，也不调用模型。
"""

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from uuid import uuid4

from .database import Database
from .domain import Actor, AccountingError
from .access import AccessControl
from .queries import health_cte, book_filter, page_bounds, inactive_source_sql
from .models import (
    ApprovalRequest,
    BookCreate,
    DraftUpdate,
    EvidenceRequest,
    ExportRequest,
    ImportRequest,
    cents_to_money,
    money_to_cents,
)

# Versioned review policy marker for audit. New separation rules must be
# approved and versioned explicitly; do not infer them from a flag.
REVIEW_POLICY_VERSION = "b01.review.2026-10-18"


CHART = [
    ("1002", "银行存款"),
    ("1122", "应收账款"),
    ("2202", "应付账款"),
    ("6001", "主营业务收入"),
    ("2241", "其他应付款"),
    ("6602.01", "管理费用—房租"),
    ("6602.02", "管理费用—办公费"),
    ("6602.03", "管理费用—交通费"),
    ("6602.04", "管理费用—通信费"),
    ("6602.99", "管理费用—其他"),
]

# 仅用于验证流程的可解释规则，不构成税务或会计处理判定。
RULES = [
    ("expense", ("房租", "租金"), "6602.01", "费用关键词：房租 / 租金"),
    ("expense", ("办公", "文具"), "6602.02", "费用关键词：办公 / 文具"),
    ("expense", ("交通", "出租车", "打车"), "6602.03", "费用关键词：交通 / 出租车 / 打车"),
    ("expense", ("通信", "话费", "宽带"), "6602.04", "费用关键词：通信 / 话费 / 宽带"),
    ("income", ("服务收入", "销售收款"), "6001", "收入关键词：服务收入 / 销售收款"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def safe_csv_text(value: str) -> str:
    # Excel 等客户端会跳过开头空白；控制字符也可启动公式解释。
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


class AccountingService:
    def __init__(self, db: Database):
        self.db = db
        self.access = AccessControl()
        # The application may bind its shared intelligence service. Legacy
        # callers still get deterministic guards through a lazy local instance.
        self.intelligence = None
        self.settlements = None
        self.cases = None
        self.learning = None
        self.bank_import = None
        self.delivery = None
        self.ledgers = None
        self.revisions = None
        self.dispositions = None

    def _member(self, conn, actor: Actor, reviewer: bool = False):
        role = self.access.firm_role(conn, actor)
        if not role or (reviewer and role not in {"owner", "admin", "reviewer"}):
            raise AccountingError("没有事务所成员权限或所需复核权限", 403)
        return {"role": role}

    def _book(self, conn, actor: Actor, book_id: str, reviewer: bool = False, edit: bool = False):
        return self.access.book(
            conn, actor, book_id, "review" if reviewer else "edit" if edit else "read"
        )

    def require_access(self, actor: Actor, book_id: str):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)

    def _company_of_book(self, actor: Actor, book_id: str):
        """Resolve the book's company up front for a company-scoped write lock."""
        with self.db.transaction(actor=actor) as conn:
            return self._book(conn, actor, book_id)["company_id"]

    def _open(self, book):
        if book["period_status"] != "open":
            raise AccountingError("该会计期间已关闭，不可写入", 403)

    def _audit(self, conn, actor, book_id, action, object_id, details, created_at=None):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book_id,
                actor.user_id,
                action,
                object_id,
                json.dumps(details, ensure_ascii=False),
                created_at or now(),
            ),
        )

    def create_book(self, actor: Actor, request: BookCreate):
        # 全局写锁：同一事务里创建企业、账套、科目与长期账套映射（跨多个域），
        # 企业范围迁移留给第三批写锁工作处理。
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            company_id = request.company_id
            if company_id:
                self.access.company(conn, actor, company_id, manage=True)
            else:
                company_id = uuid4().hex
                conn.execute(
                    "INSERT INTO companies(id,name,tax_id) VALUES(?,?,'')",
                    (company_id, request.name),
                )
                conn.execute(
                    "INSERT INTO firm_clients(firm_id,company_id,status) VALUES(?,?,'active')",
                    (actor.firm_id, company_id),
                )
                from .ai_defaults import ensure_company_ai_default

                ensure_company_ai_default(conn, actor, company_id, source="legacy_book_create")
            book_id = uuid4().hex
            conn.execute(
                "INSERT INTO books(id,firm_id,name,period,company_id) VALUES(?,?,?,?,?)",
                (book_id, actor.firm_id, request.name, request.period, company_id),
            )
            conn.execute(
                "INSERT INTO book_access(book_id,firm_id,user_id,role) VALUES(?,?,?,'reviewer')",
                (book_id, actor.firm_id, actor.user_id),
            )
            conn.execute("INSERT INTO periods VALUES(?,?,'open')", (book_id, request.period))
            conn.executemany(
                "INSERT INTO accounts(book_id,code,name,category,normal_balance) VALUES(?,?,?,?,?)",
                [
                    (
                        book_id,
                        code,
                        name,
                        "asset"
                        if code.startswith("1")
                        else "liability"
                        if code.startswith("2")
                        else "income"
                        if code == "6001"
                        else "expense",
                        "credit" if code.startswith("2") or code == "6001" else "debit",
                    )
                    for code, name in CHART
                ],
            )
            self._audit(
                conn,
                actor,
                book_id,
                "book.create",
                book_id,
                {"name": request.name, "period": request.period},
            )
            if self.ledgers is None:
                from ..ledgers.service import LedgersService

                self.ledgers = LedgersService(self.db)
            self.ledgers.ensure_book_in_transaction(conn, actor, self._book(conn, actor, book_id))
            return self._book_summary(conn, self._book(conn, actor, book_id), actor)

    def _effective_evidence(self, conn, source):
        addition = conn.execute(
            "SELECT evidence_ref FROM source_evidence WHERE source_id=? AND book_id=? ORDER BY id DESC LIMIT 1",
            (source["id"], source["book_id"]),
        ).fetchone()
        return addition["evidence_ref"] if addition else source["evidence_ref"]

    def _source_json(self, conn, source):
        draft = conn.execute(
            "SELECT status FROM drafts WHERE source_id=? AND book_id=?",
            (source["id"], source["book_id"]),
        ).fetchone()
        identity = conn.execute(
            "SELECT original_external_id FROM bank_import_keys WHERE source_id=? AND book_id=?",
            (source["id"], source["book_id"]),
        ).fetchone()
        return {
            "id": source["id"],
            "external_id": identity["original_external_id"] if identity else source["external_id"],
            "date": source["date"],
            "description": source["description"],
            "counterparty": source["counterparty"],
            "amount": cents_to_money(source["amount_cents"]),
            "direction": source["direction"],
            "evidence_ref": self._effective_evidence(conn, source),
            "original_evidence_ref": source["evidence_ref"],
            "status": draft["status"] if draft else "unprocessed",
            "source_kind": source["source_kind"],
        }

    def _intelligence_guard(self):
        if self.intelligence is None:
            # Imports are local to avoid the business/intelligence/accounting
            # import cycle. Empty fixed settings make this read-only fallback
            # independent of environment configuration and model credentials.
            from ..intelligence.provider import AISettings, OpenAICompatibleProvider
            from ..intelligence.service import IntelligenceService

            self.intelligence = IntelligenceService(
                self.db, storage=None, provider=OpenAICompatibleProvider(AISettings())
            )
        return self.intelligence

    def _ledger_guard(self):
        if self.ledgers is None:
            from ..ledgers.service import LedgersService

            self.ledgers = LedgersService(self.db)
        return self.ledgers

    def _revision_guard(self):
        if self.revisions is None:
            from ..revisions.service import FactRevisionService

            self.revisions = FactRevisionService(self.db, ledger=self)
        return self.revisions

    def _source_state_issues(self, conn, source):
        if self.db.is_postgres:
            inactive = conn.execute(
                "SELECT accounting.source_inactive(?,?)", (source["book_id"], source["id"])
            ).fetchone()[0]
            pending = conn.execute(
                "SELECT accounting.revision_source_blocked(?,?)", (source["book_id"], source["id"])
            ).fetchone()[0]
        else:
            inactive = (
                conn.execute(
                    "SELECT 1 FROM sources s WHERE s.id=? AND s.book_id=? AND "
                    + inactive_source_sql(),
                    (source["id"], source["book_id"]),
                ).fetchone()
                is not None
            )
            pending = (
                conn.execute(
                    "SELECT 1 FROM fact_revisions WHERE old_book_id=? AND old_source_id=? AND state='pending'",
                    (source["book_id"], source["id"]),
                ).fetchone()
                is not None
            )
        if inactive:
            return ["该来源已被事实修订替代或确认为重复副本，历史保留供追溯，请处理当前主来源"]
        return ["业务事实修订等待独立复核，暂不可继续生成、审核或导出"] if pending else []

    def _guard_issues(self, conn, source, actor=None):
        state_issues = self._source_state_issues(conn, source)
        if state_issues:
            return state_issues
        if source["source_kind"] == "bank":
            linked = conn.execute(
                "SELECT 1 FROM ai_bank_links WHERE bank_source_id=? AND book_id=?",
                (source["id"], source["book_id"]),
            ).fetchone()
            return (
                ["该银行流水已关联业务事项，请复核业务凭证，禁止重复审核或导出流水凭证"]
                if linked
                else []
            )
        # Scope comes from the canonical source->mapping->draft relationship,
        # never from a model-supplied event or book identity. Disabled policy
        # retains the earlier manual accounting workflow without OCR demands.
        linked_issues = self._linked_bank_issues(conn, source, actor)
        event = conn.execute(
            "SELECT e.* FROM business_mappings m JOIN business_events e "
            "ON e.id=m.event_id AND e.firm_id=m.firm_id AND e.company_id=m.company_id "
            "JOIN drafts d ON d.id=m.draft_id AND d.book_id=m.book_id AND d.source_id=m.source_id "
            "JOIN ai_company_settings s ON s.firm_id=m.firm_id AND s.company_id=m.company_id AND s.enabled=1 "
            "WHERE m.source_id=? AND m.book_id=?",
            (source["id"], source["book_id"]),
        ).fetchone()
        if event is None:
            return linked_issues
        if actor is None:
            return ["请使用当前授权会计账号核对票据及流水异常"]
        assessment = self._intelligence_guard()._assessment(conn, actor, event, persist=False)
        return linked_issues + list(
            dict.fromkeys(
                "票据核对未完成：" + issue["payload"]["message"]
                for issue in assessment["findings"]
                if issue["severity"] == "blocking" and not issue["resolved"]
            )
        )

    def _linked_bank_issues(self, conn, source, actor=None):
        if source["source_kind"] != "business":
            return []
        banks = conn.execute(
            "SELECT s.* FROM business_mappings m JOIN ai_bank_links l ON l.event_id=m.event_id AND l.book_id=m.book_id JOIN sources s ON s.id=l.bank_source_id AND s.book_id=l.book_id WHERE m.source_id=? AND m.book_id=?",
            (source["id"], source["book_id"]),
        )
        issues = []
        for bank in banks:
            if self._case_guard().blocking_issues_in_transaction(conn, actor, bank):
                issues.append("已关联银行流水存在尚未关闭的异常或补件，须先处理后复核业务凭证")
            if self._bank_import_guard().blocking_issues_in_transaction(conn, actor, bank):
                issues.append("已关联银行流水存在待核验的相似交易，须先核验独立交易，禁止重复记账")
        return list(dict.fromkeys(issues))

    def _issues(self, conn, source, lines, actor=None):
        accounts = {
            x["code"]
            for x in conn.execute(
                "SELECT code FROM accounts WHERE book_id=? AND active=1", (source["book_id"],)
            )
        }
        return (
            self._calculate_issues(source, lines, accounts, self._effective_evidence(conn, source))
            + self._guard_issues(conn, source, actor)
            + self._case_guard().blocking_issues_in_transaction(conn, actor, source)
            + self._bank_import_guard().blocking_issues_in_transaction(conn, actor, source)
            + self._settlement_guard().blocking_issues_in_transaction(conn, actor, source, lines)
        )

    def _settlement_guard(self):
        if self.settlements is None:
            from ..settlements.service import SettlementService

            self.settlements = SettlementService(self.db)
        return self.settlements

    def _bank_import_guard(self):
        if self.bank_import is None:
            from .bank_imports import BankImportService

            self.bank_import = BankImportService(self.db)
        return self.bank_import

    def _case_guard(self):
        if self.cases is None:
            from ..business.service import BusinessService
            from ..cases.service import CaseService

            self.cases = CaseService(self.db, BusinessService(self.db))
        return self.cases

    def _learning_guard(self):
        if self.learning is None:
            from ..learning.service import LearningService

            self.learning = LearningService(self.db)
        for name in ("legacy_learning", "legacy_settlements"):
            if getattr(self, name, None) is not None:
                setattr(self.learning, name, getattr(self, name))
        return self.learning

    def _delivery_guard(self):
        if self.delivery is None:
            from ..delivery.service import DeliveryService

            self.delivery = DeliveryService(self.db)
        return self.delivery

    @staticmethod
    def _calculate_issues(source, lines, accounts, evidence):
        issues = []
        if not evidence.strip():
            issues.append("缺少凭证证据引用，请补充资料后审核")
        if len(lines) < 2:
            issues.append("未形成完整分录，请会计补充对应科目")
        debit, credit = sum(x["debit_cents"] for x in lines), sum(x["credit_cents"] for x in lines)
        if debit != credit:
            issues.append("借贷不平衡")
        if debit != source["amount_cents"] or credit != source["amount_cents"]:
            issues.append("借贷合计必须分别等于原始流水金额")
        for line in lines:
            if line["account_code"] not in accounts:
                issues.append(f"科目 {line['account_code']} 不存在或已停用")
            if (line["debit_cents"] > 0) == (line["credit_cents"] > 0):
                issues.append("每行必须且只能有一侧非零金额")
        bank_debit = sum(x["debit_cents"] for x in lines if x["account_code"].startswith("1002"))
        bank_credit = sum(x["credit_cents"] for x in lines if x["account_code"].startswith("1002"))
        expected = (
            (source["amount_cents"], 0)
            if source["direction"] == "income"
            else (0, source["amount_cents"])
        )
        if source["source_kind"] == "bank" and (bank_debit, bank_credit) != expected:
            issues.append("银行存款分录的金额或方向与原始流水不符")
        return list(dict.fromkeys(issues))

    def _draft_json(self, conn, draft, actor=None):
        source = conn.execute(
            "SELECT * FROM sources WHERE id=? AND book_id=?", (draft["source_id"], draft["book_id"])
        ).fetchone()
        lines = conn.execute(
            "SELECT * FROM draft_lines WHERE draft_id=? AND book_id=? ORDER BY ordinal",
            (draft["id"], draft["book_id"]),
        ).fetchall()
        from .catalog import CatalogService

        catalog = CatalogService(self.db)
        auxiliary_values = catalog.load_auxiliaries_in_transaction(
            conn, draft["book_id"], draft["id"]
        )
        public_lines = [
            {
                "account_code": x["account_code"],
                "debit": cents_to_money(x["debit_cents"]),
                "credit": cents_to_money(x["credit_cents"]),
                "auxiliary_values": auxiliary_values.get(x["ordinal"], {}),
            }
            for x in lines
        ]
        auxiliary_issues = catalog.validate_auxiliaries_in_transaction(
            conn, actor, draft["book_id"], public_lines
        )
        if actor and getattr(self, "legacy_settlements", None):
            auxiliary_issues += self.legacy_settlements.blocking_issues_in_transaction(
                conn, actor, source, public_lines
            )
        mapping = None
        if source["source_kind"] == "business":
            row = conn.execute(
                "SELECT event_id,submitted_version FROM business_mapping_versions WHERE source_id=? AND book_id=?",
                (source["id"], source["book_id"]),
            ).fetchone()
            mapping = dict(row) if row else None
        return {
            "id": draft["id"],
            "source_id": draft["source_id"],
            "description": draft["description"],
            "status": draft["status"],
            "version": draft["version"],
            "explanation": draft["explanation"],
            "issues": self._issues(conn, source, lines, actor) + auxiliary_issues,
            "source_date": source["date"],
            "source_amount": cents_to_money(source["amount_cents"]),
            "source_description": source["description"],
            "evidence_ref": self._effective_evidence(conn, source),
            "source_kind": source["source_kind"],
            "business_origin": mapping,
            "source_inactive": bool(
                conn.execute(
                    "SELECT 1 FROM sources s WHERE s.id=? AND s.book_id=? AND "
                    + inactive_source_sql(self.db.dialect),
                    (source["id"], source["book_id"]),
                ).fetchone()
            ),
            "lines": public_lines,
        }

    def _summary_scope(self, actor, book_id=None):
        # SQLite and PostgreSQL both enforce current application permissions;
        # PostgreSQL additionally applies FORCE RLS before aggregation/paging.
        scope = """SELECT b.id,b.name,b.period,b.company_id,c.name AS company_name,
            p.status AS period_status,a.role AS book_role,cm.role AS company_role
            FROM books b JOIN companies c ON c.id=b.company_id
            JOIN firm_clients fc ON fc.company_id=b.company_id AND fc.firm_id=b.firm_id AND fc.status='active'
            JOIN periods p ON p.book_id=b.id AND p.period=b.period
            LEFT JOIN book_access a ON a.book_id=b.id AND a.firm_id=b.firm_id AND a.user_id=?
            LEFT JOIN company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=?
            WHERE b.firm_id=?"""
        values = [actor.user_id, actor.user_id, actor.firm_id]
        if book_id is not None:
            scope += " AND b.id=?"
            values.append(book_id)
        return scope, values

    def _book_summary(self, conn, book, actor=None):
        scope, values = self._summary_scope(actor, book["id"])
        summary = conn.execute(
            health_cte(scope, self.db.dialect) + "SELECT * FROM summaries", values
        ).fetchone()
        return {
            key: summary[key]
            for key in (
                "id",
                "name",
                "period",
                "period_status",
                "company_id",
                "company_name",
                "pending_count",
                "exception_count",
                "approved_count",
            )
        } | {"permissions": book["permissions"]}

    def workbench(
        self, actor: Actor, limit=50, offset=0, filter="all", search="", focus_book_id=None
    ):
        page_bounds(limit, offset)
        condition, filters = book_filter(filter, search)
        with self.db.transaction(actor=actor) as conn:
            firm_role = self.access.firm_role(conn, actor)
            scope, values = self._summary_scope(actor)
            if firm_role not in {"owner", "admin"}:
                scope += (
                    " AND a.role IS NOT NULL AND (?=1 OR cm.role IN ('owner','admin','finance'))"
                )
                values.append(1 if firm_role else 0)
            cte = health_cte(scope, self.db.dialect)
            if focus_book_id:
                focused = self._book(conn, actor, focus_book_id)
                condition, filters = book_filter("all", "")
                position = conn.execute(
                    cte + "SELECT COUNT(*) FROM summaries WHERE name<? OR (name=? AND id<?)",
                    values + [focused["name"], focused["name"], focus_book_id],
                ).fetchone()[0]
                offset = (position // limit) * limit
            # One statement materializes the health summary once for the page,
            # filtered count and global counters, including an empty-page row.
            rows = conn.execute(
                cte
                + """, filtered AS (SELECT * FROM summaries WHERE """
                + condition
                + """),
                overall AS (SELECT COUNT(*) AS all_book_count,
                    COALESCE(SUM(pending_count),0) AS all_pending_count,
                    COALESCE(SUM(exception_count),0) AS all_exception_count,
                    COALESCE(SUM(approved_count),0) AS all_approved_count FROM summaries)
                SELECT page.*,overall.*,(SELECT COUNT(*) FROM filtered) AS filtered_count
                FROM overall LEFT JOIN (SELECT * FROM filtered ORDER BY name,id LIMIT ? OFFSET ?) page ON 1=1
                ORDER BY page.name,page.id""",
                values + filters + [limit, offset],
            ).fetchall()
            stats = {
                key: rows[0]["all_" + key]
                for key in ("book_count", "pending_count", "exception_count", "approved_count")
            }
            total = rows[0]["filtered_count"]
            rows = [row for row in rows if row["id"] is not None]
            result = []
            for row in rows:
                permissions = self.access.permissions(
                    firm_role, row["book_role"], row["company_role"]
                )
                result.append(
                    {
                        key: row[key]
                        for key in (
                            "id",
                            "name",
                            "period",
                            "period_status",
                            "company_id",
                            "company_name",
                            "pending_count",
                            "exception_count",
                            "approved_count",
                        )
                    }
                    | {"permissions": {k: v for k, v in permissions.items() if k != "read"}}
                )
            return {
                "books": result,
                "stats": dict(stats),
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "total": total,
                    "has_more": offset + len(result) < total,
                },
                "exception_count_scope": "basic_accounting_checks",
            }

    def book_choices(self, actor, company_id, period=None, limit=100, offset=0):
        page_bounds(limit, offset)
        if period is not None:
            BookCreate(name="期间校验", period=period)
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id)
            role = self.access.firm_role(conn, actor)
            scope, values = self._summary_scope(actor)
            scope += " AND b.company_id=?"
            values.append(company_id)
            if period:
                scope += " AND b.period=?"
                values.append(period)
            if role not in {"owner", "admin"}:
                scope += (
                    " AND a.role IS NOT NULL AND (?=1 OR cm.role IN ('owner','admin','finance'))"
                )
                values.append(1 if role else 0)
            total = conn.execute("SELECT COUNT(*) FROM (" + scope + ") choices", values).fetchone()[
                0
            ]
            rows = conn.execute(
                scope + " ORDER BY b.name,b.id LIMIT ? OFFSET ?", values + [limit, offset]
            ).fetchall()
            result = [
                {
                    key: row[key]
                    for key in (
                        "id",
                        "name",
                        "period",
                        "period_status",
                        "company_id",
                        "company_name",
                    )
                }
                | {
                    "permissions": {
                        k: v
                        for k, v in self.access.permissions(
                            role, row["book_role"], row["company_role"]
                        ).items()
                        if k != "read"
                    }
                }
                for row in rows
            ]
            return {
                "books": result,
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "total": total,
                    "has_more": offset + len(rows) < total,
                },
            }

    def get_book(
        self,
        actor: Actor,
        book_id: str,
        limit=50,
        offset=0,
        source_offset=0,
        filter="all",
        summaries=False,
        focus_draft_id=None,
    ):
        page_bounds(limit, offset)
        page_bounds(limit, source_offset)
        if filter not in {"all", "pending", "approved", "exception"}:
            raise AccountingError("待办筛选参数无效")
        with self.db.transaction(actor=actor) as conn:
            book = self._book(conn, actor, book_id)
            scope, values = self._summary_scope(actor, book_id)
            cte = health_cte(scope, self.db.dialect)
            predicate = {
                "all": "1=1",
                "pending": "status='draft' AND source_inactive=0",
                "approved": "status IN ('approved','exported')",
                "exception": "status='draft' AND source_inactive=0 AND basic_exception=1",
            }[filter]
            if focus_draft_id:
                focus = conn.execute(
                    cte + "SELECT id FROM health WHERE id=?", values + [focus_draft_id]
                ).fetchone()
                if not focus:
                    raise AccountingError("凭证不存在或无权访问", 404)
                # Explicit evidence navigation opens the page containing that
                # draft, even when it is beyond the first bounded page.
                position = conn.execute(
                    cte + "SELECT COUNT(*) FROM health WHERE id>?", values + [focus_draft_id]
                ).fetchone()[0]
                offset = (position // limit) * limit
                predicate = "1=1"
            total = conn.execute(
                cte + "SELECT COUNT(*) FROM health WHERE " + predicate, values
            ).fetchone()[0]
            rows = conn.execute(
                cte
                + "SELECT * FROM health WHERE "
                + predicate
                + " ORDER BY id DESC LIMIT ? OFFSET ?",
                values + [limit, offset],
            ).fetchall()
            source_select = """SELECT s.*,k.original_external_id AS bank_external_id,d.status AS draft_status,COALESCE((SELECT e.evidence_ref FROM source_evidence e WHERE e.source_id=s.id AND e.book_id=s.book_id ORDER BY e.id DESC LIMIT 1),s.evidence_ref) AS current_evidence FROM sources s LEFT JOIN bank_import_keys k ON k.source_id=s.id AND k.book_id=s.book_id LEFT JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE s.book_id=?"""
            sources = [
                self._paged_source_json(row)
                for row in conn.execute(
                    source_select + " ORDER BY s.date,s.id LIMIT ? OFFSET ?",
                    (book_id, limit, source_offset),
                )
            ]
            present = {row["id"] for row in sources}
            missing = list({row["source_id"] for row in rows} - present)
            if missing:
                sources.extend(
                    self._paged_source_json(row)
                    for row in conn.execute(
                        source_select + " AND s.id IN (" + ",".join("?" for _ in missing) + ")",
                        (book_id, *missing),
                    )
                )
            drafts = [
                self._draft_summary_json(row) if summaries else self._draft_json(conn, row, actor)
                for row in rows
            ]
            source_total = conn.execute(
                "SELECT COUNT(*) FROM sources WHERE book_id=?", (book_id,)
            ).fetchone()[0]
            return {
                "book": self._book_summary(conn, book, actor),
                "accounts": [
                    dict(x)
                    for x in conn.execute(
                        "SELECT code,name,auxiliary_requirements FROM accounts WHERE book_id=? AND active=1 ORDER BY code",
                        (book_id,),
                    )
                ],
                "sources": sources,
                "drafts": drafts,
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "total": total,
                    "has_more": offset + len(rows) < total,
                },
                "source_pagination": {
                    "limit": limit,
                    "offset": source_offset,
                    "total": source_total,
                    "has_more": source_offset + limit < source_total,
                },
                "source_page_ids": [row["id"] for row in sources if row["id"] in present],
                "exception_count_scope": "basic_accounting_checks",
            }

    @staticmethod
    def _paged_source_json(row):
        return {
            key: row[key]
            for key in (
                "id",
                "external_id",
                "date",
                "description",
                "counterparty",
                "direction",
                "source_kind",
            )
        } | {
            "external_id": row["bank_external_id"] or row["external_id"],
            "amount": cents_to_money(row["amount_cents"]),
            "evidence_ref": row["current_evidence"],
            "original_evidence_ref": row["evidence_ref"],
            "status": row["draft_status"] or "unprocessed",
        }

    @staticmethod
    def _draft_summary_json(row):
        return {
            key: row[key]
            for key in (
                "id",
                "source_id",
                "description",
                "status",
                "version",
                "explanation",
                "source_date",
                "source_description",
                "source_kind",
                "evidence_ref",
            )
        } | {
            "source_amount": cents_to_money(row["amount_cents"]),
            "issues": ["基础校验存在异常，请打开凭证核对"] if row["basic_exception"] else [],
            "lines": [],
            "detail_loaded": False,
            "source_inactive": bool(row["source_inactive"]),
        }

    def get_draft(self, actor, book_id, draft_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            draft = conn.execute(
                "SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)
            ).fetchone()
            if not draft:
                raise AccountingError("凭证不存在或无权访问", 404)
            return {**self._draft_json(conn, draft, actor), "detail_loaded": True}

    def import_sources(self, actor: Actor, book_id: str, request: ImportRequest):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._book(conn, actor, book_id, edit=True)
            self._open(book)
            imported = duplicates = 0
            if request.namespace:
                result = self._bank_import_guard().import_in_transaction(conn, actor, book, request)
                self._audit(
                    conn,
                    actor,
                    book_id,
                    "source.import",
                    book_id,
                    {
                        "source_name": request.source_name,
                        "namespace": request.namespace,
                        "bank_account": request.bank_account,
                        **result,
                    },
                )
                return result
            for row in request.rows:
                if row.date[:7] != book["period"]:
                    raise AccountingError(f"流水 {row.external_id} 日期不属于当前会计期间")
                # Preserve the exact pre-v9 hash for existing imports.
                digest = fingerprint(row.model_dump(exclude={"stable_transaction_id"}))
                existing = conn.execute(
                    "SELECT fingerprint FROM sources WHERE book_id=? AND external_id=?",
                    (book_id, row.external_id),
                ).fetchone()
                if existing:
                    if existing["fingerprint"] != digest:
                        raise AccountingError(
                            f"外部 ID {row.external_id} 已存在且内容不同，整批导入已回滚", 409
                        )
                    duplicates += 1
                    continue
                source_id = uuid4().hex
                conn.execute(
                    "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        source_id,
                        book_id,
                        row.external_id,
                        request.source_name,
                        row.date,
                        row.description,
                        row.counterparty,
                        money_to_cents(row.amount),
                        row.direction,
                        row.evidence_ref,
                        digest,
                    ),
                )
                imported += 1
            self._audit(
                conn,
                actor,
                book_id,
                "source.import",
                book_id,
                {
                    "source_name": request.source_name,
                    "imported": imported,
                    "duplicates": duplicates,
                },
            )
            return {"imported": imported, "duplicates": duplicates}

    def add_evidence(self, actor: Actor, book_id: str, source_id: str, request: EvidenceRequest):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self._open(self._book(conn, actor, book_id, edit=True))
            source = conn.execute(
                "SELECT * FROM sources WHERE id=? AND book_id=?", (source_id, book_id)
            ).fetchone()
            if not source:
                raise AccountingError("来源不存在或无权访问", 404)
            draft = conn.execute(
                "SELECT * FROM drafts WHERE source_id=? AND book_id=?", (source_id, book_id)
            ).fetchone()
            if draft and draft["status"] != "draft":
                raise AccountingError("已审核资料已冻结，不可补充或修改证据", 409)
            conn.execute(
                "INSERT INTO source_evidence(source_id,book_id,evidence_ref,created_at,created_by) VALUES(?,?,?,?,?)",
                (source_id, book_id, request.evidence_ref, now(), actor.user_id),
            )
            # 补证影响审核上下文，因此使已有草稿版本失效。
            if draft:
                conn.execute(
                    "UPDATE drafts SET version=version+1 WHERE id=? AND book_id=?",
                    (draft["id"], book_id),
                )
            self._audit(
                conn,
                actor,
                book_id,
                "source.evidence.add",
                source_id,
                {"evidence_ref": request.evidence_ref, "verified": False},
            )
            return self._source_json(conn, source)

    def suggestions(self, actor: Actor, book_id: str):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            result = self.suggestions_in_transaction(conn, actor, book_id)
            return {"created": result["created"], "exceptions": result["exceptions"]}

    def suggestions_in_transaction(self, conn, actor, book_id, source_ids=None):
        """Same deterministic draft pipeline, bounded to an immutable action preview."""
        self._open(self._book(conn, actor, book_id, edit=True))
        requested = list(source_ids) if source_ids is not None else None
        params = [book_id]
        restriction = ""
        if requested is not None:
            if not requested or len(requested) > 100 or len(set(requested)) != len(requested):
                raise AccountingError("流水任务范围必须为1至100条不重复来源", 422)
            restriction = (
                " AND s.source_kind='bank' AND s.id IN (" + ",".join("?" for _ in requested) + ")"
            )
            params.extend(requested)
        sources = conn.execute(
            "SELECT s.* FROM sources s LEFT JOIN drafts d ON d.source_id=s.id WHERE s.book_id=? AND d.id IS NULL AND NOT "
            + inactive_source_sql(self.db.dialect)
            + restriction
            + " ORDER BY s.date,s.id",
            params,
        ).fetchall()
        if requested is not None and len(sources) != len(requested):
            raise AccountingError("流水已生成草稿、已隔离或任务范围发生变化，请重新预览", 409)
        created = exceptions = 0
        draft_ids = []
        # Native confirmation and its proof must share the database clock,
        # including deployments where the API runs on a different host.
        audit_stamp = (
            conn.execute(
                "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')"
            ).fetchone()[0]
            if source_ids is not None and self.db.is_postgres
            else None
        )
        for source in sources:
            payload = {
                "occurred_on": source["date"],
                "description": source["description"],
                "counterparty": source["counterparty"],
                "amount": cents_to_money(source["amount_cents"]),
                "direction": source["direction"],
                "payment_status": "paid",
                "category": "other",
                "project": "",
            }
            learned = self._learning_guard().rule_generate_in_transaction(
                conn,
                actor,
                book_id,
                "daily_cashflow",
                payload,
                source["amount_cents"],
                source_kind="bank",
            )
            legacy_clearance = getattr(self, "legacy_settlements", None)
            if legacy_clearance and legacy_clearance.source_allocated_in_transaction(
                conn, actor, source
            ):
                if source_ids is not None:
                    raise AccountingError("流水已分配历史结算，请重新预览", 409)
                continue
            if not learned and getattr(self, "legacy_learning", None):
                learned = self.legacy_learning.render_in_transaction(conn, actor, book_id, source)
            rule = next(
                (
                    r
                    for r in RULES
                    if r[0] == source["direction"]
                    and any(word in source["description"] for word in r[1])
                ),
                None,
            )
            explanation = (
                f"命中演示规则「{rule[3]}」，建议科目 {rule[2]}。依据：原始摘要「{source['description']}」。金额直接取自原始流水；未判断税额与业务实质，须会计人工复核。"
                if rule
                else "没有匹配到本账套企业规则或演示规则，仅生成银行存款行，需会计选择对应科目。"
            )
            if learned:
                explanation = learned["explanation"]
            draft_id = uuid4().hex
            conn.execute(
                "INSERT INTO drafts(id,book_id,source_id,description,explanation) VALUES(?,?,?,?,?)",
                (draft_id, book_id, source["id"], source["description"], explanation),
            )
            amount = source["amount_cents"]
            debit, credit = (amount, 0) if source["direction"] == "income" else (0, amount)
            lines = [("1002", debit, credit)]
            if rule:
                lines.insert(0, (rule[2], credit, debit))
            if learned:
                lines = learned["lines"]
            conn.executemany(
                "INSERT INTO draft_lines VALUES(?,?,?,?,?,?)",
                [(draft_id, book_id, i, *line) for i, line in enumerate(lines)],
            )
            if learned:
                from .catalog import CatalogService

                CatalogService(self.db).save_auxiliaries_in_transaction(
                    conn, actor, book_id, draft_id, learned["auxiliaries"]
                )
            draft = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            exceptions += bool(self._draft_json(conn, draft, actor)["issues"])
            self._audit(
                conn,
                actor,
                book_id,
                "draft.suggest",
                draft_id,
                {
                    "source_id": source["id"],
                    "rule": rule[3] if rule else None,
                    "company_rule_id": learned.get("rule_id") if learned else None,
                    "company_rule_version": learned.get("rule_version") if learned else None,
                    "migration_rule_id": learned.get("migration_rule_id") if learned else None,
                    "migration_rule_version": learned.get("migration_rule_version")
                    if learned
                    else None,
                },
                created_at=audit_stamp,
            )
            created += 1
            draft_ids.append(draft_id)
        return {"created": created, "exceptions": exceptions, "draft_ids": draft_ids}

    def _editable_draft(self, conn, book_id, draft_id, expected_version):
        draft = conn.execute(
            "SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)
        ).fetchone()
        if not draft:
            raise AccountingError("凭证不存在或无权访问", 404)
        if draft["status"] != "draft":
            raise AccountingError("已审核凭证已冻结，不可修改或再次审核", 409)
        if draft["version"] != expected_version:
            raise AccountingError("凭证版本已更新，请刷新后重新核对", 409)
        source = conn.execute(
            "SELECT * FROM sources WHERE id=? AND book_id=?", (draft["source_id"], book_id)
        ).fetchone()
        if self._source_state_issues(conn, source):
            raise AccountingError("来源已替代、隔离或有待审核的事实修订，请先处理修订流程", 409)
        return draft

    def update_draft(self, actor: Actor, book_id: str, draft_id: str, request: DraftUpdate):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            return self.update_draft_in_transaction(conn, actor, book_id, draft_id, request)

    def update_draft_in_transaction(self, conn, actor, book_id, draft_id, request):
        """Share the exact human-edit guards with atomic clearance commands."""
        self._open(self._book(conn, actor, book_id, edit=True))
        draft = self._editable_draft(conn, book_id, draft_id, request.expected_version)
        before = self._draft_json(conn, draft, actor)
        legacy = getattr(self, "legacy_settlements", None)
        if legacy and legacy.source_allocated_in_transaction(
            conn,
            actor,
            before["source"]
            if "source" in before
            else dict(
                conn.execute(
                    "SELECT * FROM sources WHERE id=? AND book_id=?", (draft["source_id"], book_id)
                ).fetchone()
            ),
        ):
            raise AccountingError("历史清偿已有分配依据，请先撤回未审核清偿再调整分录", 409)
        for line in request.lines:
            account = conn.execute(
                "SELECT active FROM accounts WHERE book_id=? AND code=?",
                (book_id, line.account_code),
            ).fetchone()
            if not account or not account["active"]:
                raise AccountingError(f"科目 {line.account_code} 不属于当前账套或已停用")
        conn.execute("DELETE FROM draft_lines WHERE draft_id=? AND book_id=?", (draft_id, book_id))
        conn.executemany(
            "INSERT INTO draft_lines VALUES(?,?,?,?,?,?)",
            [
                (
                    draft_id,
                    book_id,
                    i,
                    line.account_code,
                    money_to_cents(line.debit),
                    money_to_cents(line.credit),
                )
                for i, line in enumerate(request.lines)
            ],
        )
        self._save_auxiliaries(conn, actor, draft, request.lines)
        conn.execute(
            "UPDATE drafts SET description=?,version=version+1,last_manual_edit_version=version+1 WHERE id=? AND book_id=? AND version=?",
            (request.description, draft_id, book_id, request.expected_version),
        )
        updated = self._draft_json(
            conn,
            conn.execute(
                "SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)
            ).fetchone(),
            actor,
        )
        self._audit(
            conn,
            actor,
            book_id,
            "draft.update",
            draft_id,
            {
                "before": {k: before[k] for k in ("version", "description", "lines")},
                "after": {k: updated[k] for k in ("version", "description", "lines")},
                "reason": request.correction_reason,
            },
        )
        return updated

    def _save_auxiliaries(self, conn, actor, draft, lines):
        from .catalog import CatalogService

        CatalogService(self.db).save_auxiliaries_in_transaction(
            conn, actor, draft["book_id"], draft["id"], [line.auxiliary_values for line in lines]
        )

    def _apply_template_in_transaction(
        self, conn, actor, book_id, draft_id, expected_version, generated
    ):
        """A fenced worker may fill an untouched draft; it never approves it."""
        from .catalog import CatalogService

        self._open(self._book(conn, actor, book_id, edit=True))
        draft = self._editable_draft(conn, book_id, draft_id, expected_version)
        if draft["last_manual_edit_version"]:
            raise AccountingError("会计已经修改草稿，自动任务不能覆盖", 409)
        if generated["issues"]:
            raise AccountingError("模板辅助核算仍有异常，须人工处理", 409)
        conn.execute("DELETE FROM draft_lines WHERE draft_id=? AND book_id=?", (draft_id, book_id))
        conn.executemany(
            "INSERT INTO draft_lines(draft_id,book_id,ordinal,account_code,debit_cents,credit_cents) VALUES(?,?,?,?,?,?)",
            [(draft_id, book_id, i, *line) for i, line in enumerate(generated["lines"])],
        )
        CatalogService(self.db).save_auxiliaries_in_transaction(
            conn, actor, book_id, draft_id, generated["auxiliaries"]
        )
        changed = conn.execute(
            "UPDATE drafts SET explanation=?,version=version+1 WHERE id=? AND book_id=? AND version=? AND status='draft' AND last_manual_edit_version=0",
            (generated["explanation"], draft_id, book_id, expected_version),
        )
        if changed.rowcount != 1:
            raise AccountingError("草稿版本已改变，自动模板未发布", 409)
        result = self._draft_json(
            conn,
            conn.execute(
                "SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)
            ).fetchone(),
            actor,
        )
        if result["issues"]:
            raise AccountingError("模板分录存在当前核对异常，须人工处理", 409)
        self._audit(
            conn,
            actor,
            book_id,
            "draft.company_rule.apply" if generated.get("rule_id") else "draft.template.apply",
            draft_id,
            {
                "template_id": generated["template_id"],
                "template_version": generated["template_version"],
                "rule_id": generated.get("rule_id"),
                "rule_version": generated.get("rule_version"),
                "previous_version": expected_version,
                "applied_version": expected_version + 1,
            },
        )
        return result

    def approve(self, actor: Actor, book_id: str, draft_id: str, request: ApprovalRequest):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            return self._approve_in_transaction(
                conn, actor, book_id, draft_id, request.expected_version
            )

    def _approve_in_transaction(self, conn, actor, book_id, draft_id, expected_version):
        """Single and batch review share every guard and the caller's transaction."""
        book = self._book(conn, actor, book_id, reviewer=True)
        self._open(book)
        draft = self._editable_draft(conn, book_id, draft_id, expected_version)
        own_event = conn.execute(
            "SELECT 1 FROM business_mappings m JOIN business_events e ON e.id=m.event_id AND e.firm_id=m.firm_id AND e.company_id=m.company_id WHERE m.draft_id=? AND m.book_id=? AND e.applicant_id=?",
            (draft_id, book_id, actor.user_id),
        ).fetchone()
        if own_event:
            raise AccountingError("申请人不能审核本人业务事项的凭证", 403)
        value = self._draft_json(conn, draft, actor)
        if value["issues"]:
            raise AccountingError("不能审核通过：" + "；".join(value["issues"]))
        changed = conn.execute(
            "UPDATE drafts SET status='approved',version=version+1,approved_by=?,approved_at=? WHERE id=? AND book_id=? AND version=? AND status='draft'",
            (actor.user_id, now(), draft_id, book_id, expected_version),
        )
        if changed.rowcount != 1:
            raise AccountingError("凭证版本已变化，审核未提交", 409)
        self._learning_guard().capture_in_transaction(
            conn, actor, book, draft, value, expected_version
        )
        self._audit(
            conn,
            actor,
            book_id,
            "draft.approve",
            draft_id,
            {
                "reviewed_version": expected_version,
                "lines": value["lines"],
                "evidence_ref": value["evidence_ref"],
                "review_policy_version": REVIEW_POLICY_VERSION,
            },
        )
        return self._draft_json(
            conn,
            conn.execute(
                "SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)
            ).fetchone(),
            actor,
        )

    def export(self, actor: Actor, book_id: str, request: ExportRequest):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._book(conn, actor, book_id, reviewer=True)
            digest = fingerprint(sorted(request.draft_ids))
            existing = conn.execute(
                "SELECT * FROM exports WHERE book_id=? AND idempotency_key=?",
                (book_id, request.idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != digest:
                    raise AccountingError("导出幂等键已用于不同凭证集合", 409)
                return {k: existing[k] for k in ("id", "csv", "count", "filename")}
            self._open(book)
            selected = []
            for draft_id in request.draft_ids:
                draft = conn.execute(
                    "SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)
                ).fetchone()
                if not draft or draft["status"] != "approved":
                    raise AccountingError("只能导出当前账套内已审核且未导出的凭证", 409)
                value = self._draft_json(conn, draft, actor)
                if value["issues"]:
                    raise AccountingError(
                        "凭证当前校验失败，不能导出：" + "；".join(value["issues"]), 409
                    )
                selected.append(value)
            output = io.StringIO(newline="")
            writer = csv.writer(output, lineterminator="\r\n")
            writer.writerow(
                [
                    "凭证ID",
                    "日期",
                    "摘要",
                    "科目编码",
                    "借方金额",
                    "贷方金额",
                    "币种",
                    "证据引用",
                    "辅助部门",
                    "辅助人员",
                    "辅助交易对方",
                    "辅助项目",
                ]
            )
            for draft in selected:
                for line in draft["lines"]:
                    aux = line.get("auxiliary_values", {})
                    writer.writerow(
                        [
                            safe_csv_text(draft["id"]),
                            draft["source_date"],
                            safe_csv_text(draft["description"]),
                            safe_csv_text(line["account_code"]),
                            line["debit"],
                            line["credit"],
                            "CNY",
                            safe_csv_text(draft["evidence_ref"]),
                            *[
                                safe_csv_text(aux.get(key, ""))
                                for key in ("department", "employee", "counterparty", "project")
                            ],
                        ]
                    )
            export_id = uuid4().hex
            filename = f"vouchers-{book['period']}-{export_id[:8]}.csv"
            csv_text = "\ufeff" + output.getvalue()
            conn.execute(
                "INSERT INTO exports VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    export_id,
                    book_id,
                    request.idempotency_key,
                    digest,
                    csv_text,
                    len(selected),
                    filename,
                    now(),
                    actor.user_id,
                ),
            )
            conn.executemany(
                "UPDATE drafts SET status='exported',version=version+1,exported_at=? WHERE id=? AND book_id=?",
                [(now(), x["id"], book_id) for x in selected],
            )
            self._audit(
                conn,
                actor,
                book_id,
                "draft.export",
                export_id,
                {"draft_ids": request.draft_ids, "count": len(selected)},
            )
            self._delivery_guard().capture_export_in_transaction(
                conn, actor, book, export_id, selected
            )
            return {"id": export_id, "csv": csv_text, "count": len(selected), "filename": filename}

    def close_period(self, actor: Actor, book_id: str, period: str, snapshot_id=None, reason=None):
        with self.db.transaction(actor=actor) as conn:
            book = self._book(conn, actor, book_id, reviewer=True)
            if period != book["period"]:
                raise AccountingError("期间不存在", 404)
            if not snapshot_id or not reason:
                raise AccountingError("请先完成月结预检和资料完整性复核，再提供已审核快照关期", 409)
        from ..closing.service import ClosingService
        from ..closing.models import CloseRequest

        return ClosingService(self.db, self).close(
            actor, book_id, CloseRequest(snapshot_id=snapshot_id, reason=reason)
        )

    def list_exports(self, actor: Actor, book_id: str):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            records = conn.execute(
                "SELECT id,count,filename,created_at FROM exports WHERE book_id=? ORDER BY created_at DESC,id DESC",
                (book_id,),
            ).fetchall()
            return {"exports": [dict(record) for record in records]}

    def get_export(self, actor: Actor, book_id: str, export_id: str):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id, reviewer=True)
            record = conn.execute(
                "SELECT id,csv,count,filename FROM exports WHERE id=? AND book_id=?",
                (export_id, book_id),
            ).fetchone()
            if not record:
                raise AccountingError("导出记录不存在或无权访问", 404)
            return dict(record)
