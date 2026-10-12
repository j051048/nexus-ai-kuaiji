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
from .models import (
    ApprovalRequest, BookCreate, DraftUpdate, EvidenceRequest, ExportRequest, ImportRequest,
    cents_to_money, money_to_cents,
)


CHART = [
    ("1002", "银行存款"), ("1122", "应收账款"), ("2202", "应付账款"),
    ("6001", "主营业务收入"), ("2241", "其他应付款"), ("6602.01", "管理费用—房租"),
    ("6602.02", "管理费用—办公费"), ("6602.03", "管理费用—交通费"),
    ("6602.04", "管理费用—通信费"), ("6602.99", "管理费用—其他"),
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
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


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

    def _member(self, conn, actor: Actor, reviewer: bool = False):
        role = self.access.firm_role(conn, actor)
        if not role or (reviewer and role not in {"owner", "admin", "reviewer"}):
            raise AccountingError("没有事务所成员权限或所需复核权限", 403)
        return {"role": role}

    def _book(self, conn, actor: Actor, book_id: str, reviewer: bool = False, edit: bool = False):
        return self.access.book(conn, actor, book_id, "review" if reviewer else "edit" if edit else "read")

    def require_access(self, actor: Actor, book_id: str):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)

    def _open(self, book):
        if book["period_status"] != "open":
            raise AccountingError("该会计期间已关闭，不可写入", 403)

    def _audit(self, conn, actor, book_id, action, object_id, details):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
            (actor.firm_id, book_id, actor.user_id, action, object_id, json.dumps(details, ensure_ascii=False), now()),
        )

    def create_book(self, actor: Actor, request: BookCreate):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            company_id = request.company_id
            if company_id:
                self.access.company(conn, actor, company_id, manage=True)
            else:
                company_id = uuid4().hex
                conn.execute("INSERT INTO companies(id,name,tax_id) VALUES(?,?,'')", (company_id, request.name))
                conn.execute("INSERT INTO firm_clients(firm_id,company_id,status) VALUES(?,?,'active')", (actor.firm_id, company_id))
            book_id = uuid4().hex
            conn.execute("INSERT INTO books(id,firm_id,name,period,company_id) VALUES(?,?,?,?,?)", (book_id, actor.firm_id, request.name, request.period, company_id))
            conn.execute("INSERT INTO book_access(book_id,firm_id,user_id,role) VALUES(?,?,?,'reviewer')", (book_id, actor.firm_id, actor.user_id))
            conn.execute("INSERT INTO periods VALUES(?,?,'open')", (book_id, request.period))
            conn.executemany("INSERT INTO accounts(book_id,code,name) VALUES(?,?,?)", [(book_id, *row) for row in CHART])
            self._audit(conn, actor, book_id, "book.create", book_id, {"name": request.name, "period": request.period})
            return self._book_summary(conn, self._book(conn, actor, book_id), actor)

    def _effective_evidence(self, conn, source):
        addition = conn.execute(
            "SELECT evidence_ref FROM source_evidence WHERE source_id=? AND book_id=? ORDER BY id DESC LIMIT 1",
            (source["id"], source["book_id"]),
        ).fetchone()
        return addition["evidence_ref"] if addition else source["evidence_ref"]

    def _source_json(self, conn, source):
        draft = conn.execute("SELECT status FROM drafts WHERE source_id=? AND book_id=?", (source["id"], source["book_id"])).fetchone()
        return {
            "id": source["id"], "external_id": source["external_id"], "date": source["date"],
            "description": source["description"], "counterparty": source["counterparty"],
            "amount": cents_to_money(source["amount_cents"]), "direction": source["direction"],
            "evidence_ref": self._effective_evidence(conn, source), "original_evidence_ref": source["evidence_ref"],
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

            self.intelligence = IntelligenceService(self.db, storage=None, provider=OpenAICompatibleProvider(AISettings()))
        return self.intelligence

    def _guard_issues(self, conn, source, actor=None):
        if source["source_kind"] == "bank":
            linked = conn.execute("SELECT 1 FROM ai_bank_links WHERE bank_source_id=? AND book_id=?", (source["id"], source["book_id"])).fetchone()
            return ["该银行流水已关联业务事项，请复核业务凭证，禁止重复审核或导出流水凭证"] if linked else []
        # Scope comes from the canonical source->mapping->draft relationship,
        # never from a model-supplied event or book identity. Disabled policy
        # retains the earlier manual accounting workflow without OCR demands.
        event = conn.execute(
            "SELECT e.* FROM business_mappings m JOIN business_events e "
            "ON e.id=m.event_id AND e.firm_id=m.firm_id AND e.company_id=m.company_id "
            "JOIN drafts d ON d.id=m.draft_id AND d.book_id=m.book_id AND d.source_id=m.source_id "
            "JOIN ai_company_settings s ON s.firm_id=m.firm_id AND s.company_id=m.company_id AND s.enabled=1 "
            "WHERE m.source_id=? AND m.book_id=?", (source["id"], source["book_id"]),
        ).fetchone()
        if event is None:
            return []
        if actor is None:
            return ["请使用当前授权会计账号核对票据及流水异常"]
        assessment = self._intelligence_guard()._assessment(conn, actor, event, persist=False)
        return list(dict.fromkeys(
            "票据核对未完成：" + issue["payload"]["message"]
            for issue in assessment["findings"] if issue["severity"] == "blocking" and not issue["resolved"]
        ))

    def _issues(self, conn, source, lines, actor=None):
        accounts = {x["code"] for x in conn.execute("SELECT code FROM accounts WHERE book_id=? AND active=1", (source["book_id"],))}
        return self._calculate_issues(source, lines, accounts, self._effective_evidence(conn, source)) + self._guard_issues(conn, source, actor)

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
        bank_debit = sum(x["debit_cents"] for x in lines if x["account_code"] == "1002")
        bank_credit = sum(x["credit_cents"] for x in lines if x["account_code"] == "1002")
        expected = (source["amount_cents"], 0) if source["direction"] == "income" else (0, source["amount_cents"])
        if source["source_kind"] == "bank" and (bank_debit, bank_credit) != expected:
            issues.append("银行存款分录的金额或方向与原始流水不符")
        return list(dict.fromkeys(issues))

    def _draft_json(self, conn, draft, actor=None):
        source = conn.execute("SELECT * FROM sources WHERE id=? AND book_id=?", (draft["source_id"], draft["book_id"])).fetchone()
        lines = conn.execute("SELECT * FROM draft_lines WHERE draft_id=? AND book_id=? ORDER BY ordinal", (draft["id"], draft["book_id"])).fetchall()
        mapping = None
        if source["source_kind"] == "business":
            row = conn.execute("SELECT event_id,submitted_version FROM business_mappings WHERE source_id=? AND book_id=?", (source["id"], source["book_id"])).fetchone()
            mapping = dict(row) if row else None
        return {
            "id": draft["id"], "source_id": draft["source_id"], "description": draft["description"],
            "status": draft["status"], "version": draft["version"], "explanation": draft["explanation"],
            "issues": self._issues(conn, source, lines, actor), "source_date": source["date"],
            "source_amount": cents_to_money(source["amount_cents"]), "source_description": source["description"],
            "evidence_ref": self._effective_evidence(conn, source),
            "source_kind": source["source_kind"], "business_origin": mapping,
            "lines": [{"account_code": x["account_code"], "debit": cents_to_money(x["debit_cents"]), "credit": cents_to_money(x["credit_cents"])} for x in lines],
        }

    def _book_summary(self, conn, book, actor=None):
        drafts = conn.execute("SELECT * FROM drafts WHERE book_id=?", (book["id"],)).fetchall()
        pending = [x for x in drafts if x["status"] == "draft"]
        return {
            "id": book["id"], "name": book["name"], "period": book["period"], "period_status": book["period_status"],
            "company_id": book["company_id"], "company_name": book["company_name"], "permissions": book["permissions"],
            "pending_count": len(pending), "exception_count": sum(bool(self._draft_json(conn, x, actor)["issues"]) for x in pending),
            "approved_count": len(drafts) - len(pending),
        }

    def workbench(self, actor: Actor):
        with self.db.transaction(actor=actor) as conn:
            firm_role = self.access.firm_role(conn, actor)
            books = conn.execute(
                "SELECT b.id,b.name,b.period,b.company_id,c.name AS company_name,p.status AS period_status,a.role AS book_role,cm.role AS company_role, "
                "SUM(CASE WHEN d.status='draft' THEN 1 ELSE 0 END) AS pending_count, "
                "SUM(CASE WHEN d.status IN ('approved','exported') THEN 1 ELSE 0 END) AS approved_count "
                "FROM books b LEFT JOIN book_access a ON a.book_id=b.id AND a.firm_id=b.firm_id AND a.user_id=? "
                "JOIN companies c ON c.id=b.company_id JOIN firm_clients fc ON fc.company_id=b.company_id AND fc.firm_id=b.firm_id AND fc.status='active' "
                "LEFT JOIN company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=? "
                "JOIN periods p ON p.book_id=b.id AND p.period=b.period LEFT JOIN drafts d ON d.book_id=b.id "
                "WHERE b.firm_id=? GROUP BY b.id,b.name,b.period,b.company_id,c.name,p.status,a.role,cm.role ORDER BY b.name",
                (actor.user_id, actor.user_id, actor.firm_id),
            ).fetchall()
            visible = []
            for book in books:
                permissions = self.access.permissions(firm_role, book["book_role"], book["company_role"])
                if permissions["read"]:
                    visible.append({**dict(book), "permissions": {k: v for k, v in permissions.items() if k != "read"}})
            if not visible:
                return {"books": [], "stats": {"book_count": 0, "pending_count": 0, "exception_count": 0, "approved_count": 0}}
            ids = [book["id"] for book in visible]
            placeholders = ",".join("?" for _ in ids)
            # 总览始终只执行固定数量查询，避免 500 个账套逐凭证查询造成 N+1。
            # 复用同一校验函数，不在统计 SQL 中重新实现一份审核业务规则。
            pending = conn.execute(
                "SELECT s.*,d.id AS draft_id,COALESCE((SELECT e.evidence_ref FROM source_evidence e "
                "WHERE e.book_id=s.book_id AND e.source_id=s.id ORDER BY e.id DESC LIMIT 1),s.evidence_ref) AS current_evidence "
                "FROM drafts d JOIN sources s ON s.id=d.source_id AND s.book_id=d.book_id "
                f"WHERE d.book_id IN ({placeholders}) AND d.status='draft'", ids,
            ).fetchall()
            lines_by_draft = {}
            for line in conn.execute(
                "SELECT l.* FROM draft_lines l JOIN drafts d ON d.id=l.draft_id AND d.book_id=l.book_id "
                f"WHERE l.book_id IN ({placeholders}) AND d.status='draft'", ids,
            ):
                lines_by_draft.setdefault(line["draft_id"], []).append(line)
            accounts_by_book = {}
            for account in conn.execute(
                f"SELECT ac.book_id,ac.code FROM accounts ac WHERE ac.book_id IN ({placeholders}) AND ac.active=1", ids,
            ):
                accounts_by_book.setdefault(account["book_id"], set()).add(account["code"])
            exception_counts = {}
            for source in pending:
                if self._calculate_issues(source, lines_by_draft.get(source["draft_id"], []), accounts_by_book.get(source["book_id"], set()), source["current_evidence"]):
                    exception_counts[source["book_id"]] = exception_counts.get(source["book_id"], 0) + 1
            result = [{k: v for k, v in {**x, "exception_count": exception_counts.get(x["id"], 0)}.items() if k not in {"book_role", "company_role"}} for x in visible]
            return {"books": result, "stats": {"book_count": len(result), **{key: sum(x[key] for x in result) for key in ("pending_count", "exception_count", "approved_count")}}}

    def get_book(self, actor: Actor, book_id: str):
        with self.db.transaction(actor=actor) as conn:
            book = self._book(conn, actor, book_id)
            return {
                "book": self._book_summary(conn, book, actor),
                "accounts": [dict(x) for x in conn.execute("SELECT code,name FROM accounts WHERE book_id=? AND active=1 ORDER BY code", (book_id,))],
                "sources": [self._source_json(conn, x) for x in conn.execute("SELECT * FROM sources WHERE book_id=? ORDER BY date,id", (book_id,))],
                "drafts": [self._draft_json(conn, x, actor) for x in conn.execute("SELECT * FROM drafts WHERE book_id=? ORDER BY id DESC", (book_id,))],
            }

    def import_sources(self, actor: Actor, book_id: str, request: ImportRequest):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self._book(conn, actor, book_id, edit=True)
            self._open(book)
            imported = duplicates = 0
            for row in request.rows:
                if row.date[:7] != book["period"]:
                    raise AccountingError(f"流水 {row.external_id} 日期不属于当前会计期间")
                digest = fingerprint(row.model_dump())
                existing = conn.execute("SELECT fingerprint FROM sources WHERE book_id=? AND external_id=?", (book_id, row.external_id)).fetchone()
                if existing:
                    if existing["fingerprint"] != digest:
                        raise AccountingError(f"外部 ID {row.external_id} 已存在且内容不同，整批导入已回滚", 409)
                    duplicates += 1
                    continue
                source_id = uuid4().hex
                conn.execute(
                    "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (source_id, book_id, row.external_id, request.source_name, row.date, row.description, row.counterparty,
                     money_to_cents(row.amount), row.direction, row.evidence_ref, digest),
                )
                imported += 1
            self._audit(conn, actor, book_id, "source.import", book_id, {"source_name": request.source_name, "imported": imported, "duplicates": duplicates})
            return {"imported": imported, "duplicates": duplicates}

    def add_evidence(self, actor: Actor, book_id: str, source_id: str, request: EvidenceRequest):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._open(self._book(conn, actor, book_id, edit=True))
            source = conn.execute("SELECT * FROM sources WHERE id=? AND book_id=?", (source_id, book_id)).fetchone()
            if not source:
                raise AccountingError("来源不存在或无权访问", 404)
            draft = conn.execute("SELECT * FROM drafts WHERE source_id=? AND book_id=?", (source_id, book_id)).fetchone()
            if draft and draft["status"] != "draft":
                raise AccountingError("已审核资料已冻结，不可补充或修改证据", 409)
            conn.execute("INSERT INTO source_evidence(source_id,book_id,evidence_ref,created_at,created_by) VALUES(?,?,?,?,?)", (source_id, book_id, request.evidence_ref, now(), actor.user_id))
            # 补证影响审核上下文，因此使已有草稿版本失效。
            if draft:
                conn.execute("UPDATE drafts SET version=version+1 WHERE id=? AND book_id=?", (draft["id"], book_id))
            self._audit(conn, actor, book_id, "source.evidence.add", source_id, {"evidence_ref": request.evidence_ref, "verified": False})
            return self._source_json(conn, source)

    def suggestions(self, actor: Actor, book_id: str):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._open(self._book(conn, actor, book_id, edit=True))
            sources = conn.execute("SELECT s.* FROM sources s LEFT JOIN drafts d ON d.source_id=s.id WHERE s.book_id=? AND d.id IS NULL ORDER BY s.date,s.id", (book_id,)).fetchall()
            created = exceptions = 0
            for source in sources:
                rule = next((r for r in RULES if r[0] == source["direction"] and any(word in source["description"] for word in r[1])), None)
                explanation = (
                    f"命中演示规则「{rule[3]}」，建议科目 {rule[2]}。依据：原始摘要「{source['description']}」。金额直接取自原始流水；未判断税额与业务实质，须会计人工复核。"
                    if rule else "没有匹配到演示规则，仅生成银行存款行，需会计选择对应科目。系统没有接入 LLM。"
                )
                draft_id = uuid4().hex
                conn.execute("INSERT INTO drafts(id,book_id,source_id,description,explanation) VALUES(?,?,?,?,?)", (draft_id, book_id, source["id"], source["description"], explanation))
                amount = source["amount_cents"]
                debit, credit = ((amount, 0) if source["direction"] == "income" else (0, amount))
                lines = [("1002", debit, credit)]
                if rule:
                    lines.insert(0, (rule[2], credit, debit))
                conn.executemany("INSERT INTO draft_lines VALUES(?,?,?,?,?,?)", [(draft_id, book_id, i, *line) for i, line in enumerate(lines)])
                draft = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
                exceptions += bool(self._draft_json(conn, draft, actor)["issues"])
                self._audit(conn, actor, book_id, "draft.suggest", draft_id, {"source_id": source["id"], "rule": rule[3] if rule else None})
                created += 1
            return {"created": created, "exceptions": exceptions}

    def _editable_draft(self, conn, book_id, draft_id, expected_version):
        draft = conn.execute("SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)).fetchone()
        if not draft:
            raise AccountingError("凭证不存在或无权访问", 404)
        if draft["status"] != "draft":
            raise AccountingError("已审核凭证已冻结，不可修改或再次审核", 409)
        if draft["version"] != expected_version:
            raise AccountingError("凭证版本已更新，请刷新后重新核对", 409)
        return draft

    def update_draft(self, actor: Actor, book_id: str, draft_id: str, request: DraftUpdate):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._open(self._book(conn, actor, book_id, edit=True))
            draft = self._editable_draft(conn, book_id, draft_id, request.expected_version)
            before = self._draft_json(conn, draft, actor)
            for line in request.lines:
                account = conn.execute("SELECT active FROM accounts WHERE book_id=? AND code=?", (book_id, line.account_code)).fetchone()
                if not account or not account["active"]:
                    raise AccountingError(f"科目 {line.account_code} 不属于当前账套或已停用")
            conn.execute("DELETE FROM draft_lines WHERE draft_id=? AND book_id=?", (draft_id, book_id))
            conn.executemany("INSERT INTO draft_lines VALUES(?,?,?,?,?,?)", [(draft_id, book_id, i, line.account_code, money_to_cents(line.debit), money_to_cents(line.credit)) for i, line in enumerate(request.lines)])
            conn.execute("UPDATE drafts SET description=?,version=version+1 WHERE id=? AND book_id=? AND version=?", (request.description, draft_id, book_id, request.expected_version))
            updated = self._draft_json(conn, conn.execute("SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)).fetchone(), actor)
            self._audit(conn, actor, book_id, "draft.update", draft_id, {"before": {k: before[k] for k in ("version", "description", "lines")}, "after": {k: updated[k] for k in ("version", "description", "lines")}})
            return updated

    def approve(self, actor: Actor, book_id: str, draft_id: str, request: ApprovalRequest):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._open(self._book(conn, actor, book_id, reviewer=True))
            draft = self._editable_draft(conn, book_id, draft_id, request.expected_version)
            value = self._draft_json(conn, draft, actor)
            if value["issues"]:
                raise AccountingError("不能审核通过：" + "；".join(value["issues"]))
            conn.execute("UPDATE drafts SET status='approved',version=version+1,approved_by=?,approved_at=? WHERE id=? AND book_id=? AND version=?", (actor.user_id, now(), draft_id, book_id, request.expected_version))
            self._audit(conn, actor, book_id, "draft.approve", draft_id, {"reviewed_version": request.expected_version, "lines": value["lines"], "evidence_ref": value["evidence_ref"]})
            return self._draft_json(conn, conn.execute("SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)).fetchone(), actor)

    def export(self, actor: Actor, book_id: str, request: ExportRequest):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self._book(conn, actor, book_id, reviewer=True)
            digest = fingerprint(sorted(request.draft_ids))
            existing = conn.execute("SELECT * FROM exports WHERE book_id=? AND idempotency_key=?", (book_id, request.idempotency_key)).fetchone()
            if existing:
                if existing["request_fingerprint"] != digest:
                    raise AccountingError("导出幂等键已用于不同凭证集合", 409)
                return {k: existing[k] for k in ("id", "csv", "count", "filename")}
            self._open(book)
            selected = []
            for draft_id in request.draft_ids:
                draft = conn.execute("SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)).fetchone()
                if not draft or draft["status"] != "approved":
                    raise AccountingError("只能导出当前账套内已审核且未导出的凭证", 409)
                value = self._draft_json(conn, draft, actor)
                if value["issues"]:
                    raise AccountingError("凭证当前校验失败，不能导出：" + "；".join(value["issues"]), 409)
                selected.append(value)
            output = io.StringIO(newline="")
            writer = csv.writer(output, lineterminator="\r\n")
            writer.writerow(["凭证ID", "日期", "摘要", "科目编码", "借方金额", "贷方金额", "币种", "证据引用"])
            for draft in selected:
                for line in draft["lines"]:
                    writer.writerow([safe_csv_text(draft["id"]), draft["source_date"], safe_csv_text(draft["description"]), safe_csv_text(line["account_code"]), line["debit"], line["credit"], "CNY", safe_csv_text(draft["evidence_ref"])])
            export_id = uuid4().hex
            filename = f"vouchers-{book['period']}-{export_id[:8]}.csv"
            csv_text = "\ufeff" + output.getvalue()
            conn.execute("INSERT INTO exports VALUES(?,?,?,?,?,?,?,?,?)", (export_id, book_id, request.idempotency_key, digest, csv_text, len(selected), filename, now(), actor.user_id))
            conn.executemany("UPDATE drafts SET status='exported',version=version+1,exported_at=? WHERE id=? AND book_id=?", [(now(), x["id"], book_id) for x in selected])
            self._audit(conn, actor, book_id, "draft.export", export_id, {"draft_ids": request.draft_ids, "count": len(selected)})
            return {"id": export_id, "csv": csv_text, "count": len(selected), "filename": filename}

    def close_period(self, actor: Actor, book_id: str, period: str):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self._book(conn, actor, book_id, reviewer=True)
            if period != book["period"]:
                raise AccountingError("期间不存在", 404)
            if book["period_status"] == "closed":
                return {"period": period, "status": "closed"}
            conn.execute("UPDATE periods SET status='closed' WHERE book_id=? AND period=?", (book_id, period))
            self._audit(conn, actor, book_id, "period.close", period, {})
            return {"period": period, "status": "closed"}

    def list_exports(self, actor: Actor, book_id: str):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            records = conn.execute("SELECT id,count,filename,created_at FROM exports WHERE book_id=? ORDER BY created_at DESC,id DESC", (book_id,)).fetchall()
            return {"exports": [dict(record) for record in records]}

    def get_export(self, actor: Actor, book_id: str, export_id: str):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id, reviewer=True)
            record = conn.execute("SELECT id,csv,count,filename FROM exports WHERE id=? AND book_id=?", (export_id, book_id)).fetchone()
            if not record:
                raise AccountingError("导出记录不存在或无权访问", 404)
            return dict(record)
