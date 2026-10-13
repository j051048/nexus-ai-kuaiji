"""Identity profiles and explicit firm/company/book assignments."""

from uuid import uuid4

from .access import AccessControl, FIRM_MANAGERS, COMPANY_LEDGER_ROLES
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
        with self.db.transaction(write=True, system=True) as conn:
            existing = conn.execute(
                "SELECT active FROM users WHERE id=?", (identity.user_id,)
            ).fetchone()
            if existing and not existing["active"]:
                raise AccountingError("账号已停用，请联系管理员", 403)
            conn.execute(
                "INSERT INTO users(id,name,email) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,email=excluded.email",
                (identity.user_id, identity.display_name, (identity.email or "").lower()),
            )

    def me(self, user_id, firm_id=None, mode="supabase"):
        with self.db.transaction(actor=Actor(user_id, "")) as conn:
            user = self.access.require_user(conn, user_id)
            firms = conn.execute(
                "SELECT f.id,f.name,m.role FROM firms f JOIN memberships m ON m.firm_id=f.id WHERE m.user_id=? ORDER BY f.name,f.id",
                (user_id,),
            ).fetchall()
            available = {row["id"]: dict(row) for row in firms}
            clients = conn.execute(
                "SELECT DISTINCT f.id,f.name FROM firms f JOIN firm_clients fc ON fc.firm_id=f.id AND fc.status='active' "
                "JOIN company_memberships cm ON cm.company_id=fc.company_id WHERE cm.user_id=? ORDER BY f.name,f.id",
                (user_id,),
            ).fetchall()
            for row in clients:
                available.setdefault(row["id"], {**dict(row), "role": "client_employee"})
            if firm_id is not None and firm_id not in available:
                raise AccountingError("未获得该事务所工作空间授权", 403)
            selected = firm_id if firm_id is not None else next(iter(available), None)
            role = available[selected]["role"] if selected else None
            manager = role in FIRM_MANAGERS
            return {
                "mode": mode,
                "user": {"id": user["id"], "name": user["name"], "email": user["email"]},
                "firms": list(available.values()),
                "current_firm_id": selected,
                "permissions": {
                    "manage_firm": manager,
                    "create_company": manager,
                    "create_book": manager,
                },
            }

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
            self._audit(conn, actor, "company.create", company_id, request.model_dump())
            return {
                "id": company_id,
                "name": request.name,
                "tax_id": request.tax_id,
                "can_manage": True,
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
            self._audit(conn, actor, "firm.member.grant", user["id"], {"role": request.role})
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
            self._audit(conn, actor, "firm.member.revoke", user_id, {})
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
            conn.execute(
                "INSERT INTO book_access(book_id,firm_id,user_id,role) VALUES(?,?,?,?) ON CONFLICT(book_id,user_id) DO UPDATE SET role=excluded.role",
                (book_id, actor.firm_id, user_id, request.role),
            )
            self._audit(conn, actor, "book.member.grant", user_id, {"role": request.role}, book_id)
            return {"user_id": user_id, "role": request.role}

    def revoke_book_member(self, actor, book_id, user_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.book(conn, actor, book_id, "manage")
            if (
                self.access.firm_role(conn, actor) not in FIRM_MANAGERS
                and conn.execute(
                    "SELECT 1 FROM memberships WHERE firm_id=? AND user_id=?",
                    (actor.firm_id, user_id),
                ).fetchone()
            ):
                raise AccountingError("企业管理员不能撤销事务所人员的账套授权", 403)
            conn.execute(
                "DELETE FROM book_access WHERE book_id=? AND firm_id=? AND user_id=?",
                (book_id, actor.firm_id, user_id),
            )
            self._audit(conn, actor, "book.member.revoke", user_id, {}, book_id)
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
                "SELECT role FROM company_memberships WHERE company_id=? AND user_id=?",
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
                "SELECT role FROM company_memberships WHERE company_id=? AND user_id=?",
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
            self._audit(conn, actor, "company.member.revoke", user_id, {"company_id": company_id})
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
