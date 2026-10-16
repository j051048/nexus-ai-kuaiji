"""受审计的工作交接转派：把一名成员名下未结束的账套职责移交接替人。

v1 scope: open-period book roles (editor/reviewer), the latest service plan on
those books, and未关闭的异常／补件任务。主办与复核不得同人；接替人必须仍具备
对应能力（或在本批转派后具备）。逐项跳过并说明原因，一次写事务完成全部变更
并留下 member.handover 审计快照。
"""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from .access import FIRM_MANAGERS, FIRM_REVIEWERS
from .directory import DirectoryService
from .domain import AccountingError, Actor
from .service import now

UserId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


class HandoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_user_id: UserId
    reason: Reason


class HandoverService:
    def __init__(self, db, accounting, plans, cases):
        self.db, self.accounting, self.plans, self.cases = db, accounting, plans, cases
        self.access = accounting.access

    def preview(self, actor, from_user_id, to_user_id):
        with self.db.transaction(actor=actor) as conn:
            self._authorize(conn, actor, from_user_id, to_user_id)
            plan = self._plan(conn, actor, from_user_id, to_user_id)
        return self._public(from_user_id, to_user_id, plan, executed=False)

    def execute(self, actor, from_user_id, request: HandoverRequest):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._authorize(conn, actor, from_user_id, request.to_user_id)
            plan = self._plan(conn, actor, from_user_id, request.to_user_id)
            self._apply(conn, actor, from_user_id, request.to_user_id, request.reason, plan)
            self._audit(conn, actor, from_user_id, request, plan)
        return self._public(from_user_id, request.to_user_id, plan, executed=True)

    def _authorize(self, conn, actor, from_user_id, to_user_id):
        self.access.require_manager(conn, actor)
        for user_id, label in ((from_user_id, "移交人"), (to_user_id, "接替人")):
            if not isinstance(user_id, str) or not re.fullmatch(r"[\w.:-]{1,100}", user_id):
                raise AccountingError(f"{label}标识无效", 422)
        if from_user_id == to_user_id:
            raise AccountingError("接替人不能与移交人相同", 409)
        for user_id, label in ((from_user_id, "移交人"), (to_user_id, "接替人")):
            row = conn.execute(
                "SELECT u.active,m.role AS firm_role FROM users u "
                "LEFT JOIN memberships m ON m.user_id=u.id AND m.firm_id=? WHERE u.id=?",
                (actor.firm_id, user_id),
            ).fetchone()
            if not row or not row["active"]:
                raise AccountingError(f"{label}账号不存在或已停用", 404)
            if label == "接替人":
                if row["firm_role"] not in {"owner", "admin", "operator", "reviewer"}:
                    raise AccountingError("接替人需要事务所经办、复核或管理职责", 409)

    def _target_context(self, conn, actor, company_id, to_user_id):
        firm_role = self.access.firm_role(conn, Actor(to_user_id, actor.firm_id))
        company_role = self.access.company_role(
            conn, Actor(to_user_id, actor.firm_id), company_id
        )
        return firm_role, company_role

    def _capabilities(self, firm_role, book_role, company_role):
        permissions = self.access.permissions(firm_role, book_role, company_role)
        return {
            "can_lead": permissions["edit"],
            "can_review": permissions["review"],
            "can_contact": bool(company_role),
        }

    def _plan(self, conn, actor, from_user_id, to_user_id):
        books, planned, cases, skipped = [], [], [], []
        effective_roles = {}
        rows = conn.execute(
            "SELECT b.id AS book_id,b.name,b.company_id,b.period,a.role FROM book_access a "
            "JOIN books b ON b.id=a.book_id AND b.firm_id=a.firm_id "
            "JOIN periods p ON p.book_id=b.id AND p.period=b.period "
            "WHERE a.firm_id=? AND a.user_id=? AND a.role IN ('editor','reviewer') "
            "AND p.status='open' ORDER BY b.period,b.name,b.id",
            (actor.firm_id, from_user_id),
        ).fetchall()
        for row in rows:
            target = conn.execute(
                "SELECT role FROM book_access WHERE book_id=? AND user_id=?",
                (row["book_id"], to_user_id),
            ).fetchone()
            if target and target["role"] != row["role"]:
                skipped.append(
                    {
                        "kind": "book",
                        "ref": row["book_id"],
                        "title": f"{row['name']} · {row['period']}",
                        "reason": "接替人在该账套已有其他职责，主办与复核不得同人，需人工调整",
                    }
                )
                continue
            firm_role, company_role = self._target_context(conn, actor, row["company_id"], to_user_id)
            effective = target["role"] if target else row["role"]
            capability = "can_review" if row["role"] == "reviewer" else "can_lead"
            if not target and not self._capabilities(firm_role, effective, company_role)[capability]:
                skipped.append(
                    {
                        "kind": "book",
                        "ref": row["book_id"],
                        "title": f"{row['name']} · {row['period']}",
                        "reason": "接替人不具备该账套所需的"
                        + ("复核" if row["role"] == "reviewer" else "主办")
                        + "权限",
                    }
                )
                continue
            effective_roles[row["book_id"]] = effective
            books.append(
                {
                    "book_id": row["book_id"],
                    "name": row["name"],
                    "period": row["period"],
                    "role": row["role"],
                    "action": "merge" if target else "transfer",
                }
            )
        plan_rows = conn.execute(
            "SELECT v.*,b.name AS book_name,b.company_id,b.period FROM service_plan_versions v "
            "JOIN books b ON b.id=v.book_id "
            "JOIN periods p ON p.book_id=b.id AND p.period=b.period "
            "WHERE v.firm_id=? AND p.status='open' "
            "AND v.version=(SELECT MAX(x.version) FROM service_plan_versions x WHERE x.book_id=v.book_id) "
            "AND (v.lead_user_id=? OR v.reviewer_user_id=? OR v.backup_user_id=? OR v.contact_user_id=?) "
            "ORDER BY b.period,b.name,b.id",
            (actor.firm_id, from_user_id, from_user_id, from_user_id, from_user_id),
        ).fetchall()
        for row in plan_rows:
            firm_role, company_role = self._target_context(conn, actor, row["company_id"], to_user_id)
            effective = effective_roles.get(row["book_id"], self._current_book_role(conn, row["book_id"], to_user_id))
            capabilities = self._capabilities(firm_role, effective, company_role)
            changed = {}
            for field, capability, label in (
                ("lead_user_id", "can_lead", "主办"),
                ("reviewer_user_id", "can_review", "独立复核"),
                ("backup_user_id", "can_lead", "备援"),
                ("contact_user_id", "can_contact", "资料联系人"),
            ):
                if row[field] != from_user_id:
                    continue
                if not capabilities[capability]:
                    skipped.append(
                        {
                            "kind": "plan",
                            "ref": row["book_id"],
                            "title": f"{row['book_name']} · {row['period']}",
                            "reason": f"接替人不具备该账套的{label}权限",
                        }
                    )
                    continue
                changed[field] = to_user_id
            lead_after = changed.get("lead_user_id", row["lead_user_id"])
            reviewer_after = changed.get("reviewer_user_id", row["reviewer_user_id"])
            if lead_after and lead_after == reviewer_after:
                for field in ("reviewer_user_id", "lead_user_id"):
                    if field in changed:
                        skipped.append(
                            {
                                "kind": "plan",
                                "ref": row["book_id"],
                                "title": f"{row['book_name']} · {row['period']}",
                                "reason": "转派后主办与独立复核将成为同人，需另行指定他人",
                            }
                        )
                        del changed[field]
                lead_after = changed.get("lead_user_id", row["lead_user_id"])
                reviewer_after = changed.get("reviewer_user_id", row["reviewer_user_id"])
                if lead_after and lead_after == reviewer_after:
                    changed = {}
            if changed:
                planned.append(
                    {
                        "book_id": row["book_id"],
                        "book_name": row["book_name"],
                        "period": row["period"],
                        "version": row["version"] + 1,
                        "changes": changed,
                    }
                )
        case_rows = conn.execute(
            "SELECT t.* FROM case_tasks t WHERE t.firm_id=? AND t.assignee_id=? AND t.state<>'closed' "
            "ORDER BY t.id",
            (actor.firm_id, from_user_id),
        ).fetchall()
        for row in case_rows:
            firm_role, company_role = self._target_context(conn, actor, row["company_id"], to_user_id)
            effective = effective_roles.get(
                row["book_id"],
                self._current_book_role(conn, row["book_id"], to_user_id),
            )
            if not self._case_receivable(
                conn, row, firm_role, company_role, effective, to_user_id
            ):
                skipped.append(
                    {
                        "kind": "case",
                        "ref": row["id"],
                        "title": row["title"],
                        "reason": "接替人不具备该任务所需的账套复核权限或身份范围",
                    }
                )
                continue
            cases.append(
                {
                    "case_id": row["id"],
                    "kind": row["kind"],
                    "title": row["title"],
                    "company_id": row["company_id"],
                    "book_id": row["book_id"],
                }
            )
        return {"books": books, "plans": planned, "cases": cases, "skipped": skipped}

    @staticmethod
    def _current_book_role(conn, book_id, user_id):
        if not book_id:
            return None
        row = conn.execute(
            "SELECT role FROM book_access WHERE book_id=? AND user_id=?", (book_id, user_id)
        ).fetchone()
        return row["role"] if row else None

    def _case_receivable(self, conn, row, firm_role, company_role, book_role, to_user_id):
        """Mirror CaseService._scope_sql for the prospective receiver."""
        if row["kind"] == "supplement" and row["recipient_id"] == to_user_id and company_role:
            return True
        if row["event_id"]:
            event = conn.execute(
                "SELECT applicant_id FROM business_events WHERE id=?", (row["event_id"],)
            ).fetchone()
            if (
                company_role == "finance"
                and event
                and event["applicant_id"] != to_user_id
            ):
                return True
        if not row["book_id"]:
            return False
        if firm_role in FIRM_MANAGERS:
            return True
        if book_role == "reviewer" and (
            firm_role in FIRM_REVIEWERS or (firm_role is None and company_role == "finance")
        ):
            return True
        # 收集人任务：本函数只评估即将移交给接替人的任务（转派后 assignee 即为
        # 接替人），其余条件与 CaseService._scope_sql 的收集人分支保持一致。
        collector = (
            row["kind"] == "exception"
            and row["event_id"] is None
            and company_role == "finance"
            and book_role in {"editor", "reviewer"}
            and (firm_role is None or firm_role in {"owner", "admin", "operator", "reviewer"})
        )
        return bool(collector)

    def _apply(self, conn, actor, from_user_id, to_user_id, reason, plan):
        for item in plan["books"]:
            if item["action"] == "merge":
                conn.execute(
                    "DELETE FROM book_access WHERE book_id=? AND firm_id=? AND user_id=?",
                    (item["book_id"], actor.firm_id, from_user_id),
                )
            else:
                conn.execute(
                    "UPDATE book_access SET user_id=? WHERE book_id=? AND firm_id=? AND user_id=?",
                    (to_user_id, item["book_id"], actor.firm_id, from_user_id),
                )
        for item in plan["plans"]:
            row = conn.execute(
                "SELECT * FROM service_plan_versions WHERE book_id=? ORDER BY version DESC LIMIT 1",
                (item["book_id"],),
            ).fetchone()
            fields = {
                key: row[key]
                for key in ("lead_user_id", "reviewer_user_id", "backup_user_id", "contact_user_id")
            }
            fields.update(item["changes"])
            conn.execute(
                "INSERT INTO service_plan_versions(book_id,firm_id,company_id,version,lead_user_id,reviewer_user_id,backup_user_id,contact_user_id,due_on,notes,reason,updated_by,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item["book_id"],
                    actor.firm_id,
                    row["company_id"],
                    row["version"] + 1,
                    fields["lead_user_id"],
                    fields["reviewer_user_id"],
                    fields["backup_user_id"],
                    fields["contact_user_id"],
                    row["due_on"],
                    row["notes"] or "",
                    "人员交接转派：" + reason,
                    actor.user_id,
                    now(),
                ),
            )
        for item in plan["cases"]:
            conn.execute(
                "UPDATE case_tasks SET assignee_id=? WHERE id=? AND assignee_id=?",
                (to_user_id, item["case_id"], from_user_id),
            )

    def _audit(self, conn, actor, from_user_id, request, plan):
        DirectoryService._audit(
            conn,
            actor,
            "member.handover",
            from_user_id,
            {
                "to_user_id": request.to_user_id,
                "reason": request.reason,
                "books": plan["books"],
                "plans": [
                    {
                        "book_id": item["book_id"],
                        "version": item["version"],
                        "changes": item["changes"],
                    }
                    for item in plan["plans"]
                ],
                "cases": [item["case_id"] for item in plan["cases"]],
                "skipped": plan["skipped"],
            },
        )

    @staticmethod
    def _public(from_user_id, to_user_id, plan, executed):
        return {
            "from_user_id": from_user_id,
            "to_user_id": to_user_id,
            "executed": executed,
            "books": plan["books"],
            "plans": plan["plans"],
            "cases": plan["cases"],
            "skipped": plan["skipped"],
            "counts": {
                "books": len(plan["books"]),
                "plans": len(plan["plans"]),
                "cases": len(plan["cases"]),
                "skipped": len(plan["skipped"]),
            },
        }
