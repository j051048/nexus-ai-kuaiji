"""Deterministic amount provenance; account classification remains reviewed."""
from collections import Counter, defaultdict

from ..accounting.models import money_to_cents
from .checks import extraction_checks


def journal_fact_issues(source, context, suggestion):
    documents = context["documents"]
    counters = defaultdict(int)
    identities = []
    for document in documents:
        owner = (document["attachment_id"], document["extraction_id"])
        index = document.get("document_index", counters[owner])
        counters[owner] += 1
        identities.append((*owner, index))
    if len(set(identities)) != len(identities):
        return ["票据文档身份重复，须人工区分"]
    referenced = defaultdict(set)
    for ref in suggestion.evidence_refs:
        matches = []
        for index, (identity, document) in enumerate(zip(identities, documents)):
            if identity[:2] != (ref.attachment_id, ref.extraction_id):
                continue
            if ref.document_index is not None and ref.document_index != identity[2]:
                continue
            if document.get(ref.field) is None or not any(
                item["field"] == ref.field and item["page"] == ref.page
                for item in document["evidence"]
            ):
                continue
            matches.append(index)
        if len(matches) != 1:
            return ["分录引用必须绑定唯一当前文档、字段及页码"]
        index = matches[0]
        document = documents[index]
        # Raw model text is not an independent source. Its qualification is
        # enforced by persisted original_unverified findings at adoption. Here
        # also reject self-inconsistent/semantically relabelled quotations.
        failures = extraction_checks([document], [page["page"] for page in document["pages"]],
                                     {page["page"]: page["raw_text"] for page in document["pages"]})
        if any(row["code"] == "document_1_evidence_" + ref.field for row in failures):
            return ["分录引用字段与票据原文不一致"]
        referenced[ref.field].add(index)
    selected = {index for index, doc in enumerate(documents) if doc["document_type"] == "invoice"}
    if not selected:
        selected = {index for index, doc in enumerate(documents) if doc["document_type"] == "receipt"}
    if not selected or referenced["gross_amount"] != selected:
        return ["分录金额证据没有完整覆盖当前票据集合"]
    if sum(money_to_cents(documents[index]["gross_amount"]) for index in selected) != source["amount_cents"]:
        return ["引用票据合计与来源金额不一致"]
    amounts = [money_to_cents(value) for line in suggestion.lines for value in (line.debit, line.credit) if money_to_cents(value)]
    if all(amount == source["amount_cents"] for amount in amounts):
        return []
    # Splitting amounts needs a published accounting template. A model cannot
    # invent an allocation or swap net/tax merely by balancing the gross total.
    if any(referenced[field] != selected for field in ("net_amount", "tax_amount")):
        return ["拆分分录必须完整引用当前净额及税额证据"]
    verified = {"gross": source["amount_cents"]}
    for key, field in (("net", "net_amount"), ("tax", "tax_amount")):
        if any(documents[index].get(field) is None for index in selected):
            return ["未知税额不能作为零或拆分依据"]
        verified[key] = sum(money_to_cents(documents[index][field]) for index in selected)
    if verified["net"] + verified["tax"] != verified["gross"]:
        return ["净额与税额未勾稽"]
    actual = Counter((line.account_code, money_to_cents(line.debit), money_to_cents(line.credit)) for line in suggestion.lines)
    business = context["business"]
    for template in context["templates"]:
        definition = template["definition"]
        if (definition.get("business_type") != context.get("business_type")
            or definition.get("direction") != business["direction"]
            or any(definition.get(key) is not None and definition[key] != business.get(key)
                   for key in ("category", "payment_status"))):
            continue
        expected = []
        for row in definition["lines"]:
            amount = verified.get(row["amount_field"])
            if amount is None:
                break
            if amount:
                expected.append((row["account_code"], amount if row["side"] == "debit" else 0,
                                 amount if row["side"] == "credit" else 0))
        else:
            if actual == Counter(expected):
                return []
    return ["拆分金额或科目未绑定当前已发布模板，请会计人工处理"]
