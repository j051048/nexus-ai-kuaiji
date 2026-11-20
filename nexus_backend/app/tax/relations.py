"""PLT04: explicit, versioned relation between a settlement, the bank receipt
and the fee evidence that explains their difference.

A relation is registered only after the three sources agree on book, period,
amount and latest evidence version.  The assistant and the fee-variance register
both read this table, so an arbitrary same-amount source can never be presented
as the cause of a difference.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.service import now
from ..ledgers.service import LedgersService


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def source_version(fingerprint: str, evidence_ref: str) -> str:
    return hashlib.sha256(f"{fingerprint}|{evidence_ref}".encode("utf-8")).hexdigest()


class SourceRelationService:
    def __init__(self, db):
        self.db = db
        self.ledgers = LedgersService(db)

    def verify(
        self,
        actor,
        book_id,
        *,
        settlement_source_id,
        bank_source_id,
        fee_source_id=None,
        reason,
    ):
        reason = str(reason or "").strip()
        if len(reason) < 3:
            raise AccountingError("核对来源关系必须说明依据", 422)
        with self.db.transaction(actor=actor) as conn:
            prebook = self.ledgers.access.book(conn, actor, book_id, "edit")
            company_id = prebook["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "edit")
            return self.verify_in(
                conn,
                actor,
                book,
                settlement_source_id=settlement_source_id,
                bank_source_id=bank_source_id,
                fee_source_id=fee_source_id,
                reason=reason,
            )

    def verify_in(
        self,
        conn,
        actor,
        book,
        *,
        settlement_source_id,
        bank_source_id,
        fee_source_id=None,
        reason="",
    ):
        settlement_id = str(settlement_source_id or "").strip()
        bank_id = str(bank_source_id or "").strip()
        fee_id = str(fee_source_id or "").strip() or None
        if not settlement_id or not bank_id:
            raise AccountingError("请补齐结算单和银行到账来源", 422)
        if settlement_id == bank_id:
            raise AccountingError("结算单与银行到账不能是同一来源", 422)
        if fee_id and fee_id in {settlement_id, bank_id}:
            raise AccountingError("手续费凭据不能与结算单或银行到账是同一来源", 422)
        settlement = self._source(conn, book["id"], settlement_id, "结算法")
        bank = self._source(conn, book["id"], bank_id, "银行到账")
        fee = self._source(conn, book["id"], fee_id, "手续费凭据") if fee_id else None
        if settlement["source_kind"] != "business":
            raise AccountingError("结算单来源不是业务单据", 422)
        if bank["source_kind"] != "bank":
            raise AccountingError("银行到账来源不是银行流水", 422)
        if fee and fee["source_kind"] != "business":
            raise AccountingError("手续费凭据不是业务单据", 422)
        for label, row in (("结算单", settlement), ("银行到账", bank), ("手续费凭据", fee)):
            if row is None:
                continue
            if str(row["date"])[:7] != book["period"]:
                raise AccountingError(f"{label}不属于当前会计期间", 422)
            if not str(row["latest_evidence"] or "").strip():
                raise AccountingError(f"{label}缺少证据原件", 422)
        difference = int(settlement["amount_cents"]) - int(bank["amount_cents"])
        if difference == 0:
            raise AccountingError("结算单与银行到账没有差额，无需登记来源关系", 422)
        if fee and int(fee["amount_cents"]) != abs(difference):
            raise AccountingError("手续费凭据金额与差额不一致", 422)
        versions = {
            "settlement_version": source_version(
                settlement["fingerprint"], settlement["latest_evidence"]
            ),
            "bank_version": source_version(bank["fingerprint"], bank["latest_evidence"]),
            "fee_version": (
                source_version(fee["fingerprint"], fee["latest_evidence"]) if fee else None
            ),
        }
        payload = {
            "book_id": book["id"],
            "period": book["period"],
            "settlement_source_id": settlement_id,
            "bank_source_id": bank_id,
            "fee_source_id": fee_id,
            "settlement_amount_cents": int(settlement["amount_cents"]),
            "bank_amount_cents": int(bank["amount_cents"]),
            "fee_amount_cents": int(fee["amount_cents"]) if fee else None,
            "difference_cents": difference,
            **versions,
        }
        relation_sha = _digest(payload)
        existing = conn.execute(
            "SELECT * FROM fee_source_relations WHERE book_id=? AND settlement_source_id=? "
            "AND bank_source_id=? AND COALESCE(fee_source_id,'')=?",
            (book["id"], settlement_id, bank_id, fee_id or ""),
        ).fetchone()
        if existing:
            if existing["relation_sha256"] != relation_sha:
                raise AccountingError("来源关系已变化，请重新核对", 409)
            return dict(existing)
        relation_id = uuid4().hex
        stamp = now()
        conn.execute(
            "INSERT INTO fee_source_relations(id,firm_id,company_id,book_id,period,"
            "settlement_source_id,bank_source_id,fee_source_id,settlement_version,bank_version,"
            "fee_version,settlement_amount_cents,bank_amount_cents,fee_amount_cents,"
            "difference_cents,relation_sha256,verified_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                relation_id,
                book["firm_id"],
                book["company_id"],
                book["id"],
                book["period"],
                settlement_id,
                bank_id,
                fee_id,
                versions["settlement_version"],
                versions["bank_version"],
                versions["fee_version"],
                int(settlement["amount_cents"]),
                int(bank["amount_cents"]),
                int(fee["amount_cents"]) if fee else None,
                difference,
                relation_sha,
                actor.user_id,
                stamp,
            ),
        )
        self._audit(
            conn,
            actor,
            book["id"],
            "fee.source_relation.verify",
            relation_id,
            {
                "settlement_source_id": settlement_id,
                "bank_source_id": bank_id,
                "fee_source_id": fee_id,
            },
        )
        return dict(
            conn.execute("SELECT * FROM fee_source_relations WHERE id=?", (relation_id,)).fetchone()
        )

    def staleness(self, conn, relation) -> str:
        """Return the label of the first source whose version moved, else "".

        A relation pins the version of every bound source.  Once a source is
        re-imported the pinned amounts no longer describe today's evidence, so a
        difference draft built from it would be attributed to a stale fact.
        """
        book_id = relation["book_id"]
        checks = (
            ("settlement_version", relation["settlement_source_id"], "结算流水"),
            ("bank_version", relation["bank_source_id"], "银行流水"),
        )
        for column, source_id, label in checks:
            source = self._source(conn, book_id, source_id, label)
            current = source_version(source["fingerprint"], source["latest_evidence"])
            if current != relation[column]:
                return label
        fee_id = relation["fee_source_id"]
        if fee_id:
            fee = self._source(conn, book_id, fee_id, "规费流水")
            if source_version(fee["fingerprint"], fee["latest_evidence"]) != relation["fee_version"]:
                return "规费流水"
        return ""

    def find(self, conn, book_id, *, settlement_source_id, bank_source_id, fee_source_id=None):
        return conn.execute(
            "SELECT * FROM fee_source_relations WHERE book_id=? AND settlement_source_id=? "
            "AND bank_source_id=? AND COALESCE(fee_source_id,'')=?",
            (book_id, settlement_source_id, bank_source_id, str(fee_source_id or "")),
        ).fetchone()

    def _source(self, conn, book_id, source_id, label):
        row = conn.execute(
            "SELECT s.id,s.source_kind,s.amount_cents,s.date,s.fingerprint,"
            "COALESCE((SELECT se.evidence_ref FROM source_evidence se "
            "WHERE se.book_id=s.book_id AND se.source_id=s.id ORDER BY se.id DESC LIMIT 1),"
            "s.evidence_ref) AS latest_evidence "
            "FROM sources s WHERE s.id=? AND s.book_id=?",
            (source_id, book_id),
        ).fetchone()
        if not row:
            raise AccountingError(f"{label}不在当前账套", 404)
        return dict(row)

    def _audit(self, conn, actor, book_id, action, entity_id, detail):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book_id,
                actor.user_id,
                action,
                entity_id,
                json.dumps(detail, ensure_ascii=False, sort_keys=True),
                now(),
            ),
        )
