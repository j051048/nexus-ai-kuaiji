"""统一待办投影的刷新与脏企业解析。

投影只保存与操作者无关的任务行与可见性键；授权仍由读取时的实时谓词与
（PostgreSQL）逐行 RLS 决定。源表触发器把企业标记为脏，刷新在系统身份
（独立维护进程的 accounting_system）下逐企业重建；读取路径在干净时使用投影、
脏时回退实时查询，因此正确性从不依赖投影的新鲜度。legacy_migration 的
迁移支线暂不进投影，继续走实时分支。
"""

import logging
import threading
from datetime import datetime, timezone
from time import perf_counter

from ..accounting.queries import inactive_source_sql
from ..closing.queries import receipt_valid_sql

logger = logging.getLogger(__name__)

# 每轮刷新最多处理的企业数；更多脏企业由后续轮次继续收敛。
REFRESH_PER_PASS = 5

INSERTS = (
    "INSERT INTO workspace_task_projections(firm_id,company_id,kind,ref,book_id,event_id,"
    "draft_id,batch_id,applicant_id,department_id,assignee_id,recipient_id,created_by,"
    "submission_mode,ref_kind,ref_detail,source_open,state,title,due_on,updated_at,"
    "amount_cents) "
)


def _title(db, column, fallback):
    value = (
        f"({column})::jsonb->>'description'"
        if db.is_postgres
        else f"json_extract(CASE WHEN json_valid({column}) THEN {column} ELSE '{{}}' END,'$.description')"
    )
    return f"COALESCE(NULLIF(trim({value}),''),'{fallback}')"


def _amount(column):
    return f"CAST(REPLACE({column},'.','') AS BIGINT)"


def _event_amount(db, column):
    value = (
        f"({column})::jsonb->>'amount'" if db.is_postgres else f"json_extract({column},'$.amount')"
    )
    return _amount(value)


def refresh_statements(db, firm_id, company_id):
    """Actor-independent rebuild statements for one company; params per statement."""
    statements = []
    scope = (firm_id, company_id)

    # 01 员工录入草稿（可见性由读取时的成员/部门谓词决定）。
    statements.append(
        (
            INSERTS + "SELECT d.firm_id,d.company_id,'intake',d.id,NULL,NULL,NULL,NULL,NULL,"
            "d.department_id,NULL,NULL,d.created_by,'','intake','',0,d.status,"
            + _title(db, "d.fields_json", "继续录入资料")
            + ",NULL,d.updated_at,NULL FROM intake_drafts d "
            "JOIN firm_clients fc ON fc.firm_id=d.firm_id AND fc.company_id=d.company_id "
            "AND fc.status='active' WHERE d.firm_id=? AND d.company_id=? AND d.status='draft'",
            scope,
        )
    )
    events = (
        "SELECT e.* FROM business_events e JOIN firm_clients fc ON fc.firm_id=e.firm_id "
        "AND fc.company_id=e.company_id AND fc.status='active' WHERE e.firm_id=? AND e.company_id=?"
    )
    for kind, extra in (
        ("business", "e.status<>'handed_off'"),
        ("approval", "e.submission_mode='department' AND e.status='submitted'"),
        ("handoff", "e.submission_mode='direct' AND e.status='submitted'"),
        ("finance", "e.status='department_approved'"),
        ("handoff", "e.status='finance_approved'"),
    ):
        statements.append(
            (
                INSERTS + "SELECT e.firm_id,e.company_id,?,e.id,NULL,e.id,NULL,NULL,e.applicant_id,"
                "e.department_id,NULL,NULL,NULL,e.submission_mode,'','',0,e.status,"
                + _title(db, "e.payload", "业务事项")
                + ",NULL,e.updated_at,"
                + _event_amount(db, "e.payload")
                + f" FROM ({events}) e WHERE {extra}",
                (kind, *scope),
            )
        )

    source_open = (
        "CASE WHEN COALESCE(t.book_id,(SELECT bm.book_id FROM business_mappings bm "
        "WHERE bm.event_id=t.event_id)) IS NULL OR EXISTS(SELECT 1 FROM drafts d "
        "JOIN periods p ON p.book_id=d.book_id WHERE d.id=COALESCE(t.draft_id,"
        "(SELECT bm.draft_id FROM business_mappings bm WHERE bm.event_id=t.event_id)) "
        "AND d.status='draft' AND p.status='open') THEN 1 ELSE 0 END"
        if not db.is_postgres
        else "CASE WHEN accounting.case_source_open(t.id) THEN 1 ELSE 0 END"
    )
    statements.append(
        (
            INSERTS + "SELECT t.firm_id,t.company_id,'case',t.id,"
            "COALESCE(t.book_id,(SELECT bm.book_id FROM business_mappings bm WHERE bm.event_id=t.event_id)),"
            "t.event_id,t.draft_id,NULL,NULL,NULL,t.assignee_id,t.recipient_id,t.created_by,"
            "'',t.kind,t.priority,"
            + source_open
            + ",t.state,CASE WHEN t.kind='supplement' THEN '补充业务资料' ELSE '处理会计异常' END,"
            "t.due_on,t.updated_at,NULL FROM case_tasks t WHERE t.firm_id=? AND t.company_id=? "
            "AND t.state IN ('open','awaiting_info','response_submitted','in_review')",
            scope,
        )
    )

    inactive = inactive_source_sql(db.dialect, "s")
    statements.append(
        (
            INSERTS + "SELECT b.firm_id,b.company_id,'review',d.id,d.book_id,"
            "(SELECT be.id FROM business_mappings bm JOIN business_events be ON be.id=bm.event_id "
            "WHERE bm.draft_id=d.id),d.id,NULL,"
            "(SELECT be.applicant_id FROM business_mappings bm JOIN business_events be "
            "ON be.id=bm.event_id WHERE bm.draft_id=d.id),NULL,NULL,NULL,d.source_id,"
            "'','review','',0,d.status,d.description,NULL,s.date,s.amount_cents "
            "FROM drafts d JOIN sources s ON s.id=d.source_id AND s.book_id=d.book_id "
            "JOIN books b ON b.id=d.book_id JOIN periods p ON p.book_id=d.book_id "
            "AND p.period=b.period WHERE b.firm_id=? AND b.company_id=? AND d.status='draft' "
            "AND p.status='open' AND NOT "
            + inactive
            + " AND NOT EXISTS(SELECT 1 FROM ai_bank_links l "
            "WHERE l.bank_source_id=d.source_id AND l.book_id=d.book_id) "
            "AND NOT EXISTS(SELECT 1 FROM proposal_jobs j WHERE j.draft_id=d.id "
            "AND j.status IN ('waiting','queued','running','retry_wait')) "
            "AND NOT EXISTS(SELECT 1 FROM case_tasks ct WHERE COALESCE(ct.draft_id,"
            "(SELECT bm.draft_id FROM business_mappings bm WHERE bm.event_id=ct.event_id))=d.id "
            "AND ct.state IN ('open','awaiting_info','response_submitted','in_review'))",
            scope,
        )
    )
    statements.append(
        (
            INSERTS + "SELECT b.firm_id,b.company_id,'export',b.id,b.id,NULL,NULL,NULL,NULL,NULL,"
            "NULL,NULL,NULL,'','export','',0,'approved',b.name||' · '||b.period||"
            "' · 待导出凭证 · '||COUNT(*)||' 张',NULL,MAX(s.date),SUM(s.amount_cents) "
            "FROM books b JOIN periods p ON p.book_id=b.id AND p.period=b.period "
            "AND p.status='open' JOIN drafts d ON d.book_id=b.id AND d.status='approved' "
            "JOIN sources s ON s.id=d.source_id AND s.book_id=d.book_id "
            "WHERE b.firm_id=? AND b.company_id=? AND NOT "
            + inactive
            + " GROUP BY b.id,b.firm_id,b.company_id,b.name,b.period",
            scope,
        )
    )

    newest_ocr = (
        "NOT EXISTS(SELECT 1 FROM ocr_jobs n WHERE n.event_id=e.event_id "
        "AND n.attachment_id=e.attachment_id AND (n.created_at>e.created_at OR "
        "(n.created_at=e.created_at AND n.id>e.id)))"
    )
    statements.append(
        (
            INSERTS + "SELECT e.firm_id,e.company_id,'ocr',e.id,"
            "(SELECT bm.book_id FROM business_mappings bm WHERE bm.event_id=be.id),"
            "e.event_id,NULL,e.batch_id,be.applicant_id,be.department_id,NULL,NULL,NULL,"
            "be.submission_mode,'','',0,e.status,'票据识别任务',NULL,e.updated_at,"
            + _event_amount(db, "be.payload")
            + " "
            "FROM ocr_jobs e JOIN business_events be ON be.id=e.event_id "
            "JOIN firm_clients fc ON fc.firm_id=e.firm_id AND fc.company_id=e.company_id "
            "AND fc.status='active' WHERE e.firm_id=? AND e.company_id=? "
            "AND e.status IN ('queued','running','retry_wait','failed','interrupted','stale') "
            "AND e.expected_fact_version=be.fact_version AND " + newest_ocr,
            scope,
        )
    )
    newest_proposal = (
        "NOT EXISTS(SELECT 1 FROM proposal_jobs n WHERE n.draft_id=e.draft_id "
        "AND (n.created_at>e.created_at OR (n.created_at=e.created_at AND n.id>e.id)))"
    )
    statements.append(
        (
            INSERTS + "SELECT e.firm_id,e.company_id,'proposal',e.id,e.book_id,e.event_id,"
            "e.draft_id,NULL,NULL,NULL,NULL,NULL,NULL,'','proposal','',0,e.status,"
            "'分录草稿生成任务',NULL,e.updated_at,"
            + _event_amount(db, "be.payload")
            + " FROM proposal_jobs e "
            "JOIN business_events be ON be.id=e.event_id JOIN drafts d ON d.id=e.draft_id "
            "AND d.book_id=e.book_id JOIN periods p ON p.book_id=e.book_id "
            "WHERE e.firm_id=? AND e.company_id=? AND e.status IN "
            "('waiting','queued','running','retry_wait','needs_attention','failed',"
            "'interrupted','stale') AND p.status='open' AND d.status='draft' "
            "AND e.expected_fact_version=be.fact_version AND "
            + newest_proposal
            + " AND NOT EXISTS(SELECT 1 FROM case_tasks ct WHERE ct.event_id=e.event_id "
            "AND ct.state IN ('open','awaiting_info','response_submitted','in_review'))",
            scope,
        )
    )

    receipt = receipt_valid_sql(db.dialect, "x")
    statements.append(
        (
            INSERTS + "SELECT b.firm_id,b.company_id,'delivery',x.id,x.book_id,NULL,NULL,NULL,"
            "NULL,NULL,NULL,NULL,NULL,'','delivery','',0,'unverified',"
            "'核对财务软件导入结果',NULL,x.created_at,NULL FROM exports x "
            "JOIN books b ON b.id=x.book_id WHERE b.firm_id=? AND b.company_id=? AND NOT "
            + receipt,
            scope,
        )
    )
    statements.append(
        (
            INSERTS + "SELECT e.firm_id,e.company_id,'alert',e.id,NULL,NULL,NULL,NULL,NULL,NULL,"
            "NULL,NULL,NULL,'','alert','',0,e.state,e.code,NULL,e.last_seen_at,NULL "
            "FROM ops_alerts e JOIN firm_clients fc ON fc.firm_id=e.firm_id "
            "AND fc.company_id=e.company_id AND fc.status='active' "
            "WHERE e.firm_id=? AND e.company_id=? AND e.state='open'",
            scope,
        )
    )
    material_open = "AND accounting.service_material_open(e.id) " if db.is_postgres else ""
    material_period = (
        ""
        if db.is_postgres
        else "JOIN periods mp ON mp.book_id=e.book_id AND mp.period=e.period AND mp.status='open' "
    )
    statements.append(
        (
            INSERTS + "SELECT e.firm_id,e.company_id,'material',e.id,e.book_id,NULL,NULL,NULL,"
            "NULL,NULL,e.assignee_user_id,NULL,ma.created_by,'','material','',0,"
            "COALESCE(ma.state,'pending'),e.title,e.due_on,"
            "COALESCE(ma.created_at,e.created_at),NULL FROM material_requests e "
            "JOIN companies mc ON mc.id=e.company_id AND mc.status='active' "
            "JOIN firm_clients mfc ON mfc.company_id=e.company_id AND mfc.firm_id=e.firm_id "
            "AND mfc.status='active' "
            + material_period
            + "LEFT JOIN material_request_actions ma ON ma.request_id=e.id AND ma.version="
            "(SELECT MAX(mx.version) FROM material_request_actions mx WHERE mx.request_id=e.id) "
            "WHERE e.firm_id=? AND e.company_id=? " + material_open,
            scope,
        )
    )
    return statements


def refresh_company(conn, db, firm_id, company_id):
    """Rebuild one company's projection rows inside the caller's transaction."""
    started = perf_counter()
    stamp = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "DELETE FROM workspace_task_projections WHERE firm_id=? AND company_id=?",
        (firm_id, company_id),
    )
    statements = refresh_statements(db, firm_id, company_id)
    rows = 0
    for sql, params in statements:
        rows += conn.execute(sql, params).rowcount or 0
    conn.execute(
        "INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,"
        "dirty_at,built_at,failures) SELECT ?,?,1,1,COALESCE((SELECT dirty_at FROM "
        "workspace_projection_state WHERE firm_id=? AND company_id=?),?),?,0 "
        "WHERE NOT EXISTS(SELECT 1 FROM "
        "workspace_projection_state WHERE firm_id=? AND company_id=?)",
        (firm_id, company_id, firm_id, company_id, stamp, stamp, firm_id, company_id),
    )
    conn.execute(
        "UPDATE workspace_projection_state SET built_revision=revision,built_at=?,failures=0 "
        "WHERE firm_id=? AND company_id=?",
        (stamp, firm_id, company_id),
    )
    return {"rows": rows, "elapsed_ms": round((perf_counter() - started) * 1000, 1)}


def mark_failure(conn, firm_id, company_id):
    """Record one rebuild failure for a company without touching its dirty state."""
    stamp = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,"
        "dirty_at,built_at,failures) VALUES(?,?,1,0,?,'',1) "
        "ON CONFLICT(firm_id,company_id) DO UPDATE SET failures=failures+1",
        (firm_id, company_id, stamp),
    )


def pending_companies(conn, firm_id, limit):
    """Companies of one firm whose projection is dirty, never built or failing."""
    return [
        (row["firm_id"], row["company_id"])
        for row in conn.execute(
            "SELECT fc.firm_id,fc.company_id,s.revision,s.built_revision,s.failures "
            "FROM firm_clients fc LEFT JOIN workspace_projection_state s "
            "ON s.firm_id=fc.firm_id AND s.company_id=fc.company_id "
            "WHERE fc.firm_id=? AND fc.status='active' AND (s.revision IS NULL "
            "OR s.revision>s.built_revision OR s.failures>0) "
            "ORDER BY (s.revision IS NULL) DESC,COALESCE(s.dirty_at,'') DESC,fc.company_id "
            "LIMIT ?",
            (firm_id, limit),
        )
    ]


def status_snapshot(conn, firm_id, samples=10):
    """Durable gauges for the ops surface: dirty/never-built/failing companies."""
    rows = [
        dict(row)
        for row in conn.execute(
            "SELECT fc.company_id,s.revision,s.built_revision,s.failures,s.dirty_at,s.built_at "
            "FROM firm_clients fc LEFT JOIN workspace_projection_state s "
            "ON s.firm_id=fc.firm_id AND s.company_id=fc.company_id "
            "WHERE fc.firm_id=? AND fc.status='active' ORDER BY fc.company_id",
            (firm_id,),
        )
    ]
    never_built = [row for row in rows if row["revision"] is None]
    dirty = [
        row
        for row in rows
        if row["revision"] is not None and row["revision"] > row["built_revision"]
    ]
    failing = [row for row in rows if (row["failures"] or 0) > 0]
    oldest = min((row["dirty_at"] for row in dirty if row["dirty_at"]), default="")
    return {
        "total": len(rows),
        "dirty": len(dirty),
        "never_built": len(never_built),
        "failing": len(failing),
        "oldest_dirty_at": oldest,
        "samples": [
            {
                "company_id": row["company_id"],
                "revision": row["revision"],
                "built_revision": row["built_revision"],
                "failures": row["failures"] or 0,
                "dirty_at": row["dirty_at"] or "",
                "built_at": row["built_at"] or "",
            }
            for row in [*failing, *dirty, *never_built][:samples]
        ],
    }


def _identity(db):
    return (
        {}
        if getattr(db, "default_role", "accounting_app") == "accounting_projection"
        else {"system": True}
    )


def refresh_company_scoped(db, firm_id, company_id):
    """Refresh one company with the system identity plus a company write lock.

    单企业独立事务：共享全局门 + 企业锁，不再抢占全库独占锁。
    返回 "rebuilt"/"skipped"/"failed"。
    """
    try:
        with db.transaction(
            write=True,
            write_scope="company",
            company_id=company_id,
            firm_id=firm_id,
            **_identity(db),
        ) as conn:
            state = conn.execute(
                "SELECT revision,built_revision,failures FROM workspace_projection_state "
                "WHERE firm_id=? AND company_id=?",
                (firm_id, company_id),
            ).fetchone()
            if state and state["built_revision"] >= state["revision"] and not state["failures"]:
                return "skipped"
            refresh_company(conn, db, firm_id, company_id)
            return "rebuilt"
    except Exception:
        try:
            with db.transaction(
                write=True,
                write_scope="company",
                company_id=company_id,
                firm_id=firm_id,
                **_identity(db),
            ) as conn:
                mark_failure(conn, firm_id, company_id)
        except Exception:
            try:
                with db.transaction(write=True, **_identity(db)) as conn:
                    mark_failure(conn, firm_id, company_id)
            except Exception:
                pass
        logger.warning("统一待办投影重建失败，读取路径已回退实时查询")
        return "failed"


class ProjectionRefresher:
    """API 进程内的轻量后台刷新器：读请求只唤醒，绝不在请求内写库。

    轮询间隔内做一次低成本脏企业扫描，保证部署预热与失败自愈不依赖读流量；
    多实例部署时重复刷新由企业锁串行化，结果幂等。
    """

    def __init__(self, db, poll_seconds=2.0):
        self.db = db
        self.poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._event = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    def wake(self, firm_id):
        if not firm_id:
            return
        with self._lock:
            self._pending.add(firm_id)
        self._event.set()

    def drain(self):
        """Synchronously process queued firms (tests / graceful shutdown)."""
        with self._lock:
            pending, self._pending = self._pending, set()
        for firm_id in pending:
            self._refresh_firm(firm_id)
        return len(pending)

    def _refresh_firm(self, firm_id):
        try:
            with self.db.transaction(**_identity(self.db)) as conn:
                todo = pending_companies(conn, firm_id, REFRESH_PER_PASS + 1)
        except Exception:
            logger.warning("统一待办投影队列读取失败，将在下一轮重试")
            return
        for _firm, company_id in todo[:REFRESH_PER_PASS]:
            refresh_company_scoped(self.db, firm_id, company_id)
        if len(todo) > REFRESH_PER_PASS:
            self.wake(firm_id)

    def _sweep(self):
        try:
            with self.db.transaction(**_identity(self.db)) as conn:
                firms = [
                    row[0]
                    for row in conn.execute(
                        "SELECT DISTINCT fc.firm_id FROM firm_clients fc "
                        "LEFT JOIN workspace_projection_state s "
                        "ON s.firm_id=fc.firm_id AND s.company_id=fc.company_id "
                        "WHERE fc.status='active' AND (s.revision IS NULL "
                        "OR s.revision>s.built_revision OR s.failures>0) "
                        "ORDER BY fc.firm_id LIMIT 20"
                    )
                ]
        except Exception:
            return
        for firm_id in firms:
            self.wake(firm_id)

    def _tick(self):
        # Sweep first, then consume the firms discovered by this pass.
        self._sweep()
        self.drain()

    def run_once(self):
        """Run one deterministic maintenance pass for the dedicated worker/tests."""
        self._tick()

    def _loop(self):
        while not self._stop.is_set():
            self._event.wait(self.poll_seconds)
            self._event.clear()
            if self._stop.is_set():
                break
            try:
                self._tick()
            except Exception:
                logger.warning("统一待办投影后台刷新暂不可用，将在下一轮重试")
        self.drain()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="projection-refresher", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._event.set()
        if self._thread:
            self._thread.join(timeout=5)


def reachable_companies_sql(alias="s"):
    """Superset of companies whose tasks could be visible to one actor."""
    return (
        f"SELECT cm.company_id FROM company_memberships cm WHERE cm.user_id=? "
        f"UNION SELECT b.company_id FROM book_access ba JOIN books b ON b.id=ba.book_id "
        f"AND b.firm_id=ba.firm_id WHERE ba.user_id=? "
        f"UNION SELECT fc.company_id FROM memberships m JOIN firm_clients fc ON fc.firm_id=m.firm_id "
        f"AND fc.status='active' WHERE m.user_id=?"
    )


def dirty_companies_sql():
    return (
        "SELECT s.firm_id,s.company_id,s.revision,s.built_revision,s.dirty_at FROM ("
        + reachable_companies_sql()
        + ") c JOIN workspace_projection_state s ON s.company_id=c.company_id "
        "WHERE s.firm_id=? AND s.revision>s.built_revision"
    )


def dirty_companies(conn, actor, limit):
    rows = conn.execute(
        dirty_companies_sql() + " ORDER BY (s.built_revision=0) DESC,s.dirty_at DESC LIMIT ?",
        (actor.user_id, actor.user_id, actor.user_id, actor.firm_id, limit),
    ).fetchall()
    return [(row["firm_id"], row["company_id"]) for row in rows]


def companies_missing_state(conn, actor, limit):
    rows = conn.execute(
        "SELECT c.company_id FROM (" + reachable_companies_sql() + ") c "
        "WHERE NOT EXISTS(SELECT 1 FROM workspace_projection_state s WHERE "
        "s.firm_id=? AND s.company_id=c.company_id) LIMIT ?",
        (actor.user_id, actor.user_id, actor.user_id, actor.firm_id, limit),
    ).fetchall()
    return [(actor.firm_id, row["company_id"]) for row in rows]


def projection_clean(conn, actor):
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM (" + reachable_companies_sql() + ") c "
        "WHERE NOT EXISTS(SELECT 1 FROM workspace_projection_state s WHERE "
        "s.firm_id=? AND s.company_id=c.company_id AND s.revision<=s.built_revision)",
        (actor.user_id, actor.user_id, actor.user_id, actor.firm_id),
    ).fetchone()
    return row["count"] == 0
