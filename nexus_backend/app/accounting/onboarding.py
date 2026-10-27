"""Live setup facts, never a browser-maintained claim that a client is ready."""

import re

from .domain import AccountingError
from .service import now


# The PostgreSQL definer uses this same projection behind a company-manager
# guard. SQLite has no RLS; it still enters through require_admin_company.
FACTS_SQL = """
WITH args AS (SELECT ? AS company_id,? AS firm_id,? AS period),
 client AS (
   SELECT c.id,COALESCE(p.mode,'department') AS mode,COALESCE(p.version,0) AS policy_version
   FROM companies c JOIN args a ON a.company_id=c.id
   LEFT JOIN business_submission_policies p ON p.company_id=c.id AND p.firm_id=a.firm_id
 ), people AS (
   SELECT cm.* FROM company_memberships cm JOIN users u ON u.id=cm.user_id AND u.active=1
   JOIN args a ON a.company_id=cm.company_id
 ), month_books AS (
   SELECT b.* FROM books b JOIN args a ON a.company_id=b.company_id AND a.firm_id=b.firm_id AND a.period=b.period
 ), capabilities AS (
   SELECT b.id AS book_id,u.id AS user_id,
    CASE WHEN fm.role IN ('owner','admin') OR (ba.role IN ('editor','reviewer')
      AND (fm.role IN ('operator','reviewer') OR (fm.role IS NULL AND cm.role IN ('owner','admin','finance')))) THEN 1 ELSE 0 END AS can_edit,
    CASE WHEN fm.role IN ('owner','admin') OR (ba.role='reviewer'
      AND (fm.role='reviewer' OR (fm.role IS NULL AND cm.role='finance'))) THEN 1 ELSE 0 END AS can_review
   FROM month_books b JOIN users u ON u.active=1
   LEFT JOIN memberships fm ON fm.firm_id=b.firm_id AND fm.user_id=u.id
   LEFT JOIN company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
   LEFT JOIN book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id
 ), submitters AS (
   SELECT p.* FROM people p CROSS JOIN client c
   WHERE c.mode='department' OR p.role IN ('owner','admin','finance')
 )
 SELECT c.mode,c.policy_version,
  (SELECT COUNT(*) FROM people) AS active_members,
  (SELECT COUNT(*) FROM submitters) AS submitters,
  (SELECT COUNT(*) FROM month_books) AS books,
  (SELECT COUNT(*) FROM month_books b JOIN rr_book_policies rp ON rp.book_id=b.id
    WHERE EXISTS(SELECT 1 FROM capabilities x JOIN capabilities y ON y.book_id=x.book_id
      WHERE x.book_id=b.id AND x.can_edit=1 AND y.can_review=1
       AND (rp.mode='single_operator' OR x.user_id<>y.user_id))) AS staffed_books,
  (SELECT COUNT(*) FROM month_books b JOIN rr_book_policies rp ON rp.book_id=b.id WHERE rp.mode='single_operator') AS single_operator_books,
  (SELECT COUNT(*) FROM submitters s WHERE
    (c.mode='direct' AND NOT EXISTS(SELECT 1 FROM capabilities x WHERE x.can_edit=1 AND x.user_id<>s.user_id))
    OR (c.mode='department' AND NOT EXISTS(
       SELECT 1 FROM departments d JOIN args a ON a.company_id=d.company_id
       WHERE (d.id=s.department_id OR (s.department_id IS NULL AND s.role<>'employee'))
        AND EXISTS(SELECT 1 FROM department_approvers da JOIN people ap ON ap.user_id=da.user_id
          WHERE da.firm_id=a.firm_id AND da.company_id=a.company_id AND da.department_id=d.id AND da.user_id<>s.user_id)
        AND EXISTS(SELECT 1 FROM people fp WHERE fp.role='finance' AND fp.user_id<>s.user_id)
    ))) AS blocked_submitters,
  (SELECT COUNT(*) FROM business_events e JOIN args a ON a.company_id=e.company_id AND a.firm_id=e.firm_id
    JOIN business_mappings bm ON bm.event_id=e.id AND bm.firm_id=e.firm_id AND bm.company_id=e.company_id
    JOIN month_books b ON b.id=bm.book_id
    JOIN drafts dr ON dr.id=bm.draft_id AND dr.book_id=b.id
    WHERE e.status='handed_off' AND dr.status='exported') AS verified_flow,
  COALESCE((SELECT enabled FROM ai_company_settings s JOIN args a ON a.firm_id=s.firm_id AND a.company_id=s.company_id),0) AS ai_enabled
 FROM client c
"""


def company_onboarding(service, actor, company_id, period):
    if not isinstance(period, str) or not re.fullmatch(r"20[0-9]{2}-(?:0[1-9]|1[0-2])", period):
        raise AccountingError("请选择有效的开通检查月份", 422)
    with service.db.transaction(actor=actor) as conn:
        company = service.require_admin_company(conn, actor, company_id)
        link = conn.execute(
            "SELECT status FROM firm_clients WHERE firm_id=? AND company_id=?",
            (actor.firm_id, company_id),
        ).fetchone()
        active = company["status"] == "active" and link["status"] == "active"
        if not active:
            return {
                "company_id": company_id,
                "period": period,
                "active": False,
                "ready": False,
                "steps": [],
                "updated_at": now(),
                "note": "企业已暂停或归档，恢复后重新检查开通条件。",
            }
        service.access.company(conn, actor, company_id, manage=True)
        if service.db.is_postgres:
            facts = conn.execute(
                "SELECT accounting.admin_company_onboarding(?,?) AS facts", (company_id, period)
            ).fetchone()["facts"]
        else:
            facts = dict(conn.execute(FACTS_SQL, (company_id, actor.firm_id, period)).fetchone())
        direct = facts["mode"] == "direct"
        steps = [
            {
                "id": "company",
                "title": "核对企业资料",
                "complete": bool(company["name"].strip()),
                "required": True,
                "tab": "company",
                "detail": "核对名称与税号；税号缺失不会自动阻断人工试点。",
            },
            {
                "id": "policy",
                "title": "选择资料提交方式",
                "complete": facts["policy_version"] > 0,
                "required": True,
                "tab": "departments",
                "detail": "老板／出纳直接提交" if direct else "员工提交、部门审批、企业财务核验",
            },
            {
                "id": "members",
                "title": "开通资料提交人员",
                "complete": facts["submitters"] > 0,
                "required": True,
                "tab": "members",
                "detail": f"{facts['active_members']} 位有效成员，{facts['submitters']} 位可按当前模式配置提交的人员。",
            },
            {
                "id": "approval",
                "title": "核对接收与审批人员" if direct else "配置部门与独立审批人员",
                "complete": facts["submitters"] > 0 and facts["blocked_submitters"] == 0,
                "required": True,
                "tab": "books" if direct else "departments",
                "detail": f"{facts['blocked_submitters']} 位提交人员尚无完整接收路径。申请人不能审核本人事项。",
            },
            {
                "id": "books",
                "title": "建立本月账套并分配会计权限",
                "complete": facts["books"] > 0 and facts["staffed_books"] == facts["books"],
                "required": True,
                "tab": "books",
                "detail": f"本月 {facts['books']} 个账套，{facts['staffed_books']} 个满足当前复核政策的人员分工；{facts['single_operator_books']} 个显式单人办理（非独立复核）。独立复核须有不同经办人与复核人。",
            },
            {
                "id": "trial",
                "title": "验证一笔提交至审核后导出",
                "complete": facts["verified_flow"] > 0,
                "required": True,
                "tab": "work",
                "detail": "依据本月已移交且经审核导出的实际记录检查；不自动创建业务或导出。",
            },
            {
                "id": "ai",
                "title": "检查企业 AI 处理许可",
                "complete": bool(facts["ai_enabled"]),
                "required": False,
                "tab": "ai",
                "detail": "可选。开启许可仍受模型配置、费用限制和会计复核约束。",
            },
        ]
        return {
            "company_id": company_id,
            "period": period,
            "active": True,
            "ready": all(s["complete"] for s in steps if s["required"]),
            "steps": steps,
            "updated_at": now(),
            "note": "按当前有效账号、权限和记录计算；开通检查不能证明本月资料齐全或外部软件已入账。",
        }
