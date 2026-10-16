"""电子税务局《全量发票查询导出表》安全解析（CSV / XLSX）。

导出表只作证据与勾稽线索，不产生任何会计事实。解析保持有界：不执行公式、
不跟随外部引用、金额一律换算成整数“分”，并为每行生成指纹供批内/批间去重。
"""

import csv
import hashlib
import io
import re
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from ..accounting.domain import AccountingError

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 128
MAX_CELL = 2000
MAX_EXPANDED = 30 * 1024 * 1024
MAX_WARNINGS = 200
MAX_ABS_CENTS = 100_000_000_000_000
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
ALIASES = {
    "number": ("发票号码", "数电票号码", "发票号", "发票编号", "号码"),
    "code": ("发票代码", "发票代码（旧）", "发票代码(旧)", "代码"),
    "date": ("开票日期", "发票日期", "开票时间", "日期"),
    "seller": ("销售方名称", "销方名称", "销售方", "销方"),
    "seller_tax": ("销售方纳税人识别号", "销方纳税人识别号", "销售方税号", "销方税号"),
    "buyer": ("购买方名称", "购方名称", "购买方", "购方"),
    "buyer_tax": ("购买方纳税人识别号", "购方纳税人识别号", "购买方税号", "购方税号"),
    "amount": ("金额", "不含税金额", "合计金额"),
    "tax": ("税额", "合计税额"),
    "total": ("价税合计", "价税合计金额", "含税金额", "价税合计（元）", "价税合计(元)"),
    "kind": ("发票种类", "票种", "发票类型"),
    "status": ("发票状态", "状态"),
    "item": ("项目名称", "货物或应税劳务名称", "货物名称", "商品名称"),
}
CSV_CLAIMS = {
    "",
    "text/csv",
    "text/plain",
    "application/csv",
    "application/vnd.ms-excel",
    "application/octet-stream",
}
XLSX_CLAIMS = {XLSX_MEDIA, "application/zip", "application/octet-stream"}
DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y年%m月%d日",
    "%Y%m%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
)
VOID_MARK = re.compile(r"作废")
RED_MARK = re.compile(r"红冲")


def normalize_invoice_filename(value):
    name = re.sub(r"[\x00-\x1f\x7f]", "", (value or "").replace("\\", "/").split("/")[-1]).strip()
    if not name or len(name) > 150:
        raise AccountingError("文件名称须为 1–150 个字符", 422)
    return name


def _money(value, label, line, warnings):
    raw = (value or "").strip()
    for token in ("￥", "¥", ",", "，", " ", "\u3000"):
        raw = raw.replace(token, "")
    raw = raw.rstrip("元")
    if raw in {"", "-", "--", "—"}:
        return None
    negative = raw.startswith("-")
    body = raw[1:] if negative else raw
    try:
        number = Decimal(body)
    except InvalidOperation:
        warnings.append(f"第 {line} 行：{label}“{value}”无法识别，已留空")
        return None
    cents = int((number * 100).to_integral_value(rounding=ROUND_HALF_UP))
    if abs(cents) > MAX_ABS_CENTS:
        warnings.append(f"第 {line} 行：{label}“{value}”超出允许范围，已留空")
        return None
    return -cents if negative else cents


def _date(value, line, warnings):
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    if re.fullmatch(r"[0-9]{1,5}", raw):
        serial = int(raw)
        if 10000 <= serial <= 80000:  # Excel 1900 日期序列号
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date().isoformat()
    warnings.append(f"第 {line} 行：开票日期“{value}”无法识别，已留空")
    return None


def _columns(headers):
    cleaned = [header.strip().replace("\u3000", "").lower() for header in headers]
    columns = {}
    for field, aliases in ALIASES.items():
        for index, value in enumerate(cleaned):
            if value in aliases and field not in columns:
                columns[field] = index
                break
    if (
        "number" not in columns
        or "date" not in columns
        or "total" not in columns
        and "amount" not in columns
    ):
        raise AccountingError(
            "未识别为电子税务局《全量发票查询导出表》：需要发票号码、开票日期以及金额或价税合一列",
            422,
        )
    return columns


def _assemble(headers, rows, positions, source):
    columns = _columns(headers)
    warnings, results, seen, skipped = [], [], {}, 0

    def cell(row, field):
        index = columns.get(field)
        if index is None or index >= len(row):
            return ""
        return row[index]

    for row, line in zip(rows, positions):
        number = cell(row, "number").strip()
        if not number:
            skipped += 1
            warnings.append(f"第 {line} 行缺少发票号码，已跳过")
            continue
        code = cell(row, "code").strip()
        day = _date(cell(row, "date"), line, warnings)
        total = _money(cell(row, "total"), "价税合计", line, warnings)
        amount = _money(cell(row, "amount"), "金额", line, warnings)
        tax = _money(cell(row, "tax"), "税额", line, warnings)
        if total is None and amount is not None and tax is not None:
            total = amount + tax
        if amount is None and total is not None and tax is not None:
            amount = total - tax
        status = cell(row, "status").strip()
        key = f"{code}:{number}" if code else number
        if key in seen:
            duplicate = True
            warnings.append(f"第 {line} 行发票 {key} 在文件内重复出现")
            seen[key] += 1
        else:
            duplicate = False
            seen[key] = 1
        fingerprint = hashlib.sha256(
            "|".join((code, number, day or "", str(total if total is not None else ""))).encode()
        ).hexdigest()
        results.append(
            {
                "number": number,
                "code": code,
                "date": day,
                "seller": cell(row, "seller").strip(),
                "seller_tax": cell(row, "seller_tax").strip(),
                "buyer": cell(row, "buyer").strip(),
                "buyer_tax": cell(row, "buyer_tax").strip(),
                "amount_cents": amount,
                "tax_cents": tax,
                "total_cents": total,
                "kind": cell(row, "kind").strip(),
                "status": status,
                "item": cell(row, "item").strip(),
                "line": line,
                "key": key,
                "void": bool(status and VOID_MARK.search(status)),
                "red": bool(
                    (total is not None and total < 0) or (status and RED_MARK.search(status))
                ),
                "duplicate": duplicate,
                "fingerprint": fingerprint,
            }
        )
    if len(warnings) > MAX_WARNINGS:
        extra = len(warnings) - MAX_WARNINGS
        warnings = warnings[:MAX_WARNINGS] + [f"另有 {extra} 条同类提示未逐行列出"]
    return {
        "source": source,
        "headers": [header for header in headers],
        "columns": {
            field: headers[index]
            for field, index in sorted(columns.items())
            if index < len(headers)
        },
        "rows": results,
        "row_count": len(results),
        "skipped": skipped,
        "warnings": warnings,
    }


def _csv_rows(data):
    try:
        text, source = data.decode("utf-8-sig"), "CSV"
    except UnicodeDecodeError:
        try:
            text, source = data.decode("gb18030"), "CSV (GB18030)"
        except UnicodeDecodeError:
            raise AccountingError("无法识别编码，请将导出表另存为 UTF-8 或 GBK", 422) from None
    if "\x00" in text:
        raise AccountingError("导出表含有无效控制字符", 422)
    try:
        reader = csv.reader(io.StringIO(text), strict=True)
        rows, positions, previous = [], [], 0
        for row in reader:
            first_line = previous + 1
            previous = reader.line_num
            if not any(value.strip() for value in row):
                continue
            if len(row) > MAX_COLUMNS or any(len(value) > MAX_CELL for value in row):
                raise AccountingError("导出表行列超出限制，请检查文件", 413)
            rows.append(row)
            positions.append(first_line)
            if len(rows) > MAX_ROWS + 1:
                raise AccountingError(f"每次最多导入 {MAX_ROWS:,} 行发票，请拆分文件", 413)
    except csv.Error:
        raise AccountingError("CSV 引号或分隔符无效，请检查导出文件", 422) from None
    if len(rows) < 2:
        raise AccountingError("导出表须包含一行表头和至少一行发票", 422)
    return rows[0], rows[1:], positions[1:], source


def _xml(archive, name):
    with archive.open(name) as stream:
        data = stream.read(4 * 1024 * 1024 + 1)
    if (
        len(data) > 4 * 1024 * 1024
        or b"\x00" in data
        or re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", data, re.I)
    ):
        raise AccountingError("Excel 内部 XML 过大或含有不允许的实体定义", 422)
    return ET.fromstring(data)


def _xlsx_rows(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if any(
                entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                for entry in entries
            ):
                raise AccountingError("Excel 包含非标准压缩方式，请另存为 .xlsx 后上传", 422)
            if len(entries) > 2000 or sum(entry.file_size for entry in entries) > MAX_EXPANDED:
                raise AccountingError("Excel 解压内容超过限制，请拆分文件", 413)
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or any(
                PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts
                or "\\" in name
                for name in names
            ):
                raise AccountingError("Excel 内部文件路径无效", 422)
            if any(
                entry.flag_bits & 1
                or (
                    entry.file_size > 100_000
                    and entry.file_size > max(1, entry.compress_size) * 150
                )
                for entry in entries
            ):
                raise AccountingError("不支持加密或异常压缩的 Excel", 422)
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
                raise AccountingError("此 Excel 使用 1904 日期制，请将日期列转成文本后上传", 422)
            sheets = workbook.findall("s:sheets/s:sheet", NS)
            if len(sheets) != 1:
                raise AccountingError("请将发票数据工作表单独另存为一个 Excel 文件", 422)
            sheet_id = sheets[0].get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            rels = _xml(archive, "xl/_rels/workbook.xml.rels")
            target = next(
                (entry.get("Target") for entry in rels if entry.get("Id") == sheet_id), None
            )
            if not target:
                raise AccountingError("Excel 工作表关系无效", 422)
            sheet_path = (
                target.lstrip("/") if target.startswith("/") else str(PurePosixPath("xl") / target)
            )
            if sheet_path not in names or ".." in PurePosixPath(sheet_path).parts:
                raise AccountingError("Excel 工作表路径无效", 422)
            strings = []
            if "xl/sharedStrings.xml" in names:
                for item in _xml(archive, "xl/sharedStrings.xml").findall("s:si", NS):
                    value = "".join(node.text or "" for node in item.iterfind(".//s:t", NS))
                    if len(value) > MAX_CELL or len(strings) > 64064:
                        raise AccountingError("Excel 文本内容超过限制", 413)
                    strings.append(value)
            rows, positions, count = [], [], 0
            for element in _xml(archive, sheet_path).findall("s:sheetData/s:row", NS):
                cells = {}
                for cell in element.findall("s:c", NS):
                    count += 1
                    ref = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", cell.get("r", ""))
                    if not ref or count > 64064:
                        raise AccountingError("Excel 单元格位置或数量超过限制", 413)
                    column = 0
                    for char in ref[1]:
                        column = column * 26 + ord(char) - 64
                    if column > MAX_COLUMNS:
                        raise AccountingError(f"Excel 最多 {MAX_COLUMNS} 列，请删除多余空列", 413)
                    if cell.find("s:f", NS) is not None:
                        raise AccountingError(
                            "Excel 含公式，请复制并粘贴为数值后上传；系统不执行公式", 422
                        )
                    value = cell.findtext("s:v", "", NS)
                    if cell.get("t") == "s":
                        value = strings[int(value)]
                    elif cell.get("t") == "inlineStr":
                        value = "".join(node.text or "" for node in cell.iterfind(".//s:t", NS))
                    elif cell.get("t") in {"e", "b"}:
                        raise AccountingError("发票字段不能包含 Excel 错误值或布尔值", 422)
                    if column in cells:
                        raise AccountingError("Excel 有重复单元格位置", 422)
                    cells[column] = value
                if any(value.strip() for value in cells.values()):
                    rows.append([cells.get(i, "") for i in range(1, max(cells) + 1)])
                    row_index = element.get("r", "")
                    if not re.fullmatch(r"[1-9][0-9]{0,6}", row_index):
                        raise AccountingError("Excel 行号无效", 422)
                    positions.append(int(row_index))
                if len(rows) > MAX_ROWS + 1:
                    raise AccountingError(f"每次最多导入 {MAX_ROWS:,} 行发票，请拆分文件", 413)
            if len(rows) < 2:
                raise AccountingError("导出表须包含一行表头和至少一行发票", 422)
            width = len(rows[0])
            rows = [row + [""] * max(0, width - len(row)) for row in rows]
            return rows[0], rows[1:], positions[1:]
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
        raise AccountingError("Excel 文件无法安全解析，请检查或另存为 CSV", 422) from None


def parse_invoice_export(filename, data):
    """解析发票导出表；无法解析或不像导出表时抛出 AccountingError。"""
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise AccountingError("导出表不能为空，单个文件最多 10 MB", 413)
    suffix = PurePosixPath(normalize_invoice_filename(filename)).suffix.lower()
    if suffix == ".csv":
        headers, rows, positions, source = _csv_rows(data)
    elif suffix == ".xlsx" and data[:2] == b"PK":
        headers, rows, positions = _xlsx_rows(data)
        source = "Excel"
    else:
        raise AccountingError("仅支持电子税务局导出的 CSV 或 .xlsx；旧版 .xls 请另存后上传", 422)
    return _assemble(headers, rows, positions, source)


def validate_invoice_export(filename, media_type, data):
    """upload intents 校验器：返回（归一化文件名, 探测到的媒体类型）。"""
    name = normalize_invoice_filename(filename)
    claimed = (media_type or "").split(";", 1)[0].strip().lower()
    parse_invoice_export(name, data)
    if PurePosixPath(name).suffix.lower() == ".csv":
        if claimed and claimed not in CSV_CLAIMS:
            raise AccountingError("CSV 声明格式与实际内容不符", 422)
        return name, "text/csv"
    if claimed and claimed not in XLSX_CLAIMS:
        raise AccountingError("XLSX 声明格式与实际内容不符", 422)
    return name, XLSX_MEDIA
