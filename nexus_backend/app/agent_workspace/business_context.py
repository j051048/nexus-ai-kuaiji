"""Read-only current-item guidance using existing visibility and reconciliation."""

from app.accounting.domain import AccountingError
from .business_evidence import projection_findings, relationships


def project(service, actor, event_id, *, company_id, period, expected_version):
    business = service.business
    with service.db.transaction(actor=actor) as conn:
        event = business._event(conn, actor, event_id)
        business._version(event, expected_version)
        value = business._event_json(conn, actor, event)
        occurred_on = value["payload"].get("occurred_on")
        if (
            event["company_id"] != company_id
            or not isinstance(occurred_on, str)
            or occurred_on[:7] != period
        ):
            raise AccountingError("业务事项不存在或无权访问", 404)
        originals = [row for row in value["attachments"] if not row.get("superseded")]
        intelligence = getattr(business, "intelligence", None)
        assessment = intelligence._assessment(conn, actor, event) if intelligence else None
        differences = [
            {"code": row["code"], "severity": row["severity"], "message": row["payload"]["message"]}
            for row in projection_findings(assessment)
            if not row.get("resolved")
        ]
        # Recheck after gathering evidence. A changed version or revoked grant
        # must not turn a mixed read into a current actionable card.
        current = business._event(conn, actor, event_id)
        if any(current[key] != event[key] for key in ("version", "fact_version", "status")):
            raise AccountingError("事项依据已更新，请刷新事项后重新读取", 409)
        permissions = business._permissions(conn, actor, current)
        direct = event["submission_mode"] == "direct"
        owner = {
            "draft": "申请人",
            "returned": "申请人",
            "submitted": "接收会计" if direct else "部门审批人",
            "department_approved": "企业财务",
            "finance_approved": "企业财务",
            "handed_off": "会计",
        }[event["status"]]
        actionable = any(
            permissions[key]
            for key in ("edit", "submit", "department_approve", "finance_approve", "handoff")
        )
        if permissions["handoff"]:
            label, detail = "办理交接", "确认接收账套与月份后交给会计；交接不代表会计复核完成。"
        elif permissions["edit"] or permissions["submit"]:
            label, detail = "补齐并提交", "核对原件与申请内容，补齐后在现有表单提交。"
        else:
            label, detail = "核对后办理", "先核对原件与差异，再在办理区确认或填写退回原因。"
        if not actionable:
            label, detail = "等待处理", f"由{owner}继续处理；当前账号可查看依据，不能代为确认。"
        coverage = (
            "依据当前原件、申请内容及已保存的核对结果；未经人工核验，不代表已通过。"
            if originals
            else "尚无当前有效原件，请补充原件；未经人工核验，不代表已通过。"
        )
        if not assessment:
            coverage += "自动交叉核对结果暂不可用，请人工核对。"
        result = {
            "mode": "evidence_summary",
            "scope": {
                "firm_id": actor.firm_id,
                "company_id": company_id,
                "period": period,
                "event_id": event_id,
                "event_version": event["version"],
                "fact_version": event["fact_version"],
            },
            "fingerprint": assessment["fingerprint"] if assessment else None,
            "coverage": coverage,
            "evidence": [
                {"attachment_id": row["id"], "filename": row["filename"], "sha256": row["sha256"]}
                for row in originals
            ],
            "differences": differences,
            "relationships": relationships(value, assessment, owner, actionable),
            "next_step": {
                "owner": owner,
                "label": label,
                "detail": detail,
                "target": "actions" if actionable else None,
            },
        }
    # Database read transactions use a repeatable snapshot. Revalidating in
    # that same snapshot cannot observe a concurrent edit or revocation.
    with service.db.transaction(actor=actor) as conn:
        latest = business._event(conn, actor, event_id)
        if (
            any(latest[key] != event[key] for key in ("version", "fact_version", "status"))
            or business._permissions(conn, actor, latest) != permissions
        ):
            raise AccountingError("事项依据或权限已更新，请刷新事项后重新读取", 409)
        fresh_value = business._event_json(conn, actor, latest)
        fresh_assessment = intelligence._assessment(conn, actor, latest) if intelligence else None
        if (fresh_assessment["fingerprint"] if fresh_assessment else None) != result[
            "fingerprint"
        ] or relationships(fresh_value, fresh_assessment, owner, actionable)[
            "fingerprint"
        ] != result["relationships"]["fingerprint"]:
            raise AccountingError("核对依据已变化，请重新读取后再办理", 409)
    return result
