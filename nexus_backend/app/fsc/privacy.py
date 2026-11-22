"""患者隐私红线：内部医院台账只收汇总，sensitive 明细整包拒收。

设计取舍：拦截器只回答「这一包有没有可能含患者个人标识」，因此它检查的是
**字段名与值形态**（18 位身份证、病历号/就诊卡号/患者姓名等字段），不做语义猜测。
一旦命中就整包拒收（422），既不落盘也不把命中值写进错误文案——错误信息本身
也不能成为泄漏渠道。
"""

from __future__ import annotations

import re

#: 18 位公民身份号码（末位可为 X）。前后不能紧邻数字，避免把长单号误判。
ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
#: 15 位旧版身份证：仅在明确的身份字段下才拦，避免误伤内部单号。
ID_CARD_LEGACY = re.compile(r"(?<!\d)\d{15}(?!\d)")

#: 字段名命中即拒收（不区分大小写，允许中英文）。
SENSITIVE_KEYS = (
    "patient_name",
    "patient_id",
    "patient_no",
    "patient",
    "id_card",
    "id_number",
    "identity_card",
    "medical_record",
    "medical_record_no",
    "mrn",
    "visit_card",
    "visit_card_no",
    "diagnosis",
    "insurance_detail",
    "social_insurance_detail",
    "患者",
    "病人",
    "病历",
    "就诊卡",
    "身份证",
    "医保明细",
    "诊断",
)

#: 值里出现这些字样也拒收：即使字段名被改成看起来无害的别名。
SENSITIVE_MARKERS = (
    "身份证号",
    "病历号",
    "就诊卡号",
    "患者姓名",
    "医保明细",
    "idcard",
    "medicalrecord",
)

#: 拒收文案固定，不回显命中值。
MESSAGE = "检测到患者隐私字段或身份号码，内部医院台账仅接收经批准的科室/期间汇总；整包已拒收，未写入任何数据"


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _walk(value, path: str = ""):
    """Yield ``(path, key, value)`` for every key/value pair and primitive list item."""
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            yield f"{path}{name}", name, item
            yield from _walk(item, f"{path}{name}.")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            item_path = f"{path}[{index}]"
            if isinstance(item, (dict, list)):
                yield from _walk(item, f"{item_path}.")
            else:
                yield item_path, path.rstrip(".").rsplit(".", 1)[-1], item


#: 仅允许纯计数类聚合后缀对宽泛词（患者/病人/patient）豁免，且其取值必须是非字符串数字。
#: 强标识词（身份证/病历/就诊卡/医保明细/诊断）在任何后缀下均不豁免。
COUNT_ONLY_SUFFIXES = ("count", "_count", "qty", "quantity")
COUNT_EXEMPT_MARKERS = ("patient", "患者", "病人")
STRICT_KEYS = tuple(key for key in SENSITIVE_KEYS if key not in COUNT_EXEMPT_MARKERS)


def _key_is_sensitive(name: str, value) -> bool:
    lowered = name.strip().lower()
    if any(marker in lowered for marker in STRICT_KEYS):
        return True
    if any(marker in lowered for marker in COUNT_EXEMPT_MARKERS):
        is_numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        if lowered.endswith(COUNT_ONLY_SUFFIXES) and is_numeric:
            return False
        return True
    return False


def _value_is_sensitive(text: str, name: str) -> bool:
    if ID_CARD.search(text):
        return True
    lowered = text.lower()
    if any(marker in lowered for marker in SENSITIVE_MARKERS):
        return True
    if ID_CARD_LEGACY.search(text):
        # 15 位旧版身份证：在身份语境下拦截，避免误伤普通 15 位内部流水号。
        if "身份证" in text or "id" in name.lower() or "identity" in name.lower():
            return True
    return False


def find_patient_privacy(payload) -> str:
    """Return the (non-identifying) location of the first privacy hit, or ``""``."""
    for path, name, value in _walk(payload):
        if name and _key_is_sensitive(name, value):
            # 键名本身可以出现在错误里：它不含患者的真实取值。
            return path.rsplit(".", 1)[-1] or "row"
        text = _text(value)
        if text and _value_is_sensitive(text, name):
            return path.rsplit(".", 1)[-1] or "value"
    return ""


def assert_no_patient_privacy(payload, subject: str = "内部医院汇总") -> None:
    """Raise a 422 that names *where* the hit is but never *what* it was."""
    from ..accounting.domain import AccountingError

    location = find_patient_privacy(payload)
    if location:
        raise AccountingError(f"{subject}：{MESSAGE}（命中位置：{location}）", 422)
