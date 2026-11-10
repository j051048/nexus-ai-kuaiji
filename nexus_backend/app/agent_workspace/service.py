"""Authorized SQL projections. No model invocation, write, approval or export.

An activity is one persisted job. Progress measures persisted units, never an
estimated model thought chain or an entire batch that the reader cannot see.
"""

from app.accounting.domain import AccountingError
from app.accounting.models import cents_to_money, money_to_cents
from app.accounting.queries import page_bounds
from app.jobs.visibility import book_edit_scope, event_scope


class AgentWorkspaceService:
    def __init__(self, db, accounting, business, workspace, migration=None):
        self.db, self.accounting, self.business = db, accounting, business
        self.workspace, self.migration = workspace, migration
        self.access = accounting.access

    def business_context(self, actor, event_id, *, company_id, period, expected_version):
        from .business_context import project

        return project(
            self,
            actor,
            event_id,
            company_id=company_id,
            period=period,
            expected_version=expected_version,
        )

    def _run_query(self, conn, actor):
        event_sql, event_params = event_scope(actor)
        edit_sql, edit_params = book_edit_scope(actor)
        # History needs an explicit project grant intersected with current book
        # authority; owning a firm alone does not authorize historical archives.
        book_sql, book_params = self.accounting._summary_scope(actor)
        # The review projection already resolves the active user and current
        # firm membership. Share that request-local result instead of asking
        # for the same grants again before constructing the other branches.
        reviews, review_params, role = self.workspace.review._review_scope(conn, actor)
        if role not in {"owner", "admin"}:
            book_sql += (
                " AND a.role IS NOT NULL AND (?=1 OR cm.role IN ('owner','admin','finance'))"
            )
            book_params.append(int(bool(role)))
        if self.db.is_postgres:
            migration_scope = "accounting.migration_allowed(p.id,'viewer')"
            migration_params = []
            expected_total = (
                "CAST(j.checkpoint_json AS jsonb)->'metadata'->>'expected_record_count'"
            )
            expected_valid = f"COALESCE({expected_total},'') ~ '^[0-9]{{1,10}}$'"
        else:
            migration_scope = """(EXISTS(SELECT 1 FROM migration_project_access pa WHERE pa.project_id=p.id AND pa.user_id=?)
              OR (EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id=p.firm_id AND fm.user_id=? AND fm.role IN ('owner','admin'))
                AND EXISTS(SELECT 1 FROM ledger_managers lm WHERE lm.ledger_id=p.ledger_id AND lm.user_id=?)
                AND EXISTS(SELECT 1 FROM review_books rb WHERE rb.id=p.book_id)))"""
            migration_params = [actor.user_id] * 3
            expected_total = "json_extract(j.checkpoint_json,'$.metadata.expected_record_count')"
            expected_valid = f"json_type(j.checkpoint_json,'$.metadata.expected_record_count')='integer' AND {expected_total}>=0"
        description = (
            "(e.payload)::jsonb->>'description'"
            if self.db.is_postgres
            else "json_extract(e.payload,'$.description')"
        )
        terminal = "('succeeded','failed','interrupted','stale','cancelled','needs_attention')"
        cte = f"""WITH visible_events AS (SELECT e.id,e.company_id,e.fact_version,e.payload FROM business_events e WHERE {event_sql}),
          edit_books AS (SELECT b.id FROM books b WHERE {edit_sql}),
          visible_books AS ({book_sql}),review_books AS ({reviews}),
          raw_runs AS (
            SELECT 'ocr' AS kind,j.id,j.company_id,CAST(NULL AS TEXT) AS book_id,j.event_id,j.batch_id,
              '票据识别 · ' || COALESCE(NULLIF({description},''),'业务附件') AS title,j.status,
              CASE WHEN j.status IN {terminal} THEN 1 ELSE 0 END AS processed,1 AS total,
              j.updated_at,j.created_at,j.finished_at,
              CASE WHEN j.status IN ('needs_attention','failed','interrupted','stale') OR (j.status IN ('waiting','queued','running','retry_wait') AND j.expected_fact_version<>e.fact_version) THEN 1 ELSE 0 END AS needs_attention,
              j.id AS route_id,CAST(NULL AS TEXT) AS phase,CAST(NULL AS TEXT) AS project_status
            FROM ocr_jobs j JOIN visible_events e ON e.id=j.event_id AND e.company_id=j.company_id
            UNION ALL
            SELECT 'proposal',j.id,j.company_id,j.book_id,j.event_id,CAST(NULL AS TEXT),
              '分录草稿 · ' || d.description,j.status,CASE WHEN j.status IN {terminal} THEN 1 ELSE 0 END,1,
              j.updated_at,j.created_at,j.finished_at,
              CASE WHEN j.status IN ('needs_attention','failed','interrupted','stale') OR (j.status IN ('waiting','queued','running','retry_wait') AND j.expected_fact_version<>e.fact_version) THEN 1 ELSE 0 END,
              j.draft_id,CAST(NULL AS TEXT),CAST(NULL AS TEXT)
            FROM proposal_jobs j JOIN edit_books b ON b.id=j.book_id JOIN visible_events e ON e.id=j.event_id AND e.company_id=j.company_id
              JOIN drafts d ON d.id=j.draft_id AND d.book_id=j.book_id
            UNION ALL
            SELECT 'migration',j.id,j.company_id,p.book_id,CAST(NULL AS TEXT),CAST(NULL AS TEXT),
              '旧账迁移 · ' || CASE j.phase WHEN 'parse' THEN '解析资料' ELSE '核验差异' END,j.status,j.processed_count,
              CASE WHEN {expected_valid} THEN CAST({expected_total} AS BIGINT) ELSE NULL END,
              j.updated_at,j.created_at,j.finished_at,
              CASE WHEN j.status IN ('failed','stale') OR (j.status IN ('queued','running','retry_wait') AND j.expected_version<>p.version)
                OR (j.phase='validate' AND j.status='succeeded' AND j.expected_version=p.version AND p.status='needs_attention') THEN 1 ELSE 0 END,
              p.id,j.phase,p.status
            FROM migration_jobs j JOIN migration_projects p ON p.id=j.project_id AND p.firm_id=j.firm_id AND p.company_id=j.company_id
              JOIN visible_books b ON b.id=p.book_id WHERE {migration_scope}
          ),runs AS (SELECT raw_runs.*,c.name AS company_name,
            CASE WHEN raw_runs.needs_attention=1 THEN 'attention' WHEN raw_runs.status IN ('waiting','queued','running','retry_wait') THEN 'active' ELSE 'done' END AS bucket
            FROM raw_runs JOIN companies c ON c.id=raw_runs.company_id)
        """
        return cte, [*event_params, *edit_params, *book_params, *review_params, *migration_params]

    def runs(self, actor, state="active", company_id=None, q="", limit=20, offset=0):
        page_bounds(limit, offset)
        if state not in {"active", "attention", "done", "all"} or len(q) > 200:
            raise AccountingError("运行筛选参数无效")
        with self.db.transaction(actor=actor) as conn:
            query, params = self._run_query(conn, actor)
            clauses, filters = ["1=1"], []
            if state != "all":
                clauses.append("bucket=?")
                filters.append(state)
            if company_id:
                clauses.append("company_id=?")
                filters.append(company_id)
            if q.strip():
                clauses.append("(title LIKE ? ESCAPE '!' OR company_name LIKE ? ESCAPE '!')")
                filters += [self.workspace._search(q)] * 2
            where = " FROM runs WHERE " + " AND ".join(clauses)
            total = conn.execute(query + "SELECT COUNT(*)" + where, [*params, *filters]).fetchone()[
                0
            ]
            rows = conn.execute(
                query + "SELECT *" + where + " ORDER BY updated_at DESC,kind,id LIMIT ? OFFSET ?",
                [*params, *filters, limit, offset],
            ).fetchall()
            return {
                "runs": [self._run_json(row) for row in rows],
                "pagination": {
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "has_more": offset + len(rows) < total,
                },
            }

    @staticmethod
    def _run_json(row):
        item = {
            key: row[key]
            for key in (
                "kind",
                "id",
                "company_id",
                "company_name",
                "book_id",
                "event_id",
                "batch_id",
                "title",
                "status",
                "processed",
                "total",
                "updated_at",
            )
        }
        item["key"] = item["kind"] + ":" + item["id"]
        item["needs_attention"] = bool(row["needs_attention"])
        item["next_action"] = (
            "核对当前资料并处理任务"
            if item["needs_attention"]
            else "等待后台处理"
            if row["bucket"] == "active"
            else "查看已保存结果"
        )
        item["next_owner"] = (
            "账套经办会计"
            if item["kind"] == "proposal"
            else "资料经办会计"
            if item["kind"] == "migration"
            else "当前事项经办人"
        )
        if row["bucket"] == "active":
            item["next_owner"] = "后台任务"
        elif item["kind"] == "proposal" and item["status"] == "succeeded":
            item["next_action"], item["next_owner"] = "核对生成草稿并复核", "会计复核员"
        elif item["kind"] == "ocr" and item["status"] == "succeeded":
            item["next_action"] = "核对已保存识别结果"
        elif item["kind"] == "migration" and row["project_status"] == "needs_attention":
            item["next_action"] = "处理迁移校验差异"
        elif item["kind"] == "migration" and row["project_status"] == "ready":
            item["next_action"], item["next_owner"] = "等待独立验收", "独立复核会计"
        elif item["kind"] == "migration" and row["project_status"] == "accepted":
            item["next_action"], item["next_owner"] = "确认接管目标期间", "账套负责人"
        item["route"] = {"type": item["kind"], "id": row["route_id"], "job_id": item["id"]}
        if item["book_id"]:
            item["route"]["book_id"] = item["book_id"]
        return item

    def summary(self, actor):
        with self.db.transaction(actor=actor) as conn:
            query, params = self._run_query(conn, actor)
            rows = conn.execute(
                query
                + "SELECT kind,bucket,COUNT(*) AS total,MAX(updated_at) AS updated_at FROM runs GROUP BY kind,bucket",
                params,
            ).fetchall()
            counts = {key: 0 for key in ("active", "attention", "done", "total")}
            by_kind = {kind: dict(counts) for kind in ("ocr", "proposal", "migration")}
            for row in rows:
                counts[row["bucket"]] += row["total"]
                counts["total"] += row["total"]
                by_kind[row["kind"]][row["bucket"]] += row["total"]
                by_kind[row["kind"]]["total"] += row["total"]
            return {
                "counts": counts,
                "by_kind": by_kind,
                "updated_at": max((row["updated_at"] for row in rows), default=None),
                "mode": "persisted_job_states",
            }

    def companies(self, actor, q="", limit=20, offset=0):
        page_bounds(limit, offset)
        if len(q) > 200:
            raise AccountingError("企业搜索文字过长")
        with self.db.transaction(actor=actor) as conn:
            query, params = self._run_query(conn, actor)
            predicate = " WHERE company_name LIKE ? ESCAPE '!'" if q.strip() else ""
            args = [*params, *([self.workspace._search(q)] if q.strip() else [])]
            choices = (
                "SELECT company_id AS id,company_name AS name,COUNT(*) AS task_count FROM runs"
                + predicate
                + " GROUP BY company_id,company_name"
            )
            total = conn.execute(
                query + "SELECT COUNT(*) FROM (" + choices + ") choices", args
            ).fetchone()[0]
            rows = conn.execute(
                query + choices + " ORDER BY name,id LIMIT ? OFFSET ?", [*args, limit, offset]
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

    def context(self, actor, book_id, draft_id):
        with self.db.transaction(actor=actor) as conn:
            # Re-authorize on every request, including explanations after a
            # drawer has been open while assignments or facts have changed.
            book = self.access.book(conn, actor, book_id)
            row = conn.execute(
                "SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("凭证不存在或无权访问", 404)
            draft = self.accounting._draft_json(conn, row, actor)
            source = conn.execute(
                "SELECT * FROM sources WHERE id=? AND book_id=?", (row["source_id"], book_id)
            ).fetchone()
            account_names = {
                r["code"]: r["name"]
                for r in conn.execute("SELECT code,name FROM accounts WHERE book_id=?", (book_id,))
            }
            draft["lines"] = [
                {**line, "account_name": account_names.get(line["account_code"], "科目不可用")}
                for line in draft["lines"]
            ]
            public_draft = {
                key: draft[key]
                for key in (
                    "id",
                    "description",
                    "status",
                    "version",
                    "explanation",
                    "issues",
                    "lines",
                    "source_amount",
                )
            }
            public_source = {
                key: source[key]
                for key in ("id", "date", "description", "counterparty", "direction", "external_id")
            }
            public_source.update(
                amount=cents_to_money(source["amount_cents"]),
                kind=source["source_kind"],
                evidence_ref=draft["evidence_ref"],
            )
            evidence = [
                {
                    "id": "source:" + source["id"],
                    "kind": "source",
                    "label": "当前来源记录",
                    "detail": f"{source['date']} · {source['description']} · {public_source['amount']} 元；原件引用：{draft['evidence_ref'] or '尚未补充'}",
                },
                {
                    "id": f"lines:{draft_id}:v{draft['version']}",
                    "kind": "proposal",
                    "label": f"当前凭证分录 · v{draft['version']}",
                    "detail": draft["explanation"] or "当前草稿未保存生成依据",
                },
            ]
            timeline = [
                {
                    "id": "source:" + source["id"],
                    "label": "业务发生日期",
                    "status": "recorded",
                    "at": source["date"],
                    "detail": "来源记录中的日期，不代表导入或模型执行时间",
                }
            ]
            event = None
            mapping = conn.execute(
                "SELECT event_id FROM business_mapping_versions WHERE book_id=? AND source_id=? AND draft_id=?",
                (book_id, source["id"], draft_id),
            ).fetchone()
            current_mapping = (
                mapping
                and conn.execute(
                    "SELECT 1 FROM business_mappings WHERE event_id=? AND book_id=? AND source_id=? AND draft_id=?",
                    (mapping["event_id"], book_id, source["id"], draft_id),
                ).fetchone()
            )
            if mapping and current_mapping and not draft["source_inactive"]:
                # Book access does not substitute for the employee/department
                # event rules. Old mappings or revoked memberships expose none.
                try:
                    event = self.business._event(conn, actor, mapping["event_id"])
                except AccountingError as error:
                    if error.status not in {403, 404}:
                        raise
                if event:
                    from app.intelligence.current_evidence import superseded_ids

                    superseded = superseded_ids(self.db, conn, event)
                    for attachment in self.business._attachments(conn, event["id"]):
                        if attachment["id"] in superseded:
                            continue
                        evidence.append(
                            {
                                "id": "attachment:" + attachment["id"],
                                "kind": "attachment",
                                "label": attachment["filename"],
                                "detail": "员工上传的当前原件；打开后核对实际金额与业务用途",
                                "event_id": event["id"],
                                "attachment_id": attachment["id"],
                                "href": f"/api/business/attachments/{attachment['id']}/content",
                                "created_at": attachment["created_at"],
                            }
                        )
                    for action in conn.execute(
                        "SELECT id,stage,decision,created_at FROM business_actions WHERE event_id=? ORDER BY created_at,id",
                        (event["id"],),
                    ):
                        stage = {
                            "department": "部门审批",
                            "finance": "企业财务核验",
                            "handoff": "移交会计",
                            "return": "退回补充",
                        }.get(action["stage"], "业务处理")
                        timeline.append(
                            {
                                "id": action["id"],
                                "label": stage,
                                "status": action["decision"],
                                "at": action["created_at"],
                                "detail": "已保存的业务流程记录",
                            }
                        )
                    for job in conn.execute(
                        "SELECT id,status,updated_at,finished_at FROM ocr_jobs WHERE event_id=? AND expected_fact_version=? ORDER BY updated_at DESC,id LIMIT 12",
                        (event["id"], event["fact_version"]),
                    ):
                        timeline.append(
                            {
                                "id": "ocr:" + job["id"],
                                "label": "票据识别",
                                "status": job["status"],
                                "at": job["finished_at"] or job["updated_at"],
                                "detail": "当前事实版本的持久任务状态",
                            }
                        )
                    for result in conn.execute(
                        "SELECT r.id,r.created_at,r.finished_at,a.applied_draft_version FROM ai_proposal_applications a JOIN ai_runs r ON r.id=a.run_id AND r.book_id=a.book_id AND r.draft_id=a.draft_id WHERE a.book_id=? AND a.draft_id=? ORDER BY a.created_at DESC,a.id LIMIT 5",
                        (book_id, draft_id),
                    ):
                        evidence.append(
                            {
                                "id": "proposal:" + result["id"],
                                "kind": "proposal",
                                "label": f"已采纳建议 · v{result['applied_draft_version']}",
                                "detail": "该模型建议已有采纳回执；当前分录可能经后续人工修订",
                                "created_at": result["finished_at"] or result["created_at"],
                            }
                        )
            for audit in conn.execute(
                "SELECT id,action,created_at FROM audit_events WHERE book_id=? AND object_id=? AND action IN ('draft.suggest','draft.update') ORDER BY created_at,id DESC LIMIT 10",
                (book_id, draft_id),
            ):
                timeline.append(
                    {
                        "id": "audit:" + str(audit["id"]),
                        "label": "生成凭证草稿"
                        if audit["action"] == "draft.suggest"
                        else "人工修订凭证",
                        "status": "recorded",
                        "at": audit["created_at"],
                        "detail": "已保存的凭证操作记录",
                    }
                )
            if row["approved_at"]:
                timeline.append(
                    {
                        "id": "approved:" + draft_id,
                        "label": "会计复核",
                        "status": "approved",
                        "at": row["approved_at"],
                        "detail": "已保存人工复核记录",
                    }
                )
            if row["exported_at"]:
                timeline.append(
                    {
                        "id": "exported:" + draft_id,
                        "label": "导出凭证",
                        "status": "exported",
                        "at": row["exported_at"],
                        "detail": "导出不等同于外部财务软件已接收",
                    }
                )
            timeline.sort(key=lambda item: (item["at"] or "", item["id"]))
            mutable = (
                book["period_status"] == "open"
                and draft["status"] == "draft"
                and not draft["source_inactive"]
            )
            independent = not event or event["applicant_id"] != actor.user_id
            review_allowed = (
                mutable and independent and book["permissions"]["review"] and not draft["issues"]
            )
            actions = [
                {
                    "type": "review",
                    "label": "检查并复核凭证",
                    "book_id": book_id,
                    "draft_id": draft_id,
                    "enabled": bool(review_allowed),
                    "reason": "提交时仍会重新校验权限、版本、证据及借贷平衡"
                    if review_allowed
                    else "先处理阻断事项、检查当前权限和独立复核条件",
                },
                {
                    "type": "edit",
                    "label": "修改当前草稿",
                    "book_id": book_id,
                    "draft_id": draft_id,
                    "enabled": bool(mutable and book["permissions"]["edit"]),
                },
                {
                    "type": "export",
                    "label": "打开审核后导出",
                    "book_id": book_id,
                    "draft_id": draft_id,
                    "enabled": bool(
                        book["period_status"] == "open"
                        and draft["status"] == "approved"
                        and book["permissions"]["export"]
                    ),
                },
                {
                    "type": "history",
                    "label": "查看来源与修订历史",
                    "book_id": book_id,
                    "draft_id": draft_id,
                    "enabled": True,
                },
            ]
            if event:
                actions.append(
                    {
                        "type": "business",
                        "label": "查看原始业务事项",
                        "book_id": book_id,
                        "draft_id": draft_id,
                        "event_id": event["id"],
                        "enabled": True,
                    }
                )
            return {
                "mode": "evidence_summary",
                "scope": {
                    "book_id": book_id,
                    "company_id": book["company_id"],
                    "company_name": book["company_name"],
                    "book_name": book["name"],
                    "period": book["period"],
                    "period_status": book["period_status"],
                    "draft_id": draft_id,
                    "draft_version": draft["version"],
                    "fact_version": event["fact_version"] if event else None,
                },
                "draft": public_draft,
                "source": public_source,
                "evidence": evidence,
                "timeline": timeline,
                "actions": actions,
                "suggestions": [
                    "为什么使用这些科目？",
                    "金额与借贷是否一致？",
                    "哪些材料需要补充？",
                    "下一步由谁处理？",
                ],
                "notice": (
                    "此来源已被修订或隔离，仅整理该历史凭证的保存记录；请通过来源历史查看冻结原件，不关联新事项附件。"
                    if draft["source_inactive"] or mapping and not current_mapping
                    else "根据当前已保存资料整理，无新模型调用；不会自动审批、修改或导出。"
                ),
            }

    def explain(self, actor, book_id, draft_id, question):
        if not isinstance(question, str) or not question.strip() or len(question) > 1000:
            raise AccountingError("请输入 1–1000 字的问题")
        context = self.context(actor, book_id, draft_id)
        question = question.strip()
        draft, source = context["draft"], context["source"]
        evidence = context["evidence"]
        citations = evidence[:2]
        if any(
            token in question for token in ("税法", "税率", "抵扣", "税务", "合规", "优惠", "避税")
        ):
            answer = "当前资料摘要不能判断税法适用、税率或抵扣资格。请由会计结合有效票据、企业情况和适用规则独立核对；系统不会据此自动调整分录。"
            citations = []
        elif any(token in question for token in ("金额", "借贷", "平衡", "多少钱", "日期")):
            debit = sum(money_to_cents(line["debit"]) for line in draft["lines"])
            credit = sum(money_to_cents(line["credit"]) for line in draft["lines"])
            answer = f"当前来源日期 {source['date']}，金额 {source['amount']} 元。凭证借方合计 {cents_to_money(debit)} 元，贷方合计 {cents_to_money(credit)} 元，{'借贷相等' if debit == credit else '借贷不相等，需修订后复核'}。金额一致仅是校验之一，仍须核对原件、业务用途与期间。"
        elif any(token in question for token in ("科目", "分录", "原因", "为什么", "依据")):
            lines = "；".join(
                f"{line['account_code']} {line['account_name']}：借 {line['debit']} / 贷 {line['credit']}"
                for line in draft["lines"]
            )
            answer = f"当前分录为：{lines or '尚未保存分录'}。已保存的生成或修订说明：{draft['explanation'] or '缺少说明，不能推断选择依据'}。这说明当前草稿的形成依据，不能替代对实际会计处理的复核。"
        elif any(
            token in question for token in ("材料", "证据", "原件", "附件", "补充", "异常", "阻断")
        ):
            issues = "；".join(draft["issues"])
            attachments = [item for item in evidence if item["kind"] == "attachment"]
            answer = (
                f"当前有 {len(attachments)} 份可读的当前业务附件；来源存档引用为：{source['evidence_ref'] or '尚未补充'}。"
                + (
                    f"当前阻断或核验提示：{issues}。请按提示核对原件或补充资料。"
                    if issues
                    else "当前凭证校验未记录阻断项。请打开原件确认金额、交易对方和业务用途；摘要不代表原件已由助手重新验证。"
                )
            )
            citations += attachments[:5]
        elif any(token in question for token in ("下一步", "谁", "进度", "审核", "复核", "导出")):
            if draft["issues"]:
                answer = (
                    "账套经办人需先处理："
                    + "；".join(draft["issues"])
                    + "。完成后由具备复核权限且符合独立审批条件的会计核验；系统只提供入口，不执行审批。"
                )
            elif draft["status"] == "exported":
                answer = "凭证已导出。会计交付人仍须核对外部财务软件的导入回执；已导出状态不等同于客户软件已接收。"
            elif draft["status"] == "approved":
                answer = "当前凭证已完成会计复核，可由具备导出权限的会计交付人查看导出流程，并在交付后核验回执。"
            else:
                answer = "当前为未审核草稿。具备复核权限且符合独立审批条件的会计需检查原件与分录，再通过现有复核流程确认。"
            if context["scope"]["period_status"] != "open":
                answer += " 当前期间已关闭，相关写入操作不可执行。"
        else:
            answer = "当前材料不足以回答这个问题。我可以说明这张凭证已保存的科目依据、金额与借贷、原件引用、校验提示和后续处理；无法查询外部事实或代替你执行操作。"
            citations = []
        return {
            "mode": "evidence_summary",
            "question": question,
            "answer": answer,
            "citations": [
                {key: item[key] for key in ("id", "label", "detail")} for item in citations
            ],
            "scope": context["scope"],
            "actions": context["actions"],
        }
