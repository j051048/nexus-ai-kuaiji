"""PLT08-A：固定六段回答结构，全部由服务端组装与校验。

模型只负责在服务端给定的事实之间组织语言。结构中的对象标识、版本号、
整数分金额、处理人与直达链接一律由服务端填入；模型散文里的归因结论
（例如「这是银行手续费」）只要缺少本期已核验凭据，就整段改写为
「存在差异，原因未确认」；伪造的对象标识与对不上的金额直接丢弃该段散文。
"""

from __future__ import annotations

import hashlib
import json
import re

SECTIONS = (
    ("conclusion", "结论"),
    ("evidence", "依据"),
    ("amounts", "差额或数字"),
    ("unchecked", "未检查的范围"),
    ("owner", "由谁处理"),
    ("entry", "办理入口"),
)
SECTION_LABELS = dict(SECTIONS)
NONE = "无"
UNCHECKED = "未检查"
UNCONFIRMED = "存在差异，原因未确认"
ABSTAIN = "当前授权资料不足，未能形成结论"

# 归因措辞：没有已核验凭据时不得出现，避免把「差额恰好符合费率」当成原因。
ATTRIBUTION_TERMS = (
    "手续费",
    "服务费",
    "银行扣费",
    "扣费",
    "汇兑损失",
    "汇率损失",
    "坏账",
    "舞弊",
    "挪用",
)
# 只有这些原因码在服务端已核验后，对应归因才允许保留。
ATTRIBUTION_SUPPORTED_BY = {
    "fee_evidence": {"手续费", "服务费", "银行扣费", "扣费"},
}

MONEY_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+\.\d{2}")
IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+")
# 「某笔差额就是手续费」这类断定才算归因；「缺手续费凭据」是缺件说明，不算。
ATTRIBUTION_CLAIM_RE = re.compile(
    r"(?<!缺)(?<!少)(?:这是|属于|系|由于|因为|原因是|归因于|解释为|即)"
    r"(?:由|平台)?(?:银行|支付机构)?(?:收取的|扣除的)?"
    r"(手续费|服务费|银行扣费|扣费|汇兑损失|汇率损失|坏账|舞弊|挪用)"
)
ATTRIBUTION_CAUSE_RE = re.compile(
    r"(手续费|服务费|银行扣费|扣费|汇兑损失|汇率损失|坏账|舞弊|挪用)"
    r"(?:导致|造成|冲抵|抵消|解释了|覆盖了)"
)

CODES = {
    "attribution": "plt08_attribution_rewritten",
    "amount": "plt08_unknown_amount",
    "reference": "plt08_unknown_reference",
    "empty": "plt08_narrative_empty",
    "abstained": "plt08_abstained",
}


def _fingerprint(payload):
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def money_text(cents):
    """整数分 → 「100,000.00」；只用整除与字符串，不经过浮点。"""
    if type(cents) is not int:
        raise TypeError("金额必须是整数分")
    sign = "-" if cents < 0 else ""
    whole, rest = divmod(abs(cents), 100)
    grouped = f"{whole:,}"
    return f"{sign}{grouped}.{rest:02d}"


def normalized_amounts(result):
    """服务端允许在回答里出现的金额集合（去掉千分位后的规范字符串）。"""
    values = set()
    for item in result.get("amounts") or []:
        for token in MONEY_RE.findall(str(item)):
            values.add(token.replace(",", ""))
    for value in (result.get("facts") or {}).get("allowed_amounts") or []:
        values.add(str(value).replace(",", ""))
    return values


def allowed_identifiers(result):
    values = set()
    for row in result.get("evidence") or []:
        identifier = row.get("object_id")
        if isinstance(identifier, str):
            values.add(identifier)
    facts = result.get("facts") or {}
    for identifier in facts.get("allowed_ids") or []:
        values.add(str(identifier))
    return values


def guard_narrative(narrative, result):
    """把模型散文限制在当前事实内；返回 (clean_text, codes)。"""
    if not isinstance(narrative, str) or not narrative.strip():
        return "", [CODES["empty"]]
    text = narrative.strip()
    reason = (result.get("facts") or {}).get("reason") or result.get("reason")
    supported = ATTRIBUTION_SUPPORTED_BY.get(reason, set())
    codes = []
    for term in ATTRIBUTION_TERMS:
        if term in text and term not in supported:
            # 整段改写，而不是删掉一个词：残留的因果措辞会继续误导读者。
            return UNCONFIRMED, [CODES["attribution"]]
    allowed = normalized_amounts(result)
    for token in MONEY_RE.findall(text):
        if token.replace(",", "") not in allowed:
            codes.append(CODES["amount"])
            break
    identifiers = allowed_identifiers(result)
    for token in IDENTIFIER_RE.findall(text):
        if token not in identifiers:
            codes.append(CODES["reference"])
            break
    if codes:
        return "", codes
    return text, []


def _lines(value):
    if isinstance(value, str):
        return [value] if value else []
    return [str(row) for row in value or [] if str(row)]


def build_answer(result, *, narrative=None):
    """把服务端调查结果组装成六段回答；缺内容显式写「无」或「未检查」。"""
    codes = []
    facts = result.get("facts") or {}
    reason = facts.get("reason") or result.get("reason")
    state = result.get("conclusion_state")
    if state is None:
        state = "unconfirmed" if result.get("conflicting") else "confirmed"

    conclusion = _lines(result.get("conclusion"))
    narrative_line = ""
    if narrative is not None:
        clean, narrative_codes = guard_narrative(narrative, result)
        codes.extend(narrative_codes)
        if state == "abstained":
            # 没有任何依据时，模型多写一句都不能把弃答升级成结论。
            clean = ""
        if clean == UNCONFIRMED:
            state = "unconfirmed"
        elif clean:
            narrative_line = clean
    if state == "unconfirmed":
        # 服务端结论永远是结论段第一行，模型散文只能作为被校验过的补充句。
        conclusion = [UNCONFIRMED]
        if narrative_line:
            conclusion.append(narrative_line)
    elif narrative_line:
        conclusion = [narrative_line, *conclusion]
    if not conclusion:
        conclusion = [NONE]

    evidence_rows = []
    for row in result.get("evidence") or []:
        identity = row.get("object_id")
        version = row.get("version")
        label = row.get("label") or row.get("object_type") or "来源"
        line = f"{label}（{row.get('object_type') or '对象'} {identity}"
        if version:
            line += f"，版本 {str(version)[:12]}"
        line += "）"
        if row.get("href"):
            line += f"：{row['href']}"
        evidence_rows.append(line)

    amounts = _lines(result.get("amounts"))
    unchecked = []
    for row in result.get("unchecked") or []:
        label = row.get("label") or "来源"
        detail = row.get("detail") or "未加载"
        unchecked.append(f"{label}：{detail}")
    owner = result.get("owner") or {}
    entry = result.get("entry") or {}
    owner_lines = []
    for item in owner.get("items") or []:
        owner_lines.append(f"{item.get('role') or '未指定'}：{item.get('reason') or '待确认'}")
    if not owner_lines and (owner.get("role") or owner.get("reason")):
        owner_lines.append(f"{owner.get('role') or '未指定'}：{owner.get('reason') or '待确认'}")
    entry_lines = []
    if entry.get("href"):
        entry_lines.append(f"{entry.get('label') or '办理入口'}：{entry['href']}")

    sections = [
        {"key": "conclusion", "label": SECTION_LABELS["conclusion"], "lines": conclusion},
        {
            "key": "evidence",
            "label": SECTION_LABELS["evidence"],
            "lines": evidence_rows or [NONE],
        },
        {"key": "amounts", "label": SECTION_LABELS["amounts"], "lines": amounts or [NONE]},
        {
            "key": "unchecked",
            "label": SECTION_LABELS["unchecked"],
            "lines": unchecked or [NONE],
        },
        {
            "key": "owner",
            "label": SECTION_LABELS["owner"],
            "lines": owner_lines or [NONE],
        },
        {"key": "entry", "label": SECTION_LABELS["entry"], "lines": entry_lines or [NONE]},
    ]
    dependencies = [
        {
            "object_type": row.get("object_type") or "object",
            "object_id": row["object_id"],
            "version": row.get("version"),
        }
        for row in result.get("evidence") or []
        if row.get("object_id")
    ]
    answer = {
        "schema": "plt08-answer-v1",
        "kind": result.get("kind"),
        "book_id": result.get("book_id"),
        "company_id": result.get("company_id"),
        "period": result.get("period"),
        "conclusion_state": "abstained" if state == "abstained" else state,
        "sections": sections,
        "text": render_text(sections),
        "codes": sorted(set(codes)),
        "dependencies": dependencies,
        "model_calls": 0,
        "auto_retry": False,
        "cost_state": "not_incurred",
        "scope": "服务端组装；仅覆盖本账套本期间当前授权范围",
    }
    answer["answer_fingerprint"] = _fingerprint(
        {
            "sections": sections,
            "dependencies": dependencies,
            "state": answer["conclusion_state"],
        }
    )
    return answer


def render_text(sections):
    blocks = []
    for section in sections:
        blocks.append(f"【{section['label']}】")
        for line in section["lines"]:
            blocks.append(f"- {line}")
    return "\n".join(blocks)


def abstain(missing_labels, *, kind=None, book_id=None, company_id=None, period=None):
    """依据不足时明确弃答并说明缺什么，不给猜测。"""
    missing = [str(label) for label in missing_labels if str(label)]
    detail = "、".join(missing) if missing else "缺少可核验来源"
    result = {
        "kind": kind,
        "book_id": book_id,
        "company_id": company_id,
        "period": period,
        "conclusion_state": "abstained",
        "conclusion": [f"{ABSTAIN}：缺少{detail}。"],
        "evidence": [],
        "amounts": [],
        "unchecked": [{"label": item, "detail": "缺少可核验依据"} for item in missing],
        "owner": {"role": "accountant", "reason": f"需补齐{detail}后重新调查"},
        "entry": {},
        "facts": {},
    }
    answer = build_answer(result)
    answer["codes"] = sorted({*answer["codes"], CODES["abstained"]})
    return answer


def section_text(answer, key):
    for section in answer.get("sections") or []:
        if section.get("key") == key:
            return "\n".join(section.get("lines") or [])
    return ""


def has_attribution_claim(answer):
    """结论段是否残留归因断定（红绿测试用）。

    「缺手续费凭据」「请取扣款明细」只是缺件与待办说明，不算归因；
    只有「这是/属于/原因是手续费」这类断定与「手续费导致差额」才算。
    """
    conclusion = section_text(answer, "conclusion")
    if ATTRIBUTION_CLAIM_RE.search(conclusion):
        return True
    return bool(ATTRIBUTION_CAUSE_RE.search(conclusion))
