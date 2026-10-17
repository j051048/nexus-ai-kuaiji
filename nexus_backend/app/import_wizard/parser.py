"""Bounded CSV and XLSX parsing; no formulas, external resources or rounding."""

import csv
import io
import re
import zipfile
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from ..accounting.domain import AccountingError
from ..accounting.models import SourceRow, money_to_cents, cents_to_money
from .models import MappingSettings

# 月度流水在部分银行导出中可达数 MB、数千行；与全局单文件上限（10 MB）对齐。
MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 64
MAX_CELL = 2000
MAX_EXPANDED = 12 * 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_CLAIMS = {
    "",
    "text/csv",
    "text/plain",
    "application/csv",
    "application/vnd.ms-excel",
    "application/octet-stream",
}
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
ALIASES = {
    "external_id": ("external_id", "交易编号", "流水号", "交易流水号", "交易序号", "流水编号"),
    "stable_transaction_id": (
        "stable_transaction_id",
        "银行原始交易号",
        "银行交易号",
        "原始交易号",
    ),
    "date": ("date", "交易日期", "记账日期", "发生日期", "日期", "交易时间"),
    "description": ("description", "摘要", "交易摘要", "用途", "附言", "备注"),
    "counterparty": ("counterparty", "交易对方", "对方名称", "对方户名", "收款人", "付款人"),
    "amount": ("amount", "金额", "交易金额", "收支金额"),
    "direction": ("direction", "方向", "收支方向", "借贷标志", "收入支出"),
    "income": ("收入金额", "收入", "贷方发生额", "入账金额"),
    "expense": ("支出金额", "支出", "借方发生额", "出账金额"),
    "evidence_ref": ("evidence_ref", "凭据引用", "票据引用", "证据引用", "凭证资料"),
}


def _table(rows, encoding, **extra):
    if not rows or len(rows) < 2:
        raise AccountingError("文件须包含一行表头和至少一条流水", 422)
    if len(rows) > MAX_ROWS + 1 or any(len(row) > MAX_COLUMNS for row in rows):
        raise AccountingError("每次最多5,000条流水、64列，请拆分文件", 413)
    if any(len(cell) > MAX_CELL for row in rows for cell in row):
        raise AccountingError("单元格内容超过2,000字，请检查文件", 413)
    headers = [x.strip() for x in rows[0]]
    if any(not x for x in headers) or len(headers) != len(set(headers)):
        raise AccountingError("表头不能为空或重复，请先整理表头", 422)
    if any(len(row) != len(headers) for row in rows[1:]):
        raise AccountingError("部分行的列数与表头不同，请检查引号及分隔符", 422)
    suggested, ambiguous = {}, []
    for field, aliases in ALIASES.items():
        matches = [i for i, value in enumerate(headers) if value.strip().lower() in aliases]
        if len(matches) == 1:
            suggested[field] = matches[0]
        elif matches:
            ambiguous.append(field)
    return {
        "headers": headers,
        "rows": rows[1:],
        "encoding": encoding,
        "suggested_columns": suggested,
        "ambiguous_fields": ambiguous,
        **extra,
    }


def parse_csv(data):
    try:
        text, encoding = data.decode("utf-8-sig"), "UTF-8"
    except UnicodeDecodeError:
        try:
            text, encoding = data.decode("gb18030"), "GB18030 / GBK"
        except UnicodeDecodeError:
            raise AccountingError("无法识别编码，请另存为UTF-8或GBK CSV", 422) from None
    if "\x00" in text:
        raise AccountingError("CSV含有无效控制字符", 422)
    try:
        reader = csv.reader(io.StringIO(text), strict=True)
        rows, positions, previous_line = [], [], 0
        for row in reader:
            first_line = previous_line + 1
            previous_line = reader.line_num
            if not any(cell.strip() for cell in row):
                continue
            rows.append(row)
            positions.append(first_line)
            if len(rows) > MAX_ROWS + 1 or len(row) > MAX_COLUMNS:
                raise AccountingError("每次最多5,000条流水、64列，请拆分文件", 413)
        return _table(rows, encoding, row_positions=positions[1:])
    except csv.Error:
        raise AccountingError("CSV引号或分隔符无效，请检查文件", 422) from None


def _xml(archive, name):
    with archive.open(name) as stream:
        data = stream.read(4 * 1024 * 1024 + 1)
    if (
        len(data) > 4 * 1024 * 1024
        or b"\x00" in data
        or re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", data, re.I)
    ):
        raise AccountingError("Excel内部XML过大或含有不允许的实体定义", 422)
    return ET.fromstring(data)


def parse_xlsx(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if any(
                entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                for entry in entries
            ):
                raise AccountingError("Excel包含非标准压缩方式，请另存为.xlsx后上传", 422)
            if len(entries) > 200 or sum(entry.file_size for entry in entries) > MAX_EXPANDED:
                raise AccountingError("Excel解压内容超过限制，请拆分文件", 413)
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or any(
                PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts
                or "\\" in name
                for name in names
            ):
                raise AccountingError("Excel内部文件路径无效", 422)
            if any(
                entry.flag_bits & 1
                or (
                    entry.file_size > 100_000
                    and entry.file_size > max(1, entry.compress_size) * 150
                )
                for entry in entries
            ):
                raise AccountingError("不支持加密或异常压缩的Excel", 422)
            if any(
                "vbaproject" in name.lower() or "externallinks" in name.lower() for name in names
            ):
                raise AccountingError("请移除宏和外部链接后上传", 422)
            for name in names:
                if name.endswith(".rels") and any(
                    node.get("TargetMode") == "External" for node in _xml(archive, name)
                ):
                    raise AccountingError("请移除外部链接后上传", 422)
            workbook = _xml(archive, "xl/workbook.xml")
            props = workbook.find("s:workbookPr", NS)
            if props is not None and props.get("date1904") in {"1", "true"}:
                raise AccountingError("此Excel使用1904日期制，请将日期列转成文本后上传", 422)
            sheets = workbook.findall("s:sheets/s:sheet", NS)
            if len(sheets) != 1:
                raise AccountingError("请将目标流水工作表单独另存为一个Excel文件", 422)
            sheet_id = sheets[0].get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            rels = _xml(archive, "xl/_rels/workbook.xml.rels")
            target = next(
                (entry.get("Target") for entry in rels if entry.get("Id") == sheet_id), None
            )
            if not target:
                raise AccountingError("Excel工作表关系无效", 422)
            sheet_path = (
                target.lstrip("/") if target.startswith("/") else str(PurePosixPath("xl") / target)
            )
            if sheet_path not in names or ".." in PurePosixPath(sheet_path).parts:
                raise AccountingError("Excel工作表路径无效", 422)
            strings = []
            if "xl/sharedStrings.xml" in names:
                for item in _xml(archive, "xl/sharedStrings.xml").findall("s:si", NS):
                    value = "".join(node.text or "" for node in item.iterfind(".//s:t", NS))
                    if len(value) > MAX_CELL or len(strings) > 64064:
                        raise AccountingError("Excel文本内容超过限制", 413)
                    strings.append(value)
            rows, positions = [], []
            count = 0
            for element in _xml(archive, sheet_path).findall("s:sheetData/s:row", NS):
                cells = {}
                for cell in element.findall("s:c", NS):
                    count += 1
                    ref = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", cell.get("r", ""))
                    if not ref or count > 64064:
                        raise AccountingError("Excel单元格位置或数量超过限制", 413)
                    column = 0
                    for char in ref[1]:
                        column = column * 26 + ord(char) - 64
                    if column > MAX_COLUMNS:
                        raise AccountingError("Excel最多64列，请删除多余空列", 413)
                    if cell.find("s:f", NS) is not None:
                        raise AccountingError(
                            "Excel含公式，请复制并粘贴为数值后上传；系统不执行公式", 422
                        )
                    value = cell.findtext("s:v", "", NS)
                    if cell.get("t") == "s":
                        value = strings[int(value)]
                    elif cell.get("t") == "inlineStr":
                        value = "".join(node.text or "" for node in cell.iterfind(".//s:t", NS))
                    elif cell.get("t") in {"e", "b"}:
                        raise AccountingError("流水字段不能包含Excel错误值或布尔值", 422)
                    if column in cells:
                        raise AccountingError("Excel有重复单元格位置", 422)
                    cells[column] = value
                if any(value.strip() for value in cells.values()):
                    rows.append([cells.get(i, "") for i in range(1, max(cells) + 1)])
                    row_index = element.get("r", "")
                    if not re.fullmatch(r"[1-9][0-9]{0,6}", row_index):
                        raise AccountingError("Excel行号无效", 422)
                    positions.append(int(row_index))
                if len(rows) > MAX_ROWS + 1:
                    raise AccountingError("每次最多5,000条流水，请拆分文件", 413)
            if rows:
                width = len(rows[0])
                rows = [row + [""] * (width - len(row)) for row in rows]
            return _table(
                rows,
                "Excel原始单元格值",
                sheet_name=sheets[0].get("name", "Sheet1"),
                row_positions=positions[1:],
            )
    except AccountingError:
        raise
    except (
        zipfile.BadZipFile,
        ET.ParseError,
        KeyError,
        ValueError,
        IndexError,
        RuntimeError,
        OSError,
    ):
        raise AccountingError("Excel文件无法安全解析，请检查或另存为CSV", 422) from None


def parse_file(filename, data):
    if not data or len(data) > MAX_BYTES:
        raise AccountingError("文件不能为空，单个文件最多10MB", 413)
    if filename.lower().endswith(".csv"):
        return parse_csv(data)
    if filename.lower().endswith(".xlsx") and data[:2] == b"PK":
        return parse_xlsx(data)
    raise AccountingError("仅支持CSV与.xlsx；旧版.xls请另存为.xlsx或CSV", 422)


def validate_import_original(filename, claimed_type, data):
    """两步式上传校验器：返回（归一化文件名, 探测到的媒体类型）。"""
    name = re.sub(
        r"[\x00-\x1f\x7f]", "", (filename or "").replace("\\", "/").split("/")[-1]
    ).strip()
    if not 1 <= len(name) <= 150:
        raise AccountingError("文件名称须为1–150字", 422)
    parse_file(name, data)
    claimed = (claimed_type or "").split(";", 1)[0].strip().lower()
    if name.lower().endswith(".csv"):
        if claimed and claimed not in CSV_CLAIMS:
            raise AccountingError("CSV 声明格式与实际内容不符", 422)
        return name, "text/csv"
    if claimed and claimed not in {XLSX, "application/octet-stream"}:
        raise AccountingError("XLSX 声明格式与实际内容不符", 422)
    return name, XLSX


def convert_date(value, mode):
    value = value.strip()
    if mode == "excel_serial":
        if not re.fullmatch(r"[0-9]+(?:\.0+)?", value):
            raise ValueError("Excel日期序号必须是整数，日期时间请先转成日期文本")
        serial = int(Decimal(value))
        if not 61 <= serial <= 73050:
            raise ValueError("Excel日期序号超出支持范围")
        return (date(1899, 12, 30) + timedelta(days=serial)).isoformat()
    normalized = (
        value.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
    )
    parts = normalized.split("-")
    if len(parts) != 3 or any(not x.isdigit() for x in parts):
        raise ValueError("日期无法按所选格式解析，请确认年、月、日顺序")
    if mode == "ymd":
        y, m, d = parts
    elif mode == "dmy":
        d, m, y = parts
    else:
        m, d, y = parts
    if len(y) != 4:
        raise ValueError("年份必须为四位，模糊日期不能自动推断")
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        raise ValueError("日期不正确，请核对月份和日是否存在") from None


def convert_money(value, allow_grouping=False, signed=False):
    value = value.strip().replace("￥", "").replace("¥", "").replace("人民币", "").strip()
    negative = signed and value.startswith("-")
    if value.startswith(("+", "-")):
        if not signed:
            raise ValueError("此模式要求正数金额；请显式选择正负金额模式")
        value = value[1:]
    if "," in value:
        if not allow_grouping or not re.fullmatch(
            r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?", value
        ):
            raise ValueError("金额分隔符不明确；请确认千位逗号选项或整理金额")
        value = value.replace(",", "")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", value):
        raise ValueError("金额须为最多两位小数，科学计数法和四舍五入不支持")
    integer, dot, fraction = value.partition(".")
    value = (integer.lstrip("0") or "0") + (dot + fraction if dot else "")
    cents = money_to_cents(value)
    return -cents if negative else cents


def convert_row(raw, mapping: MappingSettings, file_sha, index):
    def take(field):
        return raw[mapping.columns[field]].strip() if field in mapping.columns else ""

    date_value = convert_date(take("date"), mapping.date_format)
    if mapping.amount_mode == "split":
        income = convert_money(take("income") or "0", mapping.allow_grouping)
        expense = convert_money(take("expense") or "0", mapping.allow_grouping)
        if (income > 0) == (expense > 0):
            raise ValueError("收入和支出必须且只能有一侧大于零")
        cents, direction = (income, "income") if income else (expense, "expense")
    else:
        cents = convert_money(
            take("amount"), mapping.allow_grouping, mapping.amount_mode != "direction"
        )
        if mapping.amount_mode == "direction":
            direction = {
                "income": "income",
                "收入": "income",
                "收": "income",
                "入": "income",
                "收款": "income",
                "expense": "expense",
                "支出": "expense",
                "支": "expense",
                "出": "expense",
                "付款": "expense",
            }.get(take("direction").lower())
            if not direction:
                raise ValueError("方向须明确为收入/支出；借/贷含义请先转换后导入")
        else:
            direction = (
                "income" if (cents > 0) == (mapping.amount_mode == "income_positive") else "expense"
            )
        cents = abs(cents)
    stable = take("stable_transaction_id") or take("external_id")
    generated = not bool(stable)
    external = take("external_id") or stable or f"file:{file_sha}:{index}"
    source = SourceRow(
        external_id=external,
        stable_transaction_id=stable or external,
        date=date_value,
        description=take("description"),
        counterparty=take("counterparty"),
        amount=cents_to_money(cents),
        direction=direction,
        evidence_ref=take("evidence_ref"),
    )
    return source, generated
