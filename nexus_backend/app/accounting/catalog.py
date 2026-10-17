"""Deterministic client configuration. No model output or executable formula."""

import json
from uuid import uuid4

from .access import AccessControl
from .catalog_models import (
    AccountDefinition,
    AccountImportRequest,
    AccountUpdate,
    TemplateDefinition,
    TemplateUpdate,
)
from .domain import AccountingError
from .models import cents_to_money, money_to_cents
from .service import fingerprint, now

DIMENSIONS = {"department", "counterparty", "employee", "project"}
DIMENSION_NAMES = {
    "department": "部门",
    "counterparty": "交易对方",
    "employee": "人员",
    "project": "项目",
}


class CatalogService:
    def __init__(self, db):
        self.db, self.access = db, AccessControl()

    def _book(self, conn, actor, book_id, write=False):
        book = self.access.book(conn, actor, book_id, "review" if write else "read")
        if write and book["period_status"] != "open":
            raise AccountingError("已关闭期间不能变更科目或记账模板", 403)
        return book

    def _company_of_book(self, actor, book_id):
        """Resolve the book's company up front for a company-scoped write lock."""
        with self.db.transaction(actor=actor) as conn:
            return self._book(conn, actor, book_id)["company_id"]

    @staticmethod
    def _audit(conn, actor, book_id, action, object_id, details):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book_id,
                actor.user_id,
                action,
                object_id,
                json.dumps(details, ensure_ascii=False),
                now(),
            ),
        )

    @staticmethod
    def _account(row):
        return {
            "code": row["code"],
            "name": row["name"],
            "active": bool(row["active"]),
            "category": row["category"],
            "normal_balance": row["normal_balance"],
            "auxiliary_requirements": json.loads(row["auxiliary_requirements"]),
            "version": row["version"],
        }

    @staticmethod
    def _template(row):
        return {
            "id": row["id"],
            "version": row["current_version"],
            **json.loads(row["definition"]),
            "active": bool(row["active"]),
        }

    def get(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book = self._book(conn, actor, book_id)
            accounts = [
                self._account(row)
                for row in conn.execute(
                    "SELECT * FROM accounts WHERE book_id=? ORDER BY code", (book_id,)
                )
            ]
            templates = [self._template(row) for row in self._templates(conn, book_id)]
            return {
                "book_id": book_id,
                "company_id": book["company_id"],
                "book_name": book["name"],
                "period": book["period"],
                "can_manage": book["permissions"]["review"] and book["period_status"] == "open",
                "accounts": accounts,
                "templates": templates,
            }

    @staticmethod
    def _templates(conn, book_id, active_only=False):
        return conn.execute(
            "SELECT t.*,v.definition FROM journal_templates t JOIN journal_template_versions v "
            "ON v.template_id=t.id AND v.book_id=t.book_id AND v.version=t.current_version "
            "WHERE t.book_id=?"
            + (" AND t.active=1" if active_only else "")
            + " ORDER BY t.created_at,t.id",
            (book_id,),
        ).fetchall()

    def _check_account(self, conn, book_id, row, expected_version=None):
        existing = conn.execute(
            "SELECT * FROM accounts WHERE book_id=? AND code=?", (book_id, row.code)
        ).fetchone()
        if existing:
            if expected_version != existing["version"]:
                raise AccountingError(f"科目 {row.code} 已存在或版本已变化，请重新预览", 409)
            used = conn.execute(
                "SELECT 1 FROM draft_lines WHERE book_id=? AND account_code=? LIMIT 1",
                (book_id, row.code),
            ).fetchone()
            if (
                used
                and json.loads(existing["auxiliary_requirements"]) != row.auxiliary_requirements
            ):
                raise AccountingError(
                    f"科目 {row.code} 已用于凭证，不能更改辅助核算要求；请配置新的明细科目", 409
                )
        elif expected_version is not None:
            raise AccountingError(f"科目 {row.code} 不存在，不能按旧版本更新", 409)
        return existing

    def _save_account(self, conn, actor, book_id, row, expected_version=None):
        existing = self._check_account(conn, book_id, row, expected_version)
        fields = (
            row.name,
            row.category,
            row.normal_balance,
            int(row.active),
            json.dumps(row.auxiliary_requirements),
            book_id,
            row.code,
        )
        if existing:
            conn.execute(
                "UPDATE accounts SET name=?,category=?,normal_balance=?,active=?,auxiliary_requirements=?,version=version+1 WHERE book_id=? AND code=?",
                fields,
            )
        else:
            conn.execute(
                "INSERT INTO accounts(name,category,normal_balance,active,auxiliary_requirements,book_id,code) VALUES(?,?,?,?,?,?,?)",
                fields,
            )
        result = self._account(
            conn.execute(
                "SELECT * FROM accounts WHERE book_id=? AND code=?", (book_id, row.code)
            ).fetchone()
        )
        self._audit(
            conn,
            actor,
            book_id,
            "catalog.account.update" if existing else "catalog.account.create",
            row.code,
            {
                "version": result["version"],
                "definition": row.model_dump(exclude={"expected_version"}),
            },
        )
        return result

    def create_account(self, actor, book_id, request: AccountDefinition):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self._book(conn, actor, book_id, True)
            return self._save_account(conn, actor, book_id, request)

    def update_account(self, actor, book_id, code, request: AccountUpdate):
        if code != request.code:
            raise AccountingError("科目代码不可变更，请新建科目并停用旧科目", 409)
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self._book(conn, actor, book_id, True)
            return self._save_account(conn, actor, book_id, request, request.expected_version)

    def import_accounts(self, actor, book_id, request: AccountImportRequest, preview=False):
        with self.db.transaction(write=not preview, actor=actor) as conn:
            self._book(conn, actor, book_id, True)
            # Validate the whole command before touching any row.
            existing = [
                self._check_account(conn, book_id, row, row.expected_version)
                for row in request.rows
            ]
            if preview:
                return {
                    "valid": True,
                    "create_count": sum(row is None for row in existing),
                    "update_count": sum(row is not None for row in existing),
                    "request_fingerprint": fingerprint(request.model_dump()),
                    "rows": [row.model_dump() for row in request.rows],
                }
            accounts = [
                self._save_account(conn, actor, book_id, row, row.expected_version)
                for row in request.rows
            ]
            return {"accounts": accounts, "count": len(accounts)}

    def _validate_template(self, conn, book_id, definition):
        for line in definition.lines:
            row = conn.execute(
                "SELECT active FROM accounts WHERE book_id=? AND code=?",
                (book_id, line.account_code),
            ).fetchone()
            if not row or (definition.active and not row["active"]):
                raise AccountingError(f"模板科目 {line.account_code} 不存在或已停用")

    def save_template(self, actor, book_id, request: TemplateDefinition, template_id=None):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self._book(conn, actor, book_id, True)
            self._validate_template(conn, book_id, request)
            definition = request.model_dump(exclude={"expected_version"})
            if template_id:
                previous = conn.execute(
                    "SELECT * FROM journal_templates WHERE id=? AND book_id=?",
                    (template_id, book_id),
                ).fetchone()
                if not previous:
                    raise AccountingError("模板不存在或无权访问", 404)
                if (
                    not isinstance(request, TemplateUpdate)
                    or previous["current_version"] != request.expected_version
                ):
                    raise AccountingError("模板版本已变化，请读取最新配置", 409)
                version = previous["current_version"] + 1
                conn.execute(
                    "UPDATE journal_templates SET active=?,current_version=? WHERE id=? AND book_id=?",
                    (int(request.active), version, template_id, book_id),
                )
            else:
                template_id, version = uuid4().hex, 1
                conn.execute(
                    "INSERT INTO journal_templates(id,book_id,active,current_version,created_by,created_at) VALUES(?,?,?,?,?,?)",
                    (template_id, book_id, int(request.active), version, actor.user_id, now()),
                )
            conn.execute(
                "INSERT INTO journal_template_versions(template_id,book_id,version,definition,definition_fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    template_id,
                    book_id,
                    version,
                    json.dumps(definition, ensure_ascii=False),
                    fingerprint(definition),
                    actor.user_id,
                    now(),
                ),
            )
            self._audit(
                conn,
                actor,
                book_id,
                "catalog.template.update" if version > 1 else "catalog.template.create",
                template_id,
                {"version": version, "definition_fingerprint": fingerprint(definition)},
            )
            return {"id": template_id, "version": version, **definition}

    def preview_template(self, actor, book_id, request):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id, True)
            payload = request.payload.model_dump()
            if (
                request.template.business_type == "reimbursement"
                and payload["direction"] != "expense"
            ):
                raise AccountingError("报销模板只能处理支出")
            if not self._matches(request.template, request.template.business_type, payload):
                raise AccountingError("示例事项不满足模板匹配条件")
            recognized = {"net": request.recognized_net, "tax": request.recognized_tax}
            result = self._render(
                conn,
                actor,
                book_id,
                request.template,
                payload,
                money_to_cents(payload["amount"]),
                {"department_id": request.department_id, "applicant_id": request.applicant_id},
                recognized,
            )
            return {
                **result,
                "lines": [
                    {
                        "account_code": code,
                        "debit": cents_to_money(debit),
                        "credit": cents_to_money(credit),
                        "auxiliary_values": aux,
                    }
                    for (code, debit, credit), aux in zip(result["lines"], result["auxiliaries"])
                ],
            }

    @staticmethod
    def _matches(definition, business_type, payload):
        return (
            definition.business_type == business_type
            and definition.direction == payload.get("direction")
            and (definition.category is None or definition.category == payload.get("category"))
            and (
                definition.payment_status is None
                or definition.payment_status == payload.get("payment_status")
            )
        )

    def generate_in_transaction(
        self,
        conn,
        actor,
        book_id,
        business_type,
        payload,
        amount_cents,
        context=None,
        recognized=None,
    ):
        # Edit permission is sufficient for an authorized financial handoff;
        # only reviewers can publish the configuration this operation consumes.
        book = self.access.book(conn, actor, book_id, "edit")
        if book["period_status"] != "open":
            raise AccountingError("目标会计期间已关闭，不可生成草稿", 403)
        candidates = []
        for row in self._templates(conn, book_id, True):
            definition = TemplateDefinition.model_validate_json(row["definition"])
            if self._matches(definition, business_type, payload):
                candidates.append(
                    (
                        int(definition.category is not None)
                        + int(definition.payment_status is not None),
                        row,
                        definition,
                    )
                )
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            raise AccountingError("多个同优先级记账模板匹配该事项，请会计调整配置后再处理", 409)
        _, row, definition = candidates[0]
        result = self._render(
            conn, actor, book_id, definition, payload, amount_cents, context or {}, recognized or {}
        )
        return {
            **result,
            "template_id": row["id"],
            "template_version": row["current_version"],
            "explanation": f"企业记账模板“{definition.name}”第 {row['current_version']} 版；由确定性金额规则生成，仍须会计独立复核。",
        }

    def _render(self, conn, actor, book_id, definition, payload, amount_cents, context, recognized):
        self._validate_template(conn, book_id, definition)
        if (
            type(amount_cents) is not int
            or amount_cents <= 0
            or amount_cents != money_to_cents(payload["amount"])
        ):
            raise AccountingError("模板金额与业务事项不一致")
        book = self.access.book(conn, actor, book_id)
        if payload["occurred_on"][:7] != book["period"]:
            raise AccountingError("业务发生日期不属于目标会计期间")
        amounts = {"gross": amount_cents}
        if any(line.amount_field != "gross" for line in definition.lines):
            if recognized.get("net") is None or recognized.get("tax") is None:
                raise AccountingError("拆分税额模板要求已核对的不含税金额与税额，不能根据税率推测")
            amounts.update({key: money_to_cents(recognized[key]) for key in ("net", "tax")})
            if amounts["net"] + amounts["tax"] != amount_cents:
                raise AccountingError("不含税金额加税额与事项总额不一致")
        values = {
            "department": context.get("department_id"),
            "employee": context.get("applicant_id"),
            "counterparty": payload.get("counterparty"),
            "project": payload.get("project"),
        }
        lines, auxiliaries = [], []
        for line in definition.lines:
            amount = amounts[line.amount_field]
            if amount == 0:
                continue
            account = conn.execute(
                "SELECT auxiliary_requirements FROM accounts WHERE book_id=? AND code=?",
                (book_id, line.account_code),
            ).fetchone()
            aux = {
                key: values[key]
                for key in json.loads(account["auxiliary_requirements"])
                if values.get(key)
            }
            lines.append(
                (
                    line.account_code,
                    amount if line.side == "debit" else 0,
                    amount if line.side == "credit" else 0,
                )
            )
            auxiliaries.append(aux)
        issues = self.validate_auxiliaries_in_transaction(
            conn,
            actor,
            book_id,
            [
                {"account_code": line[0], "auxiliary_values": aux}
                for line, aux in zip(lines, auxiliaries)
            ],
        )
        return {"lines": lines, "auxiliaries": auxiliaries, "issues": issues}

    def validate_auxiliaries_in_transaction(self, conn, actor, book_id, lines):
        book = self.access.book(conn, actor, book_id)
        issues = []
        for ordinal, line in enumerate(lines, 1):
            code, values = line["account_code"], line.get("auxiliary_values", {})
            account = conn.execute(
                "SELECT auxiliary_requirements FROM accounts WHERE book_id=? AND code=?",
                (book_id, code),
            ).fetchone()
            if not account:
                issues.append(f"第 {ordinal} 行科目不存在")
                continue
            required = set(json.loads(account["auxiliary_requirements"]))
            if not isinstance(values, dict) or set(values) - DIMENSIONS:
                issues.append(f"第 {ordinal} 行辅助核算字段不合法")
                continue
            for key in sorted(required | set(values)):
                value = values.get(key)
                valid = isinstance(value, str) and bool(value.strip()) and len(value) <= 200
                if valid and self.db.dialect == "postgres":
                    valid = conn.execute(
                        "SELECT accounting.catalog_aux_valid(?,?,?)", (book_id, key, value)
                    ).fetchone()[0]
                elif valid and key == "department":
                    valid = bool(
                        conn.execute(
                            "SELECT 1 FROM departments WHERE id=? AND company_id=?",
                            (value, book["company_id"]),
                        ).fetchone()
                    )
                elif valid and key == "employee":
                    valid = self._employee_valid(conn, book, value)
                if not valid:
                    issues.append(
                        f"第 {ordinal} 行科目 {code} 的{DIMENSION_NAMES[key]}辅助值缺失或不属于本企业"
                    )
        return issues

    @staticmethod
    def _employee_valid(conn, book, employee_id):
        return bool(
            conn.execute(
                "SELECT 1 WHERE EXISTS(SELECT 1 FROM catalog_auxiliary_people WHERE book_id=? AND firm_id=? AND company_id=? AND employee_id=?) "
                "OR EXISTS(SELECT 1 FROM company_memberships WHERE company_id=? AND user_id=?) "
                "OR EXISTS(SELECT 1 FROM business_mappings m JOIN business_events e ON e.id=m.event_id AND e.firm_id=m.firm_id AND e.company_id=m.company_id "
                "WHERE m.book_id=? AND m.firm_id=? AND m.company_id=? AND e.applicant_id=?)",
                (
                    book["id"],
                    book["firm_id"],
                    book["company_id"],
                    employee_id,
                    book["company_id"],
                    employee_id,
                    book["id"],
                    book["firm_id"],
                    book["company_id"],
                    employee_id,
                ),
            ).fetchone()
        )

    def _record_employee(self, conn, actor, book, employee_id):
        if self.db.dialect == "postgres":
            recorded = conn.execute(
                "SELECT accounting.record_catalog_employee(?,?)", (book["id"], employee_id)
            ).fetchone()[0]
            if not recorded:
                raise AccountingError("人员辅助项缺少本企业当前或历史归属证明")
            return
        if not self._employee_valid(conn, book, employee_id):
            raise AccountingError("人员辅助项缺少本企业当前或历史归属证明")
        if not conn.execute(
            "SELECT 1 FROM catalog_auxiliary_people WHERE book_id=? AND employee_id=?",
            (book["id"], employee_id),
        ).fetchone():
            person = conn.execute("SELECT name FROM users WHERE id=?", (employee_id,)).fetchone()
            if not person:
                raise AccountingError("人员辅助项缺少本企业当前或历史归属证明")
            conn.execute(
                "INSERT INTO catalog_auxiliary_people(book_id,firm_id,company_id,employee_id,display_name,recorded_by,recorded_at) VALUES(?,?,?,?,?,?,?)",
                (
                    book["id"],
                    actor.firm_id,
                    book["company_id"],
                    employee_id,
                    person["name"],
                    actor.user_id,
                    now(),
                ),
            )

    @staticmethod
    def load_auxiliaries_in_transaction(conn, book_id, draft_id):
        return {
            row["ordinal"]: json.loads(row["values_json"])
            for row in conn.execute(
                "SELECT ordinal,values_json FROM draft_line_auxiliaries WHERE book_id=? AND draft_id=? ORDER BY ordinal",
                (book_id, draft_id),
            )
        }

    def save_auxiliaries_in_transaction(self, conn, actor, book_id, draft_id, auxiliaries):
        book = self.access.book(conn, actor, book_id, "edit")
        if book["period_status"] != "open":
            raise AccountingError("该会计期间已关闭，不可写入辅助核算值", 403)
        draft = conn.execute(
            "SELECT status FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)
        ).fetchone()
        if not draft or draft["status"] != "draft":
            raise AccountingError("只能编辑未审核草稿的辅助值", 409)
        lines = conn.execute(
            "SELECT ordinal FROM draft_lines WHERE draft_id=? AND book_id=? ORDER BY ordinal",
            (draft_id, book_id),
        ).fetchall()
        if len(lines) != len(auxiliaries):
            raise AccountingError("辅助值数量与分录行数不一致")
        for values in auxiliaries:
            if (
                not isinstance(values, dict)
                or set(values) - DIMENSIONS
                or any(
                    not isinstance(value, str) or not value.strip() or len(value) > 200
                    for value in values.values()
                )
            ):
                raise AccountingError("辅助核算值不合法")
            if values.get("employee"):
                self._record_employee(conn, actor, book, values["employee"])
        conn.execute(
            "DELETE FROM draft_line_auxiliaries WHERE draft_id=? AND book_id=?", (draft_id, book_id)
        )
        conn.executemany(
            "INSERT INTO draft_line_auxiliaries(draft_id,book_id,ordinal,values_json) VALUES(?,?,?,?)",
            [
                (draft_id, book_id, row["ordinal"], json.dumps(values, ensure_ascii=False))
                for row, values in zip(lines, auxiliaries)
                if values
            ],
        )
