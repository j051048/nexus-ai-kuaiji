"""Isolated historical onboarding: originals -> staging -> verified cutover.

This domain never manufactures current sources, drafts, approvals or exports.
All publication is bounded by a currently authorized migration project.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.accounting.access import AccessControl
from app.accounting.catalog import CatalogService
from app.accounting.catalog_models import AccountDefinition
from app.accounting.domain import Actor, AccountingError
from app.accounting.queries import page_bounds
from app.accounting.service import fingerprint, now
from app.ledgers.service import LedgersService
from .models import FileMapping


MAX_PROJECT_RECORDS = 50_000
MAX_PROJECT_FILES = 64
MAX_PROJECT_BYTES = 64 * 1024 * 1024
JOB_FIELDS = "id,project_id,file_id,phase,status,expected_version,attempt_count,max_attempts,processed_count,record_count,error_code,error_detail,created_at,updated_at,finished_at"
KINDS = {"account", "auxiliary", "voucher", "balance", "open_item"}
CATEGORIES = {"package", "accounts", "auxiliaries", "vouchers", "balances", "open_items", "attachment"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class LegacyMigrationService:
    def __init__(self, db, storage):
        self.db, self.storage = db, storage
        self.access, self.catalog, self.ledgers = AccessControl(), CatalogService(db), LedgersService(db)

    def _target(self, conn, actor, book_id):
        book = self.access.book(conn, actor, book_id)
        ledger = conn.execute("SELECT ledger_id FROM ledger_periods WHERE book_id=? AND firm_id=? AND company_id=?", (book_id, actor.firm_id, book["company_id"])).fetchone()
        if not ledger:
            raise AccountingError("请先建立长期账套与月份关联", 409)
        manager = bool(book["permissions"]["review"] and conn.execute("SELECT 1 FROM ledger_managers WHERE ledger_id=? AND user_id=?", (ledger["ledger_id"], actor.user_id)).fetchone())
        company = conn.execute("SELECT name FROM companies WHERE id=?", (book["company_id"],)).fetchone()
        empty = not any(conn.execute("SELECT 1 FROM " + table + " WHERE book_id=? LIMIT 1", (book_id,)).fetchone() for table in ("sources", "drafts", "exports"))
        return book, ledger["ledger_id"], {"book_id": book_id, "book_name": book["name"], "company_id": book["company_id"], "company_name": company["name"] if company else "", "ledger_id": ledger["ledger_id"], "period": book["period"], "period_status": book["period_status"], "empty": empty}, manager

    def _project(self, conn, actor, book_id, project_id, capability="read"):
        self.access.require_user(conn, actor.user_id)
        row = conn.execute("SELECT * FROM migration_projects WHERE id=? AND book_id=? AND firm_id=?", (project_id, book_id, actor.firm_id)).fetchone()
        if not row:
            raise AccountingError("旧账迁移项目不存在或当前无权访问", 404)
        book, _, target, manager = self._target(conn, actor, book_id)
        grant = conn.execute("SELECT role FROM migration_project_access WHERE project_id=? AND user_id=?", (project_id, actor.user_id)).fetchone()
        firm_manager = self.access.firm_role(conn, actor) in {"owner", "admin"} and manager
        visible = bool(grant or firm_manager)
        if not visible:
            raise AccountingError("旧账项目需要明确的历史资料授权", 404)
        role = grant["role"] if grant else "manager"
        can_manage = manager and (role == "manager" or firm_manager)
        can_review = book["permissions"]["review"] and (role in {"manager", "reviewer"} or firm_manager)
        mutable = row["status"] not in {"active", "cancelled"} and book["period_status"] == "open"
        latest_mapping = conn.execute("SELECT created_by FROM migration_mapping_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
        independent = actor.user_id != row["created_by"] and actor.user_id != (latest_mapping["created_by"] if latest_mapping else row["created_by"])
        permissions = {"read": visible, "manage": can_manage, "review": can_review and independent,
                       "activate": can_manage, "upload": can_manage and mutable, "independent": independent}
        if capability == "manage" and not can_manage or capability == "review" and not permissions["review"]:
            raise AccountingError("需要当前账套权限、明确迁移授权及另一名会计复核", 403)
        return dict(row), target, permissions

    @staticmethod
    def _mutable(project, target):
        if project["status"] in {"active", "cancelled"}:
            raise AccountingError("已接管或取消的项目只读；补导请新建迁移版本", 409)
        if target["period_status"] != "open":
            raise AccountingError("目标会计期间已关闭，不能继续迁移", 409)

    @staticmethod
    def _version(project, expected):
        if project["version"] != expected:
            raise AccountingError("迁移资料已更新，请刷新项目后重试", 409)

    def _audit(self, conn, actor, book_id, action, object_id, value):
        self.ledgers._audit(conn, actor, book_id, "migration." + action, object_id, value)

    def _mapping(self, conn, project_id):
        row = conn.execute("SELECT * FROM migration_mapping_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
        return ({"version": row["version"], "created_by": row["created_by"], "created_at": row["created_at"], **json.loads(row["payload_json"])} if row else {"version": 0, "accounts": [], "auxiliaries": []})

    @staticmethod
    def _context(project):
        return {key: project[key] for key in ("external_book_id", "currency", "history_start", "history_end", "cutover_date")}

    @staticmethod
    def _active_file_sql(alias="f"):
        return "NOT EXISTS(SELECT 1 FROM migration_file_replacements replacement WHERE replacement.project_id=" + alias + ".project_id AND replacement.old_file_id=" + alias + ".id)"

    def _record_base(self, project_id):
        return """ FROM migration_records r JOIN migration_files f ON f.id=r.file_id AND f.project_id=r.project_id
          JOIN migration_jobs j ON j.id=r.job_id AND j.project_id=r.project_id
          WHERE r.project_id=? AND j.status='succeeded' AND j.phase='parse' AND """ + self._active_file_sql() + """
          AND NOT EXISTS(SELECT 1 FROM migration_jobs newer WHERE newer.file_id=j.file_id AND newer.phase='parse' AND newer.status='succeeded'
            AND (newer.created_at>j.created_at OR newer.created_at=j.created_at AND newer.id>j.id))""", [project_id]

    def _records_in_transaction(self, conn, project_id):
        base, params = self._record_base(project_id)
        total = conn.execute("SELECT count(*)" + base, params).fetchone()[0]
        if total > MAX_PROJECT_RECORDS:
            raise AccountingError("当前试点单项目最多五万条历史记录，请缩小范围；超限资料不会接管", 413)
        return [{"id": row["id"], "file_id": row["file_id"], "kind": row["kind"], "external_id": row["external_id"], "period": row["period"], "payload": json.loads(row["payload_json"]), "fingerprint": row["fingerprint"], "row_number": row["row_number"]} for row in conn.execute("SELECT r.*" + base + " ORDER BY r.kind,r.period,r.external_id,r.id", params)]

    def _basis(self, conn, project):
        if self.db.is_postgres:
            return conn.execute("SELECT accounting.migration_basis(?)", (project["id"],)).fetchone()[0]
        files = [dict(r) for r in conn.execute("SELECT id,sha256,size_bytes,mapping_json FROM migration_files f WHERE project_id=? AND " + self._active_file_sql() + " ORDER BY id", (project["id"],))]
        jobs = [dict(r) for r in conn.execute("SELECT id,file_id,expected_version FROM migration_jobs WHERE project_id=? AND phase='parse' AND status='succeeded' ORDER BY id", (project["id"],))]
        records = [dict(r) for r in conn.execute("SELECT id,job_id,fingerprint FROM migration_records WHERE project_id=? ORDER BY id", (project["id"],))]
        accounts = [dict(r) for r in conn.execute("SELECT code,name,category,normal_balance,active,auxiliary_requirements,version FROM accounts WHERE book_id=? ORDER BY code", (project["book_id"],))]
        masters = [dict(r) for r in conn.execute("SELECT id,kind,code,name,active,version FROM company_master_entities WHERE firm_id=? AND company_id=? ORDER BY id", (project["firm_id"], project["company_id"]))]
        period = conn.execute("SELECT status FROM periods WHERE book_id=?", (project["book_id"],)).fetchone()
        departments = [dict(r) for r in conn.execute("SELECT id,name FROM departments WHERE company_id=? ORDER BY id", (project["company_id"],))]
        people = [dict(r) for r in conn.execute("SELECT cm.user_id,cm.role,cm.department_id,u.active FROM company_memberships cm JOIN users u ON u.id=cm.user_id WHERE cm.company_id=? ORDER BY cm.user_id", (project["company_id"],))]
        return fingerprint({"project": {key: project[key] for key in ("id", "version", "book_id", "ledger_id", "history_start", "history_end", "cutover_date", "external_book_id", "source_system_key")}, "files": files, "jobs": jobs, "records": records, "mapping": self._mapping(conn, project["id"]), "accounts": accounts, "masters": masters, "period_status": period[0], "departments": departments, "people": people})

    def list(self, actor, book_id, limit=20, offset=0):
        page_bounds(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            book, _, target, manager = self._target(conn, actor, book_id)
            firm_manager = self.access.firm_role(conn, actor) in {"owner", "admin"} and manager
            predicate = "p.book_id=? AND p.firm_id=? AND (?=1 OR EXISTS(SELECT 1 FROM migration_project_access a WHERE a.project_id=p.id AND a.user_id=?))"
            args = [book_id, actor.firm_id, int(firm_manager), actor.user_id]
            total = conn.execute("SELECT count(*) FROM migration_projects p WHERE " + predicate, args).fetchone()[0]
            rows = [dict(r) for r in conn.execute("SELECT p.* FROM migration_projects p WHERE " + predicate + " ORDER BY p.updated_at DESC,p.id LIMIT ? OFFSET ?", [*args, limit, offset])]
            return {"target": target, "permissions": {"read": True, "manage": manager, "review": book["permissions"]["review"], "activate": manager, "can_create": manager and target["empty"] and target["period_status"] == "open"}, "projects": rows, "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total}, "vendor_native_verified": False, "official_ledger": "source_software"}

    def create(self, actor, book_id, body):
        if body.target_book_id != book_id:
            raise AccountingError("目标账套与请求路径不一致", 409)
        digest = fingerprint(body.model_dump(exclude={"idempotency_key"}))
        with self.db.transaction(write=True, actor=actor) as conn:
            _, ledger_id, target, manager = self._target(conn, actor, book_id)
            if not manager:
                raise AccountingError("创建旧账项目需要当前账套复核和长期账套管理授权", 403)
            prior = conn.execute("SELECT * FROM migration_projects WHERE firm_id=? AND created_by=? AND create_key=?", (actor.firm_id, actor.user_id, body.idempotency_key)).fetchone()
            if prior:
                if prior["create_fingerprint"] != digest or prior["book_id"] != book_id:
                    raise AccountingError("迁移编号已经用于不同资料", 409)
                return self._detail(conn, actor, book_id, prior["id"])
            if not target["empty"] or target["period_status"] != "open":
                raise AccountingError("请创建空的新月份作为接管目标；不覆盖正在办理的账务", 409)
            if body.cutover_date != target["period"] + "-01":
                raise AccountingError("接管日期必须为目标月份第一天", 409)
            bound = conn.execute("SELECT ledger_id FROM migration_projects WHERE firm_id=? AND source_system_key=? AND external_book_id=? AND status<>'cancelled'", (actor.firm_id, body.source_system_key, body.external_book_id)).fetchone()
            if bound and bound["ledger_id"] != ledger_id:
                raise AccountingError("该来源账套已经绑定其他长期账套，不能自动合并", 409)
            identity, stamp = uuid4().hex, now()
            conn.execute("INSERT INTO migration_projects(id,firm_id,company_id,ledger_id,book_id,external_book_id,source_system_key,source_product,product_version,history_start,history_end,cutover_date,currency,create_key,create_fingerprint,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (identity, actor.firm_id, target["company_id"], ledger_id, book_id, body.external_book_id, body.source_system_key, body.source_product, body.product_version, body.history_start, body.history_end, body.cutover_date, body.currency, body.idempotency_key, digest, actor.user_id, stamp, stamp))
            conn.execute("INSERT INTO migration_project_access(project_id,user_id,role,granted_by,reason,created_at) VALUES(?,?,'manager',?,?,?)", (identity, actor.user_id, actor.user_id, "创建项目时明确授权", stamp))
            self._audit(conn, actor, book_id, "project.create", identity, {"reason": body.reason, "external_book_id": body.external_book_id, "cutover_date": body.cutover_date})
            return self._detail(conn, actor, book_id, identity)

    def _detail(self, conn, actor, book_id, project_id):
        project, target, permissions = self._project(conn, actor, book_id, project_id)
        files = []
        for r in conn.execute("SELECT id,category,filename,sha256,size_bytes,media_type,mapping_json,created_at FROM migration_files f WHERE project_id=? ORDER BY created_at,id LIMIT 64", (project_id,)):
            item = dict(r)
            item["mapping"] = json.loads(item.pop("mapping_json"))
            item["superseded"] = bool(conn.execute("SELECT 1 FROM migration_file_replacements WHERE project_id=? AND old_file_id=?", (project_id, item["id"])).fetchone())
            files.append(item)
        jobs = []
        for r in conn.execute("SELECT " + JOB_FIELDS + ",checkpoint_json FROM migration_jobs WHERE project_id=? ORDER BY created_at DESC,id DESC LIMIT 30", (project_id,)):
            item = dict(r)
            item["expected_record_count"] = json.loads(item.pop("checkpoint_json")).get("metadata", {}).get("expected_record_count", 0)
            item["permissions"] = {"retry": permissions["upload"] and item["status"] in {"failed", "stale", "cancelled"}, "cancel": permissions["upload"] and item["status"] in {"queued", "running", "retry_wait"}}
            jobs.append(item)
        validation = conn.execute("SELECT * FROM migration_validations WHERE id=? AND project_id=?", (project["active_validation_id"], project_id)).fetchone()
        report = None
        if validation:
            report = {key: validation[key] for key in ("id", "input_version", "mapping_version", "basis_token", "blocking_count", "warning_count", "created_at")}
            full = json.loads(validation["report_json"])
            report.update({key: full.get(key, {}) for key in ("summary", "checks", "coverage")})
            report["project_version"] = validation["input_version"]
            report["current"] = validation["input_version"] == project["version"] and (project["status"] == "active" or validation["basis_token"] == self._basis(conn, project))
        acceptance = conn.execute("SELECT id,validation_id,expected_version,basis_token,accepted_by,reason,created_at FROM migration_acceptances WHERE project_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (project_id,)).fetchone()
        cutover = conn.execute("SELECT id,validation_id,acceptance_id,payload_json,created_at FROM migration_cutovers WHERE project_id=?", (project_id,)).fetchone()
        eligible = []
        if project["status"] == "active" and self.access.book(conn, actor, book_id)["permissions"]["review"]:
            from app.accounting.review import ReviewService
            from app.accounting.service import AccountingService
            review_scope, args, _ = ReviewService(self.db, AccountingService(self.db))._review_scope(conn, actor)
            eligible = [dict(r) for r in conn.execute("SELECT v.id,v.name,v.period,v.period_status FROM (" + review_scope + ") v JOIN ledger_periods lp ON lp.book_id=v.id WHERE lp.ledger_id=? AND v.period>=? AND v.company_id=? ORDER BY v.period DESC,v.id LIMIT 100", [*args, project["ledger_id"], project["cutover_date"][:7], project["company_id"]])]
        can_accept = bool(permissions["review"] and project["status"] == "ready" and report and report["current"] and report["blocking_count"] == 0)
        can_activate = bool(permissions["activate"] and project["status"] == "accepted" and report and report["current"] and acceptance and acceptance["validation_id"] == report["id"] and acceptance["basis_token"] == report["basis_token"] and target["empty"])
        return {"project": project, "target": target, "permissions": permissions, "files": files, "jobs": jobs, "mapping": self._mapping(conn, project_id), "latest_validation": report, "acceptance": dict(acceptance) if acceptance else None, "can_accept": can_accept, "can_activate": can_activate, "eligible_bank_books": eligible,
                "cutover": {"id": cutover["id"], "created_at": cutover["created_at"], "snapshot": json.loads(cutover["payload_json"])} if cutover else None, "vendor_native_verified": False, "official_ledger": "source_software"}

    def get(self, actor, book_id, project_id):
        with self.db.transaction(actor=actor) as conn:
            return self._detail(conn, actor, book_id, project_id)

    def _file(self, conn, actor, book_id, project_id, file_id):
        self._project(conn, actor, book_id, project_id)
        row = conn.execute("SELECT * FROM migration_files WHERE id=? AND project_id=? AND firm_id=?", (file_id, project_id, actor.firm_id)).fetchone()
        if not row:
            raise AccountingError("旧账原件不存在或当前无权访问", 404)
        return dict(row)

    def _read_blob(self, row):
        try:
            return self.storage.read(dict(row))
        except AccountingError as error:
            if "完整性" in str(error):
                raise AccountingError("旧账原件校验不一致，请核验恢复记录", 409) from None
            raise

    def original(self, actor, book_id, project_id, file_id):
        with self.db.transaction(actor=actor) as conn:
            row = self._file(conn, actor, book_id, project_id, file_id)
            return {**{k: row[k] for k in ("id", "filename", "media_type", "sha256")}, "data": self._read_blob(row)}

    def upload(self, actor, book_id, project_id, filename, data, category, mapping, expected_version, key, replaces_file_id=None, reason="上传旧账资料并确认原表范围"):
        from .storage import LegacyOriginalStore
        from .normalization import inspect_file
        if category not in CATEGORIES:
            raise AccountingError("旧账资料类型无效")
        profile = FileMapping.model_validate(mapping or {}).model_dump()
        with self.db.transaction(actor=actor) as conn:
            project, target, _ = self._project(conn, actor, book_id, project_id, "manage")
            self._mutable(project, target)
        if category == "attachment":
            from ..business.storage import AttachmentStore
            metadata = self.storage.clone(AttachmentStore).put(filename, "", data)
            inspected = {"format": "attachment", "headers": [], "samples": [], "adapter_verified": False}
        else:
            metadata = self.storage.clone(LegacyOriginalStore).put(filename, data)
            try:
                inspected = inspect_file(metadata["filename"], data, profile)
                inspected = {k: v for k, v in inspected.items() if k not in {"rows", "positions", "raw", "package"}} | {"samples": inspected.get("rows", [])[:5]}
            except Exception:
                self.storage.discard(metadata)
                raise
        commit_may_have_succeeded = False
        try:
            with self.db.transaction(write=True, actor=actor) as conn:
                project, target, _ = self._project(conn, actor, book_id, project_id, "manage")
                self._mutable(project, target)
                prior = conn.execute("SELECT * FROM migration_files WHERE project_id=? AND create_key=?", (project_id, key)).fetchone()
                if prior:
                    if prior["sha256"] != metadata["sha256"] or prior["category"] != category or json.loads(prior["mapping_json"]) != profile:
                        raise AccountingError("同一上传编号不能用于不同资料或列映射", 409)
                    self.storage.discard(metadata)
                    return {"file": {k: prior[k] for k in ("id", "filename", "sha256", "category")}, "inspection": inspected, "detail": self._detail(conn, actor, book_id, project_id)}
                self._version(project, expected_version)
                existing = conn.execute("SELECT count(*),coalesce(sum(size_bytes),0) FROM migration_files WHERE project_id=?", (project_id,)).fetchone()
                if existing[0] >= MAX_PROJECT_FILES or existing[1] + metadata["size_bytes"] > MAX_PROJECT_BYTES:
                    raise AccountingError("当前试点项目最多64个原件、合计64MB，请分批规划历史范围", 413)
                identical = conn.execute("SELECT * FROM migration_files f WHERE project_id=? AND category=? AND sha256=? AND mapping_json=? AND " + self._active_file_sql(), (project_id, category, metadata["sha256"], encode(profile))).fetchone()
                if identical and not replaces_file_id:
                    self.storage.discard(metadata)
                    return {"file": {k: identical[k] for k in ("id", "filename", "sha256", "category")}, "inspection": inspected, "detail": self._detail(conn, actor, book_id, project_id), "duplicate": True}
                identity, stamp = uuid4().hex, now()
                conn.execute("INSERT INTO migration_files(id,project_id,firm_id,company_id,category,filename,storage_key,sha256,size_bytes,media_type,mapping_json,create_key,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (identity, project_id, actor.firm_id, project["company_id"], category, metadata["filename"], metadata["storage_key"], metadata["sha256"], metadata["size_bytes"], metadata["media_type"], encode(profile), key, actor.user_id, stamp))
                if replaces_file_id:
                    old = self._file(conn, actor, book_id, project_id, replaces_file_id)
                    if old["category"] != category:
                        raise AccountingError("替代原件必须属于相同资料类型", 409)
                    conn.execute("INSERT INTO migration_file_replacements(id,project_id,old_file_id,new_file_id,firm_id,company_id,request_key,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (uuid4().hex, project_id, replaces_file_id, identity, actor.firm_id, project["company_id"], key, reason, actor.user_id, stamp))
                conn.execute("UPDATE migration_projects SET version=version+1,status='collecting',active_validation_id=NULL,updated_at=? WHERE id=?", (stamp, project_id))
                self._audit(conn, actor, book_id, "file.add", identity, {"category": category, "sha256": metadata["sha256"], "replaces_file_id": replaces_file_id, "reason": reason})
                receipt = {"file": {"id": identity, "filename": metadata["filename"], "category": category, "sha256": metadata["sha256"]}, "inspection": inspected, "detail": self._detail(conn, actor, book_id, project_id)}
                commit_may_have_succeeded = True
                self.storage.retain(metadata)
                return receipt
        except Exception:
            if commit_may_have_succeeded:
                # A lost COMMIT acknowledgement cannot establish rollback.
                # Keep the original even when a fresh read is denied or fails.
                raise AccountingError("原件保存结果暂不确定，原件已保留；请读取最新状态或使用相同请求编号重试", 503) from None
            self.storage.discard(metadata)
            raise

    def remap(self, actor, book_id, project_id, file_id, profile, body):
        with self.db.transaction(actor=actor) as conn:
            original = self._file(conn, actor, book_id, project_id, file_id)
            data = self._read_blob(original)
        return self.upload(actor, book_id, project_id, original["filename"], data, original["category"], profile, body.expected_version, body.idempotency_key, file_id, body.reason)

    def records(self, actor, book_id, project_id, kind=None, period=None, limit=20, offset=0):
        page_bounds(limit, offset)
        if kind and kind not in KINDS:
            raise AccountingError("历史资料类型无效")
        with self.db.transaction(actor=actor) as conn:
            project, _, _ = self._project(conn, actor, book_id, project_id)
            base, args = self._record_base(project_id)
            for column, value in (("kind", kind), ("period", period)):
                if value is not None:
                    base += " AND r." + column + "=?"
                    args.append(value)
            total = conn.execute("SELECT count(*)" + base, args).fetchone()[0]
            rows = []
            for r in conn.execute("SELECT r.id,r.kind,r.external_id,r.period,r.payload_json,r.file_id,r.row_number,r.fingerprint" + base + " ORDER BY r.kind,r.period,r.external_id,r.id LIMIT ? OFFSET ?", [*args, limit, offset]):
                item = dict(r)
                item["payload"] = json.loads(item.pop("payload_json"))
                rows.append(item)
            return {"records": rows, "mapping": self._mapping(conn, project_id), "archived": project["status"] == "active", "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total}}

    def save_mapping(self, actor, book_id, project_id, body):
        digest = fingerprint(body.model_dump(exclude={"idempotency_key"}))
        with self.db.transaction(write=True, actor=actor) as conn:
            project, target, _ = self._project(conn, actor, book_id, project_id, "manage")
            self._mutable(project, target)
            prior = conn.execute("SELECT request_fingerprint FROM migration_mapping_versions WHERE project_id=? AND request_key=?", (project_id, body.idempotency_key)).fetchone()
            if prior:
                if prior["request_fingerprint"] != digest:
                    raise AccountingError("映射编号已用于不同内容", 409)
                return self._detail(conn, actor, book_id, project_id)
            self._version(project, body.expected_version)
            current = self._mapping(conn, project_id)
            accounts = {r["source_code"]: r for r in current["accounts"]}
            auxiliaries = {(r["kind"], r["source_code"]): r for r in current["auxiliaries"]}
            for row in body.accounts:
                AccountDefinition.valid_code(row.target_code)
                if row.action == "use_existing" and not conn.execute("SELECT 1 FROM accounts WHERE book_id=? AND code=? AND active=1", (book_id, row.target_code)).fetchone():
                    raise AccountingError("映射目标科目必须属于当前账套且已启用")
                if row.action == "create" and conn.execute("SELECT 1 FROM accounts WHERE book_id=? AND code=?", (book_id, row.target_code)).fetchone():
                    raise AccountingError("目标科目已存在，请明确选择使用已有科目", 409)
                accounts[row.source_code] = row.model_dump()
            for row in body.auxiliaries:
                if row.action == "use_existing":
                    if row.kind in {"department", "employee"}:
                        raise AccountingError("历史部门及人员独立归档，不能映射成登录账号；请选择创建历史辅助档案")
                    if not conn.execute("SELECT 1 FROM company_master_entities WHERE id=? AND firm_id=? AND company_id=? AND kind=? AND active=1", (row.target_id, actor.firm_id, project["company_id"], row.kind)).fetchone():
                        raise AccountingError("辅助映射必须为本企业当前有效的相同类型主数据")
                auxiliaries[(row.kind, row.source_code)] = row.model_dump()
            settlement_scope = body.settlement_accounts if "settlement_accounts" in body.model_fields_set else current.get("settlement_accounts", {})
            payload = {"accounts": [accounts[k] for k in sorted(accounts)], "auxiliaries": [auxiliaries[k] for k in sorted(auxiliaries)], "settlement_accounts": settlement_scope}
            if len(accounts) > 5000 or len(auxiliaries) > 20000:
                raise AccountingError("映射超出当前试点限制", 413)
            stamp = now()
            conn.execute("INSERT INTO migration_mapping_versions(project_id,version,firm_id,company_id,payload_json,request_key,request_fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (project_id, current["version"] + 1, actor.firm_id, project["company_id"], encode(payload), body.idempotency_key, digest, actor.user_id, stamp))
            conn.execute("UPDATE migration_projects SET version=version+1,status='collecting',active_validation_id=NULL,updated_at=? WHERE id=?", (stamp, project_id))
            self._audit(conn, actor, book_id, "mapping.save", project_id, {"mapping_version": current["version"] + 1, "reason": body.reason})
            return self._detail(conn, actor, book_id, project_id)

    def inspection(self, actor, book_id, project_id, file_id):
        from .normalization import inspect_file
        with self.db.transaction(actor=actor) as conn:
            row = self._file(conn, actor, book_id, project_id, file_id)
            data = self._read_blob(row)
        if row["category"] == "attachment":
            return {"headers": [], "samples": [], "format": "attachment"}
        value = inspect_file(row["filename"], data, json.loads(row["mapping_json"]))
        return {k: v for k, v in value.items() if k not in {"rows", "positions", "raw", "package"}} | {"samples": value.get("rows", [])[:5]}

    def enqueue(self, actor, book_id, project_id, phase, body):
        if phase not in {"parse", "validate"}:
            raise AccountingError("迁移任务类型无效")
        with self.db.transaction(write=True, actor=actor) as conn:
            project, target, _ = self._project(conn, actor, book_id, project_id, "manage")
            self._mutable(project, target)
            self._version(project, body.expected_version)
            files = [dict(r) for r in conn.execute("SELECT f.* FROM migration_files f WHERE f.project_id=? AND f.category<>'attachment' AND " + self._active_file_sql() + " ORDER BY f.created_at,f.id", (project_id,))]
            if not files:
                raise AccountingError("请先上传历史账资料", 409)
            if phase == "parse":
                for f in files:
                    if not f["filename"].lower().endswith(".json") and not json.loads(f["mapping_json"]).get("confirmed", False):
                        raise AccountingError("表格列映射尚未确认，请先核对原表字段", 409)
                tasks = [(f["id"], body.idempotency_key + ":" + f["id"]) for f in files]
            else:
                if conn.execute("SELECT 1 FROM migration_jobs WHERE project_id=? AND phase='parse' AND status IN ('queued','running','retry_wait') LIMIT 1", (project_id,)).fetchone():
                    raise AccountingError("请等待原件解析完成后再校验", 409)
                for f in files:
                    if not conn.execute("SELECT 1 FROM migration_jobs WHERE file_id=? AND phase='parse' AND status='succeeded'", (f["id"],)).fetchone():
                        raise AccountingError("部分原件尚未成功解析，请处理任务后继续", 409)
                tasks = [(None, body.idempotency_key)]
            changed = False
            for file_id, key in tasks:
                digest = fingerprint({"phase": phase, "project_id": project_id, "file_id": file_id, "version": project["version"], "reason": body.reason})
                prior = conn.execute("SELECT id,request_fingerprint FROM migration_jobs WHERE project_id=? AND request_key=?", (project_id, key)).fetchone()
                if prior:
                    if prior["request_fingerprint"] != digest:
                        raise AccountingError("任务编号已用于不同迁移输入", 409)
                    continue
                active = conn.execute("SELECT 1 FROM migration_jobs WHERE project_id=? AND phase=? AND coalesce(file_id,'')=coalesce(?,'') AND status IN ('queued','running','retry_wait')", (project_id, phase, file_id)).fetchone()
                if active:
                    continue
                if phase == "parse" and conn.execute("SELECT 1 FROM migration_jobs WHERE file_id=? AND phase='parse' AND status='succeeded'", (file_id,)).fetchone():
                    continue
                stamp = now()
                conn.execute("INSERT INTO migration_jobs(id,project_id,file_id,firm_id,company_id,phase,execution_user_id,expected_version,available_at,request_key,request_fingerprint,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (uuid4().hex, project_id, file_id, actor.firm_id, project["company_id"], phase, actor.user_id, project["version"], stamp, key, digest, stamp, stamp))
                changed = True
            if changed:
                conn.execute("UPDATE migration_projects SET status='processing',active_validation_id=NULL,updated_at=? WHERE id=?", (now(), project_id))
                self._audit(conn, actor, book_id, "job.enqueue", project_id, {"phase": phase, "reason": body.reason})
            return self._detail(conn, actor, book_id, project_id)

    def job(self, actor, book_id, project_id, job_id):
        with self.db.transaction(actor=actor) as conn:
            project, target, permissions = self._project(conn, actor, book_id, project_id)
            row = conn.execute("SELECT " + JOB_FIELDS + " FROM migration_jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
            if not row:
                raise AccountingError("迁移任务不存在或无权访问", 404)
            result = dict(row)
            mutable = permissions["manage"] and project["status"] not in {"active", "cancelled"} and target["period_status"] == "open"
            result["permissions"] = {"retry": mutable and row["status"] in {"failed", "stale", "cancelled"}, "cancel": mutable and row["status"] in {"queued", "running", "retry_wait"}}
            return result

    def retry(self, actor, book_id, project_id, job_id, body):
        # A deliberate new attempt receives a new immutable identity and actor.
        # Recovery of the same leased attempt is handled only by the worker.
        with self.db.transaction(write=True, actor=actor) as conn:
            project, target, _ = self._project(conn, actor, book_id, project_id, "manage")
            self._mutable(project, target)
            self._version(project, body.expected_version)
            job = conn.execute("SELECT " + JOB_FIELDS + " FROM migration_jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
            if not job or job["status"] not in {"failed", "stale", "cancelled"}:
                raise AccountingError("该迁移任务不能手工重试", 409)
            digest = fingerprint({"job_id": job_id, "version": project["version"], "reason": body.reason})
            prior = conn.execute("SELECT request_fingerprint FROM migration_jobs WHERE project_id=? AND request_key=?", (project_id, body.idempotency_key)).fetchone()
            if prior:
                if prior["request_fingerprint"] != digest:
                    raise AccountingError("重试编号已经用于不同任务", 409)
                return self._detail(conn, actor, book_id, project_id)
            stamp = now()
            conn.execute("INSERT INTO migration_jobs(id,project_id,file_id,firm_id,company_id,phase,execution_user_id,expected_version,available_at,request_key,request_fingerprint,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (uuid4().hex, project_id, job["file_id"], actor.firm_id, project["company_id"], job["phase"], actor.user_id, project["version"], stamp, body.idempotency_key, digest, stamp, stamp))
            conn.execute("UPDATE migration_projects SET status='processing',active_validation_id=NULL,updated_at=? WHERE id=?", (stamp, project_id))
            self._audit(conn, actor, book_id, "job.retry", job_id, {"reason": body.reason})
            return self._detail(conn, actor, book_id, project_id)

    def cancel_job(self, actor, book_id, project_id, job_id, body):
        with self.db.transaction(write=True, actor=actor) as conn:
            project, target, _ = self._project(conn, actor, book_id, project_id, "manage")
            self._mutable(project, target)
            self._version(project, body.expected_version)
            row = conn.execute("SELECT id,status FROM migration_jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
            if not row:
                raise AccountingError("迁移任务不存在或无权访问", 404)
            if self.db.is_postgres:
                if not conn.execute("SELECT accounting.migration_cancel(?,?)", (job_id, body.reason)).fetchone()[0]:
                    raise AccountingError("该迁移任务已经结束，请读取最新状态", 409)
            elif row["status"] in {"queued", "running", "retry_wait"}:
                stamp = now()
                conn.execute("UPDATE migration_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,finished_at=?,updated_at=?,error_code='cancelled',error_detail=? WHERE id=?", (stamp, stamp, body.reason, job_id))
            elif row["status"] != "cancelled":
                raise AccountingError("该迁移任务已经结束", 409)
            self._audit(conn, actor, book_id, "job.cancel", job_id, {"reason": body.reason})
            return self._detail(conn, actor, book_id, project_id)

    def _current_validation(self, conn, project, validation_id=None):
        row = conn.execute("SELECT * FROM migration_validations WHERE id=? AND project_id=?", (validation_id or project["active_validation_id"], project["id"])).fetchone()
        if not row or row["id"] != project["active_validation_id"] or row["input_version"] != project["version"]:
            raise AccountingError("迁移验收报告已失效，请重新校验当前资料", 409)
        if project["status"] != "active" and row["basis_token"] != self._basis(conn, project):
            raise AccountingError("文件、映射或目标科目已变化，请重新校验", 409)
        return row

    def report(self, actor, book_id, project_id):
        with self.db.transaction(actor=actor) as conn:
            project, _, _ = self._project(conn, actor, book_id, project_id)
            row = self._current_validation(conn, project)
            return {"project_id": project_id, "validation_id": row["id"], "basis_token": row["basis_token"], "report": json.loads(row["report_json"]), "official_ledger": "source_software", "automatic_export": False}

    def issues(self, actor, book_id, project_id, severity=None, kind=None, limit=20, offset=0):
        page_bounds(limit, offset)
        if severity and severity not in {"blocking", "warning"} or kind and kind not in KINDS:
            raise AccountingError("差异筛选参数无效")
        with self.db.transaction(actor=actor) as conn:
            project, _, _ = self._project(conn, actor, book_id, project_id)
            validation = self._current_validation(conn, project)
            if self.db.is_postgres:
                base = " FROM migration_validations v CROSS JOIN LATERAL jsonb_array_elements(v.report_json::jsonb->'issues') e(value) WHERE v.id=?"
                field = lambda key: "e.value->>'" + key + "'"
                select = "e.value::text"
            else:
                base = " FROM migration_validations v,json_each(v.report_json,'$.issues') e WHERE v.id=?"
                field = lambda key: "json_extract(e.value,'$." + key + "')"
                select = "e.value"
            args = [validation["id"]]
            for key, value in (("severity", severity), ("kind", kind)):
                if value:
                    base += " AND " + field(key) + "=?"
                    args.append(value)
            total = conn.execute("SELECT count(*)" + base, args).fetchone()[0]
            rows = conn.execute("SELECT " + select + base + " ORDER BY " + field("severity") + "," + field("code") + " LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
            return {"issues": [json.loads(r[0]) for r in rows], "validation_id": validation["id"], "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total}}

    def _verify_originals(self, actor, book_id, project_id):
        # File I/O happens outside the global write coordination lock.
        with self.db.transaction(actor=actor) as conn:
            project, _, _ = self._project(conn, actor, book_id, project_id)
            basis = self._basis(conn, project)
            files = [dict(r) for r in conn.execute("SELECT * FROM migration_files f WHERE project_id=? AND " + self._active_file_sql(), (project_id,))]
        for f in files:
            self._read_blob(f)
        return basis

    def accept(self, actor, book_id, project_id, body):
        inspected_basis = self._verify_originals(actor, book_id, project_id)
        with self.db.transaction(write=True, actor=actor) as conn:
            project, target, permissions = self._project(conn, actor, book_id, project_id, "review")
            prior = conn.execute("SELECT * FROM migration_acceptances WHERE project_id=? AND request_key=?", (project_id, body.idempotency_key)).fetchone()
            if prior:
                if prior["validation_id"] != body.validation_id or prior["basis_token"] != body.basis_token or prior["accepted_by"] != actor.user_id or prior["reason"] != body.reason or prior["expected_version"] != body.expected_version:
                    raise AccountingError("验收编号已用于不同确认", 409)
                return self._detail(conn, actor, book_id, project_id)
            self._mutable(project, target)
            self._version(project, body.expected_version)
            validation = self._current_validation(conn, project, body.validation_id)
            if project["status"] != "ready" or validation["blocking_count"] != 0 or not json.loads(validation["report_json"])["coverage"].get("complete") or body.basis_token != validation["basis_token"] or inspected_basis != body.basis_token:
                raise AccountingError("须先处理所有阻断差异并重新校验，才能独立验收", 409)
            if not body.confirmed or not permissions["independent"]:
                raise AccountingError("须由另一名会计核对范围、原件、金额和映射后明确确认", 403)
            conn.execute("INSERT INTO migration_acceptances(id,project_id,validation_id,firm_id,company_id,expected_version,basis_token,request_key,accepted_by,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (uuid4().hex, project_id, validation["id"], actor.firm_id, project["company_id"], project["version"], body.basis_token, body.idempotency_key, actor.user_id, body.reason, now()))
            self._audit(conn, actor, book_id, "accept", project_id, {"validation_id": validation["id"], "reason": body.reason})
            return self._detail(conn, actor, book_id, project_id)

    def access_overview(self, actor, book_id, project_id):
        with self.db.transaction(actor=actor) as conn:
            _, _, permissions = self._project(conn, actor, book_id, project_id, "manage")
            grants = [dict(r) for r in conn.execute("SELECT a.user_id,u.name,a.role,a.reason,a.created_at FROM migration_project_access a JOIN users u ON u.id=a.user_id WHERE a.project_id=? ORDER BY a.created_at,a.user_id", (project_id,))]
        choices = self.ledgers.overview(actor, book_id)["members"]
        return {"grants": grants, "choices": choices, "can_manage": permissions["manage"]}

    def grant_access(self, actor, book_id, project_id, user_id, role, reason):
        if role not in {"viewer", "reviewer", "manager"} or not reason.strip():
            raise AccountingError("请选择权限并说明历史资料授权范围")
        with self.db.transaction(write=True, actor=actor) as conn:
            self._project(conn, actor, book_id, project_id, "manage")
            self.access.require_user(conn, user_id)
            other = Actor(user_id, actor.firm_id)
            capability = "read" if role == "viewer" else "review"
            self.access.book(conn, other, book_id, capability)
            if role == "manager":
                _, _, _, manager = self._target(conn, other, book_id)
                if not manager:
                    raise AccountingError("迁移管理还需要目标长期账套管理授权", 403)
            conn.execute("INSERT INTO migration_project_access(project_id,user_id,role,granted_by,reason,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(project_id,user_id) DO UPDATE SET role=excluded.role,granted_by=excluded.granted_by,reason=excluded.reason,created_at=excluded.created_at", (project_id, user_id, role, actor.user_id, reason, now()))
            self._audit(conn, actor, book_id, "access.grant", project_id, {"user_id": user_id, "role": role, "reason": reason})
        return self.access_overview(actor, book_id, project_id)

    def revoke_access(self, actor, book_id, project_id, user_id, reason):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._project(conn, actor, book_id, project_id, "manage")
            if not reason.strip():
                raise AccountingError("撤销授权须说明原因")
            conn.execute("DELETE FROM migration_project_access WHERE project_id=? AND user_id=?", (project_id, user_id))
            self._audit(conn, actor, book_id, "access.revoke", project_id, {"user_id": user_id, "reason": reason})
        return {"revoked": True}

    def mapping_issues(self, project, records, mapping, target_accounts=None):
        # Admission requires explicit decisions; compatible codes are suggested
        # by the UI but never silently made equivalent across two charts.
        issues = []
        account_rows = {r["payload"]["code"]: r for r in records if r["kind"] == "account"}
        aux_rows = {(r["payload"]["kind"], r["payload"]["code"]): r for r in records if r["kind"] == "auxiliary"}
        accounts = {m["source_code"]: m for m in mapping["accounts"]}
        auxiliaries = {(m["kind"], m["source_code"]): m for m in mapping["auxiliaries"]}
        for code, r in account_rows.items():
            if not r["payload"]["posting"]:
                issues.append({"code": "NONPOSTING_ACCOUNT_ARCHIVE_ONLY", "severity": "warning", "message": "来源上级科目 " + code + " 保留在历史科目树中，不创建可记账目标科目", "kind": "account", "external_id": r["external_id"], "file_id": r["file_id"], "row_number": r["row_number"]})
                continue
            if code not in accounts:
                issues.append({"code": "ACCOUNT_MAPPING_REQUIRED", "severity": "blocking", "message": "来源科目 " + code + " 尚未明确映射", "kind": "account", "external_id": r["external_id"], "file_id": r["file_id"], "row_number": r["row_number"]})
            elif accounts[code]["action"] == "create":
                from pydantic import ValidationError
                source = r["payload"]
                try:
                    AccountDefinition(code=accounts[code]["target_code"], name=source["name"], category=source["category"], normal_balance=source["normal_balance"], auxiliary_requirements=source["auxiliary_requirements"])
                except ValidationError:
                    issues.append({"code": "ACCOUNT_MAPPING_UNSUPPORTED", "severity": "blocking", "message": "来源科目 " + code + " 的名称长度或辅助维度不适用于当前目标科目，请会计整理兼容目标后重新核验", "kind": "account", "external_id": r["external_id"], "file_id": r["file_id"], "row_number": r["row_number"]})
            elif target_accounts is not None and accounts[code]["action"] == "use_existing":
                target = target_accounts.get(accounts[code]["target_code"])
                source = r["payload"]
                if not target or not target["active"] or target["category"] != source["category"] or target["normal_balance"] != source["normal_balance"] or set(json.loads(target["auxiliary_requirements"])) != set(source["auxiliary_requirements"]):
                    issues.append({"code": "ACCOUNT_MAPPING_INCOMPATIBLE", "severity": "blocking", "message": "来源科目 " + code + " 与已有目标科目的类别、余额方向或辅助维度不一致，请明确建立兼容明细科目", "kind": "account", "external_id": r["external_id"], "file_id": r["file_id"], "row_number": r["row_number"]})
        for key, r in aux_rows.items():
            if key not in auxiliaries:
                issues.append({"code": "AUXILIARY_MAPPING_REQUIRED", "severity": "blocking", "message": "来源辅助档案 " + "/".join(key) + " 尚未明确映射", "kind": "auxiliary", "external_id": r["external_id"], "file_id": r["file_id"], "row_number": r["row_number"]})
        for source_code in accounts:
            if source_code not in account_rows:
                issues.append({"code": "MAPPING_SOURCE_RETIRED", "severity": "warning", "message": "旧映射来源已不在当前资料中，本次不会应用该条映射", "kind": "account", "external_id": source_code, "row_number": 0})
        target_codes = [m["target_code"] for source_code, m in accounts.items() if source_code in account_rows and account_rows[source_code]["payload"]["posting"]]
        if len(set(target_codes)) != len(target_codes):
            issues.append({"code": "ACCOUNT_MAPPING_MERGE_UNSUPPORTED", "severity": "blocking", "message": "试点接管保留来源明细科目，不自动合并多个科目到同一目标", "kind": "account", "external_id": "", "row_number": 0})
        return issues

    def activate(self, actor, book_id, project_id, body):
        inspected_basis = self._verify_originals(actor, book_id, project_id)
        with self.db.transaction(write=True, actor=actor) as conn:
            project, target, _ = self._project(conn, actor, book_id, project_id, "manage")
            prior = conn.execute("SELECT * FROM migration_cutovers WHERE project_id=?", (project_id,)).fetchone()
            if prior:
                original = json.loads(prior["payload_json"])
                if prior["request_key"] != body.idempotency_key or prior["validation_id"] != body.validation_id or prior["created_by"] != actor.user_id or original.get("activation_request_fingerprint") != fingerprint(body.model_dump()) or not body.confirmed:
                    raise AccountingError("项目已接管，不能重新初始化；请读取原接管回执", 409)
                return self._detail(conn, actor, book_id, project_id)
            self._mutable(project, target)
            self._version(project, body.expected_version)
            validation = self._current_validation(conn, project, body.validation_id)
            if inspected_basis != body.basis_token or validation["basis_token"] != body.basis_token or validation["blocking_count"] or not body.confirmed:
                raise AccountingError("接管依据已变化或仍有阻断差异，请重新核验", 409)
            acceptance = conn.execute("SELECT * FROM migration_acceptances WHERE project_id=? AND validation_id=? AND expected_version=? AND basis_token=? ORDER BY created_at DESC,id DESC LIMIT 1", (project_id, validation["id"], project["version"], body.basis_token)).fetchone()
            if not acceptance or project["status"] != "accepted":
                raise AccountingError("须先由另一名会计验收当前报告", 409)
            if not target["empty"]:
                raise AccountingError("目标月份已存在新业务，不能再初始化接管基线", 409)
            mapping = self._mapping(conn, project_id)
            records = self._records_in_transaction(conn, project_id)
            target_accounts = {r["code"]: dict(r) for r in conn.execute("SELECT * FROM accounts WHERE book_id=?", (book_id,))}
            if any(i["severity"] == "blocking" for i in self.mapping_issues(project, records, mapping, target_accounts)):
                raise AccountingError("当前科目与辅助档案映射尚未完整", 409)
            report = json.loads(validation["report_json"])
            account_rows = {r["payload"]["code"]: r["payload"] for r in records if r["kind"] == "account"}
            aux_rows = {(r["payload"]["kind"], r["payload"]["code"]): r["payload"] for r in records if r["kind"] == "auxiliary"}
            mapping["accounts"] = [m for m in mapping["accounts"] if m["source_code"] in account_rows and account_rows[m["source_code"]]["posting"]]
            mapping["auxiliaries"] = [m for m in mapping["auxiliaries"] if (m["kind"], m["source_code"]) in aux_rows]
            master_bindings, new_entities = [], []
            for m in mapping["auxiliaries"]:
                key = (m["kind"], m["source_code"])
                source = aux_rows[key]
                if m["action"] == "use_existing":
                    entity = conn.execute("SELECT id FROM company_master_entities WHERE id=? AND firm_id=? AND company_id=? AND kind=? AND active=1", (m["target_id"], actor.firm_id, project["company_id"], m["kind"])).fetchone()
                    if not entity:
                        raise AccountingError("映射主数据已失效，请重新校验", 409)
                    identity = entity["id"]
                elif m["kind"] in {"department", "employee"}:
                    # Historical people/dimensions never become login accounts.
                    identity = "historical-" + fingerprint({"ledger": project["ledger_id"], "source_system": project["source_system_key"], "kind": m["kind"], "code": m["source_code"]})[:32]
                else:
                    entity = conn.execute("SELECT id,name FROM company_master_entities WHERE firm_id=? AND company_id=? AND kind=? AND code=?", (actor.firm_id, project["company_id"], m["kind"], m["source_code"])).fetchone()
                    if entity:
                        raise AccountingError("主数据编码已存在，请明确选择使用已有档案后重新校验", 409)
                    identity = uuid4().hex
                    new_entities.append((identity, source, m["kind"]))
                master_bindings.append({"kind": m["kind"], "source_code": m["source_code"], "target_id": identity})
            snapshot = {"schema": "nexus.legacy.cutover/v1", "project_id": project_id, "ledger_id": project["ledger_id"], "book_id": book_id,
                        "cutover_date": project["cutover_date"], "currency": "CNY", "official_ledger": "source_software",
                        "opening_is_reference": True, "automatic_export": False, "activation_request_fingerprint": fingerprint(body.model_dump()), "account_bindings": mapping["accounts"], "master_bindings": master_bindings,
                        "closing_balances": [{"record_id": r["id"], **r["payload"]} for r in records if r["kind"] == "balance" and r["period"] == project["history_end"][:7]],
                        "open_item_bindings": report.get("open_item_bindings", []), "counts": report["summary"]["counts"], "source_controls_verified": report["coverage"].get("source_controls_verified", False)}
            identity, stamp = uuid4().hex, now()
            # Native admission compares the old frozen basis first. All target
            # catalog effects below share this transaction and rollback together.
            conn.execute("INSERT INTO migration_cutovers(id,project_id,validation_id,acceptance_id,book_id,firm_id,company_id,payload_json,request_key,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (identity, project_id, validation["id"], acceptance["id"], book_id, actor.firm_id, project["company_id"], encode(snapshot), body.idempotency_key, actor.user_id, stamp))
            for m in mapping["accounts"]:
                source = account_rows[m["source_code"]]
                if m["action"] == "create":
                    definition = AccountDefinition(code=m["target_code"], name=source["name"], category=source["category"], normal_balance=source["normal_balance"], auxiliary_requirements=source["auxiliary_requirements"])
                    self.catalog._save_account(conn, actor, book_id, definition)
                elif not conn.execute("SELECT 1 FROM accounts WHERE book_id=? AND code=? AND active=1", (book_id, m["target_code"])).fetchone():
                    raise AccountingError("当前科目已变化，接管整批撤销", 409)
            for entity_id, source, kind in new_entities:
                conn.execute("INSERT INTO company_master_entities(id,firm_id,company_id,kind,code,name,recorded_book_id,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (entity_id, actor.firm_id, project["company_id"], kind, source["code"], source["name"], book_id, actor.user_id, stamp))
                self.ledgers._history(conn, actor, {"id": book_id, "company_id": project["company_id"]}, entity_id, "master.entity.create", {"kind": kind, "code": source["code"], "name": source["name"], "migration_cutover_id": identity})
            settlements = getattr(self, "settlements", None)
            if report.get("open_item_bindings"):
                if not settlements:
                    raise AccountingError("历史未结接管服务尚未启用，基线不会部分发布", 503)
                settlements.capture_cutover(conn, actor, project, report, mapping, records)
            self._audit(conn, actor, book_id, "activate", project_id, {"cutover_id": identity, "validation_id": validation["id"], "reason": body.reason, "current_sources_created": 0, "current_exports_created": 0})
            return self._detail(conn, actor, book_id, project_id)

    def template(self, actor, book_id, external_book_id, history_start=None, history_end=None, cutover_date=None):
        import calendar
        from datetime import date
        from .models import ProjectCreate
        with self.db.transaction(actor=actor) as conn:
            _, _, target, _ = self._target(conn, actor, book_id)
        cutover_date = cutover_date or target["period"] + "-01"
        end = date.fromisoformat(cutover_date) - timedelta(days=1)
        history_end = history_end or end.isoformat()
        history_start = history_start or history_end[:7] + "-01"
        context = {"external_book_id": external_book_id, "currency": "CNY", "history_start": history_start, "history_end": history_end, "cutover_date": cutover_date}
        ProjectCreate(target_book_id=book_id, source_system_key="SYNTHETIC", source_product="generic", product_version="SYNTHETIC-EXAMPLE", idempotency_key="synthetic-template-only", reason="明确合成示例，不是客户软件导出", **context)
        records = []
        chart = [("1002", "合成银行存款", "asset", "debit", []), ("2001", "合成借款", "liability", "credit", []), ("6602.02", "合成办公费用", "expense", "debit", ["counterparty"])]
        for code, name, category, side, aux in chart:
            records.append({"kind": "account", "external_id": code, "period": history_start[:7], "payload": {"code": code, "name": name, "parent_code": "", "posting": True, "category": category, "normal_balance": side, "auxiliary_requirements": aux}})
        records.append({"kind": "auxiliary", "external_id": "SYNTHETIC-PARTY", "period": history_start[:7], "payload": {"kind": "counterparty", "code": "SYNTHETIC-PARTY", "name": "合成办公供应商"}})
        month = history_start[:7]
        occurrence = 0
        from .normalization import signed_money
        while month <= history_end[:7]:
            occurrence += 1
            records.append({"kind": "voucher", "external_id": "SYNTHETIC-V-" + month, "period": month, "payload": {"external_id": "SYNTHETIC-V-" + month, "date": month + "-15", "period": month, "voucher_type": "记", "number": "001", "status": "posted", "is_reversal": False, "lines": [
                {"ordinal": 1, "account_code": "6602.02", "description": "合成办公采购，仅演示", "debit": "0.30", "credit": "0.00", "auxiliaries": {"counterparty": "SYNTHETIC-PARTY"}, "original_currency": "CNY"},
                {"ordinal": 2, "account_code": "1002", "description": "合成银行付款，仅演示", "debit": "0.00", "credit": "0.30", "auxiliaries": {}, "original_currency": "CNY"}]}})
            for code, opening, debit, credit, closing, aux in (("1002", 10000 - (occurrence - 1) * 30, 0, 30, 10000 - occurrence * 30, {}), ("2001", -10000, 0, 0, -10000, {}), ("6602.02", (occurrence - 1) * 30, 30, 0, occurrence * 30, {"counterparty": "SYNTHETIC-PARTY"})):
                records.append({"kind": "balance", "external_id": "SYNTHETIC-B-" + month + "-" + code, "period": month, "payload": {"account_code": code, "auxiliaries": aux, "opening": signed_money(opening), "period_debit": signed_money(debit), "period_credit": signed_money(credit), "closing": signed_money(closing), "scope": "posted"}})
            year, number = map(int, month.split("-"))
            month = f"{year + (number == 12):04d}-{1 if number == 12 else number + 1:02d}"
            if occurrence > 120:
                raise AccountingError("合成模板最多十年，请缩小演示范围")
        counts = {kind: sum(r["kind"] == kind for r in records) for kind in KINDS}
        return {"schema": "nexus.legacy/v1", "synthetic": True, "notice": "SYNTHETIC：不是T+或其他软件的原生导出。仅供隔离环境演示。", "context": context, "controls": {"record_counts": counts, "voucher_line_count": occurrence * 2, "posted_voucher_count": occurrence, "posted_debit": signed_money(occurrence * 30), "posted_credit": signed_money(occurrence * 30)}, "records": records}
