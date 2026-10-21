"""Deterministic checks: OCR is a proposal, never the source of balances."""

import re
import unicodedata
from datetime import date

from ..accounting.models import money_to_cents

FIELDS = (
    "invoice_number",
    "issued_on",
    "seller_name",
    "seller_tax_id",
    "buyer_name",
    "buyer_tax_id",
    "gross_amount",
    "net_amount",
    "tax_amount",
    "currency",
)

# Match the longest labels first: 金额 must not consume 不含税金额.
MONEY_LABELS = {
    "gross_amount": ("gross_amount", "价税合计", "含税金额", "总金额", "合计金额", "实付金额", "total amount", "gross amount",
                     "/EInvoice/EInvoiceData/BasicInformation/TotalTax-includedAmount"),
    "net_amount": ("net_amount", "不含税金额", "未税金额", "金额", "net amount",
                   "/EInvoice/EInvoiceData/BasicInformation/TotalAmWithoutTax"),
    "tax_amount": ("tax_amount", "税额", "税金", "tax amount",
                   "/EInvoice/EInvoiceData/BasicInformation/TotalTaxAm"),
}
_MONEY_LABEL = re.compile("|".join(re.escape(label) for label in sorted(
    (label for labels in MONEY_LABELS.values() for label in labels), key=len, reverse=True,
)), re.IGNORECASE)


def field_money_in_quote(field, value, quote):
    """A number in another labelled column is not evidence for this column."""
    text = unicodedata.normalize("NFKC", quote)
    labels = list(_MONEY_LABEL.finditer(text))
    if not labels:
        # A bare number can occur in several columns or inside a larger value.
        # No label means no deterministic field qualification, even if the
        # model's own transcript and arithmetic happen to agree.
        return False
    for index, match in enumerate(labels):
        if match.group().casefold() not in {label.casefold() for label in MONEY_LABELS[field]}:
            continue
        end = labels[index + 1].start() if index + 1 < len(labels) else len(text)
        segment = text[match.end():end]
        # One label followed by several different numbers is ambiguous.
        amounts = re.findall(r"(?<![\w.])(?:0|[1-9]\d{0,11})(?:\.\d{1,2})?(?![\w.])", segment.replace(",", ""))
        if len(amounts) == 1 and money_in_quote(value, amounts[0]):
            return True
    return False


def identifier_in_quote(value, quote):
    target = normalized(value)
    text = unicodedata.normalize("NFKC", quote).casefold()
    return bool(target) and re.search(r"(?<![a-z0-9])" + re.escape(target) + r"(?![a-z0-9])", text) is not None


def quote_in_text(quote, text):
    span, original = normalized(quote), normalized(text)
    if not span:
        return False
    start = original.find(span)
    labels = list(_NORMALIZED_MONEY_LABEL.finditer(original))
    while start >= 0:
        end = start + len(span)
        if not (
            (span[0].isdigit() and start and original[start - 1] in "0123456789.")
            or (span[-1].isdigit() and end < len(original) and original[end] in "0123456789.")
            or any(label.start() < start < label.end() for label in labels)
        ):
            return True
        start = original.find(span, start + 1)
    return False


def normalized(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "")).casefold()


_NORMALIZED_MONEY_LABEL = re.compile("|".join(re.escape(normalized(label)) for label in sorted(
    (label for labels in MONEY_LABELS.values() for label in labels), key=len, reverse=True,
)))


def finding(code, message, severity="blocking", **details):
    return {"code": code, "severity": severity, "payload": {"message": message, **details}}


def money_in_quote(value, quote):
    text = unicodedata.normalize("NFKC", quote).replace(",", "")
    amounts = re.findall(r"(?<![\d.])(?:0|[1-9]\d{0,11})(?:\.\d{1,2})?(?![\d.])", text)
    return any(money_to_cents(amount) == money_to_cents(value) for amount in amounts)


def extraction_checks(documents, page_numbers, native_texts=None):
    findings = []
    native_texts = native_texts or {}
    for index, document in enumerate(documents):
        prefix = f"document_{index + 1}"
        pages = {page["page"]: page["raw_text"] for page in document["pages"]}
        if len(pages) != len(document["pages"]) or not set(pages).issubset(set(page_numbers)):
            findings.append(finding(prefix + "_pages", "识别页面与原件页数不一致，需要核对原件"))
        if document["document_type"] in {"other", "unreadable"}:
            findings.append(
                finding(prefix + "_unreadable", "附件未被可靠识别为发票或收付款票据，请人工核对")
            )
        evidence = document["evidence"]
        if any(not native_texts.get(number, "").strip() for number in pages):
            findings.append(finding(
                prefix + "_original_unverified",
                "原件尚无独立文字证据，模型自报原文不能证明字段正确；请独立核对原件后确认",
                document_index=index,
            ))
        for field in FIELDS:
            value = document.get(field)
            if value is None:
                continue
            valid = False
            for reference in evidence:
                if reference["field"] != field or reference["page"] not in pages:
                    continue
                quote = reference["quote"]
                if not quote_in_text(quote, pages[reference["page"]]):
                    continue
                independent = native_texts.get(reference["page"], "")
                if independent.strip() and not quote_in_text(quote, independent):
                    continue
                if field in {"gross_amount", "net_amount", "tax_amount"}:
                    anchored = field_money_in_quote(field, value, quote)
                elif field == "currency":
                    anchored = (
                        normalized(value) in normalized(quote)
                        or normalized(value) == "cny"
                        and any(mark in quote for mark in ("人民币", "￥", "¥", "RMB", "CNY"))
                    )
                elif field == "issued_on":
                    anchored = normalized(value).replace("-", "") in re.sub(r"[^0-9]", "", quote)
                elif field in {"invoice_number", "seller_tax_id", "buyer_tax_id"}:
                    anchored = identifier_in_quote(value, quote)
                else:
                    anchored = bool(normalized(value)) and normalized(value) in normalized(quote)
                if anchored:
                    valid = True
                    break
            if not valid:
                findings.append(
                    finding(
                        prefix + "_evidence_" + field,
                        f"字段 {field} 缺少可复核的页码及原文证据",
                        field=field,
                        document_index=index,
                    )
                )
        if document["gross_amount"] is None:
            findings.append(finding(prefix + "_gross_missing", "未识别到票据含税/总金额"))
        if normalized(document["currency"]) != "cny":
            findings.append(
                finding(prefix + "_currency", "票据币种缺失或不是人民币，系统不会自动折算")
            )
        if document["document_type"] == "invoice":
            if document["net_amount"] is None or document["tax_amount"] is None:
                findings.append(
                    finding(prefix + "_tax_missing", "发票未税金额或税额缺失，不能按零税额处理")
                )
            elif document["gross_amount"] is not None and money_to_cents(
                document["net_amount"]
            ) + money_to_cents(document["tax_amount"]) != money_to_cents(document["gross_amount"]):
                findings.append(finding(prefix + "_tax_sum", "未税金额与税额之和不等于价税合计"))
            if not document["invoice_number"]:
                findings.append(
                    finding(prefix + "_invoice_number", "发票号码缺失，请人工核对", "warning")
                )
        if document["issued_on"] is None:
            findings.append(
                finding(prefix + "_date_missing", "票据日期未识别，请核对发生日期", "warning")
            )
    return findings


def invoice_key(document):
    number = normalized(document.get("invoice_number"))
    seller = normalized(document.get("seller_tax_id") or document.get("seller_name"))
    return (seller, number) if document.get("document_type") == "invoice" and number else None


def reconcile_checks(payload, documents, company_tax_id=""):
    result = []
    invoices = [document for document in documents if document["document_type"] == "invoice"]
    receipts = [document for document in documents if document["document_type"] == "receipt"]
    selected = invoices or receipts
    keys = [invoice_key(document) for document in invoices if invoice_key(document)]
    if len(keys) != len(set(keys)):
        result.append(
            finding("duplicate_invoice_in_event", "同一事项包含重复发票号码，需要人工区分重复附件")
        )
    if selected and all(document["gross_amount"] is not None for document in selected):
        seen = set()
        total = 0
        for document in selected:
            key = invoice_key(document)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            total += money_to_cents(document["gross_amount"])
        if total != money_to_cents(payload["amount"]):
            result.append(
                finding(
                    "document_business_amount",
                    "票据合计与业务事项金额不一致",
                    business_amount=payload["amount"],
                    document_amount=f"{total // 100}.{total % 100:02d}",
                )
            )
    for index, document in enumerate(selected):
        counterparty = (
            document["buyer_name"] if payload["direction"] == "income" else document["seller_name"]
        )
        if not counterparty or normalized(counterparty) != normalized(payload["counterparty"]):
            result.append(
                finding(
                    f"counterparty_{index}",
                    "识别的交易对方与业务事项不一致或缺失",
                    document_index=index,
                )
            )
        if company_tax_id and document["document_type"] == "invoice":
            field = "seller_tax_id" if payload["direction"] == "income" else "buyer_tax_id"
            label = "销售方" if payload["direction"] == "income" else "购买方"
            if document[field] is None:
                result.append(
                    finding(
                        f"{field}_missing_{index}", label + "税号未识别，请核对企业归属", "warning"
                    )
                )
            elif normalized(document[field]) != normalized(company_tax_id):
                result.append(
                    finding(f"{field}_{index}", label + "税号与当前企业不一致，请核对票据归属")
                )
        if document["issued_on"]:
            if (
                abs(
                    (
                        date.fromisoformat(document["issued_on"])
                        - date.fromisoformat(payload["occurred_on"])
                    ).days
                )
                > 7
            ):
                result.append(
                    finding(
                        f"issued_on_{index}", "票据日期与事项日期相差超过七天，请核对", "warning"
                    )
                )
    return result
