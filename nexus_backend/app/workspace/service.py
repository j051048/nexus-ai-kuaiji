from datetime import datetime
from zoneinfo import ZoneInfo

from app.accounting.domain import AccountingError
from app.accounting.queries import inactive_source_sql, page_bounds
from app.closing.queries import receipt_valid_sql
from app.jobs.visibility import book_edit_scope, event_scope


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
    def __init__(self, db, accounting, business, review, cases):
        self.db, self.accounting, self.business, self.review, self.cases = (
            db,
            accounting,
            business,
            review,
            cases,
        )
        self.access = accounting.access

    def profile(self, actor):
        context = self.business.context(actor)
        summary = self.accounting.workbench(actor, limit=1)["stats"]
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            scope, params, firm_role = self.review._review_scope(conn, actor)
            can_review = bool(
                conn.execute("SELECT 1 FROM (" + scope + ") v LIMIT 1", params).fetchone()
            )
        companies = context["companies"]
        can_finance = any(c["can_finance"] for c in companies)
        can_approve = any(c["approver_department_ids"] for c in companies)
        can_submit = any(c["can_submit"] for c in companies)
        can_manage = firm_role in {"owner", "admin"} or any(
            c["can_manage_approvers"] for c in companies
        )
        # The firm's accounting role is primary; a company's finance member
        # keeps the finance workflow even when additionally allowed to review.
        role = (
            "accountant"
            if firm_role in {"owner", "admin", "operator", "reviewer"}
            else "finance"
            if can_finance
            else "approver"
            if can_approve
            else "employee"
            if can_submit
            else "manager"
            if can_manage
            else "accountant"
            if can_review
            else "viewer"
        )
        return {
            "role": role,
            "can_submit": can_submit,
            "can_approve": can_approve,
            "can_finance": can_finance,
            "can_review": can_review,
            "can_accounting": summary["book_count"] > 0,
            "can_manage": can_manage,
            "companies": [{"id": c["id"], "name": c["name"]} for c in companies],
            "book_count": summary["book_count"],
        }

    def _query(self, conn, actor):
        # Authorization is part of every branch, before count, sort and limit.
        # PostgreSQL additionally retains FORCE RLS. No cached role/assignment
        # and no client supplied list of companies is treated as authority.
        events, ep = event_scope(actor)
        edits, bp = book_edit_scope(actor)
        reviews, rp, _ = self.review._review_scope(conn, actor)
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

        migration_sql, migration_params = workspace_branch(self.db, actor)
        parts.append(
            "SELECT migration_task.*,NULL AS amount_cents FROM ("
            + migration_sql
            + ") migration_task"
        )
        params.extend(migration_params)
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
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            query, params = self._query(conn, actor)
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
            total = conn.execute(query + "SELECT count(*)" + base, [*params, *filters]).fetchone()[
                0
            ]
            counts = conn.execute(
                query + "SELECT bucket,count(*) AS total FROM tasks GROUP BY bucket", params
            ).fetchall()
            order = {
                "priority": "priority,COALESCE(due_on,'9999-12-31'),updated_at DESC",
                "due": "COALESCE(due_on,'9999-12-31'),priority,updated_at DESC",
                "amount": "CASE WHEN amount_cents IS NULL THEN 1 ELSE 0 END,amount_cents DESC,priority,updated_at DESC",
                "updated": "updated_at DESC,priority",
            }[sort]
            rows = conn.execute(
                query
                + "SELECT t.*,c.name AS company_name FROM (SELECT *"
                + base
                + " ORDER BY "
                + order
                + ",kind,ref LIMIT ? OFFSET ?) t JOIN companies c ON c.id=t.company_id",
                [*params, *filters, limit, offset],
            ).fetchall()
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            tasks = []
            for row in rows:
                item = dict(row)
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
                "counts": {r["bucket"]: r["total"] for r in counts},
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

    def companies(self, actor, q="", limit=20, offset=0):
        page_bounds(limit, offset)
        if len(q) > 200:
            raise AccountingError("企业搜索文字过长")
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            query, params = self._query(conn, actor)
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
