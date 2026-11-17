"""Read-only voucher numbering gap inspection for close prechecks."""

from __future__ import annotations

from ..accounting.domain import AccountingError


class VoucherGapService:
    """Detect gaps in per-book, per-type voucher numbering.

    The check is SQL-only: it materializes the expected sequence and joins the
    voucher numbers, so it never loads voucher rows or line payloads.
    """

    def __init__(self, db, access=None):
        self.db = db
        self.access = access

    def scan(self, actor, book_id: str):
        with self.db.transaction(actor=actor) as conn:
            book = conn.execute(
                "SELECT id FROM books WHERE id=? AND firm_id=?", (book_id, actor.firm_id)
            ).fetchone()
            if not book:
                raise AccountingError("账套不存在或无权访问", 404)
            if self.access is not None:
                self.access.book(conn, actor, book_id)
            return self.scan_in_transaction(conn, book_id)

    def scan_in_transaction(self, conn, book_id: str):
        rows = conn.execute(
            "SELECT voucher_type,COUNT(*) AS count,MAX(voucher_no) AS max_no "
            "FROM vouchers WHERE book_id=? GROUP BY voucher_type ORDER BY voucher_type",
            (book_id,),
        ).fetchall()
        types = []
        missing = []
        for row in rows:
            count = int(row["count"])
            max_no = int(row["max_no"] or 0)
            type_missing = []
            if count < max_no:
                for number in self._missing_numbers(conn, book_id, row["voucher_type"], max_no):
                    item = {"voucher_type": row["voucher_type"], "voucher_no": int(number)}
                    type_missing.append(int(number))
                    missing.append(item)
            types.append(
                {
                    "voucher_type": row["voucher_type"],
                    "count": count,
                    "max_no": max_no,
                    "missing": type_missing,
                    "contiguous": not type_missing,
                }
            )
        return {
            "book_id": book_id,
            "types": types,
            "missing": missing,
            "contiguous": not missing,
        }

    def _missing_numbers(self, conn, book_id: str, voucher_type: str, max_no: int):
        if self.db.is_postgres:
            sql = (
                "SELECT n FROM generate_series(1,?) n "
                "LEFT JOIN vouchers v ON v.book_id=? AND v.voucher_type=? AND v.voucher_no=n "
                "WHERE v.id IS NULL ORDER BY n"
            )
            return [row[0] for row in conn.execute(sql, (max_no, book_id, voucher_type))]
        sql = (
            "WITH RECURSIVE seq(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM seq WHERE n<?) "
            "SELECT n FROM seq LEFT JOIN vouchers v "
            "ON v.book_id=? AND v.voucher_type=? AND v.voucher_no=seq.n "
            "WHERE v.id IS NULL ORDER BY n"
        )
        return [row[0] for row in conn.execute(sql, (max_no, book_id, voucher_type))]
