"""Stable organization identity over immutable monthly physical book partitions.

No historical record is moved or rewritten. Permission to a parent never gives
access to an old month. Inherited rules contain formulas only, never samples.
"""

import json
from datetime import date
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.service import fingerprint, now
from ..learning.models import RuleDefinition
from ..learning.service import LearningService


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class LedgersService:
    def __init__(self, db):
        self.db, self.access, self.learning = db, AccessControl(), LearningService(db)

    @staticmethod
    def _audit(conn, actor, book_id, action, object_id, value):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
            (actor.firm_id, book_id, actor.user_id, action, object_id, encode(value), now()),
        )

    def ensure_book_in_transaction(self, conn, actor, book):
        book = self.access.book(conn, actor, book["id"], "review")
        prior = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book["id"],)
        ).fetchone()
        if prior:
            return prior["ledger_id"]
        self.access.company(conn, actor, book["company_id"], manage=True)
        ledger_id, stamp = "ledger_" + book["id"], now()
        conn.execute(
            "INSERT INTO ledgers(id,firm_id,company_id,name,created_by,created_at) VALUES(?,?,?,?,?,?)",
            (ledger_id, actor.firm_id, book["company_id"], book["name"], actor.user_id, stamp),
        )
        conn.execute(
            "INSERT INTO ledger_managers VALUES(?,?,?,?,?)",
            (ledger_id, actor.user_id, actor.user_id, "创建时明确授权管理", stamp),
        )
        conn.execute(
            "INSERT INTO ledger_periods VALUES(?,?,?,?,?,?,?)",
            (
                ledger_id,
                book["id"],
                actor.firm_id,
                book["company_id"],
                book["period"],
                actor.user_id,
                stamp,
            ),
        )
        return ledger_id

    def _ledger(self, conn, actor, book_id, manage=False):
        book = self.access.book(conn, actor, book_id, "review" if manage else "read")
        row = conn.execute(
            "SELECT l.* FROM ledgers l JOIN ledger_periods p ON p.ledger_id=l.id AND p.firm_id=l.firm_id AND p.company_id=l.company_id WHERE p.book_id=? AND l.firm_id=?",
            (book_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("账套尚未完成长期账套映射，请管理员升级初始化", 409)
        if (
            manage
            and not conn.execute(
                "SELECT 1 FROM ledger_managers WHERE ledger_id=? AND user_id=?",
                (row["id"], actor.user_id),
            ).fetchone()
        ):
            raise AccountingError("需要明确的长期账套管理授权及当前来源期间复核权限", 403)
        return book, row

    def _visible_periods(self, conn, actor, ledger_id):
        result = []
        for row in conn.execute(
            "SELECT p.*,b.name,s.status FROM ledger_periods p JOIN books b ON b.id=p.book_id JOIN periods s ON s.book_id=b.id AND s.period=b.period WHERE p.ledger_id=? ORDER BY p.period",
            (ledger_id,),
        ):
            try:
                book = self.access.book(conn, actor, row["book_id"])
                result.append(
                    {
                        "book_id": row["book_id"],
                        "period": row["period"],
                        "name": row["name"],
                        "status": row["status"],
                        "permissions": book["permissions"],
                    }
                )
            except AccountingError:
                pass
        return result

    def overview(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book, ledger = self._ledger(conn, actor, book_id)
            manager = bool(
                conn.execute(
                    "SELECT 1 FROM ledger_managers WHERE ledger_id=? AND user_id=?",
                    (ledger["id"], actor.user_id),
                ).fetchone()
            )
            members = []
            if manager and book["permissions"]["review"]:
                choices = (
                    conn.execute("SELECT * FROM accounting.ledger_member_choices(?)", (book_id,))
                    if self.db.is_postgres
                    else conn.execute(
                        "SELECT a.user_id,a.role,u.name FROM book_access a JOIN users u ON u.id=a.user_id AND u.active=1 WHERE a.book_id=? ORDER BY u.name,a.user_id",
                        (book_id,),
                    )
                )
                for row in choices:
                    if self._member_current(conn, actor, book, row):
                        members.append(dict(row))
            published = [
                {
                    "id": row["id"],
                    "version": row["current_version"],
                    "name": json.loads(row["definition_json"])["name"],
                }
                for row in conn.execute(
                    "SELECT r.id,r.current_version,v.definition_json FROM learning_rules r JOIN learning_rule_versions v ON v.rule_id=r.id AND v.version=r.current_version WHERE r.book_id=? AND r.status='published' ORDER BY r.id LIMIT 100",
                    (book_id,),
                )
            ]
            candidates = [
                dict(row) | {"definition": json.loads(row["definition_json"])}
                for row in conn.execute(
                    "SELECT * FROM ledger_rule_candidates WHERE target_book_id=? ORDER BY created_at,id LIMIT 100",
                    (book_id,),
                )
            ]
            entities = [
                dict(row) | {"active": bool(row["active"])}
                for row in conn.execute(
                    "SELECT id,kind,code,name,active,version FROM company_master_entities WHERE firm_id=? AND company_id=? ORDER BY kind,code LIMIT 500",
                    (actor.firm_id, book["company_id"]),
                )
            ]
            aliases = [
                dict(row)
                for row in conn.execute(
                    "SELECT id,kind,entity_id,original,status,version,created_by,reviewed_by,reason,review_reason FROM company_master_aliases WHERE firm_id=? AND company_id=? ORDER BY created_at DESC,id LIMIT 500",
                    (actor.firm_id, book["company_id"]),
                )
            ]
            return {
                "id": ledger["id"],
                "name": ledger["name"],
                "company_id": book["company_id"],
                "book_id": book_id,
                "period": book["period"],
                "can_manage": manager and book["permissions"]["review"],
                "can_grant_manager": book["permissions"]["manage"]
                and book["permissions"]["review"],
                "can_review": book["permissions"]["review"],
                "periods": self._visible_periods(conn, actor, ledger["id"]),
                "members": members,
                "published_rules": published,
                "candidates": candidates,
                "entities": entities,
                "aliases": aliases,
            }

    def grant_manager(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger = self._ledger(conn, actor, book_id)
            self.access.book(conn, actor, book_id, "review")
            self.access.company(conn, actor, book["company_id"], manage=True)
            self.access.require_user(conn, request.user_id)
            firm = conn.execute(
                "SELECT role FROM memberships WHERE firm_id=? AND user_id=?",
                (actor.firm_id, request.user_id),
            ).fetchone()
            company = conn.execute(
                "SELECT role FROM company_memberships WHERE company_id=? AND user_id=?",
                (book["company_id"], request.user_id),
            ).fetchone()
            if not (
                (firm and firm["role"] in {"owner", "admin", "reviewer"})
                or (not firm and company and company["role"] == "finance")
            ):
                raise AccountingError(
                    "长期账套管理人须为当前事务所管理员、复核人员或本企业财务", 403
                )
            conn.execute(
                "INSERT OR IGNORE INTO ledger_managers VALUES(?,?,?,?,?)",
                (ledger["id"], request.user_id, actor.user_id, request.reason, now()),
            )
            self._audit(
                conn, actor, book_id, "ledger.manager.grant", ledger["id"], request.model_dump()
            )
            return {"ledger_id": ledger["id"], "user_id": request.user_id}

    def _member_current(self, conn, actor, book, row):
        if self.db.is_postgres:
            return bool(
                conn.execute(
                    "SELECT accounting.ledger_member_current(?,?,?)",
                    (book["id"], row["user_id"], row["role"]),
                ).fetchone()[0]
            )
        active = conn.execute(
            "SELECT 1 FROM users WHERE id=? AND active=1", (row["user_id"],)
        ).fetchone()
        firm = conn.execute(
            "SELECT role FROM memberships WHERE firm_id=? AND user_id=?",
            (actor.firm_id, row["user_id"]),
        ).fetchone()
        company = conn.execute(
            "SELECT role FROM company_memberships WHERE company_id=? AND user_id=?",
            (book["company_id"], row["user_id"]),
        ).fetchone()
        return bool(
            active
            and self.access.permissions(
                firm["role"] if firm else None, row["role"], company["role"] if company else None
            )["read"]
        )

    def create_period(self, actor, book_id, request, *, before_create=None):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger = self._ledger(conn, actor, book_id, True)
            if before_create is not None:
                before_create(conn)
            source = self.access.book(conn, actor, request.source_book_id, "review")
            mapping = conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (source["id"],)
            ).fetchone()
            if not mapping or mapping["ledger_id"] != ledger["id"]:
                raise AccountingError("继承来源必须是同一长期账套的明确期间", 409)
            digest = fingerprint(request.model_dump())
            prior = conn.execute(
                "SELECT * FROM ledger_commands WHERE ledger_id=? AND request_key=?",
                (ledger["id"], request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != digest:
                    raise AccountingError("创建期间幂等键已用于不同参数", 409)
                self.access.book(conn, actor, prior["book_id"], "read")
                return {"ledger_id": ledger["id"], "book_id": prior["book_id"], "replayed": True}
            if conn.execute(
                "SELECT 1 FROM ledger_periods WHERE ledger_id=? AND period=?",
                (ledger["id"], request.period),
            ).fetchone():
                raise AccountingError("长期账套已存在该期间，不能重复创建", 409)
            inherited = []
            native_choices = (
                {
                    row["user_id"]: row
                    for row in conn.execute(
                        "SELECT * FROM accounting.ledger_member_choices(?)", (source["id"],)
                    )
                }
                if self.db.is_postgres
                else None
            )
            for user_id in request.inherit_user_ids:
                grant = (
                    native_choices.get(user_id)
                    if native_choices is not None
                    else conn.execute(
                        "SELECT user_id,role FROM book_access WHERE book_id=? AND user_id=?",
                        (source["id"], user_id),
                    ).fetchone()
                )
                if not grant or not self._member_current(conn, actor, source, grant):
                    raise AccountingError(
                        "选定人员已停用或不再具有来源账套权限，请刷新后重新选择", 403
                    )
                inherited.append(dict(grant))
            rules = []
            for rule_id in request.inherit_rule_ids:
                row = self.learning._rule(conn, actor, source["id"], rule_id)
                if row["status"] != "published":
                    raise AccountingError("只能继承当前已发布的企业规则定义", 409)
                rules.append(row)
            target, stamp = uuid4().hex, now()
            if self.db.is_postgres:
                conn.execute(
                    "SELECT accounting.ledger_create_period(?,?,?,?,?,?::jsonb)",
                    (
                        ledger["id"],
                        target,
                        source["id"],
                        request.period,
                        request.name,
                        encode(request.inherit_user_ids),
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO books(id,firm_id,company_id,name,period) VALUES(?,?,?,?,?)",
                    (target, actor.firm_id, book["company_id"], request.name, request.period),
                )
                conn.execute(
                    "INSERT INTO book_access VALUES(?,?,?,'reviewer')",
                    (target, actor.firm_id, actor.user_id),
                )
                for member in inherited:
                    if member["user_id"] != actor.user_id:
                        conn.execute(
                            "INSERT INTO book_access VALUES(?,?,?,?)",
                            (target, actor.firm_id, member["user_id"], member["role"]),
                        )
                conn.execute("INSERT INTO periods VALUES(?,?,'open')", (target, request.period))
                conn.execute(
                    "INSERT INTO ledger_periods VALUES(?,?,?,?,?,?,?)",
                    (
                        ledger["id"],
                        target,
                        actor.firm_id,
                        book["company_id"],
                        request.period,
                        actor.user_id,
                        stamp,
                    ),
                )
            conn.execute(
                "INSERT INTO accounts(book_id,code,name,active,category,normal_balance,auxiliary_requirements,version) SELECT ?,code,name,active,category,normal_balance,auxiliary_requirements,1 FROM accounts WHERE book_id=?",
                (target, source["id"]),
            )
            for row in conn.execute(
                "SELECT t.*,v.definition,v.definition_fingerprint FROM journal_templates t JOIN journal_template_versions v ON v.template_id=t.id AND v.version=t.current_version WHERE t.book_id=? ORDER BY t.id",
                (source["id"],),
            ).fetchall():
                template_id = uuid4().hex
                conn.execute(
                    "INSERT INTO journal_templates VALUES(?,?,?,1,?,?)",
                    (template_id, target, row["active"], actor.user_id, stamp),
                )
                conn.execute(
                    "INSERT INTO journal_template_versions VALUES(?,?,1,?,?,?,?)",
                    (
                        template_id,
                        target,
                        row["definition"],
                        row["definition_fingerprint"],
                        actor.user_id,
                        stamp,
                    ),
                )
            for rule in rules:
                conn.execute(
                    "INSERT INTO ledger_rule_candidates(id,ledger_id,source_book_id,target_book_id,source_rule_id,source_rule_version,definition_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        ledger["id"],
                        source["id"],
                        target,
                        rule["id"],
                        rule["current_version"],
                        rule["definition_json"],
                        actor.user_id,
                        stamp,
                    ),
                )
            conn.execute(
                "INSERT INTO ledger_commands VALUES(?,?,?,?,?,?)",
                (ledger["id"], request.idempotency_key, digest, target, actor.user_id, stamp),
            )
            self._audit(
                conn,
                actor,
                target,
                "ledger.period.create",
                target,
                {
                    "ledger_id": ledger["id"],
                    "source_book_id": source["id"],
                    "inherit_user_ids": request.inherit_user_ids,
                    "inherit_rule_ids": request.inherit_rule_ids,
                    "catalog_copied": True,
                    "rules_published": False,
                },
            )
            return {"ledger_id": ledger["id"], "book_id": target, "replayed": False}

    def link_period(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger = self._ledger(conn, actor, book_id, True)
            source = self.access.book(conn, actor, request.source_book_id, "review")
            self.access.company(conn, actor, source["company_id"], manage=True)
            if source["firm_id"] != actor.firm_id or source["company_id"] != book["company_id"]:
                raise AccountingError("不得关联其他企业或事务所的历史账套", 404)
            mapping = conn.execute(
                "SELECT * FROM ledger_periods WHERE book_id=?", (source["id"],)
            ).fetchone()
            if not mapping or mapping["ledger_id"] != request.expected_ledger_id:
                raise AccountingError("历史账套映射已变化，请刷新后核对", 409)
            if conn.execute(
                "SELECT 1 FROM internal_period_locks WHERE book_id=?", (source["id"],)
            ).fetchone():
                raise AccountingError("已内部锁期的账套不能修改长期账套归属", 409)
            if mapping["ledger_id"] != ledger["id"] and (
                conn.execute(
                    "SELECT 1 FROM opening_bindings WHERE book_id=?", (source["id"],)
                ).fetchone()
                or conn.execute(
                    "SELECT 1 FROM vouchers WHERE book_id=?", (source["id"],)
                ).fetchone()
            ):
                raise AccountingError("已绑定正式期初或已有正式凭证的账套不能改绑长期账套", 409)
            if conn.execute(
                "SELECT 1 FROM ledger_periods WHERE ledger_id=? AND period=? AND book_id<>?",
                (ledger["id"], source["period"], source["id"]),
            ).fetchone():
                raise AccountingError("目标长期账套已存在同月期间，不能自动合并", 409)
            conn.execute(
                "UPDATE ledger_periods SET ledger_id=?,linked_by=?,linked_at=? WHERE book_id=? AND ledger_id=?",
                (ledger["id"], actor.user_id, now(), source["id"], request.expected_ledger_id),
            )
            self._audit(
                conn,
                actor,
                source["id"],
                "ledger.period.link",
                source["id"],
                {
                    "from": request.expected_ledger_id,
                    "to": ledger["id"],
                    "reason": request.reason,
                    "physical_book_unchanged": True,
                },
            )
            return {"ledger_id": ledger["id"], "book_id": source["id"]}

    def activate_candidate(self, actor, book_id, candidate_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.learning._book(conn, actor, book_id, True)
            row = conn.execute(
                "SELECT * FROM ledger_rule_candidates WHERE id=? AND target_book_id=?",
                (candidate_id, book_id),
            ).fetchone()
            if not row:
                raise AccountingError("继承候选不存在或无权访问", 404)
            if row["status"] != "pending":
                raise AccountingError("此候选已形成待发布规则，请在审核样例与规则中继续", 409)
            definition = RuleDefinition.model_validate_json(row["definition_json"])
            rule = self.learning._save_in_transaction(
                conn, actor, book, definition, request.sample_ids, request.reason, None
            )
            stored = self.learning._rule(conn, actor, book_id, rule["id"], True)
            report = self.learning._evaluation(
                conn, actor, book_id, stored, request.negative_sample_ids
            )
            if not report["passed"]:
                raise AccountingError("新期间正反例评估未通过，继承候选未启用", 409)
            evaluation_id = uuid4().hex
            conn.execute(
                "INSERT INTO learning_rule_evaluations(id,rule_id,book_id,rule_version,negative_sample_ids_json,input_fingerprint,report_json,created_by,created_at) VALUES(?,?,?,1,?,?,?,?,?)",
                (
                    evaluation_id,
                    rule["id"],
                    book_id,
                    encode(request.negative_sample_ids),
                    report["input_fingerprint"],
                    encode(report),
                    actor.user_id,
                    now(),
                ),
            )
            conn.execute(
                "UPDATE ledger_rule_candidates SET status='evaluated',target_rule_id=? WHERE id=? AND status='pending'",
                (rule["id"], candidate_id),
            )
            self._audit(
                conn,
                actor,
                book_id,
                "ledger.rule.inheritance.evaluate",
                candidate_id,
                {"rule_id": rule["id"], "evaluation_id": evaluation_id, "published": False},
            )
            return {
                "rule": rule,
                "evaluation_id": evaluation_id,
                "report": report,
                "published": False,
            }

    def _history(self, conn, actor, book, object_id, action, value):
        conn.execute(
            "INSERT INTO company_master_history VALUES(?,?,?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                actor.firm_id,
                book["company_id"],
                book["id"],
                object_id,
                action,
                encode(value),
                actor.user_id,
                now(),
            ),
        )
        self._audit(conn, actor, book["id"], action, object_id, value)

    def create_entity(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            if conn.execute(
                "SELECT 1 FROM company_master_entities WHERE firm_id=? AND company_id=? AND kind=? AND code=?",
                (actor.firm_id, book["company_id"], request.kind, request.code),
            ).fetchone():
                raise AccountingError("本企业该类型主数据编码已存在", 409)
            entity_id = uuid4().hex
            conn.execute(
                "INSERT INTO company_master_entities(id,firm_id,company_id,kind,code,name,recorded_book_id,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    entity_id,
                    actor.firm_id,
                    book["company_id"],
                    request.kind,
                    request.code,
                    request.name,
                    book_id,
                    actor.user_id,
                    now(),
                ),
            )
            self._history(
                conn, actor, book, entity_id, "master.entity.create", request.model_dump()
            )
            return {"id": entity_id, "version": 1, **request.model_dump()}

    def update_entity(self, actor, book_id, entity_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            row = conn.execute(
                "SELECT * FROM company_master_entities WHERE id=? AND firm_id=? AND company_id=?",
                (entity_id, actor.firm_id, book["company_id"]),
            ).fetchone()
            if not row:
                raise AccountingError("主数据不存在或无权访问", 404)
            self.access.book(conn, actor, row["recorded_book_id"], "review")
            if row["version"] != request.expected_version:
                raise AccountingError("主数据版本已变化", 409)
            conn.execute(
                "UPDATE company_master_entities SET name=?,active=?,version=version+1 WHERE id=? AND version=?",
                (request.name, int(request.active), entity_id, request.expected_version),
            )
            self._history(
                conn,
                actor,
                book,
                entity_id,
                "master.entity.update",
                {"before": dict(row), "after": request.model_dump()},
            )
            return {"id": entity_id, "version": row["version"] + 1}

    def create_alias(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            entity = conn.execute(
                "SELECT * FROM company_master_entities WHERE id=? AND firm_id=? AND company_id=? AND active=1",
                (request.entity_id, actor.firm_id, book["company_id"]),
            ).fetchone()
            if not entity:
                raise AccountingError("主数据不存在或已停用", 404)
            if conn.execute(
                "SELECT 1 FROM company_master_aliases WHERE firm_id=? AND company_id=? AND kind=? AND original=? AND status IN ('pending','approved')",
                (actor.firm_id, book["company_id"], entity["kind"], request.original),
            ).fetchone():
                raise AccountingError("该原始名称已有待审或已批准映射，请先处置原版本", 409)
            alias_id = uuid4().hex
            conn.execute(
                "INSERT INTO company_master_aliases(id,firm_id,company_id,kind,entity_id,original,reason,recorded_book_id,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    alias_id,
                    actor.firm_id,
                    book["company_id"],
                    entity["kind"],
                    entity["id"],
                    request.original,
                    request.reason,
                    book_id,
                    actor.user_id,
                    now(),
                ),
            )
            self._history(conn, actor, book, alias_id, "master.alias.request", request.model_dump())
            return {"id": alias_id, "version": 1, "status": "pending"}

    def decide_alias(self, actor, book_id, alias_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            row = conn.execute(
                "SELECT * FROM company_master_aliases WHERE id=? AND firm_id=? AND company_id=?",
                (alias_id, actor.firm_id, book["company_id"]),
            ).fetchone()
            if not row:
                raise AccountingError("别名映射不存在或无权访问", 404)
            self.access.book(conn, actor, row["recorded_book_id"], "review")
            if row["created_by"] == actor.user_id:
                raise AccountingError("别名映射须由另一名会计独立复核", 403)
            if (
                row["version"] != request.expected_version
                or (request.decision in {"approve", "reject"} and row["status"] != "pending")
                or (request.decision == "retire" and row["status"] != "approved")
            ):
                raise AccountingError("别名版本或审核状态已变化", 409)
            status = {"approve": "approved", "reject": "rejected", "retire": "retired"}[
                request.decision
            ]
            if (
                status == "approved"
                and conn.execute(
                    "SELECT 1 FROM company_master_aliases WHERE firm_id=? AND company_id=? AND kind=? AND original=? AND status='approved'",
                    (actor.firm_id, book["company_id"], row["kind"], row["original"]),
                ).fetchone()
            ):
                raise AccountingError("同一原始名称已有生效映射，不能覆盖", 409)
            conn.execute(
                "UPDATE company_master_aliases SET status=?,version=version+1,reviewed_by=?,review_reason=?,reviewed_at=? WHERE id=? AND version=?",
                (status, actor.user_id, request.reason, now(), alias_id, request.expected_version),
            )
            self._history(
                conn,
                actor,
                book,
                alias_id,
                "master.alias." + request.decision,
                {"before": dict(row), "decision": request.model_dump()},
            )
            return {"id": alias_id, "version": row["version"] + 1, "status": status}

    @staticmethod
    def _transport_range(valid_from, valid_to):
        try:
            start = date.fromisoformat(valid_from)
            end = date.fromisoformat(valid_to) if valid_to else None
        except (TypeError, ValueError):
            raise AccountingError("有效期格式应为 YYYY-MM-DD", 422)
        if end is not None and end < start:
            raise AccountingError("有效期结束不能早于开始", 422)
        return start.isoformat(), end.isoformat() if end else None

    @staticmethod
    def _transport_overlap(conn, table, subject_column, subject_id, start, end, extra="", params=()):
        clause = f"{subject_column}=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>=?)"
        args = [subject_id, end or "9999-12-31", start]
        if extra:
            clause += " AND " + extra
            args.extend(params)
        return conn.execute(f"SELECT * FROM {table} WHERE {clause} ORDER BY valid_from", args).fetchone()

    def create_transport_subject(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            self.access.company(conn, actor, book["company_id"], manage=True)
            if conn.execute(
                "SELECT 1 FROM transport_subjects WHERE firm_id=? AND company_id=? AND kind=? AND external_id=?",
                (actor.firm_id, book["company_id"], request.kind, request.external_id),
            ).fetchone():
                raise AccountingError("该稳定外部 ID 已存在，不能按名称合并", 409)
            subject_id = uuid4().hex
            conn.execute(
                "INSERT INTO transport_subjects(id,firm_id,company_id,kind,external_id,display_name,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (subject_id, actor.firm_id, book["company_id"], request.kind, request.external_id, request.display_name, actor.user_id, now()),
            )
            self._history(conn, actor, book, subject_id, "transport.subject.create", request.model_dump())
            return {"id": subject_id, "version": 1, **request.model_dump()}

    def update_transport_subject(self, actor, book_id, subject_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            row = conn.execute(
                "SELECT * FROM transport_subjects WHERE id=? AND firm_id=? AND company_id=?",
                (subject_id, actor.firm_id, book["company_id"]),
            ).fetchone()
            if not row:
                raise AccountingError("运输主数据不存在或无权访问", 404)
            if row["version"] != request.expected_version:
                raise AccountingError("运输主数据版本已变化", 409)
            conn.execute(
                "UPDATE transport_subjects SET display_name=?,active=?,version=version+1 WHERE id=? AND version=?",
                (request.display_name, int(request.active), subject_id, request.expected_version),
            )
            self._history(conn, actor, book, subject_id, "transport.subject.update", {"before": dict(row), "after": request.model_dump()})
            return {"id": subject_id, "version": row["version"] + 1}

    def transport_overview(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            company_id = book["company_id"]
            subjects = [dict(row) for row in conn.execute(
                "SELECT id,kind,external_id,display_name,active,version FROM transport_subjects WHERE firm_id=? AND company_id=? ORDER BY kind,external_id",
                (actor.firm_id, company_id),
            ).fetchall()]
            relations = [dict(row) for row in conn.execute(
                "SELECT r.id,r.subject_id,r.subject_kind,r.target_kind,r.target_id,r.contract_external_id,r.operating_right,r.valid_from,r.valid_to,r.status,t.name AS target_name FROM transport_relations r LEFT JOIN company_master_entities t ON t.id=r.target_id WHERE r.firm_id=? AND r.company_id=? ORDER BY r.valid_from DESC",
                (actor.firm_id, company_id),
            ).fetchall()]
            assignments = [dict(row) for row in conn.execute(
                "SELECT id,vehicle_id,driver_id,shift,contract_external_id,valid_from,valid_to FROM transport_assignments WHERE firm_id=? AND company_id=? ORDER BY valid_from DESC",
                (actor.firm_id, company_id),
            ).fetchall()]
            aliases = [dict(row) for row in conn.execute(
                "SELECT id,subject_id,alias,source,status FROM transport_aliases WHERE firm_id=? AND company_id=? ORDER BY alias",
                (actor.firm_id, company_id),
            ).fetchall()]
            return {"subjects": subjects, "relations": relations, "assignments": assignments, "aliases": aliases, "can_manage": True}

    def add_transport_alias(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            self.access.company(conn, actor, book["company_id"], manage=True)
            subject = conn.execute(
                "SELECT * FROM transport_subjects WHERE id=? AND firm_id=? AND company_id=?",
                (request.subject_id, actor.firm_id, book["company_id"]),
            ).fetchone()
            if not subject:
                raise AccountingError("运输主数据不存在或无权访问", 404)
            claimed = conn.execute(
                "SELECT subject_id FROM transport_aliases WHERE firm_id=? AND company_id=? AND alias=? AND status='approved'",
                (actor.firm_id, book["company_id"], request.alias),
            ).fetchone()
            if claimed:
                if claimed["subject_id"] == request.subject_id:
                    raise AccountingError("该历史别名已绑定", 409)
                raise AccountingError("该历史别名已属于其他运输主体", 409)
            alias_id = uuid4().hex
            conn.execute(
                "INSERT INTO transport_aliases(id,firm_id,company_id,subject_id,alias,source,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (alias_id, actor.firm_id, book["company_id"], request.subject_id, request.alias, request.source, actor.user_id, now()),
            )
            self._history(conn, actor, book, alias_id, "transport.alias.create", request.model_dump())
            return {"id": alias_id, "status": "approved"}

    def create_transport_relation(self, actor, book_id, request):
        start, end = self._transport_range(request.valid_from, request.valid_to)
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            self.access.company(conn, actor, book["company_id"], manage=True)
            subject = conn.execute(
                "SELECT * FROM transport_subjects WHERE id=? AND firm_id=? AND company_id=?",
                (request.subject_id, actor.firm_id, book["company_id"]),
            ).fetchone()
            target_kind = "project" if subject and subject["kind"] == "vehicle" else "counterparty"
            target = conn.execute(
                "SELECT * FROM company_master_entities WHERE id=? AND firm_id=? AND company_id=? AND kind=?",
                (request.target_id, actor.firm_id, book["company_id"], target_kind),
            ).fetchone()
            if not subject or not target:
                raise AccountingError("车辆/驾驶员或对应项目/往来主体不存在", 404)
            if not subject["active"] or not target["active"]:
                raise AccountingError("车辆/驾驶员或对应项目/往来主体已停用，不能新增有效关系", 409)
            existing = self._transport_overlap(conn, "transport_relations", "subject_id", subject["id"], start, end)
            if existing:
                raise AccountingError("同一运输主体的有效关系重叠，请先结束旧关系", 409)
            relation_id = uuid4().hex
            conn.execute(
                "INSERT INTO transport_relations(id,firm_id,company_id,subject_id,subject_kind,target_kind,target_id,contract_external_id,operating_right,valid_from,valid_to,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (relation_id, actor.firm_id, book["company_id"], subject["id"], subject["kind"], target_kind, target["id"], request.contract_external_id, request.operating_right, start, end, actor.user_id, now()),
            )
            self._history(conn, actor, book, relation_id, "transport.relation.create", request.model_dump())
            return {"id": relation_id, "valid_from": start, "valid_to": end, "target_kind": target_kind}

    def create_transport_assignment(self, actor, book_id, request):
        start, end = self._transport_range(request.valid_from, request.valid_to)
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            self.access.company(conn, actor, book["company_id"], manage=True)
            vehicle = conn.execute("SELECT * FROM transport_subjects WHERE id=? AND firm_id=? AND company_id=? AND kind='vehicle'", (request.vehicle_id, actor.firm_id, book["company_id"])).fetchone()
            driver = conn.execute("SELECT * FROM transport_subjects WHERE id=? AND firm_id=? AND company_id=? AND kind='driver'", (request.driver_id, actor.firm_id, book["company_id"])).fetchone()
            if not vehicle or not driver:
                raise AccountingError("车辆或驾驶员不存在", 404)
            if not vehicle["active"] or not driver["active"]:
                raise AccountingError("车辆或驾驶员已停用，不能安排任务", 409)
            if self._transport_overlap(conn, "transport_assignments", "driver_id", driver["id"], start, end):
                raise AccountingError("驾驶员在有效期内已有车辆任务，不能同时更换车辆", 409)
            if request.shift in {"full", "day"} and self._transport_overlap(conn, "transport_assignments", "vehicle_id", vehicle["id"], start, end, "shift IN ('full','day')"):
                raise AccountingError("车辆白班已安排", 409)
            if request.shift in {"full", "night"} and self._transport_overlap(conn, "transport_assignments", "vehicle_id", vehicle["id"], start, end, "shift IN ('full','night')"):
                raise AccountingError("车辆夜班已安排", 409)
            assignment_id = uuid4().hex
            conn.execute(
                "INSERT INTO transport_assignments(id,firm_id,company_id,vehicle_id,driver_id,shift,contract_external_id,valid_from,valid_to,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (assignment_id, actor.firm_id, book["company_id"], vehicle["id"], driver["id"], request.shift, request.contract_external_id, start, end, actor.user_id, now()),
            )
            self._history(conn, actor, book, assignment_id, "transport.assignment.create", request.model_dump())
            return {"id": assignment_id, "valid_from": start, "valid_to": end}

    def _transport_subject(self, conn, actor, company_id, kind, external_id, display_name):
        if not external_id and not display_name:
            raise AccountingError("必须提供稳定外部 ID 或历史名称", 422)
        if external_id:
            row = conn.execute("SELECT * FROM transport_subjects WHERE firm_id=? AND company_id=? AND kind=? AND external_id=?", (actor.firm_id, company_id, kind, external_id)).fetchone()
        else:
            rows = conn.execute("SELECT * FROM transport_subjects WHERE firm_id=? AND company_id=? AND kind=? AND display_name=?", (actor.firm_id, company_id, kind, display_name)).fetchall()
            if not rows:
                rows = conn.execute("SELECT s.* FROM transport_subjects s JOIN transport_aliases a ON a.subject_id=s.id AND a.firm_id=s.firm_id AND a.company_id=s.company_id WHERE a.firm_id=? AND a.company_id=? AND s.kind=? AND a.alias=?", (actor.firm_id, company_id, kind, display_name)).fetchall()
            if len(rows) != 1:
                raise AccountingError("历史名称无法唯一匹配，请提供稳定外部 ID", 409 if rows else 422)
            row = rows[0]
        if not row:
            raise AccountingError("运输主数据不存在", 422)
        return row

    def resolve_transport(self, actor, book_id, occurred_on, vehicle_external_id=None, vehicle_name=None, driver_external_id=None, driver_name=None):
        try:
            day = date.fromisoformat(occurred_on)
        except (TypeError, ValueError):
            raise AccountingError("发生日期格式应为 YYYY-MM-DD", 422)
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            company_id = book["company_id"]
            vehicle = self._transport_subject(conn, actor, company_id, "vehicle", vehicle_external_id, vehicle_name)
            driver = self._transport_subject(conn, actor, company_id, "driver", driver_external_id, driver_name) if (driver_external_id or driver_name) else None
            rel_v = conn.execute("SELECT * FROM transport_relations WHERE firm_id=? AND company_id=? AND subject_id=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>=?) AND status='active' ORDER BY valid_from DESC", (actor.firm_id, company_id, vehicle["id"], day.isoformat(), day.isoformat())).fetchone()
            project = conn.execute("SELECT * FROM company_master_entities WHERE id=?", (rel_v["target_id"],)).fetchone() if rel_v else None
            counterparty = None
            rel_d = None
            if driver:
                rel_d = conn.execute("SELECT * FROM transport_relations WHERE firm_id=? AND company_id=? AND subject_id=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>=?) AND status='active' ORDER BY valid_from DESC", (actor.firm_id, company_id, driver["id"], day.isoformat(), day.isoformat())).fetchone()
                counterparty = conn.execute("SELECT * FROM company_master_entities WHERE id=?", (rel_d["target_id"],)).fetchone() if rel_d else None
            assignment = None
            if driver:
                assignments = conn.execute("SELECT * FROM transport_assignments WHERE firm_id=? AND company_id=? AND vehicle_id=? AND driver_id=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>=?) ORDER BY valid_from DESC", (actor.firm_id, company_id, vehicle["id"], driver["id"], day.isoformat(), day.isoformat())).fetchall()
                if len(assignments) > 1:
                    raise AccountingError("车辆与驾驶员在该日存在多个班次，请补充班次", 409)
                assignment = assignments[0] if assignments else None
            return {
                "occurred_on": day.isoformat(),
                "vehicle": dict(vehicle),
                "project": dict(project) if project else None,
                "driver": dict(driver) if driver else None,
                "counterparty": dict(counterparty) if counterparty else None,
                "assignment": dict(assignment) if assignment else None,
                "coverage": {"vehicle_relation": bool(project), "driver_relation": bool(counterparty) if driver else None, "assignment": bool(assignment) if driver else None},
            }

    def resolve_in_transaction(self, conn, actor, company_id, kind, original, book_id=None):
        if (
            kind not in {"counterparty", "project", "bank_account"}
            or not isinstance(original, str)
            or not 1 <= len(original) <= 200
        ):
            return None
        self.access.company(conn, actor, company_id)
        if book_id:
            book = self.access.book(conn, actor, book_id)
            if book["company_id"] != company_id:
                raise AccountingError("主数据与账套企业不一致", 404)
        elif kind == "bank_account":
            raise AccountingError("银行账户映射需要明确账套授权", 403)
        if self.db.is_postgres:
            row = conn.execute(
                "SELECT * FROM accounting.master_resolve(?,?,?)", (company_id, kind, original)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT e.id AS entity_id,e.name AS canonical_value,e.version AS entity_version,a.version AS alias_version FROM company_master_aliases a JOIN company_master_entities e ON e.id=a.entity_id WHERE a.firm_id=? AND a.company_id=? AND a.kind=? AND a.original=? AND a.status='approved' AND e.active=1",
                (actor.firm_id, company_id, kind, original),
            ).fetchone()
            if not row:
                rows = conn.execute(
                    "SELECT id AS entity_id,name AS canonical_value,version AS entity_version,0 AS alias_version FROM company_master_entities WHERE firm_id=? AND company_id=? AND kind=? AND name=? AND active=1",
                    (actor.firm_id, company_id, kind, original),
                ).fetchall()
                row = rows[0] if len(rows) == 1 else None
        return (
            None
            if not row
            else dict(row)
            | {"original": original, "version": f"{row['entity_version']}:{row['alias_version']}"}
        )

    def policy_fingerprint_in_transaction(self, conn, actor, book_id):
        book = self.access.book(conn, actor, book_id)
        entities = [
            dict(row)
            for row in conn.execute(
                "SELECT id,kind,code,name,active,version FROM company_master_entities WHERE firm_id=? AND company_id=? ORDER BY id",
                (actor.firm_id, book["company_id"]),
            )
        ]
        aliases = [
            dict(row)
            for row in conn.execute(
                "SELECT id,entity_id,kind,original,status,version FROM company_master_aliases WHERE firm_id=? AND company_id=? ORDER BY id",
                (actor.firm_id, book["company_id"]),
            )
        ]
        return fingerprint({"entities": entities, "aliases": aliases})

    def context_in_transaction(self, conn, actor, company_id, payload, book_id=None):
        result = {}
        for kind in ("counterparty", "project"):
            value = self.resolve_in_transaction(
                conn, actor, company_id, kind, payload.get(kind, ""), book_id
            )
            if value is not None:
                result[kind] = value
        return result
