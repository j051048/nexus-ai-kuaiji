"""Identity profiles and explicit firm/company/book assignments."""

from uuid import uuid4

from .access import FIRM_REVIEWERS, AccessControl, FIRM_MANAGERS, COMPANY_LEDGER_ROLES
from .domain import Actor, AccountingError
from .models import (
    CompanyCreate,
    DepartmentCreate,
    FirmCreate,
    FirmMemberGrant,
    CompanyMemberGrant,
    BookMemberGrant,
)
from .service import now


class DirectoryService:
    def __init__(self, db):
        self.db = db
        self.access = AccessControl()

    def register_identity(self, identity):
        # Called only after the configured authentication provider has verified identity.
        email = (identity.email or "").lower()
        with self.db.transaction(system=True) as conn:
            existing = conn.execute(
                "SELECT active,name,email FROM users WHERE id=?", (identity.user_id,)
            ).fetchone()
            if existing and not existing["active"]:
                raise AccountingError("账号已停用，请联系管理员", 403)
            if (
                existing
                and existing["name"] == identity.display_name
                and existing["email"] == email
            ):
                # Ordinary /me reads must not queue behind accounting writes.
                # The following profile/domain request still checks live grants;
                # changed or new identities recheck active state under the lock.
                return
        with self.db.transaction(write=True, system=True) as conn:
            existing = conn.execute(
                "SELECT active FROM users WHERE id=?", (identity.user_id,)
            ).fetchone()
            if existing and not existing["active"]:
                raise AccountingError("账号已停用，请联系管理员", 403)
            conn.execute(
                "INSERT INTO users(id,name,email) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,email=excluded.email",
                (identity.user_id, identity.display_name, email),
            )

    def me(self, user_id, firm_id=None, mode="supabase"):
        with self.db.transaction(actor=Actor(user_id, "")) as conn:
            return self.me_in_connection(conn, user_id, firm_id, mode)

    def me_in_connection(self, conn, user_id, firm_id=None, mode="supabase", *, identity=None):
        """Read live identity/grants in the caller's restricted read snapshot.

        A provider-verified changed/new identity returns None so the caller can
        leave the read transaction before synchronizing it. Disabled users are
        always rejected, never treated as new registrations.
        """
        if identity is not None and identity.user_id != user_id:
            raise ValueError("身份与当前读取账号不一致")
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if user and not user["active"]:
            raise AccountingError("账号未激活或已停用", 403)
        if identity is not None and (
            not user
            or user["name"] != identity.display_name
            or user["email"] != (identity.email or "").lower()
        ):
            return None
        if not user:
            raise AccountingError("账号未激活或已停用", 403)
        # Select available workspaces and their management capability together.
        # Memberships keep their existing precedence over client-only access.
        firms = conn.execute(
            "WITH workspaces AS ("
            "SELECT f.id,f.name,m.role,0 AS priority FROM firms f "
            "JOIN memberships m ON m.firm_id=f.id WHERE m.user_id=? "
            "UNION ALL SELECT DISTINCT f.id,f.name,'client_employee',1 FROM firms f "
            "JOIN firm_clients fc ON fc.firm_id=f.id AND fc.status='active' "
            "JOIN company_memberships cm ON cm.company_id=fc.company_id WHERE cm.user_id=?"
            "), ranked AS (SELECT workspaces.*,ROW_NUMBER() OVER(PARTITION BY id ORDER BY priority) AS ordinal FROM workspaces) "
            "SELECT r.id,r.name,r.role,EXISTS(SELECT 1 FROM company_memberships cm "
            "JOIN firm_clients fc ON fc.company_id=cm.company_id WHERE fc.firm_id=r.id "
            "AND fc.status='active' AND cm.user_id=? AND cm.role IN ('owner','admin')) AS company_admin "
            "FROM ranked r WHERE ordinal=1 ORDER BY r.priority,r.name,r.id",
            (user_id, user_id, user_id),
        ).fetchall()
        available = {row["id"]: {key: row[key] for key in ("id", "name", "role")} for row in firms}
        if firm_id is not None and firm_id not in available:
            raise AccountingError("未获得该事务所工作空间授权", 403)
        selected = firm_id if firm_id is not None else next(iter(available), None)
        role = available[selected]["role"] if selected else None
        manager = role in FIRM_MANAGERS
        company_admin = any(row["id"] == selected and row["company_admin"] for row in firms)
        # Affiliation is an actual membership, not a client visible through a
        # book grant. Keep it in the same restricted identity snapshot and firm.
        company_memberships = []
        if selected:
            # /me starts with no selected firm. Company-name RLS needs the
            # verified workspace, then the caller's scope must be restored.
            previous_firm = None
            if self.db.is_postgres:
                previous_firm = conn.execute(
                    "SELECT current_setting('app.firm_id',true)"
                ).fetchone()[0]
                conn.execute("SELECT set_config('app.firm_id',?,true)", (selected,))
            company_memberships = conn.execute(
                "SELECT c.id,c.name,cm.role FROM company_memberships cm "
                "JOIN companies c ON c.id=cm.company_id AND c.status='active' "
                "JOIN firm_clients fc ON fc.company_id=c.id AND fc.firm_id=? "
                "AND fc.status='active' WHERE cm.user_id=? ORDER BY c.name,c.id",
                (selected, user_id),
            ).fetchall()
            if self.db.is_postgres:
                conn.execute("SELECT set_config('app.firm_id',?,true)", (previous_firm or "",))
            # A SQL failure aborts the enclosing transaction; Database rolls
            # back its local settings before returning the connection to pool.
        return {
            "mode": mode,
            "user": {"id": user["id"], "name": user["name"], "email": user["email"]},
            "firms": list(available.values()),
            "current_firm_id": selected,
            "company_memberships": [dict(row) for row in company_memberships],
            "permissions": {
                "manage_firm": manager,
                "manage_admin": manager or company_admin,
                "create_company": manager,
                "create_book": manager,
            },
        }

    def resolve_actor(self, user_id, firm_id=None):
        """Resolve current authorization in one RLS-scoped read, without a profile.

        Preserve /me's default order: firm memberships first, then client firms,
        each ordered by name/id. Never cache this result across requests.
        """
        with self.db.transaction(actor=Actor(user_id, "")) as conn:
            row = conn.execute(
                "WITH workspaces AS ("
                "SELECT f.id,f.name,0 AS priority FROM firms f "
                "JOIN memberships m ON m.firm_id=f.id WHERE m.user_id=? "
                "UNION ALL "
                "SELECT DISTINCT f.id,f.name,1 AS priority FROM firms f "
                "JOIN firm_clients fc ON fc.firm_id=f.id AND fc.status='active' "
                "JOIN company_memberships cm ON cm.company_id=fc.company_id WHERE cm.user_id=?"
                ") SELECT u.id AS user_id,w.id AS firm_id FROM users u "
                "LEFT JOIN workspaces w ON (CAST(? AS TEXT) IS NULL OR w.id=?) "
                "WHERE u.id=? AND u.active=1 ORDER BY w.priority,w.name,w.id LIMIT 1",
                (user_id, user_id, firm_id, firm_id, user_id),
            ).fetchone()
            if not row:
                raise AccountingError("账号未激活或已停用", 403)
            if not row["firm_id"]:
                if firm_id is not None:
                    raise AccountingError("未获得该事务所工作空间授权", 403)
                raise AccountingError("尚未获得事务所或客户企业授权，请创建事务所或联系管理员", 403)
            return Actor(user_id, row["firm_id"])

    def create_firm(self, user_id, request: FirmCreate):
        actor = Actor(user_id, "")
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_user(conn, user_id)
            if (
                conn.execute(
                    "SELECT COUNT(*) FROM memberships WHERE user_id=? AND role='owner'", (user_id,)
                ).fetchone()[0]
                >= 10
            ):
                raise AccountingError("当前账号创建的事务所数量已达验证上限", 409)
            firm_id = uuid4().hex
            conn.execute(
                "INSERT INTO firms(id,name,created_by) VALUES(?,?,?)",
                (firm_id, request.name, user_id),
            )
            conn.execute(
                "INSERT INTO memberships(firm_id,user_id,role) VALUES(?,?,'owner')",
                (firm_id, user_id),
            )
            if self.db.is_postgres:
                conn.execute("SELECT set_config('app.firm_id',?,true)", (firm_id,))
            self._audit(
                conn, Actor(user_id, firm_id), "firm.create", firm_id, {"name": request.name}
            )
            return {"id": firm_id, "name": request.name, "role": "owner"}

    @staticmethod
    def _audit(conn, actor, action, object_id, details, book_id=None):
        import json

        company_id = details.get("company_id")
        if (
            company_id is None
            and action.startswith("company.")
            and action in {"company.create", "company.update", "company.status"}
        ):
            company_id = object_id
        if company_id is None and book_id:
            book = conn.execute(
                "SELECT company_id FROM books WHERE id=? AND firm_id=?", (book_id, actor.firm_id)
            ).fetchone()
            company_id = book["company_id"] if book else None
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at,company_id) VALUES(?,?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book_id,
                actor.user_id,
                action,
                object_id,
                json.dumps(details, ensure_ascii=False),
                now(),
                company_id,
            ),
        )

    def _find_user(self, email):
        # Identity role can read only identity profiles; permission has already been checked.
        with self.db.transaction(system=True) as conn:
            users = conn.execute(
                "SELECT id,name,email FROM users WHERE lower(email)=? AND active=1",
                (email.lower(),),
            ).fetchall()
            if len(users) != 1:
                raise AccountingError("没有找到该邮箱的有效账号，请让对方先登录本系统一次", 404)
            return users[0]

    def list_companies(self, actor):
        with self.db.transaction(actor=actor) as conn:
            firm_role = self.access.firm_role(conn, actor)
            rows = conn.execute(
                "SELECT DISTINCT c.* FROM companies c JOIN firm_clients fc ON fc.company_id=c.id "
                "WHERE fc.firm_id=? AND fc.status='active' ORDER BY c.name,c.id",
                (actor.firm_id,),
            ).fetchall()
            result = []
            for company in rows:
                try:
                    self.access.company(conn, actor, company["id"])
                except AccountingError as error:
                    if error.status in {403, 404}:
                        continue
                    raise
                result.append(
                    {
                        **dict(company),
                        "can_manage": firm_role in FIRM_MANAGERS
                        or self.access.company_role(conn, actor, company["id"])
                        in {"owner", "admin"},
                    }
                )
            return {"companies": result}

    def create_company(self, actor, request: CompanyCreate):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            company_id = uuid4().hex
            conn.execute(
                "INSERT INTO companies(id,name,tax_id) VALUES(?,?,?)",
                (company_id, request.name, request.tax_id),
            )
            conn.execute(
                "INSERT INTO firm_clients(firm_id,company_id,status) VALUES(?,?,'active')",
                (actor.firm_id, company_id),
            )
            from .ai_defaults import ensure_company_ai_default

            ensure_company_ai_default(conn, actor, company_id)
            self._audit(
                conn,
                actor,
                "company.create",
                company_id,
                {
                    **request.model_dump(),
                    "company_id": company_id,
                    "before": None,
                    "after": {
                        "id": company_id,
                        **request.model_dump(),
                        "status": "active",
                        "version": 1,
                    },
                    "reason": "管理员新建客户企业",
                },
            )
            return {
                "id": company_id,
                "name": request.name,
                "tax_id": request.tax_id,
                "can_manage": True,
                "status": "active",
                "version": 1,
                "can_change_status": True,
            }

    def list_firm_members(self, actor):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            rows = conn.execute(
                "SELECT m.user_id,u.name,u.email,m.role FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.firm_id=? ORDER BY u.name,u.id",
                (actor.firm_id,),
            ).fetchall()
            return {"members": [dict(row) for row in rows]}

    def grant_firm_member(self, actor, request: FirmMemberGrant):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            user = self._find_user(request.email)
            existing = conn.execute(
                "SELECT role FROM memberships WHERE firm_id=? AND user_id=?",
                (actor.firm_id, user["id"]),
            ).fetchone()
            if existing and existing["role"] == "owner":
                raise AccountingError("所有者角色不可在普通授权入口变更", 409)
            if user["id"] == actor.user_id:
                raise AccountingError("请由其他所有者管理当前账号职责，避免自我锁定", 409)
            conn.execute(
                "INSERT INTO memberships(firm_id,user_id,role) VALUES(?,?,?) ON CONFLICT(firm_id,user_id) DO UPDATE SET role=excluded.role",
                (actor.firm_id, user["id"], request.role),
            )
            self._audit(
                conn,
                actor,
                "firm.member.grant",
                user["id"],
                {
                    "role": request.role,
                    "before": dict(existing) if existing else None,
                    "after": {"role": request.role},
                    "reason": "管理员设置事务所成员职责",
                },
            )
            return {
                "user_id": user["id"],
                "name": user["name"],
                "email": user["email"],
                "role": request.role,
            }

    def revoke_firm_member(self, actor, user_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            existing = conn.execute(
                "SELECT role FROM memberships WHERE firm_id=? AND user_id=?",
                (actor.firm_id, user_id),
            ).fetchone()
            if not existing:
                raise AccountingError("成员不存在", 404)
            if existing["role"] == "owner" or user_id == actor.user_id:
                raise AccountingError("不可撤销所有者或当前账号", 409)
            conn.execute(
                "DELETE FROM book_access WHERE firm_id=? AND user_id=?", (actor.firm_id, user_id)
            )
            conn.execute(
                "DELETE FROM memberships WHERE firm_id=? AND user_id=?", (actor.firm_id, user_id)
            )
            self._audit(
                conn,
                actor,
                "firm.member.revoke",
                user_id,
                {
                    "before": dict(existing),
                    "after": None,
                    "reason": "管理员撤销事务所成员职责及所属账套授权",
                },
            )
            return {"revoked": True}

    def list_book_members(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, "manage")
            rows = conn.execute(
                "SELECT a.user_id,u.name,u.email,a.role FROM book_access a JOIN users u ON u.id=a.user_id WHERE a.book_id=? AND a.firm_id=? ORDER BY u.name,u.id",
                (book_id, actor.firm_id),
            ).fetchall()
            return {"members": [dict(row) for row in rows]}

    def grant_book_member(self, actor, book_id, user_id, request: BookMemberGrant):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "manage")
            self.access.require_user(conn, user_id)
            member = conn.execute(
                "SELECT role FROM memberships WHERE firm_id=? AND user_id=?",
                (actor.firm_id, user_id),
            ).fetchone()
            company = conn.execute(
                "SELECT role FROM company_memberships WHERE company_id=? AND user_id=?",
                (book["company_id"], user_id),
            ).fetchone()
            if not member and (not company or company["role"] not in COMPANY_LEDGER_ROLES):
                raise AccountingError(
                    "先授予当前事务所成员或企业财务身份，再分配账套；普通员工不能读取账本", 403
                )
            if self.access.firm_role(conn, actor) not in FIRM_MANAGERS and member:
                raise AccountingError("企业管理员只能分配本企业成员，事务所人员由事务所管理", 403)
            if self.access.firm_role(conn, actor) not in FIRM_MANAGERS and user_id == actor.user_id:
                raise AccountingError("企业管理员不能变更自己的账套权限，请联系事务所管理员", 403)
            if request.role == "reviewer" and (not member or member["role"] not in FIRM_REVIEWERS):
                raise AccountingError(
                    "正式复核仅可授予事务所复核人员；客户财务请使用经办或只读", 403
                )
            existing = conn.execute(
                "SELECT role FROM book_access WHERE book_id=? AND user_id=?", (book_id, user_id)
            ).fetchone()
            conn.execute(
                "INSERT INTO book_access(book_id,firm_id,user_id,role) VALUES(?,?,?,?) ON CONFLICT(book_id,user_id) DO UPDATE SET role=excluded.role",
                (book_id, actor.firm_id, user_id, request.role),
            )
            self._audit(
                conn,
                actor,
                "book.member.grant",
                user_id,
                {
                    "company_id": book["company_id"],
                    "role": request.role,
                    "before": dict(existing) if existing else None,
                    "after": {"role": request.role},
                    "reason": "管理员设置账套授权",
                },
                book_id,
            )
            return {"user_id": user_id, "role": request.role}

    def revoke_book_member(self, actor, book_id, user_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "manage")
            if self.access.firm_role(conn, actor) not in FIRM_MANAGERS and user_id == actor.user_id:
                raise AccountingError("企业管理员不能变更自己的账套权限，请联系事务所管理员", 403)
            if (
                self.access.firm_role(conn, actor) not in FIRM_MANAGERS
                and conn.execute(
                    "SELECT 1 FROM memberships WHERE firm_id=? AND user_id=?",
                    (actor.firm_id, user_id),
                ).fetchone()
            ):
                raise AccountingError("企业管理员不能撤销事务所人员的账套授权", 403)
            existing = conn.execute(
                "SELECT role FROM book_access WHERE book_id=? AND user_id=?", (book_id, user_id)
            ).fetchone()
            conn.execute(
                "DELETE FROM book_access WHERE book_id=? AND firm_id=? AND user_id=?",
                (book_id, actor.firm_id, user_id),
            )
            self._audit(
                conn,
                actor,
                "book.member.revoke",
                user_id,
                {
                    "company_id": book["company_id"],
                    "before": dict(existing) if existing else None,
                    "after": None,
                    "reason": "管理员撤销账套授权",
                },
                book_id,
            )
            return {"revoked": True}

    def list_company_members(self, actor, company_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            rows = conn.execute(
                "SELECT m.user_id,u.name,u.email,m.role,m.department_id FROM company_memberships m JOIN users u ON u.id=m.user_id WHERE m.company_id=? ORDER BY u.name,u.id",
                (company_id,),
            ).fetchall()
            return {"members": [dict(row) for row in rows]}

    def grant_company_member(self, actor, company_id, request: CompanyMemberGrant):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            user = self._find_user(request.email)
            if self.access.firm_role(conn, actor) not in FIRM_MANAGERS:
                if request.role == "owner" or user["id"] == actor.user_id:
                    raise AccountingError(
                        "企业管理员不能变更自己的职责或授予企业所有者身份，请联系事务所管理员", 403
                    )
                if conn.execute(
                    "SELECT 1 FROM memberships WHERE firm_id=? AND user_id=?",
                    (actor.firm_id, user["id"]),
                ).fetchone():
                    raise AccountingError("事务所人员的企业身份由事务所管理员管理", 403)
            existing = conn.execute(
                "SELECT role,department_id FROM company_memberships WHERE company_id=? AND user_id=?",
                (company_id, user["id"]),
            ).fetchone()
            if (
                existing
                and existing["role"] == "owner"
                and request.role != "owner"
                and self.access.firm_role(conn, actor) not in FIRM_MANAGERS
            ):
                raise AccountingError("企业所有者职责由事务所管理员管理", 403)
            if (
                request.department_id
                and not conn.execute(
                    "SELECT 1 FROM departments WHERE id=? AND company_id=?",
                    (request.department_id, company_id),
                ).fetchone()
            ):
                raise AccountingError("部门不属于当前企业", 422)
            conn.execute(
                "INSERT INTO company_memberships(company_id,user_id,role,department_id) VALUES(?,?,?,?) ON CONFLICT(company_id,user_id) DO UPDATE SET role=excluded.role,department_id=excluded.department_id",
                (company_id, user["id"], request.role, request.department_id),
            )
            self._audit(
                conn,
                actor,
                "company.member.grant",
                user["id"],
                {
                    "company_id": company_id,
                    "role": request.role,
                    "department_id": request.department_id,
                    "before": dict(existing) if existing else None,
                    "after": {"role": request.role, "department_id": request.department_id},
                    "reason": "管理员设置企业成员职责",
                },
            )
            return {
                "user_id": user["id"],
                "name": user["name"],
                "email": user["email"],
                "role": request.role,
                "department_id": request.department_id,
            }

    def revoke_company_member(self, actor, company_id, user_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            if user_id == actor.user_id:
                raise AccountingError("不能在该入口撤销当前企业管理身份", 409)
            existing = conn.execute(
                "SELECT role,department_id FROM company_memberships WHERE company_id=? AND user_id=?",
                (company_id, user_id),
            ).fetchone()
            if not existing:
                raise AccountingError("企业成员不存在", 404)
            if (
                existing["role"] == "owner"
                and self.access.firm_role(conn, actor) not in FIRM_MANAGERS
            ):
                raise AccountingError("企业所有者职责由事务所管理员管理", 403)
            staff = conn.execute(
                "SELECT 1 FROM memberships WHERE firm_id=? AND user_id=?", (actor.firm_id, user_id)
            ).fetchone()
            if staff and self.access.firm_role(conn, actor) not in FIRM_MANAGERS:
                raise AccountingError("企业管理员不能撤销事务所人员的企业身份", 403)
            if not staff:
                conn.execute(
                    "DELETE FROM book_access WHERE firm_id=? AND user_id=? AND book_id IN (SELECT id FROM books WHERE company_id=?)",
                    (actor.firm_id, user_id, company_id),
                )
            conn.execute(
                "DELETE FROM company_memberships WHERE company_id=? AND user_id=?",
                (company_id, user_id),
            )
            self._audit(
                conn,
                actor,
                "company.member.revoke",
                user_id,
                {
                    "company_id": company_id,
                    "before": dict(existing),
                    "after": None,
                    "reason": "管理员移除企业成员及关联企业权限",
                },
            )
            return {"revoked": True}

    def list_departments(self, actor, company_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id)
            return {
                "departments": [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id,name FROM departments WHERE company_id=? ORDER BY name,id",
                        (company_id,),
                    )
                ]
            }

    def create_department(self, actor, company_id, request: DepartmentCreate):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            department_id = uuid4().hex
            conn.execute(
                "INSERT INTO departments(id,company_id,name) VALUES(?,?,?)",
                (department_id, company_id, request.name),
            )
            self._audit(
                conn,
                actor,
                "company.department.create",
                department_id,
                {"company_id": company_id, "name": request.name},
            )
            return {"id": department_id, "name": request.name}
