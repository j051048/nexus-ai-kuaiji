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
        row = conn.execute(
            "SELECT m.role FROM users u LEFT JOIN memberships m ON m.user_id=u.id AND m.firm_id=? "
            "WHERE u.id=? AND u.active=1",
            (actor.firm_id, actor.user_id),
        ).fetchone()
        if not row:
            raise AccountingError("账号未激活或已停用", 403)
        return row["role"]

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
        # One statement reads current grants and the resource under the same
        # RLS snapshot. There is no process/request authorization cache.
        row = conn.execute(
            "SELECT c.*,m.role AS _access_firm_role,cm.role AS _access_company_role,"
            "EXISTS(SELECT 1 FROM effective_book_access a JOIN books b ON b.id=a.book_id AND b.firm_id=a.firm_id "
            "WHERE b.firm_id=? AND b.company_id=c.id AND a.user_id=u.id) AS _access_assigned "
            "FROM users u LEFT JOIN memberships m ON m.user_id=u.id AND m.firm_id=? "
            "LEFT JOIN firm_clients fc ON fc.company_id=? AND fc.firm_id=? AND fc.status='active' "
            "LEFT JOIN companies c ON c.id=fc.company_id AND c.status='active' "
            "LEFT JOIN company_memberships cm ON cm.company_id=c.id AND cm.user_id=u.id "
            "WHERE u.id=? AND u.active=1",
            (actor.firm_id, actor.firm_id, company_id, actor.firm_id, actor.user_id),
        ).fetchone()
        if not row:
            raise AccountingError("账号未激活或已停用", 403)
        if not row["id"]:
            raise AccountingError("客户企业不存在或无权访问", 404)
        role = row["_access_firm_role"]
        if role not in FIRM_MANAGERS:
            own_role, assigned = row["_access_company_role"], row["_access_assigned"]
            if manage and own_role not in {"owner", "admin"}:
                raise AccountingError("需要企业管理员或事务所管理员权限", 403)
            if not manage and not own_role and not (role and assigned):
                raise AccountingError("客户企业不存在或无权访问", 404)
        tag_authorized_company(company_id)
        return {key: row[key] for key in row.keys() if not key.startswith("_access_")}

    def book(self, conn, actor: Actor, book_id, capability="read"):
        book = conn.execute(
            "SELECT b.*,m.role AS _access_firm_role,a.role AS _access_book_role,"
            "cm.role AS _access_company_role FROM users u "
            "LEFT JOIN memberships m ON m.user_id=u.id AND m.firm_id=? "
            "LEFT JOIN (SELECT b.*,c.name AS company_name,p.status AS period_status FROM books b "
            "JOIN companies c ON c.id=b.company_id JOIN firm_clients fc ON fc.company_id=b.company_id AND fc.firm_id=b.firm_id "
            "JOIN periods p ON p.book_id=b.id AND p.period=b.period "
            "WHERE b.id=? AND b.firm_id=? AND fc.status='active' AND c.status='active') b ON 1=1 "
            "LEFT JOIN effective_book_access a ON a.book_id=b.id AND a.firm_id=b.firm_id AND a.user_id=u.id "
            "LEFT JOIN company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id "
            "WHERE u.id=? AND u.active=1",
            (actor.firm_id, book_id, actor.firm_id, actor.user_id),
        ).fetchone()
        if not book:
            raise AccountingError("账号未激活或已停用", 403)
        if not book["id"]:
            raise AccountingError("账套不存在或无权访问", 404)
        permissions = self.permissions(
            book["_access_firm_role"], book["_access_book_role"], book["_access_company_role"]
        )
        if not permissions[capability]:
            if not permissions["read"] and capability != "manage":
                raise AccountingError("账套不存在或无权访问", 404)
            raise AccountingError("当前账号没有该账套的编辑、复核或管理权限", 403)
        tag_authorized_company(book["company_id"])
        return {
            **{key: book[key] for key in book.keys() if not key.startswith("_access_")},
            "permissions": {k: v for k, v in permissions.items() if k != "read"},
        }

    @staticmethod
    def permissions(firm_role, book_role, company_role):
        manager = firm_role in FIRM_MANAGERS
        principal = bool(firm_role) or company_role in COMPANY_LEDGER_ROLES
        read = manager or (principal and book_role is not None)
        edit = manager or (
            read
            and book_role in {"editor", "reviewer"}
            and (
                firm_role in FIRM_EDITORS
                or (not firm_role and company_role in COMPANY_LEDGER_ROLES)
            )
        )
        review = manager or (
            read
            and book_role == "reviewer"
            and firm_role in FIRM_REVIEWERS
        )
        return {
            "read": read,
            "edit": edit,
            "review": review,
            "export": review,
            "manage": manager or company_role in {"owner", "admin"},
        }
