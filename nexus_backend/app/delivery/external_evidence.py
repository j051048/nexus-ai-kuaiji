"""G1 external-ledger evidence: upload is not verification.

A second, currently authorized reviewer must explicitly attest that the frozen
T+ report/import file belongs to this company, period and report version.
"""

import hashlib
import json
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError, Actor
from ..accounting.service import now
from .monthly_models import ExternalEvidenceCreate, ExternalEvidenceReview


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class MonthlyExternalEvidenceService:
    def __init__(self, db):
        self.db = db
        self.access = AccessControl()

    @staticmethod
    def _sha(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _scope(self, conn, actor: Actor, book_id: str) -> dict:
        return self.access.book(conn, actor, book_id, "review")

    def _report(self, conn, book: dict, report_id: str, version: int) -> dict:
        row = conn.execute(
            "SELECT * FROM report_runs WHERE id=? AND book_id=?", (report_id, book["id"])
        ).fetchone()
        if not row:
            raise AccountingError("报表不存在或不属于当前账套", 404)
        if row["version"] != version or row["status"] != "published":
            raise AccountingError("外部证据必须绑定当前已发布报表版本", 409)
        if row["period"] != book["period"]:
            raise AccountingError("报表期间与账套期间不一致", 409)
        return dict(row)

    def list(self, actor: Actor, book_id: str) -> dict:
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id)
            rows = conn.execute(
                "SELECT * FROM monthly_external_evidence WHERE book_id=? ORDER BY created_at,id",
                (book_id,),
            ).fetchall()
            return {
                "items": [self._view(book, actor, row) for row in rows],
                "external_mode": bool(
                    conn.execute(
                        "SELECT 1 FROM bridge_configurations WHERE book_id=? LIMIT 1", (book_id,)
                    ).fetchone()
                ),
            }

    def create(self, actor: Actor, book_id: str, request: ExternalEvidenceCreate) -> dict:
        with self.db.transaction(actor=actor) as conn:
            book = self._scope(conn, actor, book_id)
            company_id = book["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._scope(conn, actor, book_id)
            report = self._report(conn, book, request.report_id, request.expected_report_version)
            sha = self._sha(request.content)
            fingerprint = self._sha(_canonical(request.model_dump()))
            existing = conn.execute(
                "SELECT * FROM monthly_external_evidence WHERE book_id=? AND report_id=? AND report_version=? AND source_kind=? AND source_version=? AND sha256=?",
                (
                    book_id,
                    request.report_id,
                    request.expected_report_version,
                    request.source_kind,
                    request.source_version,
                    sha,
                ),
            ).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise AccountingError("同一外部证据键已登记不同内容", 409)
                return self._view(book, actor, existing)
            identifier = str(uuid4())
            conn.execute(
                "INSERT INTO monthly_external_evidence(id,book_id,firm_id,company_id,period,report_id,report_version,source_kind,source_name,source_version,content_text,sha256,status,note,created_by,created_at,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    book_id,
                    book["firm_id"],
                    company_id,
                    book["period"],
                    report["id"],
                    report["version"],
                    request.source_kind,
                    request.source_name,
                    request.source_version,
                    request.content,
                    sha,
                    "pending",
                    request.note,
                    actor.user_id,
                    now(),
                    fingerprint,
                ),
            )
            row = conn.execute(
                "SELECT * FROM monthly_external_evidence WHERE id=?", (identifier,)
            ).fetchone()
            return self._view(book, actor, row)

    def review(
        self, actor: Actor, book_id: str, evidence_id: str, request: ExternalEvidenceReview
    ) -> dict:
        with self.db.transaction(actor=actor) as conn:
            company_id = self.access.book(conn, actor, book_id, "review")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._scope(conn, actor, book_id)
            row = conn.execute(
                "SELECT * FROM monthly_external_evidence WHERE id=? AND book_id=?",
                (evidence_id, book_id),
            ).fetchone()
            if not row:
                raise AccountingError("外部证据不存在或不属于当前账套", 404)
            if row["status"] != "pending":
                raise AccountingError("该外部证据已经处理，不能改写核验结论", 409)
            if row["created_by"] == actor.user_id:
                raise AccountingError("外部证据登记人不能自行核验", 403)
            conn.execute(
                "UPDATE monthly_external_evidence SET status=?,verified_by=?,verified_at=?,review_note=? WHERE id=? AND status='pending'",
                (request.decision, actor.user_id, now(), request.note, evidence_id),
            )
            row = conn.execute(
                "SELECT * FROM monthly_external_evidence WHERE id=?", (evidence_id,)
            ).fetchone()
            return self._view(book, actor, row)

    @staticmethod
    def _view(book: dict, actor: Actor, row) -> dict:
        return {
            "id": row["id"],
            "book_id": row["book_id"],
            "period": row["period"],
            "report_id": row["report_id"],
            "report_version": row["report_version"],
            "source_kind": row["source_kind"],
            "source_name": row["source_name"],
            "source_version": row["source_version"],
            "sha256": row["sha256"],
            "status": row["status"],
            "note": row["note"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "verified_by": row["verified_by"],
            "verified_at": row["verified_at"],
            "review_note": row["review_note"],
            "can_verify": bool(row["status"] == "pending" and row["created_by"] != actor.user_id),
        }

    @staticmethod
    def verified_for(conn, book_id: str, report_id: str, report_version: int):
        return conn.execute(
            "SELECT * FROM monthly_external_evidence WHERE book_id=? AND report_id=? AND report_version=? AND status='verified' ORDER BY verified_at DESC,id DESC LIMIT 1",
            (book_id, report_id, report_version),
        ).fetchone()
