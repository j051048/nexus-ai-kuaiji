"""Optional synthetic enterprise accounting-standard chart.

This seed is for offline acceptance only. It is not a customer-confirmed
chart and must not be used to infer a customer's accounting standard.
"""

from copy import deepcopy

ENTERPRISE_CHART = [
    {"code": "1001", "name": "库存现金", "category": "asset", "normal_balance": "debit"},
    {"code": "1002", "name": "银行存款", "category": "asset", "normal_balance": "debit"},
    {"code": "1122", "name": "应收账款", "category": "asset", "normal_balance": "debit"},
    {"code": "1221", "name": "其他应收款", "category": "asset", "normal_balance": "debit"},
    {"code": "1405", "name": "库存商品", "category": "asset", "normal_balance": "debit"},
    {"code": "1601", "name": "固定资产", "category": "asset", "normal_balance": "debit"},
    {"code": "1602", "name": "累计折旧", "category": "asset", "normal_balance": "credit"},
    {"code": "2202", "name": "应付账款", "category": "liability", "normal_balance": "credit"},
    {"code": "2211", "name": "应付职工薪酬", "category": "liability", "normal_balance": "credit"},
    {"code": "2221", "name": "应交税费", "category": "liability", "normal_balance": "credit"},
    {"code": "2241", "name": "其他应付款", "category": "liability", "normal_balance": "credit"},
    {"code": "4001", "name": "实收资本", "category": "equity", "normal_balance": "credit"},
    {"code": "4103", "name": "本年利润", "category": "equity", "normal_balance": "credit"},
    {"code": "4104", "name": "利润分配", "category": "equity", "normal_balance": "credit"},
    {"code": "6001", "name": "主营业务收入", "category": "income", "normal_balance": "credit"},
    {"code": "6401", "name": "主营业务成本", "category": "expense", "normal_balance": "debit"},
    {"code": "6601", "name": "销售费用", "category": "expense", "normal_balance": "debit"},
    {"code": "6602", "name": "管理费用", "category": "expense", "normal_balance": "debit"},
    {"code": "6603", "name": "财务费用", "category": "expense", "normal_balance": "debit"},
]


def enterprise_chart():
    """Return a fresh copy of the synthetic enterprise chart."""
    return deepcopy(ENTERPRISE_CHART)
