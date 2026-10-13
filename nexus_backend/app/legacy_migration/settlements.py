"""Historical reference balances cleared by real current bank drafts, never expenses."""

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import Field, StrictStr, field_validator

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.models import DraftUpdate, InputModel, cents_to_money, money_to_cents
from ..accounting.queries import inactive_source_sql, page_bounds
from ..accounting.review import ReviewService
from ..accounting.service import fingerprint, now
from ..ledgers.service import LedgersService


def encode(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


class LegacyAllocationPart(InputModel):
    item_id: StrictStr = Field(min_length=1, max_length=100)
    amount: StrictStr

    @field_validator("amount")
    @classmethod
    def exact_positive(cls, value):
        amount = money_to_cents(value)
        if amount <= 0:
            raise ValueError("清偿金额须大于零")
        return cents_to_money(amount)


class LegacyAllocationPreview(InputModel):
    bank_book_id: StrictStr = Field(min_length=1, max_length=100)
    bank_source_id: StrictStr = Field(min_length=1, max_length=100)
    bank_account_code: StrictStr = Field(min_length=1, max_length=30)
    parts: list[LegacyAllocationPart] = Field(min_length=1, max_length=50)
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("parts")
    @classmethod
    def unique_parts(cls, parts):
        if len({part.item_id for part in parts}) != len(parts):
            raise ValueError("不能重复选择同一历史未结事项")
        return sorted(parts, key=lambda part: part.item_id)


class LegacyAllocationConfirm(InputModel):
    expected_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)


class LegacyAllocationReverse(LegacyAllocationConfirm):
    reason: StrictStr = Field(min_length=3, max_length=1000)


class LegacySettlementService:
    def __init__(self, db, migration_service, accounting_service):
        self.db, self.migration, self.accounting = db, migration_service, accounting_service
        self.access, self.master = AccessControl(), LedgersService(db)

    def _project(self, conn, actor, book_id, project_id):
        project, target, permissions = self.migration._project(conn, actor, book_id, project_id)
        if project["status"] != "active":
            raise AccountingError("须先完成独立旧账验收及接管，再处理历史未结事项", 409)
        return project

    def _bank_book(self, conn, actor, project, book_id, write=False):
        book = self.access.book(conn, actor, book_id, "review")
        link = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=? AND firm_id=? AND company_id=?",
            (book_id, actor.firm_id, project["company_id"]),
        ).fetchone()
        if (
            book["company_id"] != project["company_id"]
            or not link
            or link["ledger_id"] != project["ledger_id"]
            or book["period"] < project["cutover_date"][:7]
        ):
            raise AccountingError(
                "银行流水须在同企业、同长期账套的接管后期间，且当前有复核权限", 403
            )
        if write and book["period_status"] != "open":
            raise AccountingError("已关期不能分配或撤销清偿", 409)
        return book

    def _used(self, conn, item_id):
        if self.db.is_postgres:
            value = conn.execute(
                "SELECT accounting.legacy_open_item_used(?)", (item_id,)
            ).fetchone()[0]
            if value is None:
                raise AccountingError("当前没有历史未结余额的资料授权", 403)
            return value
        return conn.execute(
            "SELECT COALESCE(SUM(a.amount_cents),0) FROM migration_clearance_allocations a WHERE a.item_id=? AND NOT EXISTS(SELECT 1 FROM migration_clearance_reversals r WHERE r.command_id=a.command_id)",
            (item_id,),
        ).fetchone()[0]

    def _item(self, conn, project, item_id):
        row = conn.execute(
            "SELECT * FROM migration_open_items WHERE id=? AND project_id=? AND firm_id=? AND company_id=?",
            (item_id, project["id"], project["firm_id"], project["company_id"]),
        ).fetchone()
        if not row:
            raise AccountingError("历史未结事项不存在或无权访问", 404)
        item = dict(row)
        item["allocated_cents"] = self._used(conn, item_id)
        item["remaining_cents"] = item["amount_cents"] - item["allocated_cents"]
        return item

    @staticmethod
    def _item_json(item):
        return {
            key: item[key]
            for key in (
                "id",
                "project_id",
                "external_id",
                "record_id",
                "kind",
                "source_account_code",
                "target_account_code",
                "counterparty_code",
                "counterparty_id",
                "occurred_on",
                "due_on",
                "created_at",
            )
        } | {
            "amount": cents_to_money(item["amount_cents"]),
            "allocated": cents_to_money(item["allocated_cents"]),
            "remaining": cents_to_money(item["remaining_cents"]),
            "currency": "CNY",
        }

    def capture_cutover(self, conn, actor, project, report, mapping, records):
        bindings = report.get("open_item_bindings", [])
        if not bindings:
            return {"created": 0}
        if report.get("blocking_count") or not report.get("coverage", {}).get(
            "open_item_reconciliation_complete"
        ):
            raise AccountingError("历史未结事项必须按科目及往来方辅助余额完整核验后才能接管", 409)
        cutover = conn.execute(
            "SELECT * FROM migration_cutovers WHERE project_id=?", (project["id"],)
        ).fetchone()
        if not cutover:
            raise AccountingError("历史未结事项需要已验收的接管快照", 409)
        snapshot = json.loads(cutover["payload_json"])
        master_bindings = {
            (entry["kind"], entry["source_code"]): entry["target_id"]
            for entry in snapshot.get("master_bindings", [])
        }
        account_bindings = {
            entry["source_code"]: entry["target_code"] for entry in mapping.get("accounts", [])
        }
        indexed = {}
        for record in records:
            if record["kind"] == "open_item":
                indexed.setdefault(record["external_id"], record)
        created = 0
        for entry in bindings:
            cents = money_to_cents(entry["remaining_amount"])
            if cents <= 0:
                continue
            record = indexed.get(entry["external_id"])
            target_account = account_bindings.get(entry["account_code"])
            party_id = master_bindings.get(("counterparty", entry["counterparty_code"]))
            if not record or not record.get("id") or not target_account or not party_id:
                raise AccountingError("历史未结事项缺少原记录、已确认科目或往来方主数据映射", 409)
            if conn.execute(
                "SELECT 1 FROM migration_open_items WHERE project_id=? AND external_id=?",
                (project["id"], entry["external_id"]),
            ).fetchone():
                continue
            payload = record["payload"]
            prior_obligation = conn.execute(
                "SELECT 1 FROM migration_open_items WHERE firm_id=? AND ledger_id=? AND source_system_key=? AND external_book_id=? AND external_id=?",
                (
                    project["firm_id"],
                    project["ledger_id"],
                    project["source_system_key"],
                    project["external_book_id"],
                    entry["external_id"],
                ),
            ).fetchone()
            if prior_obligation:
                raise AccountingError(
                    "同一历史未结义务已接管，不能按新期间或新快照重复初始化余额；请核对既有清偿记录",
                    409,
                )
            conn.execute(
                "INSERT INTO migration_open_items(id,project_id,cutover_id,record_id,ledger_id,book_id,firm_id,company_id,source_system_key,external_book_id,external_id,kind,source_account_code,target_account_code,counterparty_code,counterparty_id,amount_cents,occurred_on,due_on,master_bindings_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    project["id"],
                    cutover["id"],
                    record["id"],
                    project["ledger_id"],
                    project["book_id"],
                    project["firm_id"],
                    project["company_id"],
                    project["source_system_key"],
                    project["external_book_id"],
                    entry["external_id"],
                    entry["kind"],
                    entry["account_code"],
                    target_account,
                    entry["counterparty_code"],
                    party_id,
                    cents,
                    payload["occurred_on"],
                    payload.get("due_on"),
                    encode(snapshot.get("master_bindings", [])),
                    actor.user_id,
                    now(),
                ),
            )
            created += 1
        return {"created": created}

    def _bank(self, conn, actor, project, book_id, source_id, write=False):
        book = self._bank_book(conn, actor, project, book_id, write)
        row = conn.execute(
            "SELECT s.*,d.id AS draft_id,d.status AS draft_status,d.version AS draft_version FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE s.id=? AND s.book_id=?",
            (source_id, book_id),
        ).fetchone()
        if not row or row["source_kind"] != "bank":
            raise AccountingError("当前银行流水或草稿不存在或无权访问", 404)
        source = dict(row)
        if (
            source["draft_status"] != "draft"
            or conn.execute(
                "SELECT 1 FROM sources s WHERE s.id=? AND " + inactive_source_sql(self.db.dialect),
                (source_id,),
            ).fetchone()
        ):
            raise AccountingError("只能分配未审核且未隔离的银行流水；已审核需另行调整", 409)
        if self.db.is_postgres:
            blocked = conn.execute(
                "SELECT accounting.revision_source_blocked(?,?),accounting.bank_source_import_blocked(?,?),accounting.case_source_blocked(?,?)",
                (book_id, source_id, book_id, source_id, book_id, source_id),
            ).fetchone()
            if blocked[0] or blocked[1] or blocked[2]:
                raise AccountingError("银行流水仍有重复、事实修订或待处理异常", 409)
        else:
            from ..accounting.bank_imports import BankImportService
            from ..business.service import BusinessService
            from ..cases.service import CaseService

            if (
                conn.execute(
                    "SELECT 1 FROM fact_revisions WHERE old_book_id=? AND old_source_id=? AND state='pending'",
                    (book_id, source_id),
                ).fetchone()
                or BankImportService(self.db).blocking_issues_in_transaction(conn, actor, source)
                or CaseService(self.db, BusinessService(self.db)).blocking_issues_in_transaction(
                    conn, actor, source
                )
            ):
                raise AccountingError("银行流水仍有重复、事实修订或待处理异常", 409)
        if (
            conn.execute(
                "SELECT 1 FROM ai_bank_links WHERE bank_source_id=?", (source_id,)
            ).fetchone()
            or conn.execute(
                "SELECT 1 FROM settlement_allocations a WHERE bank_source_id=? AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)",
                (source_id,),
            ).fetchone()
            or self.source_allocated_in_transaction(conn, actor, source)
        ):
            raise AccountingError("银行流水已关联业务或另一笔核销，不能重复记账", 409)
        return book, source

    def _plan(self, conn, actor, project, body):
        book, bank = self._bank(conn, actor, project, body.bank_book_id, body.bank_source_id, True)
        bank_account = conn.execute(
            "SELECT * FROM accounts WHERE book_id=? AND code=?",
            (book["id"], body.bank_account_code),
        ).fetchone()
        if (
            not bank_account
            or not bank_account["active"]
            or bank_account["category"] != "asset"
            or bank_account["normal_balance"] != "debit"
            or not body.bank_account_code.startswith("1002")
            or json.loads(bank_account["auxiliary_requirements"])
        ):
            raise AccountingError(
                "请选择当前启用、借方正常余额且无需额外辅助项的银行存款科目（1002及明细）", 409
            )
        party = self.master.resolve_in_transaction(
            conn, actor, project["company_id"], "counterparty", bank["counterparty"], book["id"]
        )
        if not party:
            raise AccountingError("请先审核银行交易对方的企业主数据或别名", 409)
        pieces, grouped, total = [], defaultdict(int), 0
        kinds = set()
        for part in body.parts:
            item = self._item(conn, project, part.item_id)
            amount = money_to_cents(part.amount)
            entity = conn.execute(
                "SELECT * FROM company_master_entities WHERE id=? AND firm_id=? AND company_id=? AND kind='counterparty' AND active=1",
                (item["counterparty_id"], actor.firm_id, project["company_id"]),
            ).fetchone()
            account = conn.execute(
                "SELECT * FROM accounts WHERE book_id=? AND code=?",
                (book["id"], item["target_account_code"]),
            ).fetchone()
            kind = item["kind"]
            if (
                item["counterparty_id"] != party["entity_id"]
                or not entity
                or amount > item["remaining_cents"]
            ):
                raise AccountingError(
                    "清偿须为同一已审核往来方，且金额不能超过当前历史未结余额", 409
                )
            if (kind == "payable") != (bank["direction"] == "expense"):
                raise AccountingError("应付须对应支出、应收须对应收入，不能自动做净额冲抵", 409)
            required = json.loads(account["auxiliary_requirements"]) if account else []
            if (
                not account
                or not account["active"]
                or account["category"] != ("liability" if kind == "payable" else "asset")
                or account["normal_balance"] != ("credit" if kind == "payable" else "debit")
                or set(required) - {"counterparty"}
            ):
                raise AccountingError(
                    "当前应收应付科目或辅助核算配置不兼容；请先人工确认科目及补足辅助项", 409
                )
            if item["target_account_code"] == body.bank_account_code:
                raise AccountingError("应收清偿科目不能与银行存款科目相同，不能自动抵成零额", 409)
            kinds.add(kind)
            total += amount
            aux = {"counterparty": entity["name"]} if "counterparty" in required else {}
            grouped[(item["target_account_code"], encode(aux))] += amount
            pieces.append(
                {
                    "item_id": item["id"],
                    "amount_cents": amount,
                    "remaining_before": item["remaining_cents"],
                    "kind": kind,
                    "clearing_account": item["target_account_code"],
                    "clearing_account_version": account["version"],
                    "counterparty_id": entity["id"],
                    "counterparty_name": entity["name"],
                    "counterparty_version": entity["version"],
                    "auxiliary_requirements": required,
                }
            )
        if len(kinds) != 1 or total != bank["amount_cents"]:
            raise AccountingError(
                "一笔银行流水须完整分配至同类应收或应付，分配合计须精确等于流水金额", 409
            )
        lines = [
            {
                "account_code": code,
                "debit": cents_to_money(amount if bank["direction"] == "expense" else 0),
                "credit": cents_to_money(amount if bank["direction"] == "income" else 0),
                "auxiliary_values": json.loads(aux),
            }
            for (code, aux), amount in sorted(grouped.items())
        ]
        lines.append(
            {
                "account_code": body.bank_account_code,
                "debit": cents_to_money(total if bank["direction"] == "income" else 0),
                "credit": cents_to_money(total if bank["direction"] == "expense" else 0),
                "auxiliary_values": {},
            }
        )
        description = (
            "清偿历史"
            + ("应付款" if bank["direction"] == "expense" else "应收款")
            + "："
            + bank["description"]
        )[:500]
        return {
            "project_id": project["id"],
            "bank_book_id": book["id"],
            "bank_source_id": bank["id"],
            "bank_fingerprint": bank["fingerprint"],
            "bank_draft_id": bank["draft_id"],
            "bank_draft_version": bank["draft_version"],
            "bank_account_code": body.bank_account_code,
            "bank_account_version": bank_account["version"],
            "bank_party_id": party["entity_id"],
            "bank_party_version": party["version"],
            "parts": pieces,
            "total_cents": total,
            "direction": bank["direction"],
            "lines": lines,
            "description": description,
            "reason": body.reason,
        }

    def preview(self, actor, book_id, project_id, body):
        with self.db.transaction(write=True, actor=actor) as conn:
            project = self._project(conn, actor, book_id, project_id)
            plan = self._plan(conn, actor, project, body)
            identity, stamp = uuid4().hex, now()
            token = fingerprint(plan)
            conn.execute(
                "INSERT INTO migration_clearance_previews(id,project_id,book_id,bank_book_id,firm_id,company_id,created_by,request_json,plan_json,fingerprint,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    project_id,
                    book_id,
                    body.bank_book_id,
                    actor.firm_id,
                    project["company_id"],
                    actor.user_id,
                    encode(body.model_dump()),
                    encode(plan),
                    token,
                    stamp,
                    (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                ),
            )
            previous = self.accounting._draft_json(
                conn,
                conn.execute(
                    "SELECT * FROM drafts WHERE id=?", (plan["bank_draft_id"],)
                ).fetchone(),
                actor,
            )
            return {
                "id": identity,
                "fingerprint": token,
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                "plan": plan,
                "previous_draft": previous,
                "requires_accounting_review": True,
                "official_ledger": "source_software",
            }

    def _command_fingerprint(self, conn, row):
        if self.db.is_postgres:
            return conn.execute(
                "SELECT accounting.legacy_clearance_fingerprint(?)", (row["id"],)
            ).fetchone()[0]
        draft = conn.execute(
            "SELECT version,status,description FROM drafts WHERE id=? AND book_id=?",
            (row["bank_draft_id"], row["bank_book_id"]),
        ).fetchone()
        lines = [
            dict(line)
            for line in conn.execute(
                "SELECT l.*,COALESCE(a.values_json,'{}') AS auxiliary_values_json FROM draft_lines l LEFT JOIN draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.book_id=l.book_id AND a.ordinal=l.ordinal WHERE l.draft_id=? ORDER BY l.ordinal",
                (row["bank_draft_id"],),
            )
        ]
        return fingerprint(
            {
                "command_id": row["id"],
                "plan": json.loads(row["plan_json"]),
                "draft": dict(draft),
                "lines": lines,
                "reversed": bool(
                    conn.execute(
                        "SELECT 1 FROM migration_clearance_reversals WHERE command_id=?",
                        (row["id"],),
                    ).fetchone()
                ),
            }
        )

    def _receipt(self, conn, row, actor=None):
        result = {
            key: row[key]
            for key in (
                "id",
                "project_id",
                "bank_book_id",
                "bank_source_id",
                "bank_draft_id",
                "created_by",
                "created_at",
            )
        }
        result["allocations"] = [
            dict(item) | {"amount": cents_to_money(item["amount_cents"])}
            for item in conn.execute(
                "SELECT id,item_id,amount_cents,target_account_code FROM migration_clearance_allocations WHERE command_id=? ORDER BY item_id",
                (row["id"],),
            )
        ]
        result["reversed"] = bool(
            conn.execute(
                "SELECT 1 FROM migration_clearance_reversals WHERE command_id=?", (row["id"],)
            ).fetchone()
        )
        result["fingerprint"] = self._command_fingerprint(conn, row)
        draft = conn.execute(
            "SELECT status,version FROM drafts WHERE id=?", (row["bank_draft_id"],)
        ).fetchone()
        period = conn.execute(
            "SELECT status FROM periods WHERE book_id=?", (row["bank_book_id"],)
        ).fetchone()
        result["can_reverse"] = bool(
            actor
            and actor.user_id != row["created_by"]
            and draft
            and draft["status"] == "draft"
            and draft["version"] == row["applied_draft_version"]
            and period
            and period["status"] == "open"
            and not result["reversed"]
        )
        return result

    def confirm(self, actor, book_id, project_id, preview_id, body):
        with self.db.transaction(write=True, actor=actor) as conn:
            project = self._project(conn, actor, book_id, project_id)
            preview = conn.execute(
                "SELECT * FROM migration_clearance_previews WHERE id=? AND project_id=? AND created_by=? AND firm_id=?",
                (preview_id, project_id, actor.user_id, actor.firm_id),
            ).fetchone()
            if not preview:
                raise AccountingError("清偿预览不存在、属于其他人员或已撤权", 404)
            self._bank_book(conn, actor, project, preview["bank_book_id"])
            digest = fingerprint(
                {"preview_id": preview_id, "expected_fingerprint": body.expected_fingerprint}
            )
            prior = conn.execute(
                "SELECT * FROM migration_clearance_commands WHERE firm_id=? AND created_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, body.idempotency_key),
            ).fetchone()
            if prior:
                if prior["project_id"] != project_id or prior["request_fingerprint"] != digest:
                    raise AccountingError("清偿幂等键已绑定其他预览", 409)
                return self._receipt(conn, prior, actor)
            request = LegacyAllocationPreview.model_validate(json.loads(preview["request_json"]))
            plan = self._plan(conn, actor, project, request)
            if (
                preview["fingerprint"] != body.expected_fingerprint
                or fingerprint(plan) != preview["fingerprint"]
                or datetime.now(timezone.utc) > datetime.fromisoformat(preview["expires_at"])
            ):
                raise AccountingError(
                    "清偿预览已过期，或流水、余额、主数据、科目及草稿版本已变化", 409
                )
            previous = self.accounting._draft_json(
                conn,
                conn.execute(
                    "SELECT * FROM drafts WHERE id=?", (plan["bank_draft_id"],)
                ).fetchone(),
                actor,
            )
            command_id, stamp = uuid4().hex, now()
            conn.execute(
                "INSERT INTO migration_clearance_commands(id,project_id,book_id,bank_book_id,firm_id,company_id,preview_id,bank_source_id,bank_draft_id,bank_account_code,expected_draft_version,applied_draft_version,previous_draft_json,plan_json,created_by,idempotency_key,request_fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    command_id,
                    project_id,
                    book_id,
                    request.bank_book_id,
                    actor.firm_id,
                    project["company_id"],
                    preview_id,
                    plan["bank_source_id"],
                    plan["bank_draft_id"],
                    plan["bank_account_code"],
                    plan["bank_draft_version"],
                    plan["bank_draft_version"] + 1,
                    encode({"description": previous["description"], "lines": previous["lines"]}),
                    encode(plan),
                    actor.user_id,
                    body.idempotency_key,
                    digest,
                    stamp,
                ),
            )
            self.accounting.update_draft_in_transaction(
                conn,
                actor,
                request.bank_book_id,
                plan["bank_draft_id"],
                DraftUpdate(
                    expected_version=plan["bank_draft_version"],
                    description=plan["description"],
                    lines=plan["lines"],
                    correction_reason=request.reason,
                ),
            )
            for part in plan["parts"]:
                conn.execute(
                    "INSERT INTO migration_clearance_allocations(id,command_id,project_id,bank_book_id,firm_id,company_id,item_id,bank_source_id,amount_cents,target_account_code,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        command_id,
                        project_id,
                        request.bank_book_id,
                        actor.firm_id,
                        project["company_id"],
                        part["item_id"],
                        plan["bank_source_id"],
                        part["amount_cents"],
                        part["clearing_account"],
                        actor.user_id,
                        stamp,
                    ),
                )
            self.migration._audit(
                conn,
                actor,
                request.bank_book_id,
                "historical_clearance.confirm",
                command_id,
                {
                    "project_id": project_id,
                    "bank_source_id": plan["bank_source_id"],
                    "parts": plan["parts"],
                    "reason": request.reason,
                },
            )
            return self._receipt(
                conn,
                conn.execute(
                    "SELECT * FROM migration_clearance_commands WHERE id=?", (command_id,)
                ).fetchone(),
                actor,
            )

    def reverse(self, actor, book_id, project_id, command_id, body):
        with self.db.transaction(write=True, actor=actor) as conn:
            project = self._project(conn, actor, book_id, project_id)
            command = conn.execute(
                "SELECT * FROM migration_clearance_commands WHERE id=? AND project_id=? AND firm_id=?",
                (command_id, project_id, actor.firm_id),
            ).fetchone()
            if not command:
                raise AccountingError("历史清偿记录不存在或无权访问", 404)
            self._bank_book(conn, actor, project, command["bank_book_id"])
            if command["created_by"] == actor.user_id:
                raise AccountingError("撤销清偿须由另一名当前会计复核人员独立确认", 403)
            prior = conn.execute(
                "SELECT * FROM migration_clearance_reversals WHERE firm_id=? AND created_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, body.idempotency_key),
            ).fetchone()
            if prior:
                if (
                    prior["command_id"] != command_id
                    or prior["expected_fingerprint"] != body.expected_fingerprint
                    or prior["reason"] != body.reason
                ):
                    raise AccountingError("撤销幂等键已绑定不同清偿事实", 409)
                return dict(prior)
            self._bank_book(conn, actor, project, command["bank_book_id"], True)
            draft = conn.execute(
                "SELECT * FROM drafts WHERE id=?", (command["bank_draft_id"],)
            ).fetchone()
            if (
                draft["status"] != "draft"
                or draft["version"] != command["applied_draft_version"]
                or self._command_fingerprint(conn, command) != body.expected_fingerprint
                or conn.execute(
                    "SELECT 1 FROM migration_clearance_reversals WHERE command_id=?", (command_id,)
                ).fetchone()
            ):
                raise AccountingError("清偿事实已变化或银行凭证已审核/导出，只能另行调整", 409)
            original = json.loads(command["previous_draft_json"])
            identity = uuid4().hex
            conn.execute(
                "INSERT INTO migration_clearance_reversals(id,command_id,project_id,bank_book_id,firm_id,company_id,created_by,created_at,idempotency_key,reason,expected_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    command_id,
                    project_id,
                    command["bank_book_id"],
                    actor.firm_id,
                    project["company_id"],
                    actor.user_id,
                    now(),
                    body.idempotency_key,
                    body.reason,
                    body.expected_fingerprint,
                ),
            )
            self.accounting.update_draft_in_transaction(
                conn,
                actor,
                command["bank_book_id"],
                command["bank_draft_id"],
                DraftUpdate(
                    expected_version=draft["version"],
                    description=original["description"],
                    lines=original["lines"],
                    correction_reason=body.reason,
                ),
            )
            self.migration._audit(
                conn,
                actor,
                command["bank_book_id"],
                "historical_clearance.reverse",
                command_id,
                {"reason": body.reason, "project_id": project_id},
            )
            return dict(
                conn.execute(
                    "SELECT * FROM migration_clearance_reversals WHERE id=?", (identity,)
                ).fetchone()
            )

    def list(self, actor, book_id, project_id, limit=50, offset=0):
        page_bounds(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            project = self._project(conn, actor, book_id, project_id)
            total = conn.execute(
                "SELECT COUNT(*) FROM migration_open_items WHERE project_id=? AND firm_id=?",
                (project_id, actor.firm_id),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id FROM migration_open_items WHERE project_id=? AND firm_id=? ORDER BY occurred_on,external_id,id LIMIT ? OFFSET ?",
                (project_id, actor.firm_id, limit, offset),
            ).fetchall()
            review_scope, review_args, _ = ReviewService(self.db, self.accounting)._review_scope(
                conn, actor
            )
            commands = [
                self._receipt(conn, row, actor)
                for row in conn.execute(
                    "WITH current_review_books AS ("
                    + review_scope
                    + ") SELECT c.* FROM migration_clearance_commands c JOIN current_review_books b ON b.id=c.bank_book_id WHERE c.project_id=? AND c.firm_id=? ORDER BY c.created_at DESC,c.id LIMIT ?",
                    [*review_args, project_id, actor.firm_id, limit],
                )
            ]
            return {
                "items": [self._item_json(self._item(conn, project, row["id"])) for row in rows],
                "commands": commands,
                "pagination": {
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "has_more": offset + limit < total,
                },
                "official_ledger": "source_software",
                "requires_accounting_review": True,
            }

    def eligible_current_bank_sources(
        self, actor, book_id, project_id, bank_book_id, limit=50, offset=0
    ):
        page_bounds(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            project = self._project(conn, actor, book_id, project_id)
            bank_book = self._bank_book(conn, actor, project, bank_book_id)
            revision = (
                "accounting.revision_source_blocked(s.book_id,s.id)"
                if self.db.is_postgres
                else "EXISTS(SELECT 1 FROM fact_revisions f WHERE f.old_book_id=s.book_id AND f.old_source_id=s.id AND f.state='pending')"
            )
            scope = (
                "s.book_id=? AND s.source_kind='bank' AND d.status='draft' AND NOT "
                + inactive_source_sql(self.db.dialect)
                + " AND NOT "
                + revision
                + " AND NOT EXISTS(SELECT 1 FROM ai_bank_links l WHERE l.bank_source_id=s.id) AND NOT EXISTS(SELECT 1 FROM settlement_allocations a WHERE a.bank_source_id=s.id AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id)) AND NOT EXISTS(SELECT 1 FROM migration_clearance_allocations a WHERE a.bank_source_id=s.id AND NOT EXISTS(SELECT 1 FROM migration_clearance_reversals r WHERE r.command_id=a.command_id))"
            )
            if self.db.is_postgres:
                scope += " AND NOT accounting.bank_source_import_blocked(s.book_id,s.id) AND NOT accounting.case_source_blocked(s.book_id,s.id)"
            else:
                scope += " AND NOT EXISTS(SELECT 1 FROM case_tasks c WHERE c.state<>'closed' AND c.book_id=s.book_id AND (c.source_id=s.id OR c.draft_id=d.id))"
                scope += """ AND NOT EXISTS(SELECT 1 FROM sources peer LEFT JOIN bank_import_keys own_key ON own_key.source_id=s.id LEFT JOIN bank_import_keys peer_key ON peer_key.source_id=peer.id
                  WHERE peer.book_id=s.book_id AND peer.id<>s.id AND peer.source_kind='bank' AND peer.date=s.date AND peer.amount_cents=s.amount_cents AND peer.direction=s.direction AND peer.counterparty=s.counterparty
                  AND (own_key.source_id IS NOT NULL OR peer_key.source_id IS NOT NULL) AND (own_key.source_id IS NULL OR peer_key.source_id IS NULL OR own_key.bank_account=peer_key.bank_account)
                  AND NOT EXISTS(SELECT 1 FROM bank_source_dispositions x WHERE x.book_id=s.book_id AND x.source_id=peer.id)
                  AND NOT EXISTS(SELECT 1 FROM bank_duplicate_decisions x WHERE x.book_id=s.book_id AND x.source_id=CASE WHEN s.id<peer.id THEN s.id ELSE peer.id END AND x.other_source_id=CASE WHEN s.id<peer.id THEN peer.id ELSE s.id END))"""
            base = (
                " FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE "
                + scope
            )
            total = conn.execute("SELECT COUNT(*)" + base, (bank_book_id,)).fetchone()[0]
            rows = [
                dict(row) | {"amount": cents_to_money(row["amount_cents"])}
                for row in conn.execute(
                    "SELECT s.id,s.book_id,s.date,s.description,s.counterparty,s.amount_cents,s.direction,d.id AS draft_id,d.version AS draft_version"
                    + base
                    + " ORDER BY s.date,s.id LIMIT ? OFFSET ?",
                    (bank_book_id, limit, offset),
                )
            ]
            accounts = [
                dict(row)
                for row in conn.execute(
                    "SELECT code,name FROM accounts WHERE book_id=? AND active=1 AND category='asset' AND normal_balance='debit' AND code LIKE ? AND auxiliary_requirements='[]' ORDER BY code",
                    (bank_book_id, "1002%"),
                )
            ]
            return {
                "banks": rows,
                "bank_accounts": accounts,
                "bank_book_id": bank_book_id,
                "period_status": bank_book["period_status"],
                "pagination": {
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "has_more": offset + limit < total,
                },
            }

    def source_allocated_in_transaction(self, conn, actor, source):
        if self.db.is_postgres:
            return bool(
                conn.execute(
                    "SELECT accounting.legacy_clearance_source_locked(?,?)",
                    (source["book_id"], source["id"]),
                ).fetchone()[0]
            )
        return bool(
            conn.execute(
                "SELECT 1 FROM migration_clearance_allocations a WHERE a.bank_source_id=? AND a.bank_book_id=? AND NOT EXISTS(SELECT 1 FROM migration_clearance_reversals r WHERE r.command_id=a.command_id)",
                (source["id"], source["book_id"]),
            ).fetchone()
        )

    def policy_fingerprint_in_transaction(self, conn, actor, source):
        return fingerprint(
            [
                dict(row)
                for row in conn.execute(
                    "SELECT a.id,a.item_id,a.amount_cents,a.target_account_code,r.id AS reversal_id FROM migration_clearance_allocations a LEFT JOIN migration_clearance_reversals r ON r.command_id=a.command_id WHERE a.bank_source_id=? AND a.bank_book_id=? ORDER BY a.id",
                    (source["id"], source["book_id"]),
                )
            ]
        )

    def bank_clearance(self, conn, actor, source):
        command = conn.execute(
            "SELECT c.* FROM migration_clearance_commands c WHERE c.bank_source_id=? AND c.bank_book_id=? AND NOT EXISTS(SELECT 1 FROM migration_clearance_reversals r WHERE r.command_id=c.id)",
            (source["id"], source["book_id"]),
        ).fetchone()
        if not command:
            if self.db.is_postgres and self.source_allocated_in_transaction(conn, actor, source):
                return {
                    "lines": [],
                    "description": "",
                    "issues": ["当前没有该历史接管项目的明确资料授权，不能复核银行清偿凭证"],
                    "allocated": cents_to_money(source["amount_cents"]),
                    "requires_accounting_review": True,
                }
            return None
        if actor is None:
            return {
                "lines": [],
                "description": "",
                "issues": ["需要当前会计与历史资料授权才能核验清偿分录"],
                "allocated": cents_to_money(source["amount_cents"]),
                "requires_accounting_review": True,
            }
        plan, issues = json.loads(command["plan_json"]), []
        if (
            conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) FROM migration_clearance_allocations WHERE command_id=?",
                (command["id"],),
            ).fetchone()[0]
            != source["amount_cents"]
        ):
            issues.append("历史清偿分配尚未完整发布，不能复核或导出")
        try:
            project = self._project(conn, actor, command["book_id"], command["project_id"])
            self._bank_book(conn, actor, project, source["book_id"])
            party = self.master.resolve_in_transaction(
                conn,
                actor,
                project["company_id"],
                "counterparty",
                source["counterparty"],
                source["book_id"],
            )
            if (
                source["fingerprint"] != plan["bank_fingerprint"]
                or not party
                or party["entity_id"] != plan["bank_party_id"]
                or party["version"] != plan["bank_party_version"]
            ):
                issues.append("历史清偿引用的银行事实或往来方映射已变化，须人工处理")
            for part in plan["parts"]:
                account = conn.execute(
                    "SELECT active,version FROM accounts WHERE book_id=? AND code=?",
                    (source["book_id"], part["clearing_account"]),
                ).fetchone()
                entity = conn.execute(
                    "SELECT active,version FROM company_master_entities WHERE id=?",
                    (part["counterparty_id"],),
                ).fetchone()
                if (
                    not account
                    or not account["active"]
                    or account["version"] != part["clearing_account_version"]
                    or not entity
                    or not entity["active"]
                    or entity["version"] != part["counterparty_version"]
                ):
                    issues.append("历史清偿科目或往来方主数据已变化，须人工处理")
        except AccountingError:
            issues.append("当前已失去历史迁移或银行账套权限，不能复核清偿凭证")
        return {
            "lines": plan["lines"],
            "description": plan["description"],
            "issues": list(dict.fromkeys(issues)),
            "allocated": cents_to_money(plan["total_cents"]),
            "requires_accounting_review": True,
        }

    def blocking_issues_in_transaction(self, conn, actor, source, lines=None):
        clearance = self.bank_clearance(conn, actor, source)
        if clearance is None:
            return []
        issues = list(clearance["issues"])
        draft = conn.execute(
            "SELECT id,description FROM drafts WHERE source_id=? AND book_id=?",
            (source["id"], source["book_id"]),
        ).fetchone()
        if lines is None or (lines and "debit_cents" in lines[0].keys()):
            values = {
                row["ordinal"]: json.loads(row["values_json"])
                for row in conn.execute(
                    "SELECT ordinal,values_json FROM draft_line_auxiliaries WHERE draft_id=? ORDER BY ordinal",
                    (draft["id"],),
                )
            }
            raw_lines = (
                lines
                if lines is not None
                else conn.execute(
                    "SELECT * FROM draft_lines WHERE draft_id=? ORDER BY ordinal", (draft["id"],)
                )
            )
            lines = [
                {
                    "account_code": row["account_code"],
                    "debit": cents_to_money(row["debit_cents"]),
                    "credit": cents_to_money(row["credit_cents"]),
                    "auxiliary_values": values.get(row["ordinal"], {}),
                }
                for row in raw_lines
            ]
        if (
            encode(lines) != encode(clearance["lines"])
            or draft["description"] != clearance["description"]
        ):
            issues.append("历史清偿草稿必须与已确认分配一致，不能再次计入费用或收入")
        return list(dict.fromkeys(issues))
