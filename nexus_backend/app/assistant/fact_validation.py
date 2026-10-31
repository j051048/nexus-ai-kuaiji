"""Validate assistant source claims and render only server-owned conclusions.

The model's prose and task reasons are untrusted. This is a publication boundary,
not an authorization mechanism; the worker still checks its current snapshot and
policy in the atomic completion transaction.
"""

from datetime import date
from decimal import Decimal

from app.intelligence.context_pack import ContextError, load_context_pack
from app.intelligence.fact_validation import AnswerError, Claim, EvidenceFact, validate_bound_claims
from .models import AssistantModelReply, AssistantReply


FALLBACK = "当前回复缺少可核验的事实依据，请人工核对当前资料；仍须会计复核后导出。"
ABSTENTIONS = {
    "missing_facts": "当前资料不足，请人工补充并核对；仍须会计复核后导出。",
    "conflicting_evidence": "当前证据存在冲突，请人工核对原件与业务事项；仍须会计复核后导出。",
    "unsupported_case": "当前事项超出已支持的核验范围，请人工处理；仍须会计复核后导出。",
    "requires_accountant": "当前事项需要人工会计判断；仍须会计复核后导出。",
}
MONEY_FIELDS = {
    "amount": "业务事项金额",
    "source_gross_total": "资料来源绝对金额合计（含隔离及替代记录）",
    "source_amount": "凭证来源金额",
    "gross_amount": "票据价税合计",
    "net_amount": "票据不含税金额",
    "tax_amount": "票据税额",
    "debit": "凭证借方金额",
    "credit": "凭证贷方金额",
}


def _descriptor(fact, path):
    """Only typed financial leaves have publication labels; arbitrary prose has none."""
    key, pieces = fact["key"], path.split("/")[1:]
    if not path.startswith("/") or any("~" in piece or not piece for piece in pieces):
        raise ValueError("unsupported leaf path")
    field = pieces[-1]
    allowed = (
        (key.startswith("event:") and pieces == ["amount"])
        or (key.startswith("book:") and pieces == ["source_gross_total"])
        or (key.startswith("draft:") and pieces == ["source_amount"])
        or (
            key.startswith("draft:")
            and len(pieces) == 3
            and pieces[0] == "lines"
            and pieces[1].isdigit()
            and field in {"debit", "credit"}
        )
        or (
            key.startswith("ocr:")
            and len(pieces) == 1
            and field in {"gross_amount", "net_amount", "tax_amount"}
        )
    )
    if allowed:
        return field, MONEY_FIELDS[field], "decimal", "元", "CNY"
    if key.startswith("book:") and pieces == ["source_count"]:
        return field, "资料来源数量", "count", "条", ""
    if key.startswith("event:") and pieces == ["occurred_on"]:
        return field, "业务发生日期", "date", "", ""
    if key.startswith("ocr:") and pieces == ["issued_on"]:
        return field, "票据开具日期", "date", "", ""
    raise ValueError("unsupported fact field")


def _leaf(value, path):
    for piece in path.split("/")[1:]:
        if isinstance(value, dict):
            value = value[piece]
        elif isinstance(value, list) and piece.isdigit() and str(int(piece)) == piece:
            value = value[int(piece)]
        else:
            raise ValueError("not a scalar leaf")
    if value is None or type(value) not in {str, int}:
        raise ValueError("unknown is not a known scalar")
    return value


def render_assistant_reply(reply, context_pack, *, with_validation=False):
    """Render trusted conclusions; optionally retain the server's validation code.

    The default remains the legacy three-field reply. The worker persists only
    the validation enum in the turn's existing completion metadata.
    """

    def result(rendered, code):
        # The model cannot select, replace or suppress domain blockers. Only a
        # fresh, scope-bound task projection is rendered; prose stays untrusted.
        from .delivery_context import render_delivery
        from .request_budget import omission_notice

        notice = ""
        try:
            pack = load_context_pack(
                context_pack.as_dict() if hasattr(context_pack, "as_dict") else context_pack
            )
            summary, citations = render_delivery(pack)
            notice = omission_notice(pack)
        except (ContextError, KeyError, ValueError, TypeError, AttributeError):
            summary, citations = "", []
        if summary:
            answer = summary + "\n" + rendered.answer
            refs = list(dict.fromkeys([*citations, *rendered.citation_ids]))
            if len(answer) > 12000 or len(refs) > 100:
                answer = summary + "\n其他事实未展示，请缩小范围核对；仍须会计复核后导出。"
                refs = citations
            rendered = AssistantReply(
                answer=answer,
                citation_ids=refs,
                proposals=[],
            )
        if notice:
            answer = notice + "\n" + rendered.answer
            if len(answer) > 12000:
                # Never cut a financial fact halfway to make space for a notice.
                rendered = AssistantReply(
                    answer=notice
                    + "\n核对结果超过展示范围，请减少所选事项后重试；仍须会计复核后导出。",
                    citation_ids=[],
                    proposals=[],
                )
                code = "ak04_fact_missing"
            else:
                rendered = rendered.model_copy(update={"answer": answer})
        return (rendered, code) if with_validation else rendered

    reply = (
        reply
        if isinstance(reply, AssistantModelReply)
        else AssistantModelReply.model_validate(
            reply.model_dump() if hasattr(reply, "model_dump") else reply
        )
    )
    fallback = AssistantReply(answer=FALLBACK, citation_ids=list(reply.citation_ids), proposals=[])
    if reply.fact_status != "verified":
        fallback.answer = ABSTENTIONS[reply.fact_status]
        return result(
            fallback,
            {
                "missing_facts": "ak04_fact_missing",
                "conflicting_evidence": "ak04_fact_conflict",
                "unsupported_case": "ak04_fact_unsupported",
                "requires_accountant": "ak04_fact_accountant",
            }[reply.fact_status],
        )
    if not reply.claims:
        return result(fallback, "ak04_fact_invalid")
    try:
        pack = load_context_pack(
            context_pack.as_dict() if hasattr(context_pack, "as_dict") else context_pack
        )
        facts = {row["key"]: row for row in pack.facts}
        evidence = {row["id"]: row for row in pack.evidence}
        citations = next(row["value"] for row in pack.facts if row["key"] == "citation_ids")
        seen, rendered, used = set(), [], []
        for item in reply.claims:
            identity = (item.fact_key, item.path)
            if identity in seen:
                raise ValueError("duplicate claim")
            seen.add(identity)
            fact = facts[item.fact_key]
            source = evidence[fact["evidence_id"]]
            if (
                fact["source"] not in {"saved_fact", "source_unreviewed"}
                or item.source != fact["source"]
            ):
                raise ValueError("fact qualification mismatch")
            if source["version"] != fact["version"]:
                raise ValueError("source evidence version mismatch")
            field, label, value_type, unit, currency = _descriptor(fact, item.path)
            if item.field != field or item.evidence_id != fact["evidence_id"]:
                raise ValueError("field or source mismatch")
            value = _leaf(fact["value"], item.path)
            if value_type == "count" and (
                type(value) is not int or value < 0 or type(item.value) is not int
            ):
                raise ValueError("count type mismatch")
            if value_type == "date" and (
                type(value) is not str or date.fromisoformat(value).isoformat() != value
            ):
                raise ValueError("invalid date")
            if value_type == "decimal" and (
                type(value) is not str
                or type(item.value) is not str
                or not Decimal(value).is_finite()
            ):
                raise ValueError("money is not an exact decimal string")
            actual_book = (
                source.get("value", {}).get("book_id")
                if fact["key"].startswith(("book:", "draft:"))
                else None
            )
            if currency and fact["value"].get("currency", currency) != currency:
                raise ValueError("unsupported currency")
            bound_field = item.fact_key + item.path
            validate_bound_claims(
                [
                    Claim(
                        field=bound_field,
                        value=item.value,
                        kind="source_fact",
                        company_id=item.company_id,
                        book_id=item.book_id,
                        evidence_version=item.evidence_version,
                        unit=item.unit,
                        currency=item.currency,
                        evidence_ids=(item.evidence_id,),
                    )
                ],
                company_id=pack.scope["company_id"],
                book_id=actual_book,
                fact_version=None,
                evidence=[
                    EvidenceFact(
                        field=bound_field,
                        value=value,
                        company_id=pack.scope["company_id"],
                        book_id=actual_book,
                        version=fact["version"],
                        currency=currency,
                        evidence_id=fact["evidence_id"],
                        unit=unit,
                        value_type="decimal" if value_type in {"decimal", "count"} else "text",
                    )
                ],
                require_verified=True,
            )
            if item.evidence_version != fact["version"]:
                raise ValueError("stale fact version")
            qualification = (
                "待人工核验的 OCR"
                if fact["source"] == "source_unreviewed"
                else "当前已保存，尚不代表会计审核通过"
            )
            rendered.append(f"{label}：{value}{unit}（{qualification}）。")
            citation = source.get("source_id")
            if fact["source"] == "source_unreviewed":
                citation = "attachment:" + fact["value"]["attachment_id"]
            if citation not in citations:
                raise ValueError("fact has no visible citation")
            if citation not in used:
                used.append(citation)
        return result(
            AssistantReply(
                answer="\n".join(rendered) + "\n以上为资料核对结果，仍须会计复核后导出。",
                citation_ids=used,
                proposals=[],
            ),
            "ak04_fact_verified",
        )
    except (
        AnswerError,
        ContextError,
        KeyError,
        ValueError,
        TypeError,
        IndexError,
        ArithmeticError,
    ):
        return result(fallback, "ak04_fact_invalid")
