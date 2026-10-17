"""Readiness, resumable onboarding runs and governed template copying."""

import json
from uuid import uuid4

from ..accounting.catalog import CatalogService
from ..accounting.catalog_models import TemplateDefinition
from ..accounting.domain import AccountingError
from ..accounting.service import now


class OnboardingService:
    def __init__(self, db, admin, catalog=None):
        self.db = db
        self.admin = admin
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
                            "reason": request.reason,
                        },
                        ensure_ascii=False,
                    ),
                    now(),
                ),
            )
        return {"copied": copied, "skipped": skipped, "target_book_id": target["id"]}

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
