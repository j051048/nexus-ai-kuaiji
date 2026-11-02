"""Accounting setup wizard: one resumable checklist over facts that already exist.

Every step is derived from records the system already holds. Nothing in this
module writes, grants or approves anything, and a step that reports "complete"
is not evidence that a human checked the underlying documents.
"""

import re

from ..accounting.domain import AccountingError
from ..accounting.service import now

PERIOD_PATTERN = r"20\d{2}-(?:0[1-9]|1[0-2])"


def _ledger_id(conn, book_id):
    row = conn.execute(
        "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book_id,)
    ).fetchone()
    return row["ledger_id"] if row else None


def _step(step_id, title, complete, detail, owner_role, next_action, kind, book_id, period):
    return {
        "id": step_id,
        "title": title,
        "complete": bool(complete),
        "required": True,
        "detail": detail,
        "owner_role": owner_role,
        "next_action": next_action,
        "action_target": {"kind": kind, "book_id": book_id, "period": period},
    }


def setup_checklist(service, actor, company_id, book_id, period):
    if not isinstance(period, str) or not re.fullmatch(PERIOD_PATTERN, period):
        raise AccountingError("请选择有效的开通月份", 422)
    # Reuses the company-level checklist for the company status guard and for
    # the "first verified flow" step, so the two checklists cannot disagree.
    company = service.admin.onboarding(actor, company_id, period)
    if not company["active"]:
        return {
            "company_id": company_id,
            "book_id": book_id,
            "period": period,
            "ready": False,
            "steps": [],
            "updated_at": now(),
            "note": company["note"],
        }
    with service.db.transaction(actor=actor) as conn:
        service.admin.access.company(conn, actor, company_id, manage=True)
        book = conn.execute(
            "SELECT b.id,b.company_id FROM books b WHERE b.id=? AND b.firm_id=? AND b.company_id=?",
            (book_id, actor.firm_id, company_id),
        ).fetchone()
        if book is None:
            raise AccountingError("账套不存在或无权访问", 404)
        ledger_id = _ledger_id(conn, book_id)
        policy = None
        accounts = 0
        settings = None
        banks = []
        bound_versions = 0
        mode = None
        distinct_pair = 0
        if ledger_id:
            policy = conn.execute(
                "SELECT version,standard_kind,taxpayer_kind FROM accounting_policies "
                "WHERE ledger_id=? AND status='active' AND effective_from<=? "
                "AND (effective_to IS NULL OR effective_to>=?) ORDER BY version DESC LIMIT 1",
                (ledger_id, period, period),
            ).fetchone()
            accounts = conn.execute(
                "SELECT COUNT(1) FROM ledger_accounts WHERE ledger_id=? AND status='active'",
                (ledger_id,),
            ).fetchone()[0]
            from ..subledger.service import SubledgerService

            settings = SubledgerService(service.db)._control_state(conn, ledger_id, period)
            bound_versions = conn.execute(
                "SELECT COUNT(1) FROM opening_package_versions v "
                "JOIN opening_packages p ON p.id=v.package_id "
                "WHERE p.ledger_id=? AND v.status IN('approved','frozen')",
                (ledger_id,),
            ).fetchone()[0]
        banks = conn.execute(
            "SELECT a.id,a.name,(SELECT COUNT(1) FROM bank_statement_coverages c "
            "WHERE c.account_id=a.id AND c.period=? AND trim(c.verified_by)<>'') AS covered "
            "FROM bank_accounts a WHERE a.book_id=? AND a.active=1 ORDER BY a.name",
            (period, book_id),
        ).fetchall()
        binding = conn.execute(
            "SELECT COUNT(1) FROM opening_bindings WHERE book_id=? AND period=? AND status='active'",
            (book_id, period),
        ).fetchone()[0]
        policy_row = conn.execute(
            "SELECT mode FROM rr_book_policies WHERE book_id=? ORDER BY version DESC LIMIT 1",
            (book_id,),
        ).fetchone()
        mode = policy_row["mode"] if policy_row else None
        distinct_pair = conn.execute(
            "SELECT COUNT(1) FROM service_book_user_permissions e "
            "JOIN service_book_user_permissions r ON r.book_id=e.book_id AND r.user_id<>e.user_id "
            "WHERE e.book_id=? AND e.can_edit=1 AND r.can_review=1",
            (book_id,),
        ).fetchone()[0]
        covered = [row for row in banks if row["covered"]]
        trial = next(step for step in company["steps"] if step["id"] == "trial")
        steps = [
            _step(
                "template",
                "核算模板与适用范围",
                policy is not None,
                "已启用准则与纳税人身份，政策覆盖本月。"
                if policy
                else "尚未启用覆盖本月的会计政策。",
                "账套会计",
                "在期初与政策中确认适用准则、纳税人身份与生效期间",
                "opening",
                book_id,
                period,
            ),
            _step(
                "accounts",
                "科目与辅助项",
                accounts > 0,
                f"有效科目 {accounts} 个。" if accounts else "本月还没有可用科目表。",
                "账套会计",
                "按模板生成科目表，并核对需要辅助项的科目",
                "opening",
                book_id,
                period,
            ),
            _step(
                "control",
                "控制科目与辅助项",
                settings is not None and settings["status"] in ("configured", "not_applicable"),
                "本月已确认不适用。"
                if settings is not None and settings["status"] == "not_applicable"
                else "控制科目已配置。"
                if settings is not None and settings["status"] == "configured"
                else "尚未确认控制科目配置或本月不适用。",
                "账套复核",
                "配置应收应付等控制科目与必需辅助项，或明确登记本月不适用",
                "subledger",
                book_id,
                period,
            ),
            _step(
                "bank",
                "银行账户与覆盖证据",
                bool(banks) and len(covered) == len(banks),
                f"{len(banks)} 个银行账户，{len(covered)} 个已登记本月覆盖证据。"
                if banks
                else "本月还没有银行账户映射；如确无银行账户，请在月结面板完成银行对账检查。",
                "账套会计",
                "建立银行账户映射，并导入对账单、登记覆盖来源与 SHA-256",
                "bank",
                book_id,
                period,
            ),
            _step(
                "opening",
                "期初校验与冻结",
                bound_versions > 0 and binding > 0,
                "期初包已冻结并绑定本月。"
                if bound_versions > 0 and binding > 0
                else "期初包未冻结或未绑定到本账套本月。",
                "账套复核",
                "校验期初平衡与流水后冻结期初包，再绑定到本账套本月",
                "opening",
                book_id,
                period,
            ),
            _step(
                "staffing",
                "岗位分工",
                mode == "single_operator" or distinct_pair > 0,
                "已登记单人办理（非独立复核）。"
                if mode == "single_operator"
                else "存在不同经办人与复核人。"
                if distinct_pair
                else "账套缺少独立复核人，或经办与复核为同一人。",
                "事务所管理员",
                "为账套分配经办与复核岗位，独立复核须为不同人员",
                "staffing",
                book_id,
                period,
            ),
            _step(
                "first",
                "首笔业务验证",
                bool(trial["complete"]),
                str(trial["detail"]),
                "账套会计",
                "完成一笔提交、审批与导出的真实流程并保留记录",
                "trial",
                book_id,
                period,
            ),
        ]
        return {
            "company_id": company_id,
            "book_id": book_id,
            "period": period,
            "ready": all(step["complete"] for step in steps if step["required"]),
            "steps": steps,
            "updated_at": now(),
            "note": "按当前有效账号、权限与已登记记录计算；开通向导不能证明本月资料齐全或外部软件已入账。",
        }
