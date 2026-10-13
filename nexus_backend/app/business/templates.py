"""三个可版本化的 MVP 模板；变更模板须新增版本，不覆盖历史快照。"""

from app.accounting.domain import AccountingError


TEMPLATES = {
    "reimbursement": {
        "id": "reimbursement",
        "name": "员工报销",
        "version": 1,
        "description": "员工垫付费用后申请企业报销。企业付款状态为待支付。",
        "direction": "expense",
        "payment_status": "unpaid",
        "currency": "CNY",
    },
    "procurement_expense": {
        "id": "procurement_expense",
        "name": "采购 / 费用",
        "version": 1,
        "description": "企业采购与费用支出，填写已付款或待付款。",
        "direction": "expense",
        "currency": "CNY",
    },
    "daily_cashflow": {
        "id": "daily_cashflow",
        "name": "日常收支",
        "version": 1,
        "description": "已发生的企业收款或付款，选择收入或支出。",
        "payment_status": "paid",
        "currency": "CNY",
    },
}


def template_for(template_id, version=1):
    template = TEMPLATES.get(template_id)
    if template is None or type(version) is not int or version != template["version"]:
        raise AccountingError("业务模板或版本不存在")
    return dict(template)


def validate_payload(template_id, version, payload):
    template = template_for(template_id, version)
    for key in ("direction", "payment_status"):
        if key in template and payload[key] != template[key]:
            raise AccountingError(
                f"{template['name']}的{('收支方向' if key == 'direction' else '付款状态')}不符合模板"
            )
    return template
