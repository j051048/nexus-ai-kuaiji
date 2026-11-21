"""Reviewed four-tax period results.

This is the storage boundary for ``app.tax.engines``: the engines stay pure
functions, and a result only becomes usable after a reviewer other than the
author accepts it. Nothing here recomputes or "fixes" a payload — the bytes a
reviewer signs are the bytes that were computed.
"""

import json
import uuid

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.service import now


KINDS = ("vat", "surtax", "cit_prepayment", "stamp_duty")


class TaxPeriodService:
    def __init__(self, db, access_service=None):
        self.db = db
        self.access = access_service or AccessControl()

    def _company(self, actor, book_id):
        """Resolve the company up front so the write takes a company-scoped lock."""
        with self.db.transaction(actor=actor) as conn:
            return self.access.book(conn, actor, book_id, "read")["company_id"]

    def record(self, actor, book_id, *, period, kind, payload, source_digest):
        if kind not in KINDS:
            raise AccountingError(f"未知税种 {kind}", 422)
        total = payload.get("total_cents") if isinstance(payload, dict) else None
        if type(total) is not int:
            raise AccountingError("底稿金额必须是整数分", 422)
        if not isinstance(source_digest, str) or len(source_digest) < 16:
            raise AccountingError("来源指纹缺失", 422)
        company_id = self._company(actor, book_id)
        with self.db.transaction(
            actor=actor, company_id=company_id, write=True, write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, book_id, "edit")
            version = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM tax_period_runs "
                "WHERE book_id=? AND period=? AND kind=?",
                (book["id"], period, kind),
            ).fetchone()[0]
            run_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO tax_period_runs(id,firm_id,company_id,book_id,period,kind,status,"
                "version,payload_json,total_cents,source_digest,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,'draft',?,?,?,?,?,?)",
                (
                    run_id,
                    book["firm_id"],
                    book["company_id"],
                    book["id"],
                    period,
                    kind,
                    version,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    total,
                    source_digest,
                    actor.user_id,
                    now(),
                ),
            )
        return self.get(actor, book_id, run_id)

    def list(self, actor, book_id, *, period=None):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            sql = "SELECT * FROM tax_period_runs WHERE book_id=?"
            args = [book["id"]]
            if period:
                sql += " AND period=?"
                args.append(period)
            rows = conn.execute(sql + " ORDER BY period,kind,version", args).fetchall()
        return [self._json(row) for row in rows]

    def get(self, actor, book_id, run_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            row = conn.execute(
                "SELECT * FROM tax_period_runs WHERE id=? AND book_id=?",
                (run_id, book["id"]),
            ).fetchone()
        if row is None:
            raise AccountingError("税种底稿不存在", 404)
        return self._json(row)

    def review(self, actor, book_id, run_id, *, note, expected_version):
        company_id = self._company(actor, book_id)
        with self.db.transaction(
            actor=actor, company_id=company_id, write=True, write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            row = conn.execute(
                "SELECT * FROM tax_period_runs WHERE id=? AND book_id=?",
                (run_id, book["id"]),
            ).fetchone()
            if row is None:
                raise AccountingError("税种底稿不存在", 404)
            if row["created_by"] == actor.user_id:
                raise AccountingError("制单责任人不能复核本人编制版本，请交给另一位复核人", 409)
            if row["status"] == "reviewed":
                raise AccountingError("该版本已复核，不能重复复核", 409)
            if int(expected_version) != int(row["version"]):
                raise AccountingError("底稿版本已变化，请重新读取后再复核", 409)
            conn.execute(
                "UPDATE tax_period_runs SET status='reviewed',reviewed_by=?,reviewed_at=?,"
                "review_note=? WHERE id=? AND book_id=? AND status='draft'",
                (actor.user_id, now(), note, run_id, book["id"]),
            )
        return self.get(actor, book_id, run_id)

    def _json(self, row):
        data = dict(row)
        raw_payload = data.pop("payload_json")
        data["payload"] = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        data["total_cents"] = int(data["total_cents"])
        data["version"] = int(data["version"])
        return data
