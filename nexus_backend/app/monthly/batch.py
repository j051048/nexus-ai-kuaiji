"""月初批量开期：为既有客户批量创建当月账套、继承分工并批量生成催资清单。

Only reuses the governed existing paths: ledger period creation keeps its
manager grant, member re-validation, idempotency and audit; material
generation keeps its per-item validation. Every company is reported on its
own row and one failing company never rolls back the others.
"""

import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.accounting.directory import DirectoryService
from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.ledgers.models import PeriodCreate
from app.service_plans.models import Generate

MAX_COMPANIES = 200
UserId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]
Search = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]


class BatchOpen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: str
    scope: Literal["companies", "awaiting"] = "companies"
    companies: list[UserId] = Field(default_factory=list, max_length=MAX_COMPANIES)
    search: Search = ""
    generate_materials: bool = True
    reason: Reason

    @field_validator("period")
    @classmethod
    def valid_period(cls, value):
        if not re.fullmatch(r"20[0-9]{2}-(?:0[1-9]|1[0-2])", value):
            raise ValueError("请选择有效的 YYYY-MM 月份")
        return value

    @field_validator("companies")
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("企业标识不得重复")
        return sorted(value)

    @model_validator(mode="after")
    def coherent(self):
        if self.scope == "companies" and not self.companies:
            raise ValueError("请先选择要开期的企业")
        if self.scope == "awaiting" and self.companies:
            raise ValueError("按待建账范围开期时不要再提交企业清单")
        return self


def _chunks(items, size=400):
    for start in range(0, len(items), size):
        yield items[start : start + size]


class MonthlyBatchService:
    def __init__(self, db, accounting, ledgers, plans, business):
        self.db, self.accounting, self.ledgers = db, accounting, ledgers
        self.plans, self.business = plans, business
        self.access = accounting.access

    def open_month(self, actor, request: BatchOpen):
        company_ids, names = self.resolve_companies(actor, request)
        results = [
            self.open_company(actor, company_id, names.get(company_id, ""), request)
            for company_id in company_ids
        ]
        return self._result(request.period, results)

    def resolve_companies(self, actor, request: BatchOpen):
        period = request.period
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            if request.scope == "awaiting":
                company_ids = self._awaiting(conn, actor, period, request.search)
            else:
                company_ids = list(request.companies)
            names = self._names(conn, actor, company_ids)
        return company_ids, names

    def preview(self, actor, request: BatchOpen):
        """Read-only precheck: what would happen, without opening anything.

        The operator needs to see "already open / no history / no access" before
        committing 200 companies; nothing here writes a book, a plan or a
        material list.
        """
        company_ids, names = self.resolve_companies(actor, request)
        rows = []
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            # Two set-based reads instead of three queries per company: at the
            # 200-company cap the per-row form cost ~600 round trips (~7s on PG).
            opened = self._period_books(conn, actor.firm_id, request.period, company_ids)
            history = self._prior_books(conn, actor.firm_id, request.period, company_ids)
            for company_id in company_ids:
                row = self._row(company_id, names.get(company_id, ""), "ready")
                try:
                    self.access.company(conn, actor, company_id, manage=True)
                except AccountingError as error:
                    row.update(status="blocked", code="no_access", detail=error.detail)
                else:
                    if not names.get(company_id):
                        row.update(status="blocked", code="not_client", detail="不是本所的在册客户")
                    elif company_id in opened:
                        row.update(status="skipped", code="already_open", detail="该月份账套已存在")
                    elif company_id not in history:
                        row.update(
                            status="blocked",
                            code="no_history",
                            detail="没有历史账期，请先完成首次接入",
                        )
                    else:
                        row["from_period"] = history[company_id]
                rows.append(row)
        ready = sum(1 for row in rows if row["status"] == "ready")
        return {
            "period": request.period,
            "ready": ready,
            "skipped": sum(1 for row in rows if row["status"] == "skipped"),
            "blocked": sum(1 for row in rows if row["status"] == "blocked"),
            "results": rows,
            "notes": self._preview_notes(len(rows)),
        }

    def _period_books(self, conn, firm_id, period, company_ids):
        found = set()
        for chunk in _chunks(company_ids):
            marks = ",".join("?" * len(chunk))
            for row in conn.execute(
                f"SELECT company_id FROM books WHERE firm_id=? AND period=? AND company_id IN ({marks})",
                (firm_id, period, *chunk),
            ).fetchall():
                found.add(row["company_id"])
        return found

    def _prior_books(self, conn, firm_id, period, company_ids):
        latest = {}
        for chunk in _chunks(company_ids):
            marks = ",".join("?" * len(chunk))
            for row in conn.execute(
                f"SELECT company_id, MAX(period) AS prior FROM books "
                f"WHERE firm_id=? AND period<? AND company_id IN ({marks}) GROUP BY company_id",
                (firm_id, period, *chunk),
            ).fetchall():
                latest[row["company_id"]] = row["prior"]
        return latest

    @staticmethod
    def _preview_notes(count):
        notes = [
            "预检只读，不会创建账套、服务计划或催资清单。",
            "标记为跳过的企业已存在该月份账套；标记为阻塞的企业需先处理接入或权限。",
        ]
        if count >= MAX_COMPANIES:
            notes.append(
                f"范围达到单批上限 {MAX_COMPANIES} 家，未列出的客户本次不会被开期，请缩小范围分批处理。"
            )
        return notes

    def open_company(
        self, actor, company_id, company_name, request: BatchOpen, *, before_create=None
    ):
        return self._open_company(
            actor, company_id, company_name, request, before_create=before_create
        )

    @staticmethod
    def _result(period, results):
        return {
            "period": period,
            "opened": sum(1 for row in results if row["status"] == "created"),
            "skipped": sum(1 for row in results if row["status"] == "skipped"),
            "blocked": sum(1 for row in results if row["status"] == "blocked"),
            "results": results,
            "notes": [
                "批量开期只处理已有历史账期的企业；首次接入请先完成开通清单。",
                "长期账套的新期间沿用既有科目、凭证模板与当前有效成员，不会恢复已撤权账号。",
                "催资清单按长期账套模板生成；重复执行不会重复创建，成员失权会逐户报告。",
            ],
        }

    @staticmethod
    def _like(value):
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _awaiting(self, conn, actor, period, search):
        sql = (
            "SELECT c.id FROM companies c JOIN firm_clients fc "
            "ON fc.company_id=c.id AND fc.firm_id=? AND fc.status='active' "
            "WHERE c.status='active' AND NOT EXISTS(SELECT 1 FROM books b "
            "WHERE b.firm_id=fc.firm_id AND b.company_id=c.id AND b.period=?)"
        )
        values = [actor.firm_id, period]
        if search.strip():
            sql += " AND LOWER(c.name) LIKE ? ESCAPE '\\'"
            values.append("%" + self._like(search.strip().lower()) + "%")
        rows = conn.execute(sql + " ORDER BY c.name,c.id", values).fetchall()
        ids = [row["id"] for row in rows]
        if len(ids) > MAX_COMPANIES:
            raise AccountingError(
                f"待建账企业超过 {MAX_COMPANIES} 家，请先用搜索缩减范围后分批执行", 409
            )
        return ids

    def _names(self, conn, actor, company_ids):
        if not company_ids:
            return {}
        marks = ",".join("?" for _ in company_ids)
        rows = conn.execute(
            "SELECT c.id,c.name FROM companies c JOIN firm_clients fc "
            "ON fc.company_id=c.id AND fc.firm_id=? WHERE c.id IN (" + marks + ")",
            [actor.firm_id, *company_ids],
        ).fetchall()
        return {row["id"]: row["name"] for row in rows}

    @staticmethod
    def _row(company_id, company_name, status, **extra):
        row = {
            "company_id": company_id,
            "company_name": company_name,
            "status": status,
            "book_id": extra.get("book_id"),
            "plan": extra.get("plan"),
            "materials": extra.get("materials"),
            "warnings": extra.get("warnings", []),
            "message": extra.get("message", ""),
        }
        return row

    def _open_company(self, actor, company_id, company_name, request, *, before_create=None):
        period = request.period
        try:
            with self.db.transaction(actor=actor) as conn:
                self.access.require_manager(conn, actor)
                self.access.company(conn, actor, company_id, manage=True)
                existing = conn.execute(
                    "SELECT id FROM books WHERE firm_id=? AND company_id=? AND period=?",
                    (actor.firm_id, company_id, period),
                ).fetchone()
                prior = conn.execute(
                    "SELECT id,name,period FROM books WHERE firm_id=? AND company_id=? AND period<? "
                    "ORDER BY period DESC,id DESC LIMIT 1",
                    (actor.firm_id, company_id, period),
                ).fetchone()
                ledger = (
                    conn.execute(
                        "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (prior["id"],)
                    ).fetchone()
                    if prior
                    else None
                )
        except AccountingError as error:
            return self._row(company_id, company_name, "blocked", message=str(error))
        if existing:
            return self._row(
                company_id,
                company_name,
                "skipped",
                book_id=existing["id"],
                message="该企业本月账套已存在，未重复创建。",
            )
        if not prior:
            return self._row(
                company_id,
                company_name,
                "blocked",
                message="尚无历史账期或长期账套，请先在开通清单完成首次建账。",
            )
        name = f"{company_name or prior['name']} {period}"
        warnings, plan, materials = [], None, None
        try:
            if ledger:
                inherited = self._ledger_members(actor, prior["id"])
                created = self.ledgers.create_period(
                    actor,
                    prior["id"],
                    PeriodCreate(
                        period=period,
                        name=name,
                        source_book_id=prior["id"],
                        inherit_user_ids=inherited,
                        inherit_rule_ids=[],
                        idempotency_key=f"monthly-batch:{period}:{company_id}",
                    ),
                    **({"before_create": before_create} if before_create is not None else {}),
                )
                if created.get("replayed"):
                    # Another operator committed the same key between our
                    # pre-check and this transaction. The book exists, but this
                    # operator did not create it, so neither the plan/material
                    # steps nor the creation audit may run twice.
                    return self._row(
                        company_id,
                        company_name,
                        "skipped",
                        book_id=created["book_id"],
                        message="另一位操作员已创建该企业本月账套，本次未重复创建。",
                    )
                book_id = created["book_id"]
                try:
                    plan = self.plans.inherit_plan(actor, prior["id"], book_id, request.reason)
                    dropped = plan.get("dropped") or []
                    if dropped:
                        warnings.append("部分服务单分工因成员失权未继承：" + "、".join(dropped))
                except AccountingError as error:
                    warnings.append("服务单分工未继承：" + str(error))
                if request.generate_materials:
                    materials, notes = self._generate_materials(actor, book_id, request.reason)
                    warnings.extend(notes)
            else:
                book = self.accounting.create_book(
                    actor,
                    BookCreate(name=name, period=period, company_id=company_id),
                    **({"before_create": before_create} if before_create is not None else {}),
                )
                book_id = book["id"]
                self._copy_access(actor, prior["id"], book_id)
                warnings.append(
                    "该企业尚未建立长期账套：已创建账套并沿用历史成员，但未生成资料催收清单。"
                )
        except AccountingError as error:
            return self._row(company_id, company_name, "blocked", message=str(error))
        self._audit(
            actor,
            company_id,
            book_id,
            {
                "period": period,
                "source_book_id": prior["id"],
                "ledger": bool(ledger),
                "plan": plan,
                "materials": materials,
                "warnings": warnings,
                "reason": request.reason,
            },
        )
        return self._row(
            company_id,
            company_name,
            "created",
            book_id=book_id,
            plan=plan,
            materials=materials,
            warnings=warnings,
        )

    def _ledger_members(self, actor, source_book_id):
        """Current valid members of the source period, mirroring create_period."""
        with self.db.transaction(actor=actor) as conn:
            if self.db.is_postgres:
                rows = conn.execute(
                    "SELECT user_id FROM accounting.ledger_member_choices(?) ORDER BY user_id",
                    (source_book_id,),
                ).fetchall()
                ids = [row["user_id"] for row in rows]
            else:
                book = self.access.book(conn, actor, source_book_id)
                rows = conn.execute(
                    "SELECT user_id,role FROM book_access WHERE book_id=? AND user_id<>? ORDER BY user_id",
                    (source_book_id, actor.user_id),
                ).fetchall()
                ids = [
                    row["user_id"]
                    for row in rows
                    if self.ledgers._member_current(conn, actor, book, row)
                ]
        if len(ids) > 100:
            raise AccountingError("来源账套成员超过 100 人，请拆分后再批量开期", 409)
        return ids

    def _member_active(self, conn, actor, company_id, user_id):
        if not conn.execute("SELECT 1 FROM users WHERE id=? AND active=1", (user_id,)).fetchone():
            return False
        if conn.execute(
            "SELECT 1 FROM memberships WHERE firm_id=? AND user_id=?",
            (actor.firm_id, user_id),
        ).fetchone():
            return True
        row = conn.execute(
            "SELECT role FROM company_memberships WHERE company_id=? AND user_id=?",
            (company_id, user_id),
        ).fetchone()
        return bool(row and row["role"] in {"owner", "admin", "finance"})

    def _copy_access(self, actor, source_book_id, target_book_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            book = self.access.book(conn, actor, source_book_id)
            rows = conn.execute(
                "SELECT user_id,role FROM book_access WHERE book_id=? AND user_id<>?",
                (source_book_id, actor.user_id),
            ).fetchall()
            for row in rows:
                if not self._member_active(conn, actor, book["company_id"], row["user_id"]):
                    continue
                conn.execute(
                    "INSERT INTO book_access(book_id,firm_id,user_id,role) VALUES(?,?,?,?) "
                    "ON CONFLICT(book_id,user_id) DO NOTHING",
                    (target_book_id, actor.firm_id, row["user_id"], row["role"]),
                )

    def _generate_materials(self, actor, book_id, reason):
        with self.db.transaction(actor=actor) as conn:
            ledger = conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book_id,)
            ).fetchone()
            templates = (
                conn.execute(
                    "SELECT id,version FROM material_templates WHERE ledger_id=? "
                    "ORDER BY created_at,id",
                    (ledger["ledger_id"],),
                ).fetchall()
                if ledger
                else []
            )
        if not templates:
            return {"created": 0, "reused": 0}, ["长期账套尚未建立资料模板，未生成资料催收清单。"]
        created = reused = 0
        notes = []
        for template in templates:
            try:
                result = self.plans.generate(
                    actor,
                    book_id,
                    template["id"],
                    Generate(expected_version=template["version"], reason=reason),
                )
                created += result["created"]
                reused += result["reused"]
            except AccountingError as error:
                notes.append("资料清单未生成：" + str(error))
        return {"created": created, "reused": reused}, notes

    def _audit(self, actor, company_id, book_id, details):
        with self.db.transaction(write=True, actor=actor) as conn:
            DirectoryService._audit(
                conn,
                actor,
                "monthly.batch_open",
                company_id,
                {"company_id": company_id, **details},
                book_id=book_id,
            )
