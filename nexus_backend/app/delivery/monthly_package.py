"""Immutable monthly evidence packages. Originals and import receipts stay separate.

All accounting values come from the existing formal posting/report services.
Only canonical JSON is retained, so every download reconstructs identical bytes.
"""

import hashlib
import csv
import io
import json
from html import escape
from uuid import uuid4
from zipfile import ZipFile, ZipInfo, ZIP_STORED

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError, Actor
from ..accounting.service import AccountingService, now, safe_csv_text
from ..business.service import BusinessService
from ..posting.service import PostingService
from ..reports.service import ReportService
from ..service_plans.service import ServicePlansService
from ..service_plans.attachments import MaterialAttachmentService
from ..subledger.service import SubledgerService
from .service import DeliveryService, encode

MAX_ROWS = 10000
MAX_SNAPSHOT_BYTES = 8_000_000
FILE_PATHS = frozenset(
    {
        "README.html",
        "report.html",
        "account_balances.csv",
        "formal_vouchers.json",
        "formal_books.json",
        "published_report.json",
        "bank_verification.json",
        "frozen_exports.json",
        "materials.json",
        "unresolved_matters.json",
        "originals_index.json",
    }
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(data):
    return encode(data).encode("utf-8")


def pick(row, keys):
    return {key: row[key] for key in keys.split()}


def money(cents):
    return ("-" if cents < 0 else "") + f"{abs(cents) // 100}.{abs(cents) % 100:02d}"


def render_report(value):
    """Escape every dynamic field. This is an offline document, with no scripts."""
    labels = {
        "balance_sheet": "资产负债表",
        "profit_statement": "利润表",
        "cash_flow": "现金流量表",
        "assets": "资产合计（分）",
        "liabilities": "负债合计（分）",
        "equity": "所有者权益（分）",
        "unclosed_profit": "未结转利润（分）",
        "revenue_cents": "收入（分）",
        "expense_cents": "费用（分）",
        "profit_cents": "利润（分）",
        "items": "科目明细",
        "code": "科目代码",
        "name": "科目名称",
        "amount_cents": "金额（分）",
        "account_id": "科目标识",
        "section": "报表分类",
    }
    if isinstance(value, dict):
        return (
            "<dl>"
            + "".join(
                "<dt>"
                + escape(labels.get(str(k), str(k)))
                + "</dt><dd>"
                + render_report(v)
                + "</dd>"
                for k, v in value.items()
            )
            + "</dl>"
        )
    if isinstance(value, list):
        return "<ol>" + "".join("<li>" + render_report(v) + "</li>" for v in value) + "</ol>"
    return escape("—" if value is None else str(value))


def readable_files(book, version, report, trial, contact_user_id):
    head = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'"><title>月度交付包</title><style>body{font:16px sans-serif;line-height:1.7;max-width:960px;margin:40px auto;padding:16px}dt{font-weight:bold}dd{margin-bottom:12px}a{color:#174db3}</style><body>'
    identity = f"<h1>{escape(book['company_name'])} · {escape(book['period'])} 月度交付包</h1><p>版本 {version}；账套 {escape(book['id'])}。</p>"
    readme = (
        head
        + identity
        + "<p>本包冻结了会计复核后的正式凭证、账簿及所选已发布报表。金额单位见各文件字段；CSV 金额单位为人民币元，JSON 金额 *_cents 为分。</p>"
    )
    readme += "<p>客户签收确认收到此版本；签收不代表财务软件导入成功、税务申报完成或审计意见。财务软件导入回执通过独立流程登记。</p>"
    readme += "<p>原始文件不随包外发。原件索引只列标识、类型、大小与 SHA-256；如需查阅原件，应在系统内重新核验权限。</p>"
    readme += (
        "<p>报表标识："
        + escape(report["id"])
        + "；版本："
        + str(report["version"])
        + "；客户联系人："
        + escape(contact_user_id)
        + "。</p>"
    )
    readme += '<ul><li><a href="report.html">已发布财务报表</a></li><li><a href="account_balances.csv">正式科目余额 CSV</a></li><li><a href="manifest.json">版本清单与文件 SHA-256</a></li><li><a href="originals_index.json">原件索引</a></li><li><a href="unresolved_matters.json">未解决事项</a></li></ul><p>无资料请求配置时，materials.json 会明确标记 no_requests_configured，不表示系统证明所有资料已收齐。</p></body></html>'
    rendered = (
        head
        + identity
        + "<h2>已发布财务报表（冻结内容）</h2>"
        + render_report(json.loads(report["content_json"]))
        + "</body></html>"
    )
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(
        [
            "企业",
            "期间",
            "科目代码",
            "科目名称",
            "期初余额（元，借正贷负）",
            "借方发生（元）",
            "贷方发生（元）",
            "期末余额（元，借正贷负）",
        ]
    )
    for row in trial["rows"]:
        writer.writerow(
            [
                safe_csv_text(str(value))
                for value in (book["company_name"], book["period"], row["code"], row["name"])
            ]
            + [
                money(row[key])
                for key in ("opening_cents", "debit_cents", "credit_cents", "closing_cents")
            ]
        )
    return {
        "README.html": readme,
        "report.html": rendered,
        "account_balances.csv": "\ufeff" + stream.getvalue(),
    }


class MonthlyPackageService:
    def __init__(self, db, plans=None):
        self.db, self.access = db, AccessControl()
        self.posting, self.reports = PostingService(db), ReportService(db)
        self.subledger, self.delivery = SubledgerService(db), DeliveryService(db)
        self.plans = plans or ServicePlansService(db, AccountingService(db), BusinessService(db))
        if self.plans.attachments is None:
            # Only metadata methods are used; no blob store reads or writes.
            self.plans.attachments = MaterialAttachmentService(self.plans, None)

    @staticmethod
    def _rows(conn, sql, args):
        rows = conn.execute(sql, args).fetchmany(MAX_ROWS + 1)
        if len(rows) > MAX_ROWS:
            raise AccountingError("交付包数据超出单包限制，请联系管理员分批处理", 409)
        return rows

    def _contact(self, conn, actor, book):
        plan = self.plans._plan(conn, book["id"])
        user_id = plan["contact_user_id"] if plan else None
        eligible = False
        if self.db.is_postgres and not plan:
            # Service plans have staff-only RLS. A read-only recipient can prove
            # their own current eligibility without exposing another person's plan.
            eligible = bool(
                conn.execute(
                    "SELECT accounting.monthly_delivery_contact_eligible(?,?)",
                    (book["id"], actor.user_id),
                ).fetchone()[0]
            )
            if eligible:
                user_id = actor.user_id
        if user_id:
            if self.db.is_postgres:
                eligible = bool(
                    conn.execute(
                        "SELECT accounting.monthly_delivery_contact_eligible(?,?)",
                        (book["id"], user_id),
                    ).fetchone()[0]
                )
            else:
                eligible = bool(conn.execute("SELECT 1 FROM monthly_delivery_eligible_contacts WHERE book_id=? AND user_id=?", (book["id"], user_id)).fetchone())
        return {
            "user_id": user_id,
            "eligible": eligible,
            "status": "configured" if eligible else "ineligible" if user_id else "missing",
        }, plan

    def _can_review(self, conn, book, user_id):
        """Ask the same membership rules whether this person could generate the package."""
        from ..accounting.domain import Actor

        try:
            permissions = self.access.book(conn, Actor(user_id, book["firm_id"]), book["id"])[
                "permissions"
            ]
        except AccountingError:
            return False
        return bool(permissions["review"])

    def _materials(self, conn, actor, book):
        rows = self._rows(
            conn,
            "SELECT r.*,c.name AS company_name FROM material_requests r "
            "JOIN companies c ON c.id=r.company_id WHERE r.book_id=? ORDER BY r.id",
            (book["id"],),
        )
        result = []
        for row in rows:
            public = self.plans._public(conn, actor, row)
            result.append(
                {
                    **pick(public, "id title required state version evidence_current last_review"),
                    "original_refs": [
                        pick(item, "id sha256 size_bytes media_type")
                        for item in (public["latest_submission"] or {}).get("attachments", [])
                    ],
                }
            )
        return result

    def _inspect(self, conn, actor, book):
        blockers, warnings = [], []

        def block(code, message, owner, entry):
            blockers.append({"code": code, "message": message, "owner": owner, "entry": entry})

        def warn(code, message, owner, entry):
            warnings.append({"code": code, "message": message, "owner": owner, "entry": entry})

        contact, plan = self._contact(conn, actor, book)
        if not contact["eligible"]:
            if contact["user_id"]:
                block(
                    "contact_ineligible",
                    "客户联系人已配置，但缺少有效企业身份或账套查看权限",
                    "事务所管理员",
                    "企业与账号 · 账套成员",
                )
            else:
                block(
                    "contact_missing",
                    "尚未配置客户联系人；客户看不到也签收不了本月交付包",
                    "事务所管理员或服务会计",
                    "客户服务单 · 客户联系人",
                )
        if contact["user_id"] and self._can_review(conn, book, contact["user_id"]):
            # Generation stays allowed (a one-person firm still has to work), but the
            # signature boundary will refuse this person, so warn before freezing.
            warn(
                "contact_can_review",
                "该联系人同时拥有复核权限，本版交付包将无法由其签收；请另行指定只读联系人",
                "事务所管理员或服务会计",
                "客户服务单 · 客户联系人",
            )
        if not conn.execute(
            "SELECT 1 FROM internal_period_locks WHERE book_id=? AND period=?",
            (book["id"], book["period"]),
        ).fetchone():
            block(
                "period_not_locked",
                "请完成独立月结复核并锁定本月",
                "项目复核人",
                "月结 · 独立复核",
            )
        trial = None
        bank = None
        try:
            trial = self.posting.trial_balance_in_transaction(conn, actor, book["id"])
            totals = trial["totals"]
            if (
                totals["opening_cents"]
                or totals["closing_cents"]
                or totals["debit_cents"] != totals["credit_cents"]
            ):
                block("trial_not_balanced", "正式账簿试算不平衡", "记账会计", "正式账簿 · 试算平衡")
            bank = self.subledger.bank_reconciliation_in_transaction(
                conn, actor, book["id"], trial=trial
            )
            if not bank["passed"]:
                block("bank_not_verified", "银行核验尚未通过", "出纳或记账会计", "银行对账 · 核验")
        except AccountingError:
            block(
                "formal_books_not_ready",
                "正式期初、账簿或银行核验尚未就绪",
                "记账会计",
                "期初与正式记账",
            )
        reports = []
        for row in self._rows(
            conn,
            "SELECT * FROM report_runs WHERE book_id=? AND period=? AND status='published' ORDER BY created_at DESC,id",
            (book["id"], book["period"]),
        ):
            current = False
            try:
                self.reports._current_in_transaction(conn, actor, row["id"], trial=trial)
                current = bool(
                    row["reviewed_by"]
                    and row["reviewed_by"] != row["created_by"]
                    and row["published_at"]
                )
            except AccountingError:
                pass
            reports.append(
                {**pick(row, "id version status published_at content_hash"), "current": current}
            )
        if not any(row["current"] for row in reports):
            block(
                "published_report_missing",
                "缺少当前有效且经过独立复核的已发布报表",
                "复核人",
                "财务报表 · 发布",
            )
        materials = self._materials(conn, actor, book) if book["permissions"]["review"] else []
        if not book["permissions"]["review"]:
            block(
                "review_required",
                "冻结交付前请由有账套复核权限的会计核验资料与完整性",
                "复核人",
                "账套权限 · 复核",
            )
        if any(
            row["required"] and (row["state"] != "accepted" or row["evidence_current"] is not True)
            for row in materials
        ):
            block(
                "required_materials_unaccepted",
                "必需资料存在缺件、未审核或证据已变化",
                "客户或记账会计",
                "资料请求",
            )
        pending = conn.execute(
            "SELECT COUNT(*) FROM drafts d WHERE d.book_id=? AND (d.status NOT IN('approved','exported') OR NOT EXISTS(SELECT 1 FROM vouchers v WHERE v.book_id=d.book_id AND v.source_kind='draft' AND v.source_id=d.id))",
            (book["id"],),
        ).fetchone()[0]
        if pending:
            block(
                "drafts_not_posted",
                "存在未复核或尚未正式入账的凭证草稿",
                "记账会计",
                "凭证 · 复核过账",
            )
        missing_exports = conn.execute(
            "SELECT COUNT(*) FROM exports e LEFT JOIN delivery_manifests m ON m.export_id=e.id WHERE e.book_id=? AND m.export_id IS NULL",
            (book["id"],),
        ).fetchone()[0]
        if missing_exports:
            block(
                "export_evidence_missing",
                "历史导出缺少冻结交付清单，请先补齐可核验依据",
                "记账会计",
                "交付回执",
            )
        return {
            "blockers": blockers,
            "warnings": warnings,
            "reports": reports,
            "contact": contact,
            "plan": plan,
            "materials": materials,
            "trial": trial,
            "bank": bank,
        }

    def readiness(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id)
            evidence = self._inspect(conn, actor, book)
            packages = self._rows(
                conn,
                "SELECT * FROM monthly_delivery_packages WHERE book_id=? ORDER BY version DESC",
                (book_id,),
            )
            return {
                "book_id": book_id,
                "company_id": book["company_id"],
                "period": book["period"],
                "ready": not evidence["blockers"],
                "blockers": evidence["blockers"],
                "warnings": evidence["warnings"],
                "reports": evidence["reports"],
                "contact": evidence["contact"],
                "permissions": {"generate": bool(book["permissions"]["review"])},
                "packages": [self._view(conn, actor, book, row) for row in packages],
            }

    def _view(self, conn, actor, book, row, with_files=False):
        acknowledgement = conn.execute(
            "SELECT * FROM monthly_delivery_acknowledgements WHERE package_id=?", (row["id"],)
        ).fetchone()
        opening = conn.execute(
            "SELECT * FROM monthly_delivery_opens WHERE package_id=?", (row["id"],)
        ).fetchone()
        contact, _ = self._contact(conn, actor, book)
        is_contact = bool(
            contact["eligible"] and contact["user_id"] == row["contact_user_id"] == actor.user_id
        )
        can_ack = bool(is_contact and row["created_by"] != actor.user_id)
        latest = conn.execute(
            "SELECT MAX(version) FROM monthly_delivery_packages WHERE book_id=?", (row["book_id"],)
        ).fetchone()[0]
        view = {
            **pick(
                row,
                "id version book_id company_id period report_id report_version manifest_sha256 created_at created_by contact_user_id",
            ),
            "status": "frozen",
            "superseded": row["version"] != latest,
            "can_open": is_contact,
            "can_acknowledge": can_ack and not acknowledgement,
            "customer_opening_status": "opened" if opening else "not_opened",
            "customer_receipt_status": "acknowledged" if acknowledgement else "not_recorded",
            "external_import_status": "separate_workflow",
            "opening": {
                "opened_at": opening["opened_at"],
                "opened_by": opening["opened_by"],
            }
            if opening
            else None,
            "acknowledgement": {
                "acknowledged_at": acknowledgement["created_at"],
                "acknowledged_by": acknowledgement["created_by"],
                "reason": acknowledgement["reason"],
            }
            if acknowledgement
            else None,
        }
        if with_files:
            files = self._snapshot(row)["manifest"]["files"]
            view["files"] = files
            view["file_count"] = len(files)
        return view

    def _package(self, conn, actor, book_id, package_id):
        try:
            book = self.access.book(conn, actor, book_id)
        except AccountingError:
            self.access.require_user(conn, actor.user_id)
            if self.db.is_postgres:
                raw = conn.execute("SELECT accounting.recipient_package_book(?,?)", (package_id, book_id)).fetchone()[0]
                book = json.loads(raw) if raw else None
            else:
                found = conn.execute("SELECT b.* FROM books b JOIN monthly_delivery_packages p ON p.book_id=b.id JOIN monthly_delivery_eligible_contacts e ON e.book_id=b.id AND e.user_id=p.contact_user_id WHERE p.id=? AND b.id=? AND b.firm_id=? AND p.contact_user_id=? AND p.created_by<>?", (package_id, book_id, actor.firm_id, actor.user_id, actor.user_id)).fetchone()
                book = dict(found) if found else None
            if not book:
                raise AccountingError("月度交付包不存在或无权访问", 404)
            book["permissions"] = {key: False for key in ("edit", "review", "export", "manage")}
        row = conn.execute(
            "SELECT * FROM monthly_delivery_packages WHERE id=? AND book_id=?",
            (package_id, book_id),
        ).fetchone()
        if not row:
            raise AccountingError("月度交付包不存在或无权访问", 404)
        return book, row

    def _files(self, conn, actor, book, report, evidence, version):
        vouchers = []
        for row in self._rows(
            conn,
            "SELECT * FROM vouchers WHERE book_id=? ORDER BY voucher_date,voucher_type,voucher_no",
            (book["id"],),
        ):
            lines = self._rows(
                conn,
                "SELECT * FROM voucher_lines WHERE voucher_id=? ORDER BY line_no",
                (row["id"],),
            )
            vouchers.append(
                {
                    **pick(
                        row,
                        "id period voucher_type voucher_no voucher_date status description source_kind source_id source_version posted_by posted_at",
                    ),
                    "lines": [
                        pick(
                            line,
                            "id line_no account_id account_version code_snapshot name_snapshot debit_cents credit_cents aux_json",
                        )
                        for line in lines
                    ],
                }
            )
        books = {
            "trial_balance": evidence["trial"],
            "daily_journal": self.posting.daily_journal_in_transaction(conn, actor, book["id"]),
            "account_ledgers": {
                row["account_id"]: self.posting.account_ledger_in_transaction(
                    conn, actor, book["id"], row["account_id"]
                )
                for row in evidence["trial"]["rows"]
                if row["is_leaf"]
            },
        }
        exports = [
            self.delivery._manifest(conn, row)
            for row in self._rows(
                conn, "SELECT * FROM exports WHERE book_id=? ORDER BY created_at,id", (book["id"],)
            )
        ]
        matters = [
            pick(row, "id kind title state priority version")
            for row in self._rows(
                conn,
                "SELECT t.* FROM case_tasks t WHERE t.firm_id=? AND t.company_id=? AND t.state<>'closed' AND (t.book_id=? OR EXISTS(SELECT 1 FROM business_mappings m WHERE m.event_id=t.event_id AND m.book_id=?)) ORDER BY t.id",
                (book["firm_id"], book["company_id"], book["id"], book["id"]),
            )
        ]
        originals = [
            {"kind": "material", **item}
            for row in evidence["materials"]
            for item in row["original_refs"]
        ]
        queries = (
            (
                "business",
                "SELECT a.id,a.sha256,a.size_bytes,a.media_type FROM business_attachments a JOIN business_mappings m ON m.event_id=a.event_id WHERE m.book_id=?",
            ),
        )
        for kind, sql in queries:
            originals.extend(
                {"kind": kind, **dict(row)}
                for row in self._rows(conn, sql + " ORDER BY id", (book["id"],))
            )
        files = {
            "formal_vouchers.json": vouchers,
            "formal_books.json": books,
            "published_report.json": {
                "report": pick(
                    report,
                    "id version period template_id template_version status content_hash reviewed_by reviewed_at published_by published_at",
                ),
                "content": json.loads(report["content_json"]),
            },
            "bank_verification.json": evidence["bank"],
            "frozen_exports.json": exports,
            "materials.json": {
                "status": "configured" if evidence["materials"] else "no_requests_configured",
                "items": evidence["materials"],
            },
            "unresolved_matters.json": matters,
            "originals_index.json": {
                "originals_included": False,
                "index_scope": "submitted_materials_and_mapped_business_attachments",
                "items": originals,
            },
        }
        return {
            **{path: encode(value) for path, value in files.items()},
            **readable_files(
                book, version, report, evidence["trial"], evidence["contact"]["user_id"]
            ),
        }

    def _company(self, actor, book_id, capability="read"):
        with self.db.transaction(actor=actor) as conn:
            return self.access.book(conn, actor, book_id, capability)["company_id"]

    def create(self, actor, book_id, request):
        fingerprint = digest(encoded(request.model_dump()))
        company_id = self._company(actor, book_id, "review")
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            if not self.db.is_postgres:
                # SQLite has no built-in SHA-256. Install a deterministic,
                # transaction-local connection capability for the native guard.
                conn.create_function(
                    "monthly_delivery_sha256",
                    1,
                    lambda value: digest(value.encode("utf-8")) if isinstance(value, str) else None,
                    deterministic=True,
                )
            book = self.access.book(conn, actor, book_id, "review")
            existing = conn.execute(
                "SELECT * FROM monthly_delivery_packages WHERE book_id=? AND created_by=? AND idempotency_key=?",
                (book_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != fingerprint:
                    raise AccountingError("幂等标识已用于不同交付内容", 409)
                return self._view(conn, actor, book, existing)
            report = conn.execute(
                "SELECT * FROM report_runs WHERE id=? AND book_id=?", (request.report_id, book_id)
            ).fetchone()
            if not report:
                raise AccountingError("报表不存在或不属于当前账套", 404)
            if (
                report["version"] != request.expected_report_version
                or report["status"] != "published"
            ):
                raise AccountingError("请选择相同版本的已发布报表", 409)
            evidence = self._inspect(conn, actor, book)
            if evidence["blockers"]:
                raise AccountingError("；".join(x["message"] for x in evidence["blockers"]), 409)
            self.reports._current_in_transaction(
                conn, actor, request.report_id, trial=evidence["trial"]
            )
            version = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM monthly_delivery_packages WHERE book_id=?",
                (book_id,),
            ).fetchone()[0]
            identifier, stamp = str(uuid4()), now()
            files = self._files(conn, actor, book, report, evidence, version)
            manifest = {
                "schema_version": 1,
                "package_id": identifier,
                "version": version,
                "book_id": book_id,
                "company_id": book["company_id"],
                "period": book["period"],
                "report_id": report["id"],
                "report_version": report["version"],
                "created_at": stamp,
                "created_by": actor.user_id,
                "contact_user_id": evidence["contact"]["user_id"],
                "service_plan_version": evidence["plan"]["version"],
                "originals_included": False,
                "external_import_status": "separate_workflow",
                "files": [
                    {
                        "path": path,
                        "sha256": digest(data.encode("utf-8")),
                        "size_bytes": len(data.encode("utf-8")),
                    }
                    for path, data in sorted(files.items())
                ],
            }
            # Preserve exact UTF-8 file text for database and download hash checks.
            snapshot = encode(
                {"manifest": manifest, "manifest_json": encode(manifest), "files": files}
            )
            if len(snapshot.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
                raise AccountingError("月度交付快照超过单包大小限制", 409)
            conn.execute(
                "INSERT INTO monthly_delivery_packages(id,book_id,firm_id,company_id,period,version,report_id,report_version,contact_user_id,service_plan_version,manifest_sha256,snapshot_json,idempotency_key,request_fingerprint,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    book_id,
                    book["firm_id"],
                    book["company_id"],
                    book["period"],
                    version,
                    report["id"],
                    report["version"],
                    evidence["contact"]["user_id"],
                    evidence["plan"]["version"],
                    digest(encoded(manifest)),
                    snapshot,
                    request.idempotency_key,
                    fingerprint,
                    request.reason,
                    actor.user_id,
                    stamp,
                ),
            )
            return self._view(
                conn,
                actor,
                book,
                conn.execute(
                    "SELECT * FROM monthly_delivery_packages WHERE id=?", (identifier,)
                ).fetchone(),
            )

    @staticmethod
    def _snapshot(row):
        snapshot = json.loads(row["snapshot_json"])
        manifest = snapshot["manifest"]
        if (
            snapshot["manifest_json"] != encode(manifest)
            or digest(snapshot["manifest_json"].encode("utf-8")) != row["manifest_sha256"]
        ):
            raise AccountingError("冻结交付包完整性核验失败", 409)
        files = snapshot["files"]
        if (
            set(files) != FILE_PATHS
            or set(files) != {item["path"] for item in manifest["files"]}
            or len(manifest["files"]) != len(FILE_PATHS)
        ):
            raise AccountingError("冻结交付包文件清单不一致", 409)
        for item in manifest["files"]:
            content = files[item["path"]].encode("utf-8")
            if digest(content) != item["sha256"] or len(content) != item["size_bytes"]:
                raise AccountingError("冻结交付包文件校验失败", 409)
        return snapshot

    def detail(self, actor, book_id, package_id):
        with self.db.transaction(actor=actor) as conn:
            book, row = self._package(conn, actor, book_id, package_id)
            return {
                "package": self._view(conn, actor, book, row, with_files=True),
                "manifest": self._snapshot(row)["manifest"],
            }

    def open(self, actor, book_id, package_id):
        """Record that the contact reached this frozen version; never a receipt."""
        with self.db.transaction(actor=actor) as conn:
            company_id = self._package(conn, actor, book_id, package_id)[0]["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book, row = self._package(conn, actor, book_id, package_id)
            contact, _ = self._contact(conn, actor, book)
            if (
                not contact["eligible"]
                or contact["user_id"] != actor.user_id
                or row["contact_user_id"] != actor.user_id
            ):
                raise AccountingError(
                    "只有仍有本版资料包访问权限的本版客户联系人可以打开交付包", 403
                )
            self._snapshot(row)
            if not conn.execute(
                "SELECT 1 FROM monthly_delivery_opens WHERE package_id=?", (package_id,)
            ).fetchone():
                conn.execute(
                    "INSERT INTO monthly_delivery_opens(package_id,book_id,firm_id,company_id,opened_by,opened_at) VALUES(?,?,?,?,?,?)",
                    (
                        package_id,
                        book_id,
                        book["firm_id"],
                        book["company_id"],
                        actor.user_id,
                        now(),
                    ),
                )
            return self._view(conn, actor, book, row, with_files=True)

    def download(self, actor, book_id, package_id):
        with self.db.transaction(actor=actor) as conn:
            _, row = self._package(conn, actor, book_id, package_id)
            snapshot = self._snapshot(row)
        stream = io.BytesIO()
        with ZipFile(stream, "w", compression=ZIP_STORED) as archive:
            for name, data in sorted(
                {"manifest.json": snapshot["manifest_json"], **snapshot["files"]}.items()
            ):
                entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.external_attr = 0o600 << 16
                archive.writestr(entry, data.encode("utf-8"))
        return stream.getvalue()

    def acknowledge(self, actor, book_id, package_id, request):
        with self.db.transaction(actor=actor) as conn:
            company_id = self._package(conn, actor, book_id, package_id)[0]["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book, row = self._package(conn, actor, book_id, package_id)
            contact, _ = self._contact(conn, actor, book)
            if (
                not contact["eligible"]
                or contact["user_id"] != actor.user_id
                or row["contact_user_id"] != actor.user_id
                or row["created_by"] == actor.user_id
            ):
                raise AccountingError("只有仍有本版资料包访问权限的本版客户联系人可以签收", 403)
            self._snapshot(row)
            if request.manifest_sha256 != row["manifest_sha256"]:
                raise AccountingError("签收哈希与本版冻结清单不一致", 409)
            previous = conn.execute(
                "SELECT * FROM monthly_delivery_acknowledgements WHERE package_id=?", (package_id,)
            ).fetchone()
            if previous:
                if previous["reason"] != request.reason or previous["created_by"] != actor.user_id:
                    raise AccountingError("本版已经签收，不能改写签收内容", 409)
            else:
                conn.execute(
                    "INSERT INTO monthly_delivery_acknowledgements(package_id,book_id,firm_id,company_id,manifest_sha256,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        package_id,
                        book_id,
                        book["firm_id"],
                        book["company_id"],
                        request.manifest_sha256,
                        request.reason,
                        actor.user_id,
                        now(),
                    ),
                )
            return self._view(conn, actor, book, row)
