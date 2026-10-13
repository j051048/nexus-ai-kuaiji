import json
from uuid import uuid4
from app.accounting.access import AccessControl
from app.accounting.domain import AccountingError
from app.accounting.bank_imports import DUPLICATE_SQL
from app.accounting.service import fingerprint, now


class SourceDispositionService:
    def __init__(self, db):
        self.db = db
        self.access = AccessControl()

    @staticmethod
    def _row(conn, source):
        return conn.execute("SELECT * FROM bank_source_dispositions WHERE book_id=? AND source_id=?",
                            (source["book_id"], source["id"])).fetchone()

    def is_source_inactive(self, conn, actor, source):
        self.access.book(conn, actor, source["book_id"])
        return self._row(conn, source) is not None

    def blocking_issues_in_transaction(self, conn, actor, source):
        return ["该银行来源已核验为重复副本并隔离，请使用保留的主来源；历史资料仍可查看"] if self.is_source_inactive(conn, actor, source) else []

    def meta_in_transaction(self, conn, actor, source):
        self.access.book(conn, actor, source["book_id"])
        result = self._row(conn, source)
        return {"inactive": bool(result), "disposition": dict(result) if result else None}

    def overview(self, actor, book_id, source_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id)
            source = conn.execute("SELECT * FROM sources WHERE id=? AND book_id=?", (source_id, book_id)).fetchone()
            if not source:
                raise AccountingError("来源不存在或无权访问", 404)
            draft = conn.execute("SELECT status,version FROM drafts WHERE source_id=? AND book_id=?", (source_id, book_id)).fetchone()
            result = self._row(conn, source)
            linked = conn.execute("SELECT 1 FROM ai_bank_links WHERE bank_source_id=?", (source_id,)).fetchone()
            from ..settlements.service import SettlementService
            allocated = SettlementService(self.db).source_allocated(conn, actor, source) if book['permissions']['review'] else False
            from ..cases.service import CaseService
            from ..business.service import BusinessService
            active_case = bool(CaseService(self.db, BusinessService(self.db)).blocking_issues_in_transaction(conn, actor, source)) if book['permissions']['review'] else False
            canonical_of_other = conn.execute("SELECT 1 FROM bank_source_dispositions WHERE canonical_source_id=? AND book_id=?", (source_id, book_id)).fetchone()
            return {"source_id": source_id, "inactive": bool(result), "disposition": dict(result) if result else None,
                    "can_isolate": bool(book["permissions"]["review"] and book["period_status"] == "open" and
                                         source["source_kind"] == "bank" and draft and draft["status"] == "draft" and
                                         not result and not linked and not allocated and not active_case and not canonical_of_other),
                    "expected_version": draft["version"] if draft else None}

    def confirm(self, actor, book_id, source_id, canonical_id, request):
        command = {"book_id": book_id, "source_id": source_id, "canonical_source_id": canonical_id,
                   **request.model_dump(exclude={"idempotency_key"})}
        digest = fingerprint(command)
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            source = conn.execute("SELECT * FROM sources WHERE id=? AND book_id=?", (source_id, book_id)).fetchone()
            canonical = conn.execute("SELECT * FROM sources WHERE id=? AND book_id=?", (canonical_id, book_id)).fetchone()
            if not source or not canonical:
                raise AccountingError("来源不存在或无权访问", 404)
            previous = conn.execute("SELECT * FROM bank_source_dispositions WHERE firm_id=? AND reviewed_by=? AND idempotency_key=?",
                                    (actor.firm_id, actor.user_id, request.idempotency_key)).fetchone()
            if previous:
                if previous["request_fingerprint"] != digest:
                    raise AccountingError("操作编号已用于另一项核验，不能覆盖回执", 409)
                return {"inactive": True, "duplicate": True, "disposition": dict(previous)}
            if source_id == canonical_id:
                raise AccountingError("主来源与重复副本不能相同", 409)
            if book["period_status"] != "open":
                raise AccountingError("该会计期间已关闭，不可隔离", 403)
            if self._row(conn, source) or self._row(conn, canonical):
                raise AccountingError("来源已被隔离，不能反向选择或建立隔离链", 409)
            if conn.execute("SELECT 1 FROM bank_source_dispositions WHERE canonical_source_id=? AND book_id=?", (source_id, book_id)).fetchone():
                raise AccountingError("该来源已是其他副本的主来源，不能再隔离", 409)
            candidate = conn.execute(DUPLICATE_SQL + " AND o.id=?", (source_id, book_id, canonical_id)).fetchone()
            if not candidate:
                raise AccountingError("两项来源不符合相同交易候选事实，不能隔离", 409)
            if candidate["distinguished"]:
                raise AccountingError("该对来源已核验为独立交易，不能覆盖历史决定", 409)
            if source["fingerprint"] != request.source_fingerprint or canonical["fingerprint"] != request.canonical_fingerprint:
                raise AccountingError("来源事实已变化，请重新核验", 409)
            draft = conn.execute("SELECT * FROM drafts WHERE source_id=? AND book_id=?", (source_id, book_id)).fetchone()
            if not draft or draft["status"] != "draft" or draft["version"] != request.expected_version:
                raise AccountingError("重复副本草稿已变化或已冻结，请重新核验", 409)
            if conn.execute("SELECT 1 FROM ai_bank_links WHERE bank_source_id=?", (source_id,)).fetchone():
                raise AccountingError("重复副本已经关联业务事项，不能隔离，请保留原关联并处理另一副本", 409)
            from ..settlements.service import SettlementService
            if SettlementService(self.db).source_allocated(conn, actor, source):
                raise AccountingError('重复副本已有有效核销分配，须先通过独立核销撤销流程处理，不能隔离', 409)
            from ..cases.service import CaseService
            from ..business.service import BusinessService
            if CaseService(self.db, BusinessService(self.db)).blocking_issues_in_transaction(conn, actor, source):
                raise AccountingError('重复副本还有未关闭的异常或补件，请先完成原任务再隔离，以保留补件核验和月结闭环', 409)
            if draft["exported_at"] is not None:
                raise AccountingError("重复副本已经导出，不可隔离", 409)
            stamp = now()
            if self.db.is_postgres:
                result_id = conn.execute("SELECT accounting.confirm_same_bank_transaction(?,?,?,?,?,?,?,?,?)",
                    (book_id, source_id, canonical_id, request.expected_version, request.source_fingerprint,
                     request.canonical_fingerprint, request.reason, request.idempotency_key, digest)).fetchone()[0]
            else:
                result_id = uuid4().hex
                # Version invalidation precedes the immutable isolation receipt.
                conn.execute("UPDATE drafts SET version=version+1 WHERE id=? AND book_id=?", (draft["id"], book_id))
                conn.execute("INSERT INTO bank_source_dispositions(id,firm_id,book_id,company_id,source_id,canonical_source_id,source_fingerprint,canonical_fingerprint,expected_version,reason,reviewed_by,idempotency_key,request_fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (result_id, actor.firm_id, book_id, book["company_id"], source_id, canonical_id, request.source_fingerprint,
                     request.canonical_fingerprint, request.expected_version, request.reason, actor.user_id,
                     request.idempotency_key, digest, stamp))
                conn.execute("INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
                    (actor.firm_id, book_id, actor.user_id, "bank.duplicate.isolate", source_id,
                     json.dumps({"canonical_source_id": canonical_id, "reason": request.reason, "disposition_id": result_id}, ensure_ascii=False), stamp))
            result = conn.execute("SELECT * FROM bank_source_dispositions WHERE id=?", (result_id,)).fetchone()
            return {"inactive": True, "duplicate": False, "disposition": dict(result)}
