"""Month-specific external reference facts and deterministic recurring draft plans.

An independently checked uploaded balance is still a customer supplied reference.
Only the supplied dimensions are known. Receipt verification never proves that
the connected accounting product's live ledger is complete.
"""

import json
import csv
import hashlib
import io
from collections import defaultdict
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.catalog import CatalogService
from ..accounting.domain import AccountingError
from ..accounting.models import cents_to_money, money_to_cents
from ..accounting.queries import inactive_source_sql
from ..accounting.service import fingerprint, now
from ..delivery.service import DeliveryService
from ..closing.queries import receipt_valid_sql
from .models import BaselineCreate, BalanceLine, signed_cents, signed_money


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


BASELINE_COLUMNS = "id,book_id,ledger_id,firm_id,company_id,kind,version,period,external_reference,evidence_ref,reason,source_kind,source_id,lines_json,created_by,created_at,request_key,request_fingerprint,original_format,original_filename,original_sha256"


def strict_loads(content):
    def unique_fields(pairs):
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("余额文件不能包含重复 JSON 字段")
        return dict(pairs)

    def invalid_constant(_):
        raise ValueError("余额文件不能包含非有限数字")

    return json.loads(content, object_pairs_hook=unique_fields, parse_constant=invalid_constant)


def previous_period(period):
    year, month = map(int, period.split("-"))
    return f"{year if month > 1 else year - 1:04d}-{month - 1 if month > 1 else 12:02d}"


class MonthlyService:
    def __init__(self, db, accounting):
        self.db, self.accounting = db, accounting
        self.access, self.catalog, self.delivery = (
            AccessControl(),
            CatalogService(db),
            DeliveryService(db),
        )

    def _scope(self, conn, actor, book_id, capability="read", open_required=False):
        book = self.access.book(conn, actor, book_id, capability)
        link = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=? AND firm_id=? AND company_id=?",
            (book_id, actor.firm_id, book["company_id"]),
        ).fetchone()
        if not link:
            raise AccountingError("须先初始化长期账套与期间映射", 409)
        if open_required and book["period_status"] != "open":
            raise AccountingError("已关期间不能新增月度事实、检查或固定事项草稿", 409)
        return book, link["ledger_id"]

    def _company_of_book(self, actor, book_id):
        """Resolve the book's company up front for a company-scoped write lock."""
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._scope(conn, actor, book_id)
            return book["company_id"]

    def _audit(self, conn, actor, book_id, action, identifier, details):
        self.accounting._audit(conn, actor, book_id, action, identifier, details)

    def _baseline(self, conn, book_id, identifier):
        row = conn.execute(
            "SELECT " + BASELINE_COLUMNS + " FROM monthly_baselines WHERE id=? AND book_id=?",
            (identifier, book_id),
        ).fetchone()
        if not row:
            raise AccountingError("参考余额不存在或无权访问", 404)
        return row

    @staticmethod
    def _baseline_json(conn, row):
        confirmation = conn.execute(
            "SELECT c.*,u.name AS confirmed_name FROM monthly_baseline_confirmations c JOIN users u ON u.id=c.confirmed_by WHERE c.baseline_id=?",
            (row["id"],),
        ).fetchone()
        return {
            **{
                key: row[key]
                for key in (
                    "id",
                    "book_id",
                    "ledger_id",
                    "kind",
                    "version",
                    "period",
                    "external_reference",
                    "evidence_ref",
                    "reason",
                    "source_kind",
                    "source_id",
                    "created_by",
                    "created_at",
                )
            },
            "lines": [
                {
                    "account_code": x["account_code"],
                    "auxiliary_values": x["auxiliary_values"],
                    "balance": signed_money(x["balance_cents"]),
                }
                for x in json.loads(row["lines_json"])
            ],
            "confirmed": bool(confirmation),
            "confirmed_by": confirmation["confirmed_by"] if confirmation else None,
            "confirmed_name": confirmation["confirmed_name"] if confirmation else None,
            "confirmation_reason": confirmation["reason"] if confirmation else None,
            "original": {
                "format": row["original_format"],
                "filename": row["original_filename"],
                "sha256": row["original_sha256"],
                "download_path": f"/api/books/{row['book_id']}/monthly/baselines/{row['id']}/original",
            },
            "coverage": "listed_dimensions_only",
            "evidence_mode": "customer_supplied_reference",
            "official_report": False,
        }

    def _validate_lines(self, conn, actor, book_id, lines, balance=False):
        accounts = {
            r["code"]: r
            for r in conn.execute("SELECT * FROM accounts WHERE book_id=? AND active=1", (book_id,))
        }
        if any(x["account_code"] not in accounts for x in lines):
            raise AccountingError("余额或计划须使用当前企业已启用的实际会计科目", 409)
        issues = (
            []
            if balance
            else self.catalog.validate_auxiliaries_in_transaction(conn, actor, book_id, lines)
        )
        # Stable IDs are mandatory where the company has master data. A text
        # alias is evidence, never the identity used to aggregate balances.
        for line in lines:
            if balance:
                values = line["auxiliary_values"]
                if set(values) - {"department", "employee", "counterparty", "project"} or any(
                    not isinstance(value, str) or not value.strip() or len(value) > 200
                    for value in values.values()
                ):
                    issues.append("余额辅助维度不合法")
                if (
                    values.get("department")
                    and not conn.execute(
                        "SELECT 1 FROM departments d JOIN books b ON b.company_id=d.company_id WHERE b.id=? AND d.id=?",
                        (book_id, values["department"]),
                    ).fetchone()
                ):
                    issues.append("余额部门不属于本企业")
                if values.get("employee") and not self.catalog._employee_valid(
                    conn, self.access.book(conn, actor, book_id), values["employee"]
                ):
                    issues.append("余额人员不属于本企业")
            for kind in ("counterparty", "project"):
                value = line["auxiliary_values"].get(kind)
                if (
                    value
                    and not conn.execute(
                        "SELECT 1 FROM company_master_entities m JOIN books b ON b.firm_id=m.firm_id AND b.company_id=m.company_id WHERE b.id=? AND m.kind=? AND m.id=? AND m.active=1",
                        (book_id, kind, value),
                    ).fetchone()
                ):
                    issues.append("往来方和项目须选择本企业已审核的主数据 ID")
        if issues:
            raise AccountingError("；".join(dict.fromkeys(issues)), 409)
        return [
            {
                "code": code,
                "name": accounts[code]["name"],
                "category": accounts[code]["category"],
                "normal_balance": accounts[code]["normal_balance"],
                "auxiliary_requirements": json.loads(accounts[code]["auxiliary_requirements"]),
            }
            for code in sorted({x["account_code"] for x in lines})
        ]

    def _derived_lines(self, conn, actor, book, ledger_id, request):
        if request.source_kind == "manual":
            return [
                {
                    "account_code": x.account_code,
                    "auxiliary_values": x.auxiliary_values,
                    "balance_cents": signed_cents(x.balance),
                }
                for x in request.lines
            ]
        wanted_period = (
            previous_period(book["period"]) if request.kind == "opening" else book["period"]
        )
        if request.source_kind == "legacy_cutover":
            row = conn.execute(
                "SELECT c.*,p.ledger_id,p.status,p.history_end FROM migration_cutovers c JOIN migration_projects p ON p.id=c.project_id WHERE c.id=? AND c.firm_id=? AND c.company_id=? AND p.ledger_id=? AND p.status='active'",
                (request.source_id, actor.firm_id, book["company_id"], ledger_id),
            ).fetchone()
            if not row or row["history_end"][:7] != wanted_period:
                raise AccountingError("旧账参考须来自同长期账套已独立验收接管的对应期末", 409)
            self.access.book(conn, actor, row["book_id"])
            body = json.loads(row["payload_json"])
            mapping = {x["source_code"]: x["target_code"] for x in body["account_bindings"]}
            masters = {
                (x["kind"], x["source_code"]): x["target_id"]
                for x in body.get("master_bindings", [])
            }
            lines = []
            for x in body["closing_balances"]:
                values = {
                    kind: masters.get((kind, value), value)
                    for kind, value in x.get("auxiliaries", {}).items()
                }
                lines.append(
                    {
                        "account_code": mapping[x["account_code"]],
                        "auxiliary_values": values,
                        "balance_cents": signed_cents(x["closing"]),
                        "record_id": x["record_id"],
                    }
                )
            # Aggregate totals and auxiliary details are separate dimensions;
            # consumers must never add them together as a trial balance.
            return lines
        row = conn.execute(
            "SELECT * FROM bridge_import_previews WHERE id=? AND firm_id=? AND company_id=? AND ledger_id=? AND state='confirmed'",
            (request.source_id, actor.firm_id, book["company_id"], ledger_id),
        ).fetchone()
        if not row:
            raise AccountingError("软件余额须来自同长期账套已确认的文件导入", 409)
        self.access.book(conn, actor, row["book_id"])
        config = conn.execute(
            "SELECT payload_json FROM bridge_configurations WHERE ledger_id=? AND version=?",
            (ledger_id, row["configuration_version"]),
        ).fetchone()
        mapping = {x["external_code"]: x["local_code"] for x in json.loads(config[0])["accounts"]}
        result = []
        for record in json.loads(row["records_json"]):
            if record["kind"] == "balance" and record["period"] == wanted_period:
                if record["code"] not in mapping:
                    raise AccountingError("外部余额科目缺少已确认映射", 409)
                result.append(
                    {
                        "account_code": mapping[record["code"]],
                        "auxiliary_values": {},
                        "balance_cents": money_to_cents(record["debit"])
                        - money_to_cents(record["credit"]),
                        "record_id": record["external_id"],
                    }
                )
        if not result:
            raise AccountingError("导入文件没有所需月份的明确余额，请使用对应月份资料", 409)
        return result

    def create_baseline(self, actor, book_id, request, original=None):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book, ledger_id = self._scope(conn, actor, book_id, "edit", True)
            digest = fingerprint(
                request.model_dump(exclude={"idempotency_key"})
                | {"book_id": book_id, "original_hash": original["sha256"] if original else None}
            )
            prior = conn.execute(
                "SELECT "
                + BASELINE_COLUMNS
                + " FROM monthly_baselines WHERE firm_id=? AND created_by=? AND request_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != digest or prior["book_id"] != book_id:
                    raise AccountingError("请求编号已用于不同月度余额", 409)
                return self._baseline_json(conn, prior)
            lines = self._derived_lines(conn, actor, book, ledger_id, request)
            if (
                not lines
                or len(lines) > 1000
                or len({(x["account_code"], encode(x["auxiliary_values"])) for x in lines})
                != len(lines)
            ):
                raise AccountingError("参考余额为空、超出上限或同一维度重复", 409)
            self._validate_lines(conn, actor, book_id, lines, balance=True)
            version = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM monthly_baselines WHERE book_id=? AND kind=?",
                (book_id, request.kind),
            ).fetchone()[0]
            identifier = uuid4().hex
            if original is None:
                content = encode(request.model_dump())
                file_format = "structured_entry"
                if request.source_kind == "legacy_cutover":
                    content = conn.execute(
                        "SELECT payload_json FROM migration_cutovers WHERE id=?",
                        (request.source_id,),
                    ).fetchone()[0]
                    file_format = "legacy_snapshot"
                elif request.source_kind == "bridge_import":
                    content = conn.execute(
                        "SELECT records_json FROM bridge_import_previews WHERE id=?",
                        (request.source_id,),
                    ).fetchone()[0]
                    file_format = "bridge_snapshot"
                original = {
                    "format": file_format,
                    "filename": f"monthly-{identifier}.json",
                    "content": content,
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
            conn.execute(
                "INSERT INTO monthly_baselines(id,book_id,ledger_id,firm_id,company_id,kind,version,period,external_reference,evidence_ref,reason,source_kind,source_id,lines_json,created_by,created_at,request_key,request_fingerprint,original_format,original_filename,original_content,original_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    book_id,
                    ledger_id,
                    actor.firm_id,
                    book["company_id"],
                    request.kind,
                    version,
                    previous_period(book["period"])
                    if request.kind == "opening"
                    else book["period"],
                    request.external_reference,
                    request.evidence_ref,
                    request.reason,
                    request.source_kind,
                    request.source_id,
                    encode(lines),
                    actor.user_id,
                    now(),
                    request.idempotency_key,
                    digest,
                    original["format"],
                    original["filename"],
                    original["content"],
                    original["sha256"],
                ),
            )
            self._audit(
                conn,
                actor,
                book_id,
                "monthly.baseline.create",
                identifier,
                {"version": version, "source_kind": request.source_kind, "kind": request.kind},
            )
            return self._baseline_json(conn, self._baseline(conn, book_id, identifier))

    def confirm_baseline(self, actor, book_id, identifier, request):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self._scope(conn, actor, book_id, "review", True)
            row = self._baseline(conn, book_id, identifier)
            if row["created_by"] == actor.user_id:
                raise AccountingError("外部余额须由另一位具有该期间复核权限的会计独立确认", 403)
            if (
                conn.execute(
                    "SELECT MAX(version) FROM monthly_baselines WHERE book_id=? AND kind=?",
                    (book_id, row["kind"]),
                ).fetchone()[0]
                != row["version"]
            ):
                raise AccountingError("余额已被新版本替代，请核对最新资料", 409)
            prior = conn.execute(
                "SELECT * FROM monthly_baseline_confirmations WHERE baseline_id=?", (identifier,)
            ).fetchone()
            if prior:
                if prior["confirmed_by"] != actor.user_id or prior["reason"] != request.reason:
                    raise AccountingError("余额已确认，确认记录不可改写", 409)
                return self._baseline_json(conn, row)
            self._validate_lines(conn, actor, book_id, json.loads(row["lines_json"]), balance=True)
            conn.execute(
                "INSERT INTO monthly_baseline_confirmations(baseline_id,book_id,firm_id,company_id,confirmed_by,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    identifier,
                    book_id,
                    actor.firm_id,
                    row["company_id"],
                    actor.user_id,
                    request.reason,
                    now(),
                ),
            )
            self._audit(
                conn,
                actor,
                book_id,
                "monthly.baseline.confirm",
                identifier,
                {"reason": request.reason},
            )
            return self._baseline_json(conn, row)

    def import_baseline(self, actor, book_id, request):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._scope(conn, actor, book_id, "edit", True)
            wanted = (
                previous_period(book["period"]) if request.kind == "opening" else book["period"]
            )
        try:
            if request.format == "json":
                body = strict_loads(request.content)
                if (
                    not isinstance(body, dict)
                    or set(body) != {"period", "currency", "scope", "lines"}
                    or body["period"] != wanted
                    or body["currency"] != "CNY"
                    or body["scope"] != "posted"
                    or not isinstance(body["lines"], list)
                ):
                    raise ValueError("invalid balance envelope")
                raw_lines = body["lines"]
            else:
                reader = csv.DictReader(io.StringIO(request.content.lstrip("\ufeff")))
                if len(reader.fieldnames or []) != 6 or set(reader.fieldnames or []) != {
                    "account_code",
                    "balance",
                    "auxiliary_values",
                    "period",
                    "currency",
                    "scope",
                }:
                    raise ValueError("invalid balance columns")
                raw_lines = []
                for row in reader:
                    if (
                        row["period"] != wanted
                        or row["currency"] != "CNY"
                        or row["scope"] != "posted"
                        or None in row
                    ):
                        raise ValueError("invalid balance scope")
                    raw_lines.append(
                        {
                            "account_code": row["account_code"],
                            "balance": row["balance"],
                            "auxiliary_values": strict_loads(row["auxiliary_values"] or "{}"),
                        }
                    )
                    if len(raw_lines) > 1000:
                        raise ValueError("too many lines")
            payload = BaselineCreate(
                kind=request.kind,
                external_reference=request.external_reference,
                evidence_ref=request.evidence_ref,
                reason=request.reason,
                idempotency_key=request.idempotency_key,
                lines=[BalanceLine.model_validate(x) for x in raw_lines],
            )
        except (ValueError, TypeError, KeyError, csv.Error) as error:
            raise AccountingError(
                "请使用 UTF-8 中性余额模板，明确对应月份、CNY、仅已记账口径及净借余额；不能猜补缺字段",
                400,
            ) from error
        return self.create_baseline(
            actor,
            book_id,
            payload,
            {
                "format": request.format,
                "filename": request.filename,
                "content": request.content,
                "sha256": hashlib.sha256(request.content.encode("utf-8")).hexdigest(),
            },
        )

    def original(self, actor, book_id, identifier):
        with self.db.transaction(actor=actor) as conn:
            self._scope(conn, actor, book_id)
            row = conn.execute(
                "SELECT original_filename,original_format,original_content,original_sha256 FROM monthly_baselines WHERE id=? AND book_id=?",
                (identifier, book_id),
            ).fetchone()
            if not row:
                raise AccountingError("参考余额原件不存在或无权访问", 404)
            return {
                "filename": row["original_filename"],
                "format": row["original_format"],
                "content": row["original_content"],
                "sha256": row["original_sha256"],
            }

    def _movements(self, conn, actor, book_id):
        # A latest receipt can revoke the previous apparent match. We use the
        # delivery module's exact frozen line/date/amount checks, never status
        # supplied by a model or a bare external voucher number.
        receipts = conn.execute(
            "SELECT r.* FROM delivery_receipts r WHERE r.book_id=? AND r.id=(SELECT r2.id FROM delivery_receipts r2 WHERE r2.export_id=r.export_id ORDER BY r2.created_at DESC,r2.id DESC LIMIT 1) ORDER BY r.created_at,r.id LIMIT 201",
            (book_id,),
        ).fetchall()
        if len(receipts) > 200:
            raise AccountingError("当月交付批次超出 MVP 核验上限，请分批核对后联系管理员", 409)
        verified, evidence = set(), []
        for export in conn.execute(
            "SELECT x.* FROM exports x WHERE x.book_id=? AND " + receipt_valid_sql(self.db.dialect),
            (book_id,),
        ):
            manifest = self.delivery._manifest(conn, export)
            verified.update(x["draft_id"] for x in manifest["items"])
        for receipt in receipts:
            export = conn.execute(
                "SELECT * FROM exports WHERE id=? AND book_id=?", (receipt["export_id"], book_id)
            ).fetchone()
            if export:
                evidence.append(
                    {
                        "receipt_id": receipt["id"],
                        "export_id": export["id"],
                        "body_hash": receipt["request_fingerprint"],
                    }
                )
        result = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        facts = []
        rows = conn.execute(
            "SELECT d.id,d.version,d.status,l.account_code,l.debit_cents,l.credit_cents,l.ordinal,a.values_json FROM drafts d JOIN sources s ON s.id=d.source_id AND s.book_id=d.book_id JOIN draft_lines l ON l.draft_id=d.id AND l.book_id=d.book_id LEFT JOIN draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.book_id=l.book_id AND a.ordinal=l.ordinal WHERE d.book_id=? AND NOT ("
            + inactive_source_sql(self.db.dialect)
            + ") AND NOT EXISTS(SELECT 1 FROM ai_bank_links link WHERE link.bank_source_id=s.id AND link.book_id=s.book_id) ORDER BY d.id,l.ordinal",
            (book_id,),
        )
        for row in rows:
            group = (
                "unreviewed"
                if row["status"] == "draft"
                else "approved_pending_delivery"
                if row["status"] == "approved"
                else "verified_delivery"
                if row["id"] in verified
                else "exported_pending_receipt"
            )
            values = json.loads(row["values_json"] or "{}")
            key = (row["account_code"], encode(values))
            result[key][group][0] += row["debit_cents"]
            result[key][group][1] += row["credit_cents"]
            facts.append(
                {
                    "draft_id": row["id"],
                    "version": row["version"],
                    "status": row["status"],
                    "ordinal": row["ordinal"],
                    "account_code": row["account_code"],
                    "debit_cents": row["debit_cents"],
                    "credit_cents": row["credit_cents"],
                    "auxiliary_values": values,
                    "group": group,
                }
            )
        return result, facts, evidence

    def _reference(self, conn, actor, book, opening=None, closing=None):
        movements, facts, receipts = self._movements(conn, actor, book["id"])
        baselines = []
        for kind in ("opening", "closing"):
            row = opening if kind == "opening" else closing
            if row is None:
                row = conn.execute(
                    "SELECT b.id,b.lines_json,b.kind,b.version FROM monthly_baselines b JOIN monthly_baseline_confirmations c ON c.baseline_id=b.id WHERE b.book_id=? AND b.kind=? AND b.version=(SELECT MAX(x.version) FROM monthly_baselines x WHERE x.book_id=b.book_id AND x.kind=b.kind)",
                    (book["id"], kind),
                ).fetchone()
            baselines.append(row)
        indexed = [
            {
                (x["account_code"], encode(x["auxiliary_values"])): x["balance_cents"]
                for x in json.loads(row["lines_json"])
            }
            if row
            else {}
            for row in baselines
        ]
        names = {
            r["code"]: r["name"]
            for r in conn.execute("SELECT code,name FROM accounts WHERE book_id=?", (book["id"],))
        }
        lines = []
        for key in sorted(set(movements) | set(indexed[0]) | set(indexed[1])):
            groups = movements[key]
            if key[1] == "{}" and (key in indexed[0] or key in indexed[1]):
                groups = defaultdict(lambda: [0, 0])
                for dimension, values in movements.items():
                    if dimension[0] == key[0]:
                        for group, amounts in values.items():
                            groups[group][0] += amounts[0]
                            groups[group][1] += amounts[1]
            base, actual = indexed[0].get(key), indexed[1].get(key)
            verified = groups["verified_delivery"]
            expected = None if base is None else base + verified[0] - verified[1]
            lines.append(
                {
                    "account_code": key[0],
                    "account_name": names.get(key[0], key[0]),
                    "auxiliary_values": json.loads(key[1]),
                    "baseline": None if base is None else signed_money(base),
                    "baseline_state": "unknown" if base is None else "confirmed_reference",
                    "reference_closing": None if expected is None else signed_money(expected),
                    "actual": None if actual is None else signed_money(actual),
                    "difference": None
                    if actual is None or expected is None
                    else signed_money(actual - expected),
                    **{
                        group: {
                            "debit": cents_to_money(groups[group][0]),
                            "credit": cents_to_money(groups[group][1]),
                            "net": signed_money(groups[group][0] - groups[group][1]),
                        }
                        for group in (
                            "verified_delivery",
                            "approved_pending_delivery",
                            "exported_pending_receipt",
                            "unreviewed",
                        )
                    },
                }
            )
        inputs = {
            "book_id": book["id"],
            "period": book["period"],
            "opening_id": baselines[0]["id"] if baselines[0] else None,
            "closing_id": baselines[1]["id"] if baselines[1] else None,
            "facts": facts,
            "receipts": receipts,
        }
        return {
            "lines": lines,
            "opening_id": inputs["opening_id"],
            "closing_id": inputs["closing_id"],
            "currency": "CNY",
            "official_report": False,
            "coverage": "listed_dimensions_only",
            "unknown_count": sum(x["difference"] is None for x in lines),
            "difference_count": sum(x["difference"] not in (None, "0.00") for x in lines),
            "pending_delivery_count": len(
                {
                    x["draft_id"]
                    for x in facts
                    if x["group"] in {"approved_pending_delivery", "exported_pending_receipt"}
                }
            ),
            "unreviewed_count": len({x["draft_id"] for x in facts if x["group"] == "unreviewed"}),
            "receipt_evidence": receipts,
            "input_fingerprint": fingerprint(inputs),
        }, inputs

    def _check_json(self, conn, actor, book, row, current_inputs=None):
        report = json.loads(row["report_json"])
        opening = self._baseline(conn, book["id"], row["opening_id"])
        closing = self._baseline(conn, book["id"], row["closing_id"])
        if current_inputs is None:
            fresh, _ = self._reference(conn, actor, book, opening, closing)
            current_fingerprint = fresh["input_fingerprint"]
        else:
            current_fingerprint = fingerprint(
                current_inputs | {"opening_id": row["opening_id"], "closing_id": row["closing_id"]}
            )
        latest = all(
            conn.execute(
                "SELECT MAX(version) FROM monthly_baselines WHERE book_id=? AND kind=?",
                (book["id"], x["kind"]),
            ).fetchone()[0]
            == x["version"]
            for x in (opening, closing)
        )
        closure = conn.execute(
            "SELECT c.*,u.name AS closed_name FROM monthly_check_closures c JOIN users u ON u.id=c.closed_by WHERE c.check_id=?",
            (row["id"],),
        ).fetchone()
        return {
            "id": row["id"],
            "version": row["version"],
            "book_id": row["book_id"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "current": latest and current_fingerprint == row["input_fingerprint"],
            "report": report,
            "state": "manually_checked" if closure else "needs_review",
            "closure": dict(closure) if closure else None,
        }

    def create_check(self, actor, book_id, request):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book, ledger_id = self._scope(conn, actor, book_id, "review", True)
            digest = fingerprint(
                request.model_dump(exclude={"idempotency_key"}) | {"book_id": book_id}
            )
            prior = conn.execute(
                "SELECT * FROM monthly_checks WHERE firm_id=? AND created_by=? AND request_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != digest or prior["book_id"] != book_id:
                    raise AccountingError("请求编号已用于不同月度检查", 409)
                return self._check_json(conn, actor, book, prior)
            baselines = [
                self._baseline(conn, book_id, identity)
                for identity in (request.opening_id, request.closing_id)
            ]
            for kind, row in zip(("opening", "closing"), baselines):
                if (
                    row["kind"] != kind
                    or not conn.execute(
                        "SELECT 1 FROM monthly_baseline_confirmations WHERE baseline_id=?",
                        (row["id"],),
                    ).fetchone()
                    or row["version"]
                    != conn.execute(
                        "SELECT MAX(version) FROM monthly_baselines WHERE book_id=? AND kind=?",
                        (book_id, kind),
                    ).fetchone()[0]
                ):
                    raise AccountingError("检查须选用最新且经独立确认的期初及期末外部余额", 409)
            report, inputs = self._reference(conn, actor, book, *baselines)
            identifier = uuid4().hex
            version = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM monthly_checks WHERE book_id=?", (book_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO monthly_checks(id,book_id,ledger_id,firm_id,company_id,version,opening_id,closing_id,inputs_json,report_json,input_fingerprint,created_by,created_at,request_key,request_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    book_id,
                    ledger_id,
                    actor.firm_id,
                    book["company_id"],
                    version,
                    request.opening_id,
                    request.closing_id,
                    encode(inputs),
                    encode(report),
                    report["input_fingerprint"],
                    actor.user_id,
                    now(),
                    request.idempotency_key,
                    digest,
                ),
            )
            self._audit(
                conn,
                actor,
                book_id,
                "monthly.check.create",
                identifier,
                {
                    "version": version,
                    "difference_count": report["difference_count"],
                    "unknown_count": report["unknown_count"],
                },
            )
            return self._check_json(
                conn,
                actor,
                book,
                conn.execute("SELECT * FROM monthly_checks WHERE id=?", (identifier,)).fetchone(),
            )

    def close_check(self, actor, book_id, identifier, request):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book, _ = self._scope(conn, actor, book_id, "review", True)
            row = conn.execute(
                "SELECT * FROM monthly_checks WHERE id=? AND book_id=?", (identifier, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("月度检查不存在或无权访问", 404)
            current = self._check_json(conn, actor, book, row)
            if not current["current"]:
                raise AccountingError("资料、草稿或回执已变化，请重新生成检查快照", 409)
            if current["closure"]:
                if (
                    current["closure"]["closed_by"] != actor.user_id
                    or current["closure"]["reason"] != request.reason
                ):
                    raise AccountingError("核对记录不可改写", 409)
                return current
            conn.execute(
                "INSERT INTO monthly_check_closures(check_id,book_id,firm_id,company_id,closed_by,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    identifier,
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    actor.user_id,
                    request.reason,
                    now(),
                ),
            )
            self._audit(
                conn,
                actor,
                book_id,
                "monthly.check.manual_close",
                identifier,
                {"reason": request.reason, "amounts_unchanged": True},
            )
            return self._check_json(conn, actor, book, row)

    def _plan(self, conn, ledger_id, identifier, version=None):
        row = conn.execute(
            "SELECT * FROM recurring_plan_versions WHERE ledger_id=? AND plan_id=? "
            + ("AND version=?" if version else "ORDER BY version DESC LIMIT 1"),
            (ledger_id, identifier, version) if version else (ledger_id, identifier),
        ).fetchone()
        if not row:
            raise AccountingError("固定事项计划不存在或无权访问", 404)
        return row

    def _plan_json(self, conn, actor, book, ledger_id, row):
        approval = conn.execute(
            "SELECT a.*,u.name AS approved_name FROM recurring_plan_approvals a JOIN users u ON u.id=a.approved_by WHERE a.plan_id=? AND a.version=?",
            (row["plan_id"], row["version"]),
        ).fetchone()
        generated = conn.execute(
            "SELECT g.*,d.status AS draft_status,d.version AS draft_version FROM recurring_generations g JOIN drafts d ON d.id=g.draft_id AND d.book_id=g.book_id WHERE g.plan_id=? AND g.book_id=?",
            (row["plan_id"], book["id"]),
        ).fetchone()
        lines = [
            {
                "account_code": x["account_code"],
                "debit": cents_to_money(x["debit_cents"]),
                "credit": cents_to_money(x["credit_cents"]),
                "auxiliary_values": x["auxiliary_values"],
            }
            for x in json.loads(row["lines_json"])
        ]
        issues = []
        try:
            catalog = self._validate_lines(conn, actor, book["id"], lines)
            if catalog != json.loads(row["catalog_json"]):
                issues.append("目标月份科目配置已变化，须创建并独立审核新计划版本")
        except AccountingError as error:
            issues.append(str(error))
        applicable = row["start_period"] <= book["period"] and (
            not row["end_period"] or book["period"] <= row["end_period"]
        )
        return {
            **{
                key: row[key]
                for key in (
                    "plan_id",
                    "version",
                    "name",
                    "start_period",
                    "end_period",
                    "day",
                    "description",
                    "evidence_ref",
                    "reason",
                    "created_by",
                    "created_at",
                    "book_id",
                )
            },
            "id": row["plan_id"],
            "lines": lines,
            "current": True,
            "approved": bool(approval),
            "approved_by": approval["approved_by"] if approval else None,
            "approved_name": approval["approved_name"] if approval else None,
            "generated_current": {
                "draft_id": generated["draft_id"],
                "book_id": generated["book_id"],
                "status": generated["draft_status"],
                "plan_version": generated["version"],
            }
            if generated
            else None,
            "issues": issues,
            "applicable": applicable,
            "can_generate": bool(
                book["permissions"]["edit"]
                and book["period_status"] == "open"
                and approval
                and applicable
                and not generated
                and not issues
            ),
            "can_approve": bool(
                book["permissions"]["review"]
                and row["created_by"] != actor.user_id
                and not approval
                and not issues
            ),
        }

    def create_plan(self, actor, book_id, request):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book, ledger_id = self._scope(conn, actor, book_id, "review", True)
            digest = fingerprint(
                request.model_dump(exclude={"idempotency_key"}) | {"book_id": book_id}
            )
            prior = conn.execute(
                "SELECT * FROM recurring_plan_versions WHERE firm_id=? AND created_by=? AND request_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != digest or prior["book_id"] != book_id:
                    raise AccountingError("请求编号已用于不同固定事项计划", 409)
                return self._plan_json(conn, actor, book, ledger_id, prior)
            current = self._plan(conn, ledger_id, request.plan_id) if request.plan_id else None
            if (current["version"] if current else 0) != request.expected_version:
                raise AccountingError("固定事项计划已更新，请刷新最新版本", 409)
            lines = [
                {
                    "account_code": x.account_code,
                    "debit_cents": money_to_cents(x.debit),
                    "credit_cents": money_to_cents(x.credit),
                    "auxiliary_values": x.auxiliary_values,
                }
                for x in request.lines
            ]
            catalog = self._validate_lines(conn, actor, book_id, lines)
            identity, version = request.plan_id or uuid4().hex, request.expected_version + 1
            conn.execute(
                "INSERT INTO recurring_plan_versions(plan_id,version,ledger_id,book_id,firm_id,company_id,name,start_period,end_period,day,description,evidence_ref,reason,lines_json,catalog_json,created_by,created_at,request_key,request_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    version,
                    ledger_id,
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    request.name,
                    request.start_period,
                    request.end_period,
                    request.day,
                    request.description,
                    request.evidence_ref,
                    request.reason,
                    encode(lines),
                    encode(catalog),
                    actor.user_id,
                    now(),
                    request.idempotency_key,
                    digest,
                ),
            )
            self._audit(
                conn,
                actor,
                book_id,
                "monthly.plan.create",
                identity,
                {
                    "version": version,
                    "amount": cents_to_money(sum(x["debit_cents"] for x in lines)),
                },
            )
            return self._plan_json(
                conn, actor, book, ledger_id, self._plan(conn, ledger_id, identity)
            )

    def approve_plan(self, actor, book_id, identifier, request):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book, ledger_id = self._scope(conn, actor, book_id, "review", True)
            row = self._plan(conn, ledger_id, identifier)
            if request.version != row["version"]:
                raise AccountingError("须审批当前最新计划版本", 409)
            if row["created_by"] == actor.user_id:
                raise AccountingError("固定事项须由另一位具有该期间复核权限的会计独立审批", 403)
            result = self._plan_json(conn, actor, book, ledger_id, row)
            if result["issues"]:
                raise AccountingError("；".join(result["issues"]), 409)
            prior = conn.execute(
                "SELECT * FROM recurring_plan_approvals WHERE plan_id=? AND version=?",
                (identifier, request.version),
            ).fetchone()
            if prior:
                if prior["approved_by"] != actor.user_id or prior["reason"] != request.reason:
                    raise AccountingError("固定事项审批记录不可改写", 409)
                return result
            conn.execute(
                "INSERT INTO recurring_plan_approvals(plan_id,version,ledger_id,book_id,firm_id,company_id,approved_by,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    request.version,
                    ledger_id,
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    actor.user_id,
                    request.reason,
                    now(),
                ),
            )
            self._audit(
                conn,
                actor,
                book_id,
                "monthly.plan.approve",
                identifier,
                {"version": request.version},
            )
            return self._plan_json(conn, actor, book, ledger_id, row)

    def generate(self, actor, book_id, identifier, request):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book, ledger_id = self._scope(conn, actor, book_id, "edit", True)
            row = self._plan(conn, ledger_id, identifier)
            digest = fingerprint(
                {"book_id": book_id, "plan_id": identifier, "version": request.version}
            )
            prior = conn.execute(
                "SELECT * FROM recurring_generations WHERE firm_id=? AND created_by=? AND request_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior and prior["request_fingerprint"] != digest:
                raise AccountingError("请求编号已用于不同固定事项生成", 409)
            generated = conn.execute(
                "SELECT * FROM recurring_generations WHERE plan_id=? AND book_id=?",
                (identifier, book_id),
            ).fetchone()
            if generated:
                if generated["version"] != request.version:
                    raise AccountingError(
                        "本月已生成旧版本草稿，请复核原草稿，不能按新版本重复生成", 409
                    )
                return {
                    "draft_id": generated["draft_id"],
                    "book_id": book_id,
                    "plan_id": identifier,
                    "version": generated["version"],
                    "created": False,
                }
            if row["version"] != request.version:
                raise AccountingError("只能从当前已审批计划版本生成", 409)
            public = self._plan_json(conn, actor, book, ledger_id, row)
            if not public["can_generate"]:
                raise AccountingError("计划未独立审批、不在生效月份或科目配置已变化，不能生成", 409)
            lines = json.loads(row["lines_json"])
            amount = sum(x["debit_cents"] for x in lines)
            source_id, draft_id = uuid4().hex, uuid4().hex
            identity = f"monthly:{identifier}:{book['period']}"
            evidence_ref = row["evidence_ref"]
            source_fingerprint = fingerprint(
                {
                    "plan_id": identifier,
                    "version": row["version"],
                    "book_id": book_id,
                    "period": book["period"],
                    "lines": lines,
                }
            )
            conn.execute(
                "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,'business')",
                (
                    source_id,
                    book_id,
                    identity,
                    "经独立审批的固定月度事项",
                    f"{book['period']}-{row['day']:02d}",
                    row["description"],
                    "",
                    amount,
                    "expense",
                    evidence_ref,
                    source_fingerprint,
                ),
            )
            conn.execute(
                "INSERT INTO drafts(id,book_id,source_id,description,explanation) VALUES(?,?,?,?,?)",
                (
                    draft_id,
                    book_id,
                    source_id,
                    row["description"],
                    f"固定事项“{row['name']}”第 {row['version']} 版，使用会计明确金额与实际科目；仍须人工复核后导出。",
                ),
            )
            conn.executemany(
                "INSERT INTO draft_lines(draft_id,book_id,ordinal,account_code,debit_cents,credit_cents) VALUES(?,?,?,?,?,?)",
                [
                    (draft_id, book_id, i, x["account_code"], x["debit_cents"], x["credit_cents"])
                    for i, x in enumerate(lines)
                ],
            )
            self.catalog.save_auxiliaries_in_transaction(
                conn, actor, book_id, draft_id, [x["auxiliary_values"] for x in lines]
            )
            conn.execute(
                "INSERT INTO recurring_generations(plan_id,version,ledger_id,book_id,firm_id,company_id,period,source_id,draft_id,created_by,created_at,request_key,request_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    row["version"],
                    ledger_id,
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    book["period"],
                    source_id,
                    draft_id,
                    actor.user_id,
                    now(),
                    request.idempotency_key,
                    digest,
                ),
            )
            self._audit(
                conn,
                actor,
                book_id,
                "monthly.plan.generate",
                draft_id,
                {
                    "plan_id": identifier,
                    "version": row["version"],
                    "source_id": source_id,
                    "auto_approved": False,
                },
            )
            return {
                "draft_id": draft_id,
                "book_id": book_id,
                "plan_id": identifier,
                "version": row["version"],
                "created": True,
            }

    def overview(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book, ledger_id = self._scope(conn, actor, book_id)
            baselines = [
                self._baseline_json(conn, row)
                for row in conn.execute(
                    "SELECT "
                    + BASELINE_COLUMNS
                    + " FROM monthly_baselines WHERE book_id=? ORDER BY kind,version DESC LIMIT 100",
                    (book_id,),
                )
            ]
            reference, current_inputs = self._reference(conn, actor, book)
            checks = [
                self._check_json(conn, actor, book, row, current_inputs)
                for row in conn.execute(
                    "SELECT * FROM monthly_checks WHERE book_id=? ORDER BY version DESC LIMIT 20",
                    (book_id,),
                )
            ]
            plans = [
                self._plan_json(conn, actor, book, ledger_id, row)
                for row in conn.execute(
                    "SELECT p.* FROM recurring_plan_versions p WHERE p.ledger_id=? AND p.version=(SELECT MAX(x.version) FROM recurring_plan_versions x WHERE x.plan_id=p.plan_id) ORDER BY p.created_at DESC,p.plan_id LIMIT 100",
                    (ledger_id,),
                )
            ]
            return {
                "book_id": book_id,
                "ledger_id": ledger_id,
                "period": book["period"],
                "company_id": book["company_id"],
                "period_status": book["period_status"],
                "can_edit": book["permissions"]["edit"] and book["period_status"] == "open",
                "can_review": book["permissions"]["review"] and book["period_status"] == "open",
                "baselines": baselines,
                "checks": checks,
                "plans": plans,
                "reference": reference,
                "accounts": [
                    {
                        "code": r["code"],
                        "name": r["name"],
                        "active": bool(r["active"]),
                        "auxiliary_requirements": json.loads(r["auxiliary_requirements"]),
                    }
                    for r in conn.execute(
                        "SELECT code,name,active,auxiliary_requirements FROM accounts WHERE book_id=? ORDER BY code",
                        (book_id,),
                    )
                ],
                "live_tplus_connected": False,
            }
