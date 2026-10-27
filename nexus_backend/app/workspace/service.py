from datetime import datetime
import json
import logging
from zoneinfo import ZoneInfo

from app.accounting.access import FIRM_MANAGERS
from app.accounting.domain import AccountingError
from app.accounting.queries import inactive_source_sql, page_bounds
from app.closing.queries import receipt_valid_sql
from app.jobs.visibility import book_edit_scope, event_scope
from .projection import (
    pending_companies,
    reachable_companies_sql,
    refresh_company_scoped,
    status_snapshot,
)

logger = logging.getLogger(__name__)


KINDS = {
    "intake",
    "business",
    "approval",
    "finance",
    "handoff",
    "case",
    "review",
    "export",
    "ocr",
    "proposal",
    "delivery",
    "alert",
    "migration",
    "material",
}
TEMPLATES = {
    "reimbursement": "报销",
    "procurement_expense": "采购 / 费用",
    "daily_cashflow": "日常收支",
}
ALERTS = {
    "budget_exhausted": "模型预算已用尽",
    "quota_overrun": "模型费用超限",
    "usage_uncertain": "模型费用待核实",
    "task_failures": "后台任务失败",
    "exception_backlog": "异常处理积压",
    "queue_wait": "任务排队过久",
    "worker_unavailable": "后台处理不可用",
}


class WorkspaceService:
    def __init__(self, db, accounting, business, review, cases, projection_db=None):
        self.db, self.accounting, self.business, self.review, self.cases = (
            db,
            accounting,
            business,
            review,
            cases,
        )
        self.projection_db = projection_db
        self.access = accounting.access
        # 后台投影刷新器（由 create_app 注入）；未注入时读取始终回退实时查询。
        self.refresher = None

    def profile(self, actor):
        with self.db.transaction(actor=actor) as conn:
            return self.profile_in_connection(conn, actor)

    def profile_in_connection(self, conn, actor):
        # One projection resolves the live actor, grants, company choices and
        # book capabilities. It remains inside the caller's RLS transaction;
        # no authorization or accounting snapshot survives this request.
        scope, params = self.accounting._summary_scope(actor)
        scope += """ AND c.status='active' AND EXISTS(SELECT 1 FROM active_actor aa
            WHERE aa.firm_role IN ('owner','admin') OR (a.role IS NOT NULL
              AND (aa.firm_role IS NOT NULL OR cm.role IN ('owner','admin','finance'))))"""
        briefing_json = (
            "(SELECT COALESCE(json_agg(json_build_object("
            "'book_id',b.id,'book_name',b.name,'period',b.period,"
            "'company_id',b.company_id,'company_name',b.company_name)),'[]'::json) "
            "FROM closed_briefings b)"
            if self.db.is_postgres
            else "(SELECT COALESCE(json_group_array(json_object("
            "'book_id',b.id,'book_name',b.name,'period',b.period,"
            "'company_id',b.company_id,'company_name',b.company_name)),'[]') "
            "FROM closed_briefings b)"
        )
        rows = conn.execute(
            """WITH active_actor AS (SELECT u.id,m.role AS firm_role FROM users u
                LEFT JOIN memberships m ON m.user_id=u.id AND m.firm_id=?
                WHERE u.id=? AND u.active=1), visible_books AS ("""
            + scope
            + """), book_capabilities AS (
                SELECT b.company_id,COUNT(*) AS book_count,
                  MAX(CASE WHEN aa.firm_role IN ('owner','admin') OR
                    (b.book_role='reviewer' AND (aa.firm_role='reviewer'
                      OR (aa.firm_role IS NULL AND b.company_role='finance')))
                    THEN 1 ELSE 0 END) AS can_review
                FROM visible_books b CROSS JOIN active_actor aa GROUP BY b.company_id
              ), closed_briefings AS (
                SELECT b.id,b.name,b.period,b.company_id,b.company_name
                FROM visible_books b WHERE b.period_status='closed'
                ORDER BY b.period DESC,b.name,b.id LIMIT 3
              ), visible_companies AS (
                SELECT c.id,c.name,cm.role,cm.department_id,
                    COALESCE(p.mode,'department') AS submission_mode,
                    EXISTS(SELECT 1 FROM departments d WHERE d.company_id=c.id) AS has_department,
                    EXISTS(SELECT 1 FROM department_approvers da
                        JOIN company_memberships member ON member.company_id=da.company_id
                            AND member.user_id=da.user_id
                        WHERE da.firm_id=fc.firm_id AND da.company_id=c.id
                            AND da.user_id=?) AS can_approve,
                    COALESCE(b.book_count,0) AS book_count,
                    COALESCE(b.can_review,0) AS can_review
                    FROM companies c
                    JOIN firm_clients fc ON fc.company_id=c.id AND fc.firm_id=? AND fc.status='active'
                    LEFT JOIN company_memberships cm ON cm.company_id=c.id AND cm.user_id=?
                    LEFT JOIN business_submission_policies p ON p.company_id=c.id AND p.firm_id=fc.firm_id
                    LEFT JOIN book_capabilities b ON b.company_id=c.id
                    CROSS JOIN active_actor aa
                    WHERE c.status='active' AND (aa.firm_role IN ('owner','admin')
                      OR cm.role IS NOT NULL OR b.company_id IS NOT NULL)
              ) SELECT aa.firm_role AS _firm_role,c.*,"""
            + briefing_json
            + """ AS briefings_json FROM active_actor aa
                LEFT JOIN visible_companies c ON 1=1 ORDER BY c.name,c.id""",
            (actor.firm_id, actor.user_id, *params, actor.user_id, actor.firm_id, actor.user_id),
        ).fetchall()
        if not rows:
            raise AccountingError("账号未激活或已停用", 403)
        firm_role = rows[0]["_firm_role"]
        manager = firm_role in FIRM_MANAGERS
        companies = [row for row in rows if row["id"] is not None]
        book_count = sum(row["book_count"] for row in companies)
        can_review = any(row["can_review"] for row in companies)
        can_finance = any(row["role"] == "finance" for row in companies)
        can_approve = any(row["can_approve"] for row in companies)
        can_submit = any(
            row["role"] in {"owner", "admin", "finance"}
            if row["submission_mode"] == "direct"
            else bool(
                row["role"]
                and (row["department_id"] or row["role"] != "employee" and row["has_department"])
            )
            for row in companies
        )
        can_manage = manager or any(row["role"] in {"owner", "admin"} for row in companies)
        raw_briefings = rows[0]["briefings_json"]
        briefings = json.loads(raw_briefings) if isinstance(raw_briefings, str) else raw_briefings
        # Primary jobs precede additive submission/approval/book capabilities.
        # This is a firm-wide presentation identity: management in one company
        # never grants management in another; resource authorization stays live.
        role = (
            "accountant"
            if firm_role in {"owner", "admin", "operator", "reviewer"}
            else "manager"
            if can_manage
            else "finance"
            if can_finance
            else "approver"
            if can_approve
            else "employee"
            if can_submit
            else "accountant"
            if can_review
            else "viewer"
        )
        # Additive read-only presentation identity. It does not alter grants;
        # API authorization still uses the existing capability/scope checks.
        display_role = {
            "owner": "firm_owner",
            "admin": "admin",
            "reviewer": "reviewer",
            "operator": "accountant",
        }.get(firm_role, role)
        result = {
            "role": role,
            "display_role": display_role,
            "can_submit": can_submit,
            "can_approve": can_approve,
            "can_finance": can_finance,
            "can_review": can_review,
            "can_accounting": bool(book_count),
            "can_manage": can_manage,
            "can_operations": can_manage or can_finance,
            # Firm-manager-only entry for the firm's own production (model cost
            # and run monitoring) surface. Kept additive so existing clients that
            # read can_operations stay compatible.
            "can_firm_operations": bool(manager),
            "companies": [{"id": c["id"], "name": c["name"]} for c in companies],
            "book_count": book_count,
        }
        if briefings:
            result["briefings"] = briefings
        return result

    def _task_gates(self, conn, actor):
        """Resolve the live firm role plus capability probes for pruning.

        One statement replaces the former role lookup, so a task page keeps the
        same number of database round trips. The probes only drop branches that
        provably yield no rows for the current live grants; they can never widen
        visibility. Every kept branch keeps its own authorization predicate and
        PostgreSQL FORCE RLS remains the final authority.
        """
        row = conn.execute(
            """SELECT m.role AS firm_role,
              EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id=? AND fm.user_id=? AND fm.role='reviewer') AS firm_reviewer,
              EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.user_id=?) AS company_member,
              EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.user_id=? AND cm.role IN ('owner','admin')) AS company_manager,
              EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.user_id=? AND cm.role='finance') AS finance,
              EXISTS(SELECT 1 FROM department_approvers da WHERE da.firm_id=? AND da.user_id=?) AS approver,
              EXISTS(SELECT 1 FROM book_access ba WHERE ba.firm_id=? AND ba.user_id=?) AS book_access,
              EXISTS(SELECT 1 FROM book_access ba WHERE ba.firm_id=? AND ba.user_id=? AND ba.role='reviewer') AS book_reviewer,
              NOT EXISTS(SELECT 1 FROM ("""
            + reachable_companies_sql()
            + """) c WHERE NOT EXISTS(
                SELECT 1 FROM workspace_projection_state s WHERE s.firm_id=? AND s.company_id=c.company_id
                AND s.revision<=s.built_revision)) AS projection_clean
            FROM users u LEFT JOIN memberships m ON m.user_id=u.id AND m.firm_id=?
            WHERE u.id=? AND u.active=1""",
            (
                actor.firm_id,
                actor.user_id,
                actor.user_id,
                actor.user_id,
                actor.user_id,
                actor.firm_id,
                actor.user_id,
                actor.firm_id,
                actor.user_id,
                actor.firm_id,
                actor.user_id,
                actor.user_id,
                actor.user_id,
                actor.user_id,
                actor.firm_id,
                actor.firm_id,
                actor.user_id,
            ),
        ).fetchone()
        if not row:
            raise AccountingError("账号未激活或已停用", 403)
        firm_manager = row["firm_role"] in FIRM_MANAGERS
        # Superset of every edit-scope holder: firm managers, company managers,
        # company finance or any explicit book assignment.
        book_worker = (
            firm_manager
            or bool(row["company_manager"])
            or bool(row["finance"])
            or bool(row["book_access"])
            or bool(row["firm_reviewer"])
        )
        # review_books is empty exactly when the actor has no manager or
        # reviewer grant, so review/export/delivery cannot yield rows.
        reviewer = firm_manager or bool(row["firm_reviewer"]) or bool(row["book_reviewer"])
        member = bool(row["company_member"]) or bool(row["approver"]) or book_worker
        manager = firm_manager or bool(row["company_manager"])
        any_task = member or reviewer
        gates = {
            "intake": member,
            "business": member,
            "approval": bool(row["approver"]),
            "finance": bool(row["finance"]),
            "handoff": book_worker,
            "case": any_task,
            "review": reviewer,
            "export": reviewer,
            "delivery": reviewer,
            "ocr": any_task,
            "proposal": book_worker,
            "alert": manager,
            "material": any_task,
            "migration": book_worker,
        }
        return row["firm_role"], gates, bool(row["projection_clean"])

    def projection_status(self, actor):
        """只读诊断：脏、从未构建与连续失败的企业分布（事务所管理层）。"""
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            return status_snapshot(conn, actor.firm_id)

    def rebuild_projections(self, actor, limit=20):
        """管理端批量重建：每次最多 50 家，返回已重建、失败与剩余数量。"""
        if type(limit) is not int or not 1 <= limit <= 50:
            raise AccountingError("每次最多重建 50 家企业", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            pending = pending_companies(conn, actor.firm_id, limit)
        rebuilt = failed = 0
        projection_db = self.projection_db or self.db
        for firm_id, company_id in pending:
            outcome = refresh_company_scoped(projection_db, firm_id, company_id)
            if outcome == "rebuilt":
                rebuilt += 1
            elif outcome == "failed":
                failed += 1
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            remaining = len(pending_companies(conn, actor.firm_id, 1000))
        return {"rebuilt": rebuilt, "failed": failed, "remaining": remaining}

    def _projected_query(self, conn, actor, role_gates=None):
        """Same task projection as _query, served from the materialized rows.

        条件分两类：与操作者无关的结构条件已在刷新时冻结；此处只保留实时授权
        谓词（成员、部门审批、账套授权、复核范围）与随状态变化的展示字段。
        """
        events, ep = event_scope(actor)
        edits, bp = book_edit_scope(actor)
        firm_role, gates, _ = role_gates or self._task_gates(conn, actor)
        reviews, rp, _ = self.review._review_scope(conn, actor, role=firm_role)
        params = [*ep, *rp, *bp]
        ctes = f"""WITH visible_events AS (SELECT e.* FROM business_events e WHERE {events}),
          review_books AS ({reviews}),
          edit_books AS (SELECT b.* FROM books b WHERE {edits})"""
        columns = [
            "kind",
            "ref",
            "company_id",
            "book_id",
            "event_id",
            "draft_id",
            "batch_id",
            "title",
            "state",
            "priority",
            "due_on",
            "updated_at",
            "bucket",
            "action_label",
            "responsible",
            "amount_cents",
        ]
        parts = []

        def branch(kind, select_list, where, args=()):
            if not gates.get(kind, True):
                return
            parts.append(
                "SELECT "
                + ",".join(value + " AS " + column for value, column in zip(select_list, columns))
                + " FROM workspace_task_projections p WHERE "
                + where
            )
            params.extend(args)

        branch(
            "intake",
            [
                "'intake'",
                "p.ref",
                "p.company_id",
                "NULL",
                "NULL",
                "NULL",
                "NULL",
                "p.title",
                "p.state",
                "2",
                "NULL",
                "p.updated_at",
                "'action'",
                "'继续录入'",
                "'我'",
                "NULL",
            ],
            "p.kind='intake' AND p.state='draft' AND p.firm_id=? AND p.created_by=? "
            "AND EXISTS(SELECT 1 FROM firm_clients fc WHERE fc.firm_id=p.firm_id "
            "AND fc.company_id=p.company_id AND fc.status='active') "
            "AND EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=p.company_id "
            "AND cm.user_id=? AND (cm.role<>'employee' OR cm.department_id=p.department_id))",
            (actor.firm_id, actor.user_id, actor.user_id),
        )
        branch(
            "business",
            [
                "'business'",
                "p.ref",
                "p.company_id",
                "NULL",
                "p.ref",
                "NULL",
                "NULL",
                "p.title",
                "p.state",
                "CASE WHEN p.state='returned' THEN 1 ELSE 2 END",
                "NULL",
                "p.updated_at",
                "CASE WHEN p.state IN ('draft','returned') THEN 'action' ELSE 'waiting' END",
                "CASE WHEN p.state='returned' THEN '修改并重提' WHEN p.state='draft' "
                "THEN '继续填写' ELSE '查看进度' END",
                "CASE WHEN p.state='submitted' AND p.submission_mode='direct' THEN '授权会计' "
                "WHEN p.state='submitted' THEN '部门审批人' WHEN p.state='department_approved' "
                "THEN '企业财务' WHEN p.state='finance_approved' THEN '企业财务移交' ELSE '我' END",
                "p.amount_cents",
            ],
            "p.kind='business' AND p.state<>'handed_off' AND p.firm_id=? AND p.applicant_id=? "
            "AND EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=p.company_id "
            "AND cm.user_id=?)",
            (actor.firm_id, actor.user_id, actor.user_id),
        )
        branch(
            "approval",
            [
                "'approval'",
                "p.ref",
                "p.company_id",
                "NULL",
                "p.ref",
                "NULL",
                "NULL",
                "p.title",
                "p.state",
                "2",
                "NULL",
                "p.updated_at",
                "'action'",
                "'审批事项'",
                "'我'",
                "p.amount_cents",
            ],
            "p.kind='approval' AND p.submission_mode='department' AND p.state='submitted' "
            "AND p.applicant_id<>? AND EXISTS(SELECT 1 FROM department_approvers da "
            "JOIN company_memberships cm ON cm.company_id=da.company_id AND cm.user_id=da.user_id "
            "WHERE da.firm_id=? AND da.company_id=p.company_id AND da.department_id=p.department_id "
            "AND da.user_id=?)",
            (actor.user_id, actor.firm_id, actor.user_id),
        )
        period = self.business.submission.period_sql("be")
        branch(
            "handoff",
            [
                "'handoff'",
                "p.ref",
                "p.company_id",
                "NULL",
                "p.ref",
                "NULL",
                "NULL",
                "p.title",
                "p.state",
                "2",
                "NULL",
                "p.updated_at",
                "'action'",
                "'接收并生成草稿'",
                "'授权会计'",
                "p.amount_cents",
            ],
            "p.kind='handoff' AND p.submission_mode='direct' AND p.state='submitted' "
            "AND p.applicant_id<>? AND EXISTS(SELECT 1 FROM business_events be "
            "WHERE be.id=p.event_id AND EXISTS(SELECT 1 FROM edit_books b "
            f"WHERE b.company_id=p.company_id AND b.period={period}))",
            (actor.user_id,),
        )
        branch(
            "finance",
            [
                "'finance'",
                "p.ref",
                "p.company_id",
                "NULL",
                "p.ref",
                "NULL",
                "NULL",
                "p.title",
                "p.state",
                "2",
                "NULL",
                "p.updated_at",
                "'action'",
                "'核验资料'",
                "'我'",
                "p.amount_cents",
            ],
            "p.kind='finance' AND p.state='department_approved' AND p.applicant_id<>? "
            "AND EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=p.company_id "
            "AND cm.user_id=? AND cm.role='finance')",
            (actor.user_id, actor.user_id),
        )
        branch(
            "handoff",
            [
                "'handoff'",
                "p.ref",
                "p.company_id",
                "NULL",
                "p.ref",
                "NULL",
                "NULL",
                "p.title",
                "p.state",
                "2",
                "NULL",
                "p.updated_at",
                "'action'",
                "'选择账套并移交'",
                "'我'",
                "p.amount_cents",
            ],
            "p.kind='handoff' AND p.state='finance_approved' "
            "AND EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=p.company_id "
            "AND cm.user_id=? AND cm.role='finance')",
            (actor.user_id,),
        )

        scope, _ = self.cases._scope_sql(actor)
        scope = scope.replace("t.", "p.").replace("p.kind", "p.ref_kind")
        recipient = "p.ref_kind='supplement' AND p.recipient_id=? AND p.state='awaiting_info'"
        independent = (
            "NOT EXISTS(SELECT 1 FROM case_responses r WHERE r.case_id=p.ref AND r.created_by=? "
            "AND NOT EXISTS(SELECT 1 FROM case_responses newer WHERE newer.case_id=r.case_id "
            "AND (newer.created_at>r.created_at OR newer.created_at=r.created_at "
            "AND newer.id>r.id)))"
        )
        manager = (
            "(EXISTS(SELECT 1 FROM review_books rb WHERE rb.id=COALESCE(p.book_id,"
            "(SELECT bm.book_id FROM business_mappings bm WHERE bm.event_id=p.event_id))) "
            "OR EXISTS(SELECT 1 FROM visible_events be JOIN company_memberships cm "
            "ON cm.company_id=be.company_id WHERE be.id=p.event_id AND cm.user_id=? "
            "AND cm.role='finance' AND be.applicant_id<>?))"
        )
        collector = (
            "(p.event_id IS NULL AND p.assignee_id=? AND EXISTS(SELECT 1 FROM edit_books eb "
            "JOIN company_memberships cm ON cm.company_id=eb.company_id WHERE eb.id=p.book_id "
            "AND cm.user_id=? AND cm.role='finance'))"
        )
        actionable = (
            f"(({recipient}) OR ({collector} AND p.state='open') OR ({manager} "
            "AND p.state IN ('open','in_review','response_submitted') "
            "AND (p.assignee_id IS NULL OR p.assignee_id=?) AND " + independent + "))"
        )
        branch(
            "case",
            [
                "'case'",
                "p.ref",
                "p.company_id",
                "NULL",
                "NULL",
                "NULL",
                "NULL",
                "p.title",
                "p.state",
                "CASE p.ref_detail WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END",
                "p.due_on",
                "p.updated_at",
                f"CASE WHEN p.source_open=1 AND {actionable} THEN 'action' ELSE 'waiting' END",
                "'打开处理任务'",
                "CASE WHEN p.state='awaiting_info' THEN '资料提供人' ELSE '任务处理人' END",
                "NULL",
            ],
            "p.kind='case' AND p.state IN ('open','awaiting_info','response_submitted','in_review') "
            "AND (" + scope + ")",
            (actor.user_id,) * 21,
        )
        branch(
            "review",
            [
                "'review'",
                "p.ref",
                "p.company_id",
                "p.book_id",
                "p.event_id",
                "p.draft_id",
                "NULL",
                "p.title",
                "p.state",
                "2",
                "NULL",
                "p.updated_at",
                "'action'",
                "'同屏复核'",
                "'会计复核员'",
                "p.amount_cents",
            ],
            "p.kind='review' AND p.state='draft' "
            "AND EXISTS(SELECT 1 FROM review_books rb WHERE rb.id=p.book_id) "
            "AND (p.applicant_id IS NULL OR p.applicant_id<>?)",
            (actor.user_id,),
        )
        branch(
            "export",
            [
                "'export'",
                "p.ref",
                "p.company_id",
                "p.book_id",
                "NULL",
                "NULL",
                "NULL",
                "p.title",
                "p.state",
                "2",
                "NULL",
                "p.updated_at",
                "'action'",
                "'导出已审核凭证'",
                "'会计交付人'",
                "p.amount_cents",
            ],
            "p.kind='export' AND p.state='approved' "
            "AND EXISTS(SELECT 1 FROM review_books rb WHERE rb.id=p.book_id)",
        )
        ocr_open = (
            "NOT EXISTS(SELECT 1 FROM business_mappings frozen JOIN drafts d "
            "ON d.id=frozen.draft_id AND d.book_id=frozen.book_id JOIN periods p2 "
            "ON p2.book_id=frozen.book_id WHERE frozen.event_id=p.event_id "
            "AND (d.status<>'draft' OR p2.status<>'open'))"
        )
        processor = (
            "(EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=p.company_id "
            "AND cm.user_id=? AND (p.applicant_id=? OR cm.role='finance' OR "
            "EXISTS(SELECT 1 FROM department_approvers da WHERE da.firm_id=p.firm_id "
            "AND da.company_id=p.company_id AND da.department_id=p.department_id "
            "AND da.user_id=?))) OR EXISTS(SELECT 1 FROM edit_books eb WHERE eb.id=p.book_id))"
        )
        branch(
            "ocr",
            [
                "'ocr'",
                "p.ref",
                "p.company_id",
                "NULL",
                "p.event_id",
                "NULL",
                "p.batch_id",
                "p.title",
                "p.state",
                "CASE WHEN p.state IN ('failed','interrupted') THEN 1 ELSE 2 END",
                "NULL",
                "p.updated_at",
                f"CASE WHEN p.state IN ('failed','interrupted','stale') AND {ocr_open} "
                f"AND {processor} THEN 'action' ELSE 'waiting' END",
                "'查看识别任务'",
                "'当前任务经办人'",
                "p.amount_cents",
            ],
            "p.kind='ocr' AND p.state IN ('queued','running','retry_wait','failed',"
            "'interrupted','stale') AND " + processor,
            (actor.user_id,) * 6,
        )
        branch(
            "proposal",
            [
                "'proposal'",
                "p.ref",
                "p.company_id",
                "p.book_id",
                "p.event_id",
                "p.draft_id",
                "NULL",
                "p.title",
                "p.state",
                "1",
                "NULL",
                "p.updated_at",
                "CASE WHEN p.state IN ('needs_attention','failed','interrupted','stale') "
                "THEN 'action' ELSE 'waiting' END",
                "'处理生成任务'",
                "'账套经办人'",
                "p.amount_cents",
            ],
            "p.kind='proposal' AND p.state IN ('waiting','queued','running','retry_wait',"
            "'needs_attention','failed','interrupted','stale') "
            "AND EXISTS(SELECT 1 FROM edit_books b WHERE b.id=p.book_id)",
        )
        branch(
            "delivery",
            [
                "'delivery'",
                "p.ref",
                "p.company_id",
                "p.book_id",
                "NULL",
                "NULL",
                "NULL",
                "p.title",
                "p.state",
                "2",
                "NULL",
                "p.updated_at",
                "'action'",
                "'核验导入回执'",
                "'会计交付人'",
                "NULL",
            ],
            "p.kind='delivery' AND EXISTS(SELECT 1 FROM review_books rb WHERE rb.id=p.book_id)",
        )
        alert_manager = (
            "(EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=p.firm_id AND m.user_id=? "
            "AND m.role IN ('owner','admin')) OR EXISTS(SELECT 1 FROM company_memberships cm "
            "WHERE cm.company_id=p.company_id AND cm.user_id=? AND cm.role IN ('owner','admin')))"
        )
        branch(
            "alert",
            [
                "'alert'",
                "p.ref",
                "p.company_id",
                "NULL",
                "NULL",
                "NULL",
                "NULL",
                "p.title",
                "p.state",
                "CASE WHEN p.ref_detail='critical' THEN 0 ELSE 1 END",
                "NULL",
                "p.updated_at",
                "'action'",
                "'处理运行告警'",
                "'企业 / 事务所管理员'",
                "NULL",
            ],
            "p.kind='alert' AND p.state='open' AND " + alert_manager + " AND p.firm_id=? "
            "AND EXISTS(SELECT 1 FROM firm_clients fc WHERE fc.firm_id=p.firm_id "
            "AND fc.company_id=p.company_id AND fc.status='active')",
            (actor.user_id, actor.user_id, actor.firm_id),
        )
        branch(
            "material",
            [
                "'material'",
                "p.ref",
                "p.company_id",
                "CASE WHEN EXISTS(SELECT 1 FROM edit_books eb WHERE eb.id=p.book_id) "
                "THEN p.book_id ELSE NULL END",
                "NULL",
                "NULL",
                "NULL",
                "p.title",
                "p.state",
                "2",
                "p.due_on",
                "p.updated_at",
                "CASE WHEN p.state='submitted' AND (p.created_by=? OR NOT EXISTS("
                "SELECT 1 FROM review_books rb WHERE rb.id=p.book_id)) "
                "THEN 'waiting' ELSE 'action' END",
                "CASE WHEN p.state='submitted' THEN '查看资料验收' ELSE '提供本期资料' END",
                "CASE WHEN p.state='submitted' THEN '独立验收人' ELSE '本人资料请求' END",
                "NULL",
            ],
            "p.kind='material' AND p.firm_id=? AND ((p.assignee_id=? AND EXISTS("
            "SELECT 1 FROM company_memberships mm WHERE mm.company_id=p.company_id "
            "AND mm.user_id=?) AND p.state IN ('pending','returned','submitted')) "
            "OR (p.state='submitted' AND p.created_by<>? AND EXISTS("
            "SELECT 1 FROM review_books rb WHERE rb.id=p.book_id)))",
            (actor.user_id, actor.firm_id, actor.user_id, actor.user_id, actor.user_id),
        )
        from app.legacy_migration.workspace import workspace_branch

        if gates.get("migration", True):
            migration_sql, migration_params = workspace_branch(self.db, actor)
            parts.append(
                "SELECT migration_task.*,NULL AS amount_cents FROM ("
                + migration_sql
                + ") migration_task"
            )
            params.extend(migration_params)
        if not parts:
            parts.append(
                "SELECT "
                + ",".join("NULL AS " + column for column in columns)
                + " FROM (SELECT 1) AS empty_projection WHERE 1=0"
            )
        return ctes + ", tasks AS (" + " UNION ALL ".join(parts) + ") ", params

    def _query(self, conn, actor, role_gates=None):
        # Authorization is part of every branch, before count, sort and limit.
        # PostgreSQL additionally retains FORCE RLS. No cached role/assignment
        # and no client supplied list of companies is treated as authority.
        events, ep = event_scope(actor)
        edits, bp = book_edit_scope(actor)
        firm_role, gates, _ = role_gates or self._task_gates(conn, actor)
        reviews, rp, _ = self.review._review_scope(conn, actor, role=firm_role)
        case_scope, cp = self.cases._scope_sql(actor)
        params = [*ep, *rp, *bp, actor.firm_id, *cp]
        ctes = f"""WITH visible_events AS (SELECT e.* FROM business_events e WHERE {events}),
          review_books AS ({reviews}),
          edit_books AS (SELECT b.* FROM books b WHERE {edits}),
          visible_cases AS (SELECT t.* FROM {self.cases._task_table()} t
            JOIN firm_clients fc ON fc.firm_id=t.firm_id AND fc.company_id=t.company_id AND fc.status='active'
            WHERE t.firm_id=? AND {case_scope})"""
        columns = [
            "kind",
            "ref",
            "company_id",
            "book_id",
            "event_id",
            "draft_id",
            "batch_id",
            "title",
            "state",
            "priority",
            "due_on",
            "updated_at",
            "bucket",
            "action_label",
            "responsible",
            "amount_cents",
        ]
        parts = []

        def branch(kind, table, where, values=None, args=()):
            if not gates.get(kind, True):
                return
            fields = {
                "kind": f"'{kind}'",
                "ref": "e.id",
                "company_id": "e.company_id",
                "book_id": "NULL",
                "event_id": "e.id",
                "draft_id": "NULL",
                "batch_id": "NULL",
                "title": "'业务事项'",
                "state": "e.status",
                "priority": "2",
                "due_on": "NULL",
                "updated_at": "e.updated_at",
                "bucket": "'action'",
                "action_label": "'查看事项'",
                "responsible": "'我'",
                "amount_cents": "NULL",
                **(values or {}),
            }
            if kind in {"business", "approval", "finance", "handoff"}:
                fields["amount_cents"] = event_amount("e.payload")
            elif kind in {"ocr", "proposal"}:
                fields["amount_cents"] = event_amount("be.payload")
            elif kind == "review":
                fields["amount_cents"] = "s.amount_cents"
            elif kind == "export":
                fields["amount_cents"] = "SUM(s.amount_cents)"
            parts.append(
                "SELECT "
                + ",".join(fields[c] + " AS " + c for c in columns)
                + " FROM "
                + table
                + " WHERE "
                + where
            )
            params.extend(args)

        # A fully pruned actor (for example a revoked employee) still needs one
        # valid empty projection so counts, pagination and the single-statement
        # contract keep working without resurrecting any branch.
        if not any(gates.values()):
            parts.append(
                "SELECT "
                + ",".join("NULL AS " + column for column in columns)
                + " FROM (SELECT 1) AS empty_projection WHERE 1=0"
            )

        def business_title(column, fallback):
            # Only used within already authorized event/intake branches.
            # A title remains ordinary text for React to escape; it is never a
            # link, HTML fragment, SQL input or source of authorization.
            value = (
                f"({column})::jsonb->>'description'"
                if self.db.is_postgres
                else f"json_extract(CASE WHEN json_valid({column}) THEN {column} ELSE '{{}}' END,'$.description')"
            )
            return f"COALESCE(NULLIF(trim({value}),''),'{fallback}')"

        def event_amount(column):
            # Business payloads canonicalize validated amounts to two decimal
            # places. Strip the dot into integer cents, never a float sort key.
            value = (
                f"({column})::jsonb->>'amount'"
                if self.db.is_postgres
                else f"json_extract({column},'$.amount')"
            )
            return f"CAST(REPLACE({value},'.','') AS BIGINT)"

        membership = "EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=e.company_id AND cm.user_id=?)"
        intake_member = "EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=e.company_id AND cm.user_id=? AND (cm.role<>'employee' OR cm.department_id=e.department_id))"
        branch(
            "intake",
            "intake_drafts e",
            "e.firm_id=? AND e.created_by=? AND e.status='draft' AND "
            + intake_member
            + " AND EXISTS(SELECT 1 FROM firm_clients fc WHERE fc.firm_id=e.firm_id AND fc.company_id=e.company_id AND fc.status='active')",
            {
                "event_id": "NULL",
                "title": business_title("e.fields_json", "继续录入资料"),
                "action_label": "'继续录入'",
            },
            (actor.firm_id, actor.user_id, actor.user_id),
        )
        branch(
            "business",
            "visible_events e",
            "e.applicant_id=? AND e.status<>'handed_off' AND " + membership,
            {
                "title": business_title("e.payload", "我的业务事项"),
                "priority": "CASE WHEN e.status='returned' THEN 1 ELSE 2 END",
                "bucket": "CASE WHEN e.status IN ('draft','returned') THEN 'action' ELSE 'waiting' END",
                "action_label": "CASE WHEN e.status='returned' THEN '修改并重提' WHEN e.status='draft' THEN '继续填写' ELSE '查看进度' END",
                "responsible": "CASE WHEN e.status='submitted' AND e.submission_mode='direct' THEN '授权会计' WHEN e.status='submitted' THEN '部门审批人' WHEN e.status='department_approved' THEN '企业财务' WHEN e.status='finance_approved' THEN '企业财务移交' ELSE '我' END",
            },
            (actor.user_id, actor.user_id),
        )
        department = "EXISTS(SELECT 1 FROM department_approvers da JOIN company_memberships cm ON cm.company_id=da.company_id AND cm.user_id=da.user_id WHERE da.firm_id=e.firm_id AND da.company_id=e.company_id AND da.department_id=e.department_id AND da.user_id=?)"
        finance = "EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=e.company_id AND cm.user_id=? AND cm.role='finance')"
        branch(
            "approval",
            "visible_events e",
            "e.submission_mode='department' AND e.status='submitted' AND e.applicant_id<>? AND "
            + department,
            {"title": business_title("e.payload", "待部门审批"), "action_label": "'审批事项'"},
            (actor.user_id, actor.user_id),
        )
        branch(
            "handoff",
            "business_events e",
            "e.firm_id=? AND e.submission_mode='direct' AND e.status='submitted' AND e.applicant_id<>? AND EXISTS(SELECT 1 FROM edit_books b WHERE b.company_id=e.company_id AND b.period="
            + self.business.submission.period_sql()
            + ")",
            {
                "title": business_title("e.payload", "待接收企业资料"),
                "action_label": "'接收并生成草稿'",
                "responsible": "'授权会计'",
            },
            (actor.firm_id, actor.user_id),
        )
        branch(
            "finance",
            "visible_events e",
            "e.status='department_approved' AND e.applicant_id<>? AND " + finance,
            {"title": business_title("e.payload", "待财务核验"), "action_label": "'核验资料'"},
            (actor.user_id, actor.user_id),
        )
        branch(
            "handoff",
            "visible_events e",
            "e.status='finance_approved' AND " + finance,
            {
                "title": business_title("e.payload", "待移交会计"),
                "action_label": "'选择账套并移交'",
            },
            (actor.user_id,),
        )
        # Private recipient tasks intentionally expose no ledger identifiers or
        # internal case title. The exact case endpoint returns permitted detail.
        recipient = "e.kind='supplement' AND e.recipient_id=? AND e.state='awaiting_info'"
        independent = "NOT EXISTS(SELECT 1 FROM case_responses r WHERE r.case_id=e.id AND r.created_by=? AND NOT EXISTS(SELECT 1 FROM case_responses newer WHERE newer.case_id=r.case_id AND (newer.created_at>r.created_at OR newer.created_at=r.created_at AND newer.id>r.id)))"
        manager = "(EXISTS(SELECT 1 FROM review_books rb WHERE rb.id=COALESCE(e.book_id,(SELECT bm.book_id FROM business_mappings bm WHERE bm.event_id=e.event_id))) OR EXISTS(SELECT 1 FROM visible_events be JOIN company_memberships cm ON cm.company_id=be.company_id WHERE be.id=e.event_id AND cm.user_id=? AND cm.role='finance' AND be.applicant_id<>?))"
        collector = "(e.event_id IS NULL AND e.assignee_id=? AND EXISTS(SELECT 1 FROM edit_books eb JOIN company_memberships cm ON cm.company_id=eb.company_id WHERE eb.id=e.book_id AND cm.user_id=? AND cm.role='finance'))"
        actionable = f"(({recipient}) OR ({collector} AND e.state='open') OR ({manager} AND e.state IN ('open','in_review','response_submitted') AND (e.assignee_id IS NULL OR e.assignee_id=?) AND {independent}))"
        if self.db.is_postgres:
            source_open = "accounting.case_source_open(e.id)"
        else:
            source_open = "(COALESCE(e.book_id,(SELECT bm.book_id FROM business_mappings bm WHERE bm.event_id=e.event_id)) IS NULL OR EXISTS(SELECT 1 FROM drafts d JOIN periods p ON p.book_id=d.book_id WHERE d.id=COALESCE(e.draft_id,(SELECT bm.draft_id FROM business_mappings bm WHERE bm.event_id=e.event_id)) AND d.status='draft' AND p.status='open'))"
        branch(
            "case",
            "visible_cases e",
            "e.state IN ('open','awaiting_info','response_submitted','in_review')",
            {
                "event_id": "NULL",
                "title": "CASE WHEN e.kind='supplement' THEN '补充业务资料' ELSE '处理会计异常' END",
                "state": "e.state",
                "priority": "CASE e.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END",
                "due_on": "e.due_on",
                "bucket": f"CASE WHEN {source_open} AND {actionable} THEN 'action' ELSE 'waiting' END",
                "action_label": "'打开处理任务'",
                "responsible": "CASE WHEN e.state='awaiting_info' THEN '资料提供人' ELSE '任务处理人' END",
            },
            (actor.user_id,) * 7,
        )
        inactive = inactive_source_sql(self.db.dialect)
        branch(
            "review",
            "drafts e JOIN review_books b ON b.id=e.book_id JOIN sources s ON s.id=e.source_id AND s.book_id=e.book_id LEFT JOIN business_mappings bm ON bm.draft_id=e.id LEFT JOIN visible_events be ON be.id=bm.event_id",
            f"e.status='draft' AND b.period_status='open' AND NOT {inactive} AND (be.applicant_id IS NULL OR be.applicant_id<>?) AND NOT EXISTS(SELECT 1 FROM ai_bank_links l WHERE l.bank_source_id=e.source_id AND l.book_id=e.book_id) AND NOT EXISTS(SELECT 1 FROM proposal_jobs j WHERE j.draft_id=e.id AND j.status IN ('waiting','queued','running','retry_wait')) AND NOT EXISTS(SELECT 1 FROM visible_cases ct WHERE COALESCE(ct.draft_id,(SELECT mapped.draft_id FROM business_mappings mapped WHERE mapped.event_id=ct.event_id))=e.id AND ct.state IN ('open','awaiting_info','response_submitted','in_review'))",
            {
                "company_id": "b.company_id",
                "book_id": "e.book_id",
                "event_id": "be.id",
                "draft_id": "e.id",
                "title": "e.description",
                "updated_at": "s.date",
                "action_label": "'同屏复核'",
                "responsible": "'会计复核员'",
            },
            (actor.user_id,),
        )
        branch(
            "export",
            "review_books b JOIN drafts e ON e.book_id=b.id JOIN sources s ON s.id=e.source_id AND s.book_id=e.book_id",
            f"e.status='approved' AND b.period_status='open' AND NOT {inactive} GROUP BY b.id,b.company_id,b.name,b.period",
            {
                "ref": "b.id",
                "company_id": "b.company_id",
                "book_id": "b.id",
                "event_id": "NULL",
                "title": "b.name || ' · ' || b.period || ' · 待导出凭证 · ' || COUNT(*) || ' 张'",
                "state": "'approved'",
                "updated_at": "MAX(s.date)",
                "action_label": "'导出已审核凭证'",
                "responsible": "'会计交付人'",
            },
        )
        ocr_open = "NOT EXISTS(SELECT 1 FROM business_mappings frozen JOIN drafts d ON d.id=frozen.draft_id AND d.book_id=frozen.book_id JOIN periods p ON p.book_id=frozen.book_id WHERE frozen.event_id=e.event_id AND (d.status<>'draft' OR p.status<>'open'))"
        ocr_processor = "(EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=be.company_id AND cm.user_id=? AND (be.applicant_id=? OR cm.role='finance' OR EXISTS(SELECT 1 FROM department_approvers da WHERE da.firm_id=be.firm_id AND da.company_id=be.company_id AND da.department_id=be.department_id AND da.user_id=?))) OR EXISTS(SELECT 1 FROM business_mappings current JOIN edit_books eb ON eb.id=current.book_id WHERE current.event_id=be.id))"
        branch(
            "ocr",
            "ocr_jobs e JOIN visible_events be ON be.id=e.event_id",
            "e.status IN ('queued','running','retry_wait','failed','interrupted','stale') AND e.expected_fact_version=be.fact_version AND NOT EXISTS(SELECT 1 FROM ocr_jobs newer WHERE newer.event_id=e.event_id AND newer.attachment_id=e.attachment_id AND (newer.created_at>e.created_at OR newer.created_at=e.created_at AND newer.id>e.id))",
            {
                "event_id": "e.event_id",
                "batch_id": "e.batch_id",
                "title": "'票据识别任务'",
                "bucket": f"CASE WHEN e.status IN ('failed','interrupted','stale') AND {ocr_open} AND {ocr_processor} THEN 'action' ELSE 'waiting' END",
                "priority": "CASE WHEN e.status IN ('failed','interrupted') THEN 1 ELSE 2 END",
                "action_label": "'查看识别任务'",
                "responsible": "'当前任务经办人'",
            },
            (actor.user_id,) * 3,
        )
        branch(
            "proposal",
            "proposal_jobs e JOIN edit_books b ON b.id=e.book_id JOIN visible_events be ON be.id=e.event_id JOIN drafts d ON d.id=e.draft_id AND d.book_id=e.book_id JOIN periods p ON p.book_id=e.book_id",
            "e.status IN ('waiting','queued','running','retry_wait','needs_attention','failed','interrupted','stale') AND p.status='open' AND d.status='draft' AND e.expected_fact_version=be.fact_version AND NOT EXISTS(SELECT 1 FROM proposal_jobs newer WHERE newer.draft_id=e.draft_id AND (newer.created_at>e.created_at OR newer.created_at=e.created_at AND newer.id>e.id)) AND NOT EXISTS(SELECT 1 FROM visible_cases ct WHERE ct.event_id=e.event_id AND ct.state IN ('open','awaiting_info','response_submitted','in_review'))",
            {
                "book_id": "e.book_id",
                "event_id": "e.event_id",
                "draft_id": "e.draft_id",
                "title": "'分录草稿生成任务'",
                "bucket": "CASE WHEN e.status IN ('needs_attention','failed','interrupted','stale') THEN 'action' ELSE 'waiting' END",
                "priority": "1",
                "action_label": "'处理生成任务'",
                "responsible": "'账套经办人'",
            },
        )
        receipt = receipt_valid_sql(self.db.dialect)
        branch(
            "delivery",
            "exports x JOIN review_books b ON b.id=x.book_id",
            f"NOT {receipt}",
            {
                "ref": "x.id",
                "company_id": "b.company_id",
                "book_id": "x.book_id",
                "event_id": "NULL",
                "title": "'核对财务软件导入结果'",
                "state": "'unverified'",
                "updated_at": "x.created_at",
                "action_label": "'核验导入回执'",
                "responsible": "'会计交付人'",
            },
        )
        manager = "EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=e.firm_id AND m.user_id=? AND m.role IN ('owner','admin')) OR EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=e.company_id AND cm.user_id=? AND cm.role IN ('owner','admin'))"
        branch(
            "alert",
            "ops_alerts e JOIN firm_clients fc ON fc.firm_id=e.firm_id AND fc.company_id=e.company_id",
            "e.firm_id=? AND fc.status='active' AND e.state='open' AND (" + manager + ")",
            {
                "event_id": "NULL",
                "title": "e.code",
                "state": "e.state",
                "priority": "CASE WHEN e.severity='critical' THEN 0 ELSE 1 END",
                "updated_at": "e.last_seen_at",
                "action_label": "'处理运行告警'",
                "responsible": "'企业 / 事务所管理员'",
            },
            (actor.firm_id, actor.user_id, actor.user_id),
        )
        from app.legacy_migration.workspace import workspace_branch

        if gates.get("migration", True):
            migration_sql, migration_params = workspace_branch(self.db, actor)
            parts.append(
                "SELECT migration_task.*,NULL AS amount_cents FROM ("
                + migration_sql
                + ") migration_task"
            )
            params.extend(migration_params)
        # A request can exist before any business event. Visibility and action
        # ownership are resolved before counts/pagination, like other branches.
        branch(
            "material",
            "material_requests e JOIN companies mc ON mc.id=e.company_id AND mc.status='active' "
            "JOIN firm_clients mfc ON mfc.company_id=e.company_id AND mfc.firm_id=e.firm_id AND mfc.status='active' "
            + (
                ""
                if self.db.is_postgres
                else "JOIN periods mp ON mp.book_id=e.book_id AND mp.period=e.period AND mp.status='open' "
            )
            + "LEFT JOIN material_request_actions ma ON ma.request_id=e.id "
            "AND ma.version=(SELECT MAX(mx.version) FROM material_request_actions mx WHERE mx.request_id=e.id)",
            ("accounting.service_material_open(e.id) AND " if self.db.is_postgres else "")
            + "e.firm_id=? AND ((e.assignee_user_id=? AND EXISTS(SELECT 1 FROM company_memberships mm "
            "WHERE mm.company_id=e.company_id AND mm.user_id=?) AND COALESCE(ma.state,'pending') IN ('pending','returned','submitted')) "
            "OR (ma.state='submitted' AND ma.created_by<>? AND EXISTS(SELECT 1 FROM review_books rb WHERE rb.id=e.book_id)))",
            {
                "event_id": "NULL",
                "book_id": "CASE WHEN EXISTS(SELECT 1 FROM edit_books eb WHERE eb.id=e.book_id) THEN e.book_id ELSE NULL END",
                "title": "e.title",
                "state": "COALESCE(ma.state,'pending')",
                "due_on": "e.due_on",
                "updated_at": "COALESCE(ma.created_at,e.created_at)",
                "bucket": "CASE WHEN ma.state='submitted' AND (ma.created_by=? OR NOT EXISTS(SELECT 1 FROM review_books rb WHERE rb.id=e.book_id)) THEN 'waiting' ELSE 'action' END",
                "action_label": "CASE WHEN ma.state='submitted' THEN '查看资料验收' ELSE '提供本期资料' END",
                "responsible": "CASE WHEN ma.state='submitted' THEN '独立验收人' ELSE '本人资料请求' END",
            },
            # SELECT placeholders precede the WHERE clause placeholders.
            (actor.user_id, actor.firm_id, actor.user_id, actor.user_id, actor.user_id),
        )
        return ctes + ", tasks AS (" + " UNION ALL ".join(parts) + ") ", params

    def list(
        self,
        actor,
        bucket="action",
        kind=None,
        company_id=None,
        limit=20,
        offset=0,
        ref=None,
        sort="priority",
        q="",
        use_projection=True,
    ):
        with self.db.transaction(actor=actor) as conn:
            return self.list_in_connection(
                conn,
                actor,
                bucket,
                kind,
                company_id,
                limit,
                offset,
                ref,
                sort,
                q,
                use_projection,
            )

    def list_in_connection(
        self,
        conn,
        actor,
        bucket="action",
        kind=None,
        company_id=None,
        limit=20,
        offset=0,
        ref=None,
        sort="priority",
        q="",
        use_projection=True,
    ):
        page_bounds(limit, offset)
        if (
            bucket not in {"action", "waiting", "all"}
            or kind is not None
            and kind not in KINDS
            or sort not in {"priority", "due", "amount", "updated"}
            or len(q) > 200
        ):
            raise AccountingError("待办筛选参数无效")
        # _query resolves the active user and current firm role through the
        # review scope; do not repeat that identical authorization round trip.
        role_gates = self._task_gates(conn, actor)
        projected = use_projection and role_gates[2]
        if use_projection and not projected and self.refresher is not None:
            # 读请求只唤醒后台刷新，绝不在请求内写库。
            self.refresher.wake(actor.firm_id)
        query, params = (
            self._projected_query(conn, actor, role_gates)
            if projected
            else self._query(conn, actor, role_gates)
        )
        where, filters = ["1=1"], []
        for col, value in (
            ("bucket", None if bucket == "all" else bucket),
            ("kind", kind),
            ("company_id", company_id),
            ("ref", ref),
        ):
            if value is not None:
                where.append(col + "=?")
                filters.append(value)
        if q.strip():
            where.append(
                "(title LIKE ? ESCAPE '!' OR company_id IN (SELECT id FROM companies WHERE name LIKE ? ESCAPE '!'))"
            )
            filters.extend([self._search(q)] * 2)
        base = " FROM tasks WHERE " + " AND ".join(where)
        order = {
            "priority": "priority,COALESCE(due_on,'9999-12-31'),updated_at DESC",
            "due": "COALESCE(due_on,'9999-12-31'),priority,updated_at DESC",
            "amount": "CASE WHEN amount_cents IS NULL THEN 1 ELSE 0 END,amount_cents DESC,priority,updated_at DESC",
            "updated": "updated_at DESC,priority",
        }[sort]
        rows = conn.execute(
            query + ", filtered_tasks AS (SELECT *" + base + "), task_counts AS (SELECT "
            "COALESCE(SUM(CASE WHEN bucket='action' THEN 1 ELSE 0 END),0) AS _action_count,"
            "COALESCE(SUM(CASE WHEN bucket='waiting' THEN 1 ELSE 0 END),0) AS _waiting_count "
            "FROM tasks), matching_count AS (SELECT COUNT(*) AS _total FROM filtered_tasks),"
            " task_page AS (SELECT * FROM filtered_tasks ORDER BY "
            + order
            + ",kind,ref LIMIT ? OFFSET ?) "
            "SELECT t.*,c.name AS company_name,n._total,k._action_count,k._waiting_count "
            "FROM matching_count n CROSS JOIN task_counts k LEFT JOIN task_page t ON 1=1 "
            "LEFT JOIN companies c ON c.id=t.company_id ORDER BY " + order + ",kind,ref",
            [*params, *filters, limit, offset],
        ).fetchall()
        # A metadata row survives an empty page, including offset past the last
        # task. PostgreSQL materializes the reused authorized task projection
        # once rather than recomputing its RLS-heavy branches three times.
        total = rows[0]["_total"]
        counts = {
            bucket: rows[0]["_" + bucket + "_count"]
            for bucket in ("action", "waiting")
            if rows[0]["_" + bucket + "_count"]
        }
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        tasks = []
        for row in rows:
            if row["ref"] is None:
                continue
            item = {key: row[key] for key in row.keys() if not key.startswith("_")}
            item["key"] = item["kind"] + ":" + item["ref"]
            amount = item.pop("amount_cents")
            # PostgreSQL SUM(bigint) yields Decimal; aggregate task totals
            # can exceed the single-transaction amount ceiling. Their
            # input is still exclusively validated integer cents.
            cents = int(amount) if amount is not None else None
            item["amount"] = f"{cents // 100}.{cents % 100:02d}" if cents is not None else None
            item["overdue"] = bool(item["due_on"] and item["due_on"] < today)
            if item["kind"] == "alert":
                item["title"] = ALERTS.get(item["title"], "待处理运行告警")
            tasks.append(item)
        return {
            "tasks": tasks,
            "counts": counts,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
        }

    @staticmethod
    def _search(value):
        return "%" + value.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"

    def companies(self, actor, q="", limit=20, offset=0, use_projection=True):
        page_bounds(limit, offset)
        if len(q) > 200:
            raise AccountingError("企业搜索文字过长")
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            role_gates = self._task_gates(conn, actor)
            projected = use_projection and role_gates[2]
            if use_projection and not projected and self.refresher is not None:
                self.refresher.wake(actor.firm_id)
            query, params = (
                self._projected_query(conn, actor, role_gates)
                if projected
                else self._query(conn, actor, role_gates)
            )
            search = " WHERE c.name LIKE ? ESCAPE '!'" if q.strip() else ""
            args = [*params, *([self._search(q)] if q.strip() else [])]
            choices = (
                "SELECT c.id,c.name,COUNT(*) AS task_count FROM tasks t JOIN companies c ON c.id=t.company_id"
                + search
                + " GROUP BY c.id,c.name"
            )
            total = conn.execute(
                query + "SELECT COUNT(*) FROM (" + choices + ") choices", args
            ).fetchone()[0]
            rows = conn.execute(
                query + choices + " ORDER BY c.name,c.id LIMIT ? OFFSET ?", [*args, limit, offset]
            ).fetchall()
            return {
                "companies": [dict(row) for row in rows],
                "pagination": {
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "has_more": offset + len(rows) < total,
                },
            }

    def get(self, actor, kind, ref):
        result = self.list(actor, bucket="all", kind=kind, ref=ref, limit=1)
        if not result["tasks"]:
            raise AccountingError("事项已处理、权限已变更或不存在，请刷新待办", 404)
        return result["tasks"][0]
