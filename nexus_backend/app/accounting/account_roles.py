"""Account role resolution for business rules and reconciliation.

The chart of accounts is seed data; roles are book policy.  A book may use a
non-default account code and still declare it as its bank or payable account.
"""

from ..accounting.domain import AccountingError

ROLES = frozenset({"bank", "payable", "receivable"})
DEFAULT_CODES = {
    "bank": ("1002",),
    "payable": ("2202", "2241"),
    "receivable": ("1122", "1221"),
}


class AccountRoleService:
    @staticmethod
    def codes(conn, book_id: str, role: str) -> list[str]:
        if role not in ROLES:
            raise AccountingError("未知科目角色", 422)
        rows = conn.execute(
            "SELECT code FROM account_roles WHERE book_id=? AND role=? ORDER BY code",
            (book_id, role),
        ).fetchall()
        return [row["code"] for row in rows]

    @staticmethod
    def bank_code(conn, book_id: str) -> str:
        codes = AccountRoleService.codes(conn, book_id, "bank")
        if not codes:
            raise AccountingError("账套未配置银行科目角色", 409)
        return codes[0]

    @staticmethod
    def set_role(conn, book_id: str, code: str, role: str, *, enabled: bool = True) -> None:
        if role not in ROLES:
            raise AccountingError("未知科目角色", 422)
        if not conn.execute(
            "SELECT 1 FROM accounts WHERE book_id=? AND code=? AND active=1", (book_id, code)
        ).fetchone():
            raise AccountingError("科目不存在或已停用", 422)
        if enabled:
            conn.execute(
                "INSERT INTO account_roles(book_id,code,role) VALUES(?,?,?) ON CONFLICT DO NOTHING",
                (book_id, code, role),
            )
        else:
            conn.execute(
                "DELETE FROM account_roles WHERE book_id=? AND code=? AND role=?",
                (book_id, code, role),
            )

    @staticmethod
    def ensure_defaults(conn, book_id: str) -> None:
        existing = conn.execute(
            "SELECT 1 FROM account_roles WHERE book_id=? LIMIT 1", (book_id,)
        ).fetchone()
        if existing:
            return
        for role, codes in DEFAULT_CODES.items():
            for code in codes:
                if conn.execute(
                    "SELECT 1 FROM accounts WHERE book_id=? AND code=? AND active=1",
                    (book_id, code),
                ).fetchone():
                    conn.execute(
                        "INSERT INTO account_roles(book_id,code,role) VALUES(?,?,?) "
                        "ON CONFLICT DO NOTHING",
                        (book_id, code, role),
                    )
