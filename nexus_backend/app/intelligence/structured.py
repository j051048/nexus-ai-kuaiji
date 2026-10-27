"""Bounded, offline parsing of the MOF guide's EInvoice 0.3 XML shape.

Parsing is not signature verification, invoice authenticity checking or approval.
OFD/XBRL and arbitrary vendor XML are deliberately separate format adapters.
"""

from dataclasses import dataclass
from datetime import datetime
import re

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError

from ..accounting.models import cents_to_money, money_to_cents
from .models import InvoiceExtraction, PreparedPage, ProviderResult
from .provider import ProviderError

XML_MEDIA_TYPE = "application/xml"
STRUCTURED_VERSION = "cn-einvoice-xml-v1"
STRUCTURED_PROVIDER = "local-structured-parser"
MAX_XML_BYTES = 1_048_576
MAX_XML_NODES = 10_000
MAX_XML_DEPTH = 32
SUPPORTED_VERSIONS = {"0.3"}
FIELD_PATHS = {
    "invoice_number": "/EInvoice/Header/EIid",
    "seller_name": "/EInvoice/EInvoiceData/SellerInformation/SellerName",
    "seller_tax_id": "/EInvoice/EInvoiceData/SellerInformation/SellerIdNum",
    "buyer_name": "/EInvoice/EInvoiceData/BuyerInformation/BuyerName",
    "buyer_tax_id": "/EInvoice/EInvoiceData/BuyerInformation/BuyerIdNum",
    "gross_amount": "/EInvoice/EInvoiceData/BasicInformation/TotalTax-includedAmount",
    "net_amount": "/EInvoice/EInvoiceData/BasicInformation/TotalAmWithoutTax",
    "tax_amount": "/EInvoice/EInvoiceData/BasicInformation/TotalTaxAm",
    "issued_on": "/EInvoice/EInvoiceData/BasicInformation/RequestTime",
    "currency": "适配器币种约束",
}


class StructuredError(ProviderError):
    def __init__(self, code, detail):
        super().__init__(code, detail, status=422)


def safe_xml(data):
    """No network, DTD, entity, XInclude or recursive expansion is permitted."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_XML_BYTES:
        raise StructuredError("structured_size", "XML 票据不能为空且最多 1 MiB，请人工处理")
    try:
        root = fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DefusedXmlException, ParseError, ValueError):
        raise StructuredError(
            "structured_invalid", "XML 损坏或包含禁止的 DTD、实体及外部引用，请人工核对原件"
        ) from None
    stack, count = [(root, 1)], 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > MAX_XML_DEPTH or count > MAX_XML_NODES:
            raise StructuredError("structured_size", "XML 结构超过安全处理范围，请人工处理")
        # schemaLocation and signed Reference URI are retained but never fetched.
        if node.tag == "{http://www.w3.org/2001/XInclude}include":
            raise StructuredError("structured_invalid", "XML 含有外部包含指令，请人工处理")
        stack.extend((child, depth + 1) for child in node)
    return root


def _element(parent, tag, required=True):
    matches = [child for child in parent if child.tag == tag]
    if len(matches) > 1 or required and not matches:
        raise StructuredError("structured_ambiguous", "XML 必要字段缺失或存在重复字段，请人工核对")
    return matches[0] if matches else None


def _value(parent, tag, *, required=True, limit=200):
    node = _element(parent, tag, required=required)
    if node is None:
        return None
    if len(node) or not node.text or not node.text.strip() or len(node.text.strip()) > limit:
        raise StructuredError(
            "structured_invalid", "XML 字段为空、过长或结构不符合支持格式，请人工核对"
        )
    return node.text.strip()


@dataclass(frozen=True)
class StructuredDocument:
    result: ProviderResult
    pages: list[PreparedPage]


def parse_invoice_xml(data):
    root = safe_xml(data)
    if root.tag != "EInvoice":
        raise StructuredError(
            "structured_unsupported", "暂仅支持 EInvoice 0.3 数电发票 XML；该格式需人工处理"
        )
    header, body = _element(root, "Header"), _element(root, "EInvoiceData")
    version = _value(header, "Version", limit=20)
    if version not in SUPPORTED_VERSIONS:
        raise StructuredError("structured_unsupported", "该数电发票 XML 版本尚未验证，请人工处理")
    inherent = _element(header, "InherentLabel")
    blue = _value(_element(inherent, "InIssuType"), "LabelCode", limit=10)
    invoice_type = _value(_element(inherent, "EInvoiceType"), "LabelCode", limit=10)
    vat_type = _value(_element(inherent, "GeneralOrSpecialVAT"), "LabelCode", limit=10)
    if blue != "Y":
        raise StructuredError(
            "structured_red_invoice", "红字或未知状态发票须由会计按冲销、调整流程人工处理"
        )
    if invoice_type != "01" or vat_type not in {"01", "02"}:
        raise StructuredError("structured_unsupported", "该票种尚未支持本地解析，请人工处理")
    seller, buyer, basic = (
        _element(body, tag) for tag in ("SellerInformation", "BuyerInformation", "BasicInformation")
    )
    originals = {
        "invoice_number": _value(header, "EIid", limit=100),
        "seller_name": _value(seller, "SellerName"),
        "seller_tax_id": _value(seller, "SellerIdNum", limit=50),
        "buyer_name": _value(buyer, "BuyerName"),
        "buyer_tax_id": _value(buyer, "BuyerIdNum", required=False, limit=50),
        "gross_amount": _value(basic, "TotalTax-includedAmount", limit=30),
        "net_amount": _value(basic, "TotalAmWithoutTax", limit=30),
        "tax_amount": _value(basic, "TotalTaxAm", limit=30),
        "issued_on": _value(basic, "RequestTime", limit=30),
    }
    if not re.fullmatch(r"[0-9]{20}", originals["invoice_number"]):
        raise StructuredError("structured_invalid", "数电发票号码须为 20 位数字，请人工核对")
    values = dict(originals)
    try:
        for field in ("gross_amount", "net_amount", "tax_amount"):
            values[field] = cents_to_money(money_to_cents(originals[field]))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", originals["issued_on"]):
            raise ValueError
        values["issued_on"] = datetime.fromisoformat(originals["issued_on"]).date().isoformat()
    except ValueError:
        raise StructuredError(
            "structured_invalid", "XML 金额或开票时间格式无效，请人工核对；系统不会推测或舍入"
        ) from None
    # Currency is a declared parser profile constraint, not a copied invoice field.
    values["currency"] = "CNY"
    originals["currency"] = "CNY（国内数电增值税发票格式限定；非原文币种字段）"
    quotes = {
        field: f"{FIELD_PATHS[field]}: {value}"
        for field, value in originals.items()
        if value is not None
    }
    text = "\n".join(quotes.values())
    document = InvoiceExtraction.model_validate(
        {
            "documents": [
                {
                    "document_type": "invoice",
                    **values,
                    "pages": [{"page": 1, "raw_text": text}],
                    "evidence": [
                        {"field": field, "page": 1, "quote": quote}
                        for field, quote in quotes.items()
                    ],
                }
            ]
        }
    )
    return StructuredDocument(
        ProviderResult(
            document, STRUCTURED_PROVIDER, "einvoice-" + version, STRUCTURED_VERSION, None
        ),
        [PreparedPage(page=1, image_data_url="", native_text=text)],
    )


def is_structured(media_type):
    return media_type == XML_MEDIA_TYPE


def extraction_metadata(run):
    if run["prompt_version"].startswith("independent-invoice-extraction-facts-v4:"):
        return {
            "extraction_method": "paddleocr",
            "verification_status": "not_verified",
            "method_label": "独立 OCR 文字与字段提取，仍需会计复核",
        }
    if run["prompt_version"] == STRUCTURED_VERSION:
        return {
            "extraction_method": "structured_xml",
            "verification_status": "not_verified",
            "method_label": "数电票 XML 本地解析",
            "field_paths": dict(FIELD_PATHS),
        }
    return {"extraction_method": "vision_model"}


def verification_finding():
    from .checks import finding

    return finding(
        "structured_verification_pending",
        "已读取 XML 字段；尚未验签或验真，须核对原件并由会计复核",
        "warning",
        verification_status="not_verified",
    )
