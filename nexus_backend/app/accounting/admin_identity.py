"""Durable, scoped invitations and new employee password provisioning.

Supabase operations run outside DB transactions. Activation secrets are returned
once and only their digests are persisted. Initial passwords exist only for the
duration of a request; neither passwords nor their hashes are persisted locally.
"""

import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode
from uuid import uuid4
from pydantic import ValidationError

from app.auth.admin import SupabaseAdmin
from app.auth.supabase import Identity

from .access import AccessControl, FIRM_MANAGERS
from .directory import DirectoryService
from .domain import Actor, AccountingError
from .service import now
from .admin_identity_models import AccountRequestCreate


class AdminIdentityService:
    def __init__(self, db, provider=None):
        self.db = db
        self.provider = provider if provider is not None else SupabaseAdmin.from_env()
        self.access = AccessControl()
        self.directory = DirectoryService(db)

    @staticmethod
    def _public(row):
        fields = (
            "id",
            "name",
            "email",
            "company_id",
            "company_role",
            "department_id",
            "firm_role",
            "provisioning_mode",
            "status",
            "attempts",
            "error",
            "created_at",
            "updated_at",
            "expires_at",
            "completed_by",
        )
        result = {key: row[key] for key in fields}
        result["existing_user"] = bool(row["existing_user"])
        result["activation_expired"] = row["status"] == "awaiting_activation" and row[
            "expires_at"
        ] <= int(time.time())
        return result

    def preview_accounts(self, actor, items):
        """Read-only bounded readiness; create/accept still reauthorize individually."""
        if not isinstance(items, list) or not 1 <= len(items) <= 50:
            raise AccountingError("每批预检须为 1–50 人", 422)
        rows, seen, companies = [], set(), set()
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            for index, raw in enumerate(items):
                row = {"index": index, "ready": False, "book_access": "none"}
                try:
                    request = AccountRequestCreate.model_validate(raw)
                    payload = request.model_dump(exclude={"initial_password"})
                    if (
                        not request.company_id
                        or request.company_role not in {"employee", "finance"}
                        or request.provisioning_mode != "activation"
                    ):
                        raise AccountingError(
                            "批量入职只支持本企业员工或企业财务的本人激活；其他授权请单独办理", 422
                        )
                    self._authorize(conn, actor, payload)
                    companies.add(request.company_id)
                    if len(companies) != 1:
                        raise AccountingError("每批只能办理一家企业", 422)
                    row.update(
                        email=request.email,
                        name=request.name,
                        company_id=request.company_id,
                        company_role=request.company_role,
                        department_id=request.department_id,
                    )
                    if request.email in seen:
                        row.update(code="duplicate_email", message="名单中邮箱重复，请保留一行")
                    else:
                        seen.add(request.email)
                        pending = conn.execute(
                            "SELECT id FROM admin_account_requests WHERE firm_id=? AND company_id=? AND email=? "
                            "AND (status='pending' OR (status='awaiting_activation' AND expires_at>?)) LIMIT 1",
                            (actor.firm_id, request.company_id, request.email, int(time.time())),
                        ).fetchone()
                        if pending:
                            row.update(
                                code="existing_request",
                                message="已有待完成邀请，请先查看开通记录，勿重复生成",
                            )
                            rows.append(row)
                            continue
                        # Both activation and password onboarding need the same department readiness.
                        self._validate_password_department(
                            conn, actor, {**payload, "provisioning_mode": "password"}
                        )
                        row.update(
                            ready=True,
                            code="ready",
                            message="可生成激活邀请；不授予账套、复核或导出权限",
                        )
                except ValidationError:
                    row.update(
                        code="invalid_fields", message="姓名、邮箱或角色格式不正确，请核对本行"
                    )
                except AccountingError as failure:
                    row.update(code="not_ready", message=str(failure))
                rows.append(row)
        return {
            "items": rows,
            "ready": all(row["ready"] for row in rows),
            "provisioning_enabled": self.provider.configured,
        }

    def _authorize(self, conn, actor, target):
        self.access.require_user(conn, actor.user_id)
        if target["company_id"]:
            self.access.company(conn, actor, target["company_id"], manage=True)
            if (
                target["company_role"] == "owner"
                and self.access.firm_role(conn, actor) not in FIRM_MANAGERS
            ):
                raise AccountingError("企业所有者只能由事务所负责人授权", 403)
            if (
                target["department_id"]
                and not conn.execute(
                    "SELECT 1 FROM departments WHERE id=? AND company_id=?",
                    (target["department_id"], target["company_id"]),
                ).fetchone()
            ):
                raise AccountingError("部门不属于当前企业", 422)
        else:
            self.access.require_manager(conn, actor)
        creator = conn.execute("SELECT email FROM users WHERE id=?", (actor.user_id,)).fetchone()
        if creator["email"].lower() == target["email"]:
            raise AccountingError("不能通过开户邀请变更自己的权限", 403)

    @staticmethod
    def _validate_password_department(conn, actor, target):
        # This is provisioning readiness, not scope authorization. A policy
        # change must never prevent viewing/cancelling an old failed request or
        # replaying an already completed request without restoring its grant.
        if (
            target["provisioning_mode"] == "password"
            and target["company_role"] == "employee"
            and not target["department_id"]
        ):
            policy = conn.execute(
                "SELECT mode FROM business_submission_policies WHERE firm_id=? AND company_id=?",
                (actor.firm_id, target["company_id"]),
            ).fetchone()
            if not policy or policy["mode"] == "department":
                raise AccountingError("部门审批模式下请先建立部门，并为员工分配所属部门后开户", 422)

    def _get(self, conn, actor, request_id):
        row = conn.execute(
            "SELECT * FROM admin_account_requests WHERE id=? AND firm_id=?",
            (request_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("开户请求不存在或不可访问", 404)
        self._authorize(conn, actor, row)
        return row

    def _originator_authorized(self, actor, row):
        if row["created_by"] == actor.user_id:
            return  # _get already checked this identity in the current transaction.
        originator = Actor(row["created_by"], row["firm_id"])
        try:
            # Explicit original actor scope is necessary: the current company
            # administrator cannot inspect another person's firm memberships.
            with self.db.transaction(actor=originator) as conn:
                self._authorize(conn, originator, row)
        except AccountingError:
            raise AccountingError("开户发起人的授权已失效，请当前管理员新建邀请", 403) from None

    def list_requests(self, actor, company_id=None, limit=50, offset=0):
        with self.db.transaction(actor=actor) as conn:
            if company_id:
                self.access.company(conn, actor, company_id, manage=True)
            else:
                self.access.require_manager(conn, actor)
            sql = "SELECT * FROM admin_account_requests WHERE firm_id=?"
            params = [actor.firm_id]
            if company_id:
                sql += " AND company_id=?"
                params.append(company_id)
            rows = conn.execute(
                sql + " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (*params, limit, offset)
            ).fetchall()
            return {
                "requests": [self._public(row) for row in rows],
                "provisioning_enabled": self.provider.configured,
            }

    def create(self, actor, request):
        payload = request.model_dump(exclude={"idempotency_key", "initial_password"})
        fingerprint_payload = {
            key: value
            for key, value in payload.items()
            if key != "provisioning_mode" or value != "activation"
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        with self.db.transaction(write=True, actor=actor) as conn:
            self._authorize(conn, actor, payload)
            prior = conn.execute(
                "SELECT * FROM admin_account_requests WHERE firm_id=? AND created_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise AccountingError("同一开户请求编号不能用于不同人员或权限", 409)
                result = {"request": self._public(prior)}
                if prior["provisioning_mode"] == "password" and prior["status"] == "completed":
                    result["password_already_created"] = True
                return result
            self._validate_password_department(conn, actor, payload)
            request_id = uuid4().hex
            timestamp = now()
            conn.execute(
                "INSERT INTO admin_account_requests(id,firm_id,created_by,idempotency_key,fingerprint,name,email,"
                "company_id,company_role,department_id,firm_role,provisioning_mode,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)",
                (
                    request_id,
                    actor.firm_id,
                    actor.user_id,
                    request.idempotency_key,
                    fingerprint,
                    request.name,
                    request.email,
                    request.company_id,
                    request.company_role,
                    request.department_id,
                    request.firm_role,
                    request.provisioning_mode,
                    timestamp,
                    timestamp,
                ),
            )
            self.directory._audit(
                conn,
                actor,
                "admin.account.request",
                request_id,
                {
                    "company_id": request.company_id,
                    "email": request.email,
                    "role": request.company_role or request.firm_role,
                    "provisioning_mode": request.provisioning_mode,
                },
            )
        return self.retry(actor, request_id, request.initial_password)

    def retry(self, actor, request_id, initial_password=None):
        secret = secrets.token_urlsafe(32)
        digest = hashlib.sha256(secret.encode()).hexdigest()
        lease = uuid4().hex
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._get(conn, actor, request_id)
            if row["status"] == "completed":
                response = {"request": self._public(row)}
                if row["provisioning_mode"] == "password":
                    response["password_already_created"] = True
                return response
            if row["status"] == "cancelled":
                raise AccountingError("邀请已撤销，请创建新的开户请求", 409)
            self._originator_authorized(actor, row)
            self._validate_password_department(conn, actor, row)
            password_mode = row["provisioning_mode"] == "password"
            if password_mode and initial_password is None:
                raise AccountingError(
                    "请重新输入初始密码后重试；此前已创建的账号仍沿用首次密码", 422
                )
            if not password_mode and initial_password is not None:
                raise AccountingError("激活邀请不能携带初始密码", 422)
            if row["lease_until"] > int(time.time()):
                return {"request": self._public(row)}
            conn.execute(
                "UPDATE admin_account_requests SET status='pending',attempts=attempts+1,error='',"
                "token_digest=?,expires_at=?,lease_token=?,lease_until=?,updated_at=? WHERE id=?",
                (
                    "" if password_mode else digest,
                    0 if password_mode else int(time.time()) + 3600,
                    lease,
                    int(time.time()) + 180,
                    now(),
                    request_id,
                ),
            )
            original = dict(row)
        try:
            if password_mode:
                password = initial_password.get_secret_value()
                try:
                    result = self.provider.provision_password(
                        original["email"], original["name"], request_id, password
                    )
                finally:
                    del password
            else:
                result = self.provider.provision(original["email"], original["name"], request_id)
        except AccountingError as error:
            return self._finish(actor, request_id, lease, error=error.detail)
        except Exception:
            return self._finish(
                actor, request_id, lease, error="开户暂未完成，可安全重试；尚未授予权限"
            )
        if password_mode:
            return self._finish_password(actor, request_id, lease, original, result)
        response = self._finish(actor, request_id, lease, result=result)
        if response.pop("_issued", False):
            fragment = {
                "request_id": request_id,
                "token": secret,
                "mode": "existing" if result["existing_user"] else "invite",
            }
            if not result["existing_user"]:
                fragment["token_hash"] = result["token_hash"]
            response["activation_url"] = (
                self.provider.public_app_url + "/account-activation#" + urlencode(fragment)
            )
        return response

    def _finish_password(self, actor, request_id, lease, original, result):
        # Provider verified this new identity; a local profile alone carries no
        # company or ledger grant. Recheck both administrators after the remote
        # call, then publish the grant and terminal request in one transaction.
        self.directory.register_identity(
            Identity(result["user_id"], original["email"], original["name"])
        )
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._get(conn, actor, request_id)
            if (
                row["lease_token"] != lease
                or row["status"] != "pending"
                or row["lease_until"] <= int(time.time())
            ):
                response = {"request": self._public(row)}
                if row["status"] == "completed":
                    response["password_already_created"] = True
                return response
            self._originator_authorized(actor, row)
            if row["provisioning_mode"] != "password" or result["existing_user"]:
                raise AccountingError("开户方式或身份不匹配，尚未授予企业权限", 409)
            self._validate_password_department(conn, actor, row)
            self._grant(conn, actor, row, result["user_id"])
            conn.execute(
                "UPDATE admin_account_requests SET status='completed',provider_user_id=?,completed_by=?,"
                "existing_user=0,error='',lease_token='',lease_until=0,updated_at=? WHERE id=?",
                (result["user_id"], result["user_id"], now(), request_id),
            )
            self.directory._audit(
                conn,
                actor,
                "admin.account.password_created",
                request_id,
                {
                    "company_id": row["company_id"],
                    "created_user_id": result["user_id"],
                    "role": row["company_role"],
                    "department_id": row["department_id"],
                    "password_already_created": bool(result.get("password_already_created")),
                },
            )
            return {
                "request": self._public(
                    conn.execute(
                        "SELECT * FROM admin_account_requests WHERE id=?", (request_id,)
                    ).fetchone()
                ),
                "password_already_created": bool(result.get("password_already_created")),
            }

    def _finish(self, actor, request_id, lease, result=None, error=None):
        # Authorization may have been revoked while Auth was processing. In that
        # case no local grant/activation link is produced; the lease can expire.
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._get(conn, actor, request_id)
            if row["lease_token"] != lease or row["status"] != "pending":
                return {"request": self._public(row)}
            self._originator_authorized(actor, row)
            conn.execute(
                "UPDATE admin_account_requests SET status=?,provider_user_id=?,existing_user=?,error=?,"
                "lease_token='',lease_until=0,updated_at=? WHERE id=?",
                (
                    "failed" if error else "awaiting_activation",
                    result["user_id"] if result else row["provider_user_id"],
                    int(result["existing_user"]) if result else row["existing_user"],
                    error or "",
                    now(),
                    request_id,
                ),
            )
            self.directory._audit(
                conn,
                actor,
                "admin.account.failed" if error else "admin.account.link_created",
                request_id,
                {"company_id": row["company_id"]},
            )
            response = {
                "request": self._public(
                    conn.execute(
                        "SELECT * FROM admin_account_requests WHERE id=?", (request_id,)
                    ).fetchone()
                )
            }
            if not error:
                response["_issued"] = True
            return response

    def cancel(self, actor, request_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._get(conn, actor, request_id)
            if row["status"] == "completed":
                raise AccountingError("开户已完成，请在成员管理中撤销相应授权", 409)
            if row["status"] == "cancelled":
                return {"request": self._public(row)}
            conn.execute(
                "UPDATE admin_account_requests SET status='cancelled',token_digest='',lease_token='',lease_until=0,updated_at=? WHERE id=?",
                (now(), request_id),
            )
            self.directory._audit(
                conn, actor, "admin.account.cancel", request_id, {"company_id": row["company_id"]}
            )
            return {
                "request": self._public(
                    conn.execute(
                        "SELECT * FROM admin_account_requests WHERE id=?", (request_id,)
                    ).fetchone()
                )
            }

    @staticmethod
    def _verify_recipient(row, identity, token):
        valid = (
            row
            and row["provisioning_mode"] == "activation"
            and row["provider_user_id"] == identity.user_id
            and row["email"] == (identity.email or "").lower()
            and hmac.compare_digest(row["token_digest"], hashlib.sha256(token.encode()).hexdigest())
        )
        if not valid:
            raise AccountingError("邀请无效或与当前登录账号不匹配", 403)

    def accept(self, identity, request_id, token):
        # Restricted identity role can read request metadata, but cannot grant or
        # mutate requests. Supabase identity verification happens before this call.
        with self.db.transaction(system=True) as conn:
            row = conn.execute(
                "SELECT * FROM admin_account_requests WHERE id=?", (request_id,)
            ).fetchone()
            self._verify_recipient(row, identity, token)
            original = dict(row)
        self.directory.register_identity(identity)
        actor = Actor(original["created_by"], original["firm_id"])
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._get(conn, actor, request_id)
            self._verify_recipient(row, identity, token)
            if row["status"] == "completed":
                # Retrying acceptance never recreates a subsequently revoked grant.
                return {
                    "completed": True,
                    "firm_id": row["firm_id"],
                    "company_id": row["company_id"],
                }
            if row["status"] != "awaiting_activation" or row["expires_at"] <= int(time.time()):
                raise AccountingError("邀请已失效，请管理员重新生成激活链接", 409)
            self._grant(conn, actor, row, identity.user_id)
            conn.execute(
                "UPDATE admin_account_requests SET status='completed',completed_by=?,updated_at=? WHERE id=?",
                (identity.user_id, now(), request_id),
            )
            self.directory._audit(
                conn,
                actor,
                "admin.account.accept",
                request_id,
                {
                    "company_id": row["company_id"],
                    "accepted_by": identity.user_id,
                    "role": row["company_role"] or row["firm_role"],
                },
            )
            return {"completed": True, "firm_id": row["firm_id"], "company_id": row["company_id"]}

    def _grant(self, conn, actor, row, user_id):
        self.access.require_user(conn, user_id)
        if row["company_id"]:
            if self.access.firm_role(conn, actor) not in FIRM_MANAGERS:
                if conn.execute(
                    "SELECT 1 FROM memberships WHERE firm_id=? AND user_id=?",
                    (actor.firm_id, user_id),
                ).fetchone():
                    raise AccountingError("事务所成员的企业身份由事务所负责人管理", 403)
            existing = conn.execute(
                "SELECT role FROM company_memberships WHERE company_id=? AND user_id=?",
                (row["company_id"], user_id),
            ).fetchone()
            if existing:
                raise AccountingError("该账号已是企业成员，请在成员管理中调整权限", 409)
            conn.execute(
                "INSERT INTO company_memberships(company_id,user_id,role,department_id) VALUES(?,?,?,?)",
                (row["company_id"], user_id, row["company_role"], row["department_id"]),
            )
        else:
            if conn.execute(
                "SELECT 1 FROM memberships WHERE firm_id=? AND user_id=?", (actor.firm_id, user_id)
            ).fetchone():
                raise AccountingError("该账号已是事务所成员，请在成员管理中调整权限", 409)
            conn.execute(
                "INSERT INTO memberships(firm_id,user_id,role) VALUES(?,?,?)",
                (actor.firm_id, user_id, row["firm_role"]),
            )
