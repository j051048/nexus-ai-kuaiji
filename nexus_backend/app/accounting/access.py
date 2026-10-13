"""Authorization derives from current database membership, never JWT metadata."""
from .domain import AccountingError, Actor
from .telemetry import tag_authorized_company

FIRM_MANAGERS = {"owner", "admin"}
FIRM_EDITORS = {"owner", "admin", "operator", "reviewer"}
FIRM_REVIEWERS = {"owner", "admin", "reviewer"}
COMPANY_LEDGER_ROLES = {"owner", "admin", "finance"}


class AccessControl:
    @staticmethod
    def require_user(conn, user_id):
        user = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
        if not user:
            raise AccountingError("账号未激活或已停用", 403)
        return user

    def firm_role(self, conn, actor: Actor):
        self.require_user(conn, actor.user_id)
        row = conn.execute("SELECT role FROM memberships WHERE firm_id=? AND user_id=?", (actor.firm_id, actor.user_id)).fetchone()
        return row["role"] if row else None

    def require_manager(self, conn, actor: Actor):
        if self.firm_role(conn, actor) not in FIRM_MANAGERS:
            raise AccountingError("需要事务所所有者或管理员权限", 403)

    def company_role(self, conn, actor: Actor, company_id):
        row = conn.execute(
            "SELECT m.role FROM company_memberships m JOIN firm_clients fc ON fc.company_id=m.company_id "
            "WHERE m.company_id=? AND m.user_id=? AND fc.firm_id=? AND fc.status='active'",
            (company_id, actor.user_id, actor.firm_id),
        ).fetchone()
        return row["role"] if row else None

    def company(self, conn, actor: Actor, company_id, manage=False):
        self.require_user(conn, actor.user_id)
        company = conn.execute(
            "SELECT c.* FROM companies c JOIN firm_clients fc ON fc.company_id=c.id "
            "WHERE c.id=? AND fc.firm_id=? AND fc.status='active'", (company_id, actor.firm_id),
        ).fetchone()
        if not company:
            raise AccountingError("客户企业不存在或无权访问", 404)
        role = self.firm_role(conn, actor)
        if role not in FIRM_MANAGERS:
            own_role = self.company_role(conn, actor, company_id)
            assigned = conn.execute(
                "SELECT 1 FROM book_access a JOIN books b ON b.id=a.book_id AND b.firm_id=a.firm_id "
                "WHERE b.firm_id=? AND b.company_id=? AND a.user_id=?", (actor.firm_id, company_id, actor.user_id),
            ).fetchone()
            if manage and own_role not in {"owner", "admin"}:
                raise AccountingError("需要企业管理员或事务所管理员权限", 403)
            if not manage and not own_role and not (role and assigned):
                raise AccountingError("客户企业不存在或无权访问", 404)
        tag_authorized_company(company_id)
        return company

    def book(self, conn, actor: Actor, book_id, capability="read"):
        role = self.firm_role(conn, actor)
        book = conn.execute(
            "SELECT b.*,c.name AS company_name,p.status AS period_status FROM books b "
            "JOIN companies c ON c.id=b.company_id JOIN firm_clients fc ON fc.company_id=b.company_id AND fc.firm_id=b.firm_id "
            "JOIN periods p ON p.book_id=b.id AND p.period=b.period "
            "WHERE b.id=? AND b.firm_id=? AND fc.status='active'", (book_id, actor.firm_id),
        ).fetchone()
        if not book:
            raise AccountingError("账套不存在或无权访问", 404)
        grant = conn.execute("SELECT role FROM book_access WHERE book_id=? AND firm_id=? AND user_id=?", (book_id, actor.firm_id, actor.user_id)).fetchone()
        company_role = self.company_role(conn, actor, book["company_id"])
        permissions = self.permissions(role, grant["role"] if grant else None, company_role)
        if not permissions[capability]:
            if not permissions["read"] and capability != "manage":
                raise AccountingError("账套不存在或无权访问", 404)
            raise AccountingError("当前账号没有该账套的编辑、复核或管理权限", 403)
        tag_authorized_company(book['company_id'])
        return {**dict(book), "permissions": {k: v for k, v in permissions.items() if k != "read"}}

    @staticmethod
    def permissions(firm_role, book_role, company_role):
        manager = firm_role in FIRM_MANAGERS
        principal = bool(firm_role) or company_role in COMPANY_LEDGER_ROLES
        read = manager or (principal and book_role is not None)
        edit = manager or (read and book_role in {"editor", "reviewer"} and (firm_role in FIRM_EDITORS or (not firm_role and company_role in COMPANY_LEDGER_ROLES)))
        review = manager or (read and book_role == "reviewer" and (firm_role in FIRM_REVIEWERS or (not firm_role and company_role == "finance")))
        return {"read": read, "edit": edit, "review": review, "export": review, "manage": manager or company_role in {"owner", "admin"}}
