"""Paged month views from current grants; no cached completeness claims."""

import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..accounting.domain import AccountingError
from ..accounting.queries import health_cte, inactive_source_sql, page_bounds
from ..accounting.service import now
from ..closing.queries import _json, receipt_valid_sql


class MonthlyWorkbenchService:
    def __init__(self, db, accounting, business, cases):
        self.db, self.accounting, self.business, self.cases = db, accounting, business, cases
        self.access = accounting.access

    @staticmethod
    def _groups(conn, sql, values):
        return {row["book_id"]: dict(row) for row in conn.execute(sql, values)}

    def list(self, actor, period, search="", limit=20, offset=0, focus="all"):
        page_bounds(limit, offset)
        if focus not in {"all", "mine", "due", "materials", "review", "receipt"}:
            raise AccountingError("请选择有效的月度工作筛选")
        if not isinstance(period, str) or not re.fullmatch(r"20[0-9]{2}-(?:0[1-9]|1[0-2])", period):
            raise AccountingError("请选择有效的 YYYY-MM 月份")
        if not isinstance(search, str) or len(search) > 120:
            raise AccountingError("企业搜索不得超过 120 个字符")
        escaped = (
            search.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        with self.db.transaction(actor=actor) as conn:
            role = self.access.firm_role(conn, actor)
            scope, values = self.accounting._summary_scope(actor)
            if role not in {"owner", "admin"}:
                scope += (
                    " AND a.role IS NOT NULL AND (?=1 OR cm.role IN ('owner','admin','finance'))"
                )
                values.append(1 if role else 0)
            scope += " AND b.period=?"
            values.append(period)
            no_book = """SELECT NULL AS id,NULL AS name,? AS period,c.id AS company_id,
                c.name AS company_name,'no_book' AS period_status,NULL AS book_role,
                cm.role AS company_role FROM companies c
                JOIN firm_clients fc ON fc.company_id=c.id AND fc.firm_id=? AND fc.status='active'
                LEFT JOIN company_memberships cm ON cm.company_id=c.id AND cm.user_id=?
                WHERE ?=1 AND NOT EXISTS(SELECT 1 FROM books b
                    WHERE b.company_id=c.id AND b.firm_id=fc.firm_id AND b.period=?)"""
            values.extend(
                [period, actor.firm_id, actor.user_id, int(role in {"owner", "admin"}), period]
            )
            condition, focus_values = self._focus(actor, role, focus)
            cte = f"WITH visible AS ({scope} UNION ALL {no_book}), filtered AS (SELECT v.* FROM visible v WHERE (LOWER(company_name) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(name,'')) LIKE ? ESCAPE '\\') AND ({condition}))"
            values.extend(["%" + escaped + "%"] * 2)
            values.extend(focus_values)
            rows = conn.execute(
                cte
                + """ SELECT page.*,(SELECT COUNT(*) FROM filtered) AS total FROM
                (SELECT 1 AS one) anchor LEFT JOIN
                (SELECT * FROM filtered ORDER BY company_name,COALESCE(name,''),company_id,id LIMIT ? OFFSET ?) page ON 1=1
                ORDER BY company_name,COALESCE(name,''),company_id,id""",
                [*values, limit, offset],
            ).fetchall()
            total = rows[0]["total"]
            rows = [dict(row) for row in rows if row["company_id"] is not None]
            ids = [row["id"] for row in rows if row["id"]]
            facts = self._facts(conn, actor, ids, period) if ids else {}
            public = [self._row(row, role, facts) for row in rows]
            for item in public:
                item["work"] = self._work(item)
            return {
                "period": period,
                "focus": focus,
                "rows": public,
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "total": total,
                    "has_more": offset + len(public) < total,
                },
                "updated_at": now(),
                "notes": [
                    "每行是当前授权的企业、账套与月份；同企业多账套分别核对。",
                    "业务待交按当前企业财务或会计接收入口计数，本人和部门事项在各自入口处理；不能证明客户已交齐资料。",
                    "交付统计单位是导出批次；核验一致不表示厂商正式过账。",
                    "月结旧快照仅作历史参考，进入月结页重新预检后才能锁期。",
                    "人工耗时只累计明确开启且确认活跃的计时；缺少记录表示未测量。",
                ],
            }

    def _focus(self, actor, role, focus):
        """Predicates precede LIMIT and use the same authorization as row details."""
        manager = role in {"owner", "admin"}
        can_edit = (
            "1=1"
            if manager
            else (
                "v.book_role IN ('editor','reviewer') AND "
                + (
                    "1=1"
                    if role in {"operator", "reviewer"}
                    else "v.company_role IN ('owner','admin','finance')"
                    if not role
                    else "1=0"
                )
            )
        )
        can_review = "1=1" if manager else "v.book_role='reviewer'" if role == "reviewer" else "1=0"
        plan = "p.book_id=v.id AND p.version=(SELECT MAX(q.version) FROM service_plan_versions q WHERE q.book_id=v.id)"
        if focus == "mine":
            return (
                f"({can_edit}) AND EXISTS(SELECT 1 FROM service_plan_versions p WHERE {plan} AND (p.lead_user_id=? OR p.reviewer_user_id=? OR p.backup_user_id=?))",
                [actor.user_id] * 3,
            )
        if focus == "due":
            cutoff = (
                datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=7)
            ).isoformat()
            return (
                f"({can_edit}) AND EXISTS(SELECT 1 FROM service_plan_versions p WHERE {plan} AND p.due_on<=?) AND NOT EXISTS(SELECT 1 FROM monthly_delivery_packages p JOIN monthly_delivery_acknowledgements a ON a.package_id=p.id WHERE p.book_id=v.id AND p.version=(SELECT MAX(q.version) FROM monthly_delivery_packages q WHERE q.book_id=v.id))",
                [cutoff],
            )
        if focus == "materials":
            return (
                f"({can_edit}) AND EXISTS(SELECT 1 FROM material_requests r LEFT JOIN material_request_actions a ON a.request_id=r.id AND a.version=(SELECT MAX(q.version) FROM material_request_actions q WHERE q.request_id=r.id) WHERE r.book_id=v.id AND COALESCE(a.state,'pending') IN ('pending','returned'))",
                [],
            )
        if focus == "review":
            inactive = inactive_source_sql(self.db.dialect)
            return (
                f"({can_review}) AND EXISTS(SELECT 1 FROM drafts d JOIN sources s ON s.id=d.source_id AND s.book_id=d.book_id WHERE d.book_id=v.id AND d.status='draft' AND NOT ({inactive}))",
                [],
            )
        if focus == "receipt":
            return (
                f"({can_review}) AND EXISTS(SELECT 1 FROM monthly_delivery_packages p WHERE p.book_id=v.id AND p.version=(SELECT MAX(q.version) FROM monthly_delivery_packages q WHERE q.book_id=v.id) AND NOT EXISTS(SELECT 1 FROM monthly_delivery_acknowledgements a WHERE a.package_id=p.id))",
                [],
            )
        return "1=1", []

    @staticmethod
    def _work(row):
        plan = row.get("service_plan") or {}
        work = {
            "owner": plan.get("lead_name") or "主办会计待安排",
            "due_on": plan.get("due_on"),
            "reason": "核对本月服务进度",
            "target": "service",
            "label": "继续办理",
        }
        if not row["book_id"]:
            return {
                **work,
                "owner": "事务所管理员",
                "reason": "尚未开通本月账套",
                "target": None,
                "label": "等待开通",
            }
        if (row.get("material_requests") or {}).get("pending", 0) > 0:
            work.update(
                reason="有待补交或退回的资料；请在服务单查看各项负责人", label="查看待补资料"
            )
        elif row["permissions"]["review"] and (row["drafts"]["pending"] or 0) > 0:
            work.update(
                owner=plan.get("reviewer_name") or "授权复核员",
                reason="有待复核凭证；须核对当前版本制单责任",
                target="review",
                label="继续复核",
            )
        elif (row.get("customer_package") or {}).get("pending"):
            work.update(
                owner="指定客户签收人",
                reason="最新月度包尚无客户签收；查看不等于签收",
                target="delivery",
                label="查看签收进度",
            )
        if not (
            row["permissions"]["edit"]
            or row["permissions"]["review"]
            or row["permissions"]["manage"]
        ):
            work.update(target=None, label="当前仅可查看")
        return work

    def _facts(self, conn, actor, ids, period):
        marks = ",".join("?" for _ in ids)
        scope, params = self.accounting._summary_scope(actor)
        scope += f" AND b.id IN ({marks})"
        params.extend(ids)
        health = self._groups(
            conn,
            health_cte(scope, self.db.dialect)
            + """ SELECT v.id AS book_id,
                SUM(CASE WHEN h.status='draft' AND h.source_inactive=0 THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN h.status='approved' THEN 1 ELSE 0 END) AS approved,
                SUM(CASE WHEN h.status='exported' THEN 1 ELSE 0 END) AS exported,
                SUM(CASE WHEN h.status='draft' AND h.source_inactive=0 AND h.basic_exception=1 THEN 1 ELSE 0 END) AS basic_exceptions
                FROM visible_books v LEFT JOIN health h ON h.book_id=v.id GROUP BY v.id""",
            params,
        )
        inactive = inactive_source_sql(self.db.dialect)
        sources = self._groups(
            conn,
            f"""SELECT s.book_id,COUNT(*) AS sources,
                SUM(CASE WHEN trim(COALESCE((SELECT se.evidence_ref FROM source_evidence se
                    WHERE se.book_id=s.book_id AND se.source_id=s.id ORDER BY se.id DESC LIMIT 1),s.evidence_ref))=''
                    THEN 1 ELSE 0 END) AS missing
                FROM sources s WHERE s.book_id IN ({marks}) AND NOT ({inactive}) GROUP BY s.book_id""",
            ids,
        )
        event_month = self.business.submission.period_sql()
        finance = """EXISTS(SELECT 1 FROM company_memberships cm
            WHERE cm.company_id=e.company_id AND cm.user_id=? AND cm.role='finance')"""
        direct = """(e.submission_mode='direct' AND e.status IN ('submitted','returned') AND e.applicant_id<>?
            AND EXISTS(SELECT 1 FROM books b LEFT JOIN effective_book_access ba ON ba.book_id=b.id AND ba.user_id=?
                LEFT JOIN company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=?
                WHERE b.firm_id=e.firm_id AND b.company_id=e.company_id AND b.period=? AND (
                    EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id=b.firm_id AND fm.user_id=? AND fm.role IN ('owner','admin'))
                    OR ba.role IN ('editor','reviewer') AND (
                        EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id=b.firm_id AND fm.user_id=? AND fm.role IN ('owner','admin','operator','reviewer'))
                        OR NOT EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id=b.firm_id AND fm.user_id=?) AND cm.role IN ('owner','admin','finance')
                    )
                )))"""
        business = {
            row["company_id"]: row["pending"]
            for row in conn.execute(
                f"""SELECT e.company_id,COUNT(*) AS pending FROM business_events e
                    WHERE e.firm_id=? AND {event_month}=? AND e.status<>'handed_off'
                    AND e.company_id IN (SELECT b.company_id FROM books b WHERE b.id IN ({marks}))
                    AND (({finance}) OR {direct}) GROUP BY e.company_id""",
                [
                    actor.firm_id,
                    period,
                    *ids,
                    actor.user_id,
                    actor.user_id,
                    actor.user_id,
                    actor.user_id,
                    period,
                    actor.user_id,
                    actor.user_id,
                    actor.user_id,
                ],
            )
        }
        case_scope, cp = self.cases._scope_sql(actor)
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        cases = self._groups(
            conn,
            f"""SELECT COALESCE(t.book_id,bm.book_id) AS book_id,COUNT(*) AS open,
                SUM(CASE WHEN t.state='awaiting_info' THEN 1 ELSE 0 END) AS awaiting_info,
                SUM(CASE WHEN t.due_on<? THEN 1 ELSE 0 END) AS overdue
                FROM {self.cases._task_table()} t LEFT JOIN business_mappings bm ON bm.event_id=t.event_id
                WHERE t.firm_id=? AND t.state<>'closed' AND {case_scope}
                AND COALESCE(t.book_id,bm.book_id) IN ({marks}) GROUP BY COALESCE(t.book_id,bm.book_id)""",
            [today, actor.firm_id, *cp, *ids],
        )
        json_value, each = _json(self.db.dialect)
        items = each("r.body_json", "items", "item")
        reported_status = json_value("item.value", "status")
        missing_id = json_value("item.value", "external_voucher_id")
        unknown = f"""(NOT EXISTS(SELECT 1 FROM {items}) OR EXISTS(SELECT 1 FROM {items}
            WHERE {reported_status} IS NULL OR {reported_status} NOT IN ('accepted','rejected')
            OR {reported_status}='accepted' AND trim(COALESCE({missing_id},''))=''))"""
        valid = receipt_valid_sql(self.db.dialect)
        delivery = self._groups(
            conn,
            f"""SELECT x.book_id,
                SUM(CASE WHEN r.id IS NULL THEN 1 ELSE 0 END) AS unverified,
                SUM(CASE WHEN r.id IS NOT NULL AND {unknown} THEN 1 ELSE 0 END) AS unknown,
                SUM(CASE WHEN r.id IS NOT NULL AND NOT {unknown} AND NOT ({valid}) THEN 1 ELSE 0 END) AS differences,
                SUM(CASE WHEN {valid} THEN 1 ELSE 0 END) AS verified
                FROM exports x LEFT JOIN delivery_receipts r ON r.export_id=x.id AND r.book_id=x.book_id
                AND NOT EXISTS(SELECT 1 FROM delivery_receipts newer WHERE newer.export_id=r.export_id AND newer.book_id=r.book_id
                    AND (newer.created_at>r.created_at OR newer.created_at=r.created_at AND newer.id>r.id))
                WHERE x.book_id IN ({marks}) GROUP BY x.book_id""",
            ids,
        )
        snapshots = self._groups(
            conn,
            f"""SELECT s.book_id,s.id,s.report_json,s.created_at FROM closing_snapshots s
                WHERE s.book_id IN ({marks}) AND NOT EXISTS(SELECT 1 FROM closing_snapshots newer
                    WHERE newer.book_id=s.book_id AND (newer.created_at>s.created_at OR newer.created_at=s.created_at AND newer.id>s.id))""",
            ids,
        )
        ledgers = self._groups(
            conn, f"SELECT book_id,ledger_id FROM ledger_periods WHERE book_id IN ({marks})", ids
        )
        timers = self._groups(
            conn,
            f"""SELECT book_id,SUM(credited_seconds) AS seconds FROM human_work_sessions
                WHERE firm_id=? AND book_id IN ({marks}) GROUP BY book_id""",
            [actor.firm_id, *ids],
        )
        audits = self._groups(
            conn,
            f"""SELECT book_id,
                COUNT(DISTINCT CASE WHEN action='draft.approve' THEN object_id ELSE NULL END) AS reviews,
                SUM(CASE WHEN action IN ('draft.update','closing.review','closing.close','review.confirm') THEN 1 ELSE 0 END) AS manual_actions
                FROM audit_events WHERE firm_id=? AND book_id IN ({marks}) GROUP BY book_id""",
            [actor.firm_id, *ids],
        )
        service_plans = self._groups(
            conn,
            f"""SELECT p.*,labels.lead_name,labels.reviewer_name FROM service_plan_versions p
                LEFT JOIN LATERAL accounting.service_plan_labels(p.book_id) labels ON true
                WHERE p.book_id IN ({marks})
                AND p.version=(SELECT max(v.version) FROM service_plan_versions v WHERE v.book_id=p.book_id)"""
            if self.db.is_postgres
            else f"""SELECT p.*,lead.name AS lead_name,reviewer.name AS reviewer_name FROM service_plan_versions p
                LEFT JOIN users lead ON lead.id=p.lead_user_id LEFT JOIN users reviewer ON reviewer.id=p.reviewer_user_id WHERE p.book_id IN ({marks})
                AND p.version=(SELECT max(v.version) FROM service_plan_versions v WHERE v.book_id=p.book_id)""",
            ids,
        )
        current = (
            "accounting.service_material_current(r.id)"
            if self.db.is_postgres
            else "EXISTS(SELECT 1 FROM service_material_currentness c WHERE c.request_id=r.id AND c.is_current=1)"
        )
        materials = self._groups(
            conn,
            f"""SELECT r.book_id,COUNT(*) AS total,
                SUM(CASE WHEN COALESCE(a.state,'pending') IN('pending','returned') THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN a.state='submitted' THEN 1 ELSE 0 END) AS submitted,
                SUM(CASE WHEN a.state='accepted' AND {current} THEN 1 ELSE 0 END) AS accepted,
                SUM(CASE WHEN r.required=1 AND NOT ({current}) THEN 1 ELSE 0 END) AS required_pending
                FROM material_requests r LEFT JOIN material_request_actions a ON a.request_id=r.id
                AND a.version=(SELECT max(v.version) FROM material_request_actions v WHERE v.request_id=r.id)
                WHERE r.book_id IN ({marks}) GROUP BY r.book_id""",
            ids,
        )
        packages = self._groups(
            conn,
            f"""SELECT p.book_id,p.id,p.version,p.manifest_sha256,
                CASE WHEN a.package_id IS NULL THEN 1 ELSE 0 END AS pending
                FROM monthly_delivery_packages p LEFT JOIN monthly_delivery_acknowledgements a ON a.package_id=p.id
                WHERE p.book_id IN ({marks}) AND p.version=(SELECT MAX(q.version) FROM monthly_delivery_packages q WHERE q.book_id=p.book_id)""",
            ids,
        )
        return {
            "packages": packages,
            "health": health,
            "sources": sources,
            "business": business,
            "cases": cases,
            "delivery": delivery,
            "snapshots": snapshots,
            "ledgers": ledgers,
            "timers": timers,
            "audits": audits,
            "service_plans": service_plans,
            "material_requests": materials,
        }

    def _row(self, row, role, facts):
        book_id = row["id"]
        permission = self.access.permissions(role, row["book_role"], row["company_role"])
        permission.pop("read")

        def group(kind):
            return facts.get(kind, {}).get(book_id, {})

        def count(kind, key):
            return int(group(kind).get(key) or 0)

        review = bool(book_id and permission["review"])
        health = {
            key: count("health", key)
            for key in ("pending", "approved", "exported", "basic_exceptions")
        }
        snapshot = group("snapshots") if review else {}
        closed = row["period_status"] == "closed"
        state = "no_book" if not book_id else "closed" if closed else "open"
        last = json.loads(snapshot["report_json"]) if snapshot else None
        business_count = facts.get("business", {}).get(row["company_id"])
        # A zero visible business count cannot establish company completeness.
        # Staff intake is private and is deliberately not aggregated here.
        return {
            "row_key": book_id or "company:" + row["company_id"] + ":" + row["period"],
            "company_id": row["company_id"],
            "company_name": row["company_name"],
            "book_id": book_id,
            "book_name": row["name"],
            "ledger_id": group("ledgers").get("ledger_id"),
            "period": row["period"],
            "status": state,
            "permissions": permission,
            "materials": {
                "sources": count("sources", "sources") if book_id else None,
                "missing": count("sources", "missing") if book_id else None,
                "business_pending": int(business_count) if business_count is not None else None,
                "intake_pending": None,
            },
            "drafts": health if book_id else {key: None for key in health},
            "exceptions": {
                key: count("cases", key) if review else None
                for key in ("open", "awaiting_info", "overdue")
            },
            "delivery": {
                key: count("delivery", key) if review else None
                for key in ("unverified", "unknown", "differences", "verified")
            },
            "customer_package": dict(group("packages")) if review and group("packages") else None,
            "closing": {
                "state": "closed"
                if closed
                else "restricted"
                if not review
                else "recheck"
                if snapshot
                else "not_checked",
                "blockers": None,
                "snapshot_id": snapshot.get("id"),
                "last_check": {
                    "passed": last["passed"] is True,
                    "issue_count": last["issue_count"],
                    "created_at": snapshot["created_at"],
                }
                if last
                else None,
            },
            "efficiency": {
                "reviews": count("audits", "reviews") if review else None,
                "manual_actions": count("audits", "manual_actions") if review else None,
                "handling_seconds": group("timers").get("seconds") if review else None,
            },
            "service_plan": {
                key: group("service_plans").get(key)
                for key in (
                    "version",
                    "lead_user_id",
                    "lead_name",
                    "reviewer_user_id",
                    "reviewer_name",
                    "backup_user_id",
                    "contact_user_id",
                    "due_on",
                )
            }
            if permission["edit"] and group("service_plans")
            else None,
            "material_requests": {
                key: count("material_requests", key)
                for key in ("total", "pending", "submitted", "accepted", "required_pending")
            }
            if permission["edit"]
            else None,
            "data_scope": {
                "business": "company_finance_or_accounting_reception",
                "intake": "not_aggregated",
                "exceptions": "visible_book_cases",
                "delivery": "export_batches",
                "efficiency": "explicit_book_sessions",
            },
            "warnings": ["未提交资料由资料负责人核对；没有可见待办不表示本月资料齐全。"],
        }
