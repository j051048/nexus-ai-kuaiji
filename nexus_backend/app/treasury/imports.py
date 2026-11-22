"""银行流水落库（确认步）。

预览只做归一化与匹配建议，不写库；确认才把流水写成 ``bank_statement_lines``。
同一条柜台流水重复导入不会落第二行：``external_ref``（账号|流水号）在同一账套内
唯一，第二遍导入返回 ``skipped`` 而不是新行——重复的流水行会让对账凭空多出一笔钱。
"""

from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError, Actor
from ..accounting.database import Database
from .adapters import AdapterError, parse_bank_csv


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class BankStatementImportService:
    def __init__(self, db: Database, access: AccessControl | None = None):
        self.db = db
        self.access = access or AccessControl()

    def confirm(self, actor: Actor, book_id: str, *, csv_text: str, bank_account_id: str = ""):
        try:
            lines = parse_bank_csv(csv_text, source="counter_csv")
        except AdapterError as error:
            raise AccountingError(str(error), 400) from error
        if not lines:
            raise AccountingError("CSV 里没有可落库的流水行", 422)
        with self.db.transaction(actor=actor) as conn:
            company_id = self.access.book(conn, actor, book_id, "read")["company_id"]
        inserted, skipped = [], []
        with self.db.transaction(
            actor=actor, company_id=company_id, write=True, write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, book_id, "edit")
            if (
                bank_account_id
                and not conn.execute(
                    "SELECT 1 FROM bank_accounts WHERE id=? AND book_id=? AND active IS TRUE",
                    (bank_account_id, book["id"]),
                ).fetchone()
            ):
                raise AccountingError("银行账户不存在或不属于当前账套", 404)
            for line in lines:
                reference = f"{line.account}|{line.serial}"
                existing = conn.execute(
                    "SELECT id FROM bank_statement_lines WHERE book_id=? AND external_ref=?",
                    (book["id"], reference),
                ).fetchone()
                if existing:
                    skipped.append({"external_ref": reference, "id": existing["id"]})
                    continue
                amount = int(line.amount_cents)
                if line.direction == "expense":
                    amount = -amount
                identifier = uuid4().hex
                conn.execute(
                    "INSERT INTO bank_statement_lines(id,book_id,period,line_date,description,"
                    "amount_cents,status,created_by,created_at,bank_account_id,external_ref) "
                    "VALUES(?,?,?,?,?,?,'unmatched',?,?,?,?)",
                    (
                        identifier,
                        book["id"],
                        book["period"],
                        line.date,
                        f"{line.counterparty} {line.serial}".strip() or "银行流水",
                        amount,
                        actor.user_id,
                        _now(),
                        bank_account_id or None,
                        reference,
                    ),
                )
                inserted.append(
                    {"external_ref": reference, "id": identifier, "line": line.as_dict()}
                )
        return {
            "book_id": book_id,
            "period": book["period"],
            "inserted": inserted,
            "skipped": skipped,
            "inserted_count": len(inserted),
            "skipped_count": len(skipped),
            "posted": False,
            "note": "流水行已落库为待勾对状态；勾对到正式分录后才用于银行对账通过。",
        }
