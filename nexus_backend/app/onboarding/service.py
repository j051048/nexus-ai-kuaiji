"""Readiness, resumable onboarding runs and governed template copying."""

import json
from datetime import date
from uuid import uuid4

from ..accounting.catalog import CatalogService
from ..accounting.catalog_models import TemplateDefinition
from ..accounting.domain import AccountingError
from ..accounting.service import now
from ..ledgers.models import EntityCreate
from ..service_plans.models import PlanSave, TemplateCreate


class OnboardingService:
    def __init__(self, db, admin, accounting, ledgers, plans, catalog=None):
        self.db = db
        self.admin = admin
        self.accounting = accounting
        self.ledgers = ledgers
        self.plans = plans
        self.catalog = catalog or CatalogService(db)

    def _book(self, conn, actor, company_id, period):
        self.admin.access.company(conn, actor, company_id, manage=True)
        return conn.execute(
            "SELECT b.*,p.status AS period_status FROM books b LEFT JOIN periods p "
            "ON p.book_id=b.id WHERE b.firm_id=? AND b.company_id=? AND b.period=? "
            "ORDER BY b.id DESC LIMIT 1",
            (actor.firm_id, company_id, period),
        ).fetchone()

    def _readiness(self, actor, company_id, period, source_company_id=""):
        checklist = self.admin.onboarding(actor, company_id, period)
        with self.db.transaction(actor=actor) as conn:
            self.admin.access.require_manager(conn, actor)
            target = self._book(conn, actor, company_id, period)
            source = (
                self._book(conn, actor, source_company_id, period)
                if source_company_id and source_company_id != company_id
                else None
            )
            company = conn.execute(
                "SELECT id,name,tax_id FROM companies WHERE id=?", (company_id,)
            ).fetchone()
            source_ledger = (
                conn.execute(
                    "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (source["id"],)
                ).fetchone()
                if source
                else None
            )
            target_ledger = (
                conn.execute(
                    "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (target["id"],)
                ).fetchone()
                if target
                else None
            )
            plan = (
                conn.execute(
                    "SELECT * FROM service_plan_versions WHERE book_id=? "
                    "ORDER BY version DESC LIMIT 1",
                    (source["id"],),
                ).fetchone()
                if source
                else None
            )
            target_plan = (
                conn.execute(
                    "SELECT 1 FROM service_plan_versions WHERE book_id=? LIMIT 1",
                    (target["id"],),
                ).fetchone()
                if target
                else None
            )
            material_count = (
                conn.execute(
                    "SELECT COUNT(*) FROM material_templates WHERE ledger_id=?",
                    (source_ledger["ledger_id"],),
                ).fetchone()[0]
                if source_ledger
                else 0
            )
            bank_count = (
                conn.execute(
                    "SELECT COUNT(*) FROM company_master_entities WHERE firm_id=? "
                    "AND company_id=? AND kind='bank_account' AND active=1",
                    (actor.firm_id, source_company_id),
                ).fetchone()[0]
                if source
                else 0
            )
            opening = (
                conn.execute(
                    "SELECT 1 FROM monthly_baselines b JOIN monthly_baseline_confirmations c "
                    "ON c.baseline_id=b.id WHERE b.book_id=? AND b.kind='opening' LIMIT 1",
                    (target["id"],),
                ).fetchone()
                if target
                else None
            )
        required = [step for step in checklist["steps"] if step["required"]]
        missing = [step["id"] for step in required if not step["complete"]]
        blockers = []
        if not company:
            blockers.append("目标企业不存在")
        if company and not company["tax_id"]:
            blockers.append("尚未填写企业税号")
        if not target:
            blockers.append("当前期间尚未建立账套")
        if target and target["period_status"] != "open":
            blockers.append("当前账套不是开放期间")
        if source_company_id and not source:
            blockers.append("来源企业当前期间没有可复制账套")
        return {
            "company_id": company_id,
            "period": period,
            "source_company_id": source_company_id or None,
            "ready": not blockers and not missing,
            "checklist": checklist,
            "missing_steps": missing,
            "blockers": blockers,
            "target_book_id": target["id"] if target else None,
            "source_book_id": source["id"] if source else None,
            "resources": {
                "service_plan": bool(plan and not target_plan),
                "material_templates": material_count,
                "bank_accounts": bank_count,
                "opening_balance_ready": bool(opening),
                "target_ledger_ready": bool(target_ledger),
            },
        }

    def preview(self, actor, company_id, period, source_company_id=""):
        return self._readiness(actor, company_id, period, source_company_id)

    def start(self, actor, request):
        preview = self._readiness(
            actor, request.company_id, request.period, request.source_company_id
        )
        status = "ready" if preview["ready"] else "blocked"
        run_id, stamp = uuid4().hex, now()
        with self.db.transaction(write=True, actor=actor) as conn:
            self.admin.access.require_manager(conn, actor)
            conn.execute(
                "INSERT INTO onboarding_runs(id,firm_id,company_id,period,source_company_id,status,"
                "steps_json,result_json,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    actor.firm_id,
                    request.company_id,
                    request.period,
                    request.source_company_id or None,
                    status,
                    json.dumps(preview, ensure_ascii=False),
                    "{}",
                    actor.user_id,
                    stamp,
                    stamp,
                ),
            )
            conn.execute(
                "INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at) "
                "VALUES(?,?,'onboarding.start',?,?,?)",
                (
                    actor.firm_id,
                    actor.user_id,
                    run_id,
                    json.dumps(
                        {
                            "company_id": request.company_id,
                            "period": request.period,
                            "source_company_id": request.source_company_id,
                            "reason": request.reason,
                        },
                        ensure_ascii=False,
                    ),
                    stamp,
                ),
            )
            row = conn.execute("SELECT * FROM onboarding_runs WHERE id=?", (run_id,)).fetchone()
            return self._json(row)

    def refresh(self, actor, run_id):
        with self.db.transaction(actor=actor) as conn:
            self.admin.access.require_manager(conn, actor)
            row = conn.execute(
                "SELECT * FROM onboarding_runs WHERE id=? AND firm_id=?",
                (run_id, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("开通记录不存在或无权访问", 404)
        preview = self._readiness(
            actor, row["company_id"], row["period"], row["source_company_id"] or ""
        )
        status = "completed" if preview["ready"] else "blocked"
        with self.db.transaction(write=True, actor=actor) as conn:
            self.admin.access.require_manager(conn, actor)
            conn.execute(
                "UPDATE onboarding_runs SET status=?,steps_json=?,updated_at=? WHERE id=?",
                (
                    status,
                    json.dumps(preview, ensure_ascii=False),
                    now(),
                    run_id,
                ),
            )
            row = conn.execute("SELECT * FROM onboarding_runs WHERE id=?", (run_id,)).fetchone()
            return self._json(row)

    def list(self, actor, limit=20, offset=0):
        with self.db.transaction(actor=actor) as conn:
            self.admin.access.require_manager(conn, actor)
            rows = conn.execute(
                "SELECT r.*,c.name AS company_name FROM onboarding_runs r "
                "JOIN companies c ON c.id=r.company_id WHERE r.firm_id=? "
                "ORDER BY r.created_at DESC,r.id DESC LIMIT ? OFFSET ?",
                (actor.firm_id, limit, offset),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM onboarding_runs WHERE firm_id=?", (actor.firm_id,)
            ).fetchone()[0]
            return {"runs": [self._json(row) for row in rows], "total": total}

    def get(self, actor, run_id):
        with self.db.transaction(actor=actor) as conn:
            self.admin.access.require_manager(conn, actor)
            row = conn.execute(
                "SELECT r.*,c.name AS company_name FROM onboarding_runs r "
                "JOIN companies c ON c.id=r.company_id WHERE r.id=? AND r.firm_id=?",
                (run_id, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("开通记录不存在或无权访问", 404)
            return self._json(row)

    def copy_catalog_templates(self, actor, request):
        if request.source_company_id == request.target_company_id:
            raise AccountingError("来源企业和目标企业不能相同", 422)
        with self.db.transaction(actor=actor) as conn:
            self.admin.access.require_manager(conn, actor)
            source = self._book(conn, actor, request.source_company_id, request.period)
            target = self._book(conn, actor, request.target_company_id, request.period)
            if not source:
                raise AccountingError("来源企业当前期间没有账套", 409)
            if not target:
                raise AccountingError("目标企业当前期间没有账套", 409)
            if target["period_status"] != "open":
                raise AccountingError("目标账套已锁期，不能复制模板", 409)
        source_catalog = self.catalog.get(actor, source["id"])
        target_catalog = self.catalog.get(actor, target["id"])
        signatures = {
            json.dumps(
                {key: value for key, value in item.items() if key not in {"id", "version"}},
                sort_keys=True,
                ensure_ascii=False,
            )
            for item in target_catalog["templates"]
        }
        copied, skipped = 0, 0
        for item in source_catalog["templates"]:
            if not item.get("active", True):
                continue
            definition = TemplateDefinition.model_validate(
                {key: value for key, value in item.items() if key not in {"id", "version"}}
            )
            signature = json.dumps(
                definition.model_dump(),
                sort_keys=True,
                ensure_ascii=False,
            )
            if signature in signatures:
                skipped += 1
                continue
            self.catalog.save_template(actor, target["id"], definition)
            signatures.add(signature)
            copied += 1
        plan_copied = self._copy_service_plan(actor, source["id"], target["id"], request.reason)
        materials, materials_skipped = self._copy_material_templates(
            actor, source["id"], target["id"], request.reason
        )
        banks, banks_skipped = self._copy_bank_accounts(
            actor,
            source["id"],
            target["id"],
            source["company_id"],
            target["company_id"],
        )
        with self.db.transaction(write=True, actor=actor) as conn:
            conn.execute(
                "INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at) "
                "VALUES(?,?,'onboarding.templates.copy',?,?,?)",
                (
                    actor.firm_id,
                    actor.user_id,
                    request.target_company_id,
                    json.dumps(
                        {
                            "source_company_id": request.source_company_id,
                            "period": request.period,
                            "copied": copied,
                            "skipped": skipped,
                            "service_plan": plan_copied,
                            "material_templates": materials,
                            "material_templates_skipped": materials_skipped,
                            "bank_accounts": banks,
                            "bank_accounts_skipped": banks_skipped,
                            "reason": request.reason,
                        },
                        ensure_ascii=False,
                    ),
                    now(),
                ),
            )
        return {
            "copied": copied,
            "skipped": skipped,
            "service_plan": plan_copied,
            "material_templates": materials,
            "material_templates_skipped": materials_skipped,
            "bank_accounts": banks,
            "bank_accounts_skipped": banks_skipped,
            "target_book_id": target["id"],
        }

    def _copy_service_plan(self, actor, source_book_id, target_book_id, reason):
        with self.db.transaction(actor=actor) as conn:
            if conn.execute(
                "SELECT 1 FROM service_plan_versions WHERE book_id=? LIMIT 1",
                (target_book_id,),
            ).fetchone():
                return False
            source = conn.execute(
                "SELECT * FROM service_plan_versions WHERE book_id=? ORDER BY version DESC LIMIT 1",
                (source_book_id,),
            ).fetchone()
        if not source:
            return False
        due_on = date.fromisoformat(source["due_on"]) if source["due_on"] else date.today()
        self.plans.save_plan(
            actor,
            target_book_id,
            PlanSave(
                expected_version=0,
                lead_user_id=source["lead_user_id"],
                reviewer_user_id=source["reviewer_user_id"],
                backup_user_id=source["backup_user_id"],
                contact_user_id=source["contact_user_id"],
                due_on=due_on,
                notes=source["notes"] or "",
                reason=reason,
            ),
        )
        return True

    def _copy_material_templates(self, actor, source_book_id, target_book_id, reason):
        with self.db.transaction(actor=actor) as conn:
            source_ledger = conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (source_book_id,)
            ).fetchone()
            target_ledger = conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (target_book_id,)
            ).fetchone()
            if not source_ledger or not target_ledger:
                return 0, 0
            rows = conn.execute(
                "SELECT * FROM material_templates WHERE ledger_id=? ORDER BY created_at,id",
                (source_ledger["ledger_id"],),
            ).fetchall()
            existing = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM material_templates WHERE ledger_id=?",
                    (target_ledger["ledger_id"],),
                )
            }
        copied = skipped = 0
        for row in rows:
            if row["name"] in existing:
                skipped += 1
                continue
            self.plans.create_template(
                actor,
                target_book_id,
                TemplateCreate(
                    name=row["name"],
                    items=json.loads(row["items_json"]),
                    reason=reason,
                ),
            )
            existing.add(row["name"])
            copied += 1
        return copied, skipped

    def _copy_bank_accounts(
        self, actor, source_book_id, target_book_id, source_company_id, target_company_id
    ):
        with self.db.transaction(actor=actor) as conn:
            rows = conn.execute(
                "SELECT code,name FROM company_master_entities WHERE firm_id=? "
                "AND company_id=? AND kind='bank_account' AND active=1 ORDER BY code",
                (actor.firm_id, source_company_id),
            ).fetchall()
            existing = {
                row["code"]
                for row in conn.execute(
                    "SELECT code FROM company_master_entities WHERE firm_id=? "
                    "AND company_id=? AND kind='bank_account'",
                    (actor.firm_id, target_company_id),
                )
            }
        copied = skipped = 0
        for row in rows:
            if row["code"] in existing:
                skipped += 1
                continue
            self.ledgers.create_entity(
                actor,
                target_book_id,
                EntityCreate(kind="bank_account", code=row["code"], name=row["name"]),
            )
            existing.add(row["code"])
            copied += 1
        return copied, skipped

    @staticmethod
    def _json(row):
        data = dict(row)
        value = {
            key: data[key]
            for key in (
                "id",
                "company_id",
                "period",
                "source_company_id",
                "status",
                "created_by",
                "created_at",
                "updated_at",
            )
        }
        value["steps"] = json.loads(data["steps_json"])
        value["company_name"] = data.get("company_name", "")
        return value
