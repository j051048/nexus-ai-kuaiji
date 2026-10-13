"""Pure, bounded legacy data decoding. No ledger writes or AI amount inference."""
import csv
import calendar
import hashlib
import io
import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from ..accounting.domain import AccountingError
from ..accounting.invariants import MAX_CENTS, validate_date, validate_period
from ..import_wizard.parser import convert_date

MAX_BYTES = 10 * 1024 * 1024
MAX_RECORDS = 50_000
MAX_LINES = 500
MAX_COLUMNS = 64
MAX_CELL = 4000
MAX_EXPANDED = 64 * 1024 * 1024
MAX_XML = 48 * 1024 * 1024
SCHEMA = 'nexus.legacy/v1'
KINDS = {'account', 'auxiliary', 'voucher', 'balance', 'open_item'}
AUX_KINDS = {'counterparty', 'project', 'bank_account', 'department', 'employee'}
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
ALIASES = {
    'external_id': ('external_id', '原始ID', '原始编号', '凭证ID', '记录ID'),
    'period': ('period', '会计期间', '期间'), 'date': ('date', '凭证日期', '业务日期', '日期'),
    'code': ('code', '代码', '编码', '科目编码', '档案编码'), 'name': ('name', '名称', '科目名称', '档案名称'),
    'parent_code': ('parent_code', '上级科目编码'), 'posting': ('posting', '末级科目', '是否明细科目'),
    'category': ('category', '科目类别'), 'normal_balance': ('normal_balance', '余额方向'),
    'auxiliary_requirements': ('auxiliary_requirements', '辅助核算维度'),
    'kind': ('kind', '档案类型', '往来类型'), 'voucher_type': ('voucher_type', '凭证字', '凭证类别'),
    'number': ('number', '凭证号'), 'status': ('status', '凭证状态'),
    'is_reversal': ('is_reversal', '红字冲销', '是否红字'), 'ordinal': ('ordinal', '分录序号', '行号'),
    'account_code': ('account_code', '分录科目编码', '科目编码'),
    'description': ('description', '摘要'), 'debit': ('debit', '借方金额'), 'credit': ('credit', '贷方金额'),
    'original_currency': ('original_currency', '原币币种'), 'currency': ('currency', '币种'),
    'auxiliaries': ('auxiliaries', '辅助核算'), 'counterparty': ('counterparty', '往来方编码'),
    'project': ('project', '项目编码'), 'department': ('department', '部门编码'),
    'employee': ('employee', '员工编码'), 'bank_account': ('bank_account', '银行账户编码'),
    'opening': ('opening', '期初净余额'), 'closing': ('closing', '期末净余额'),
    'opening_debit': ('opening_debit', '期初借方余额'), 'opening_credit': ('opening_credit', '期初贷方余额'),
    'closing_debit': ('closing_debit', '期末借方余额'), 'closing_credit': ('closing_credit', '期末贷方余额'),
    'period_debit': ('period_debit', '本期借方发生额'), 'period_credit': ('period_credit', '本期贷方发生额'),
    'year_debit': ('year_debit', '本年借方累计'), 'year_credit': ('year_credit', '本年贷方累计'),
    'scope': ('scope', '余额口径'), 'counterparty_code': ('counterparty_code', '往来方编码'),
    'original_amount': ('original_amount', '原始金额'), 'settled_amount': ('settled_amount', '已核销金额'),
    'remaining_amount': ('remaining_amount', '未结金额'), 'occurred_on': ('occurred_on', '发生日期'),
    'due_on': ('due_on', '到期日期'), 'voucher_ids': ('voucher_ids', '关联凭证ID'),
    'quantity': ('quantity', '数量', '库存数量'), 'exchange_rate': ('exchange_rate', '汇率'),
    'unit_price': ('unit_price', '单价'),
    'foreign_amount': ('foreign_amount', '原币金额', '外币金额'),
    'unit': ('unit', '计量单位', '数量单位'),
    'external_book_id': ('external_book_id', '外部账套', '账套编码', '账套ID', '外部账套ID'),
}
STATUS = {'posted': 'posted', 'unposted': 'unposted', 'void': 'void', '已记账': 'posted', '未记账': 'unposted', '作废': 'void'}
DIMENSIONS = {'往来方': 'counterparty', '项目': 'project', '银行账户': 'bank_account', '部门': 'department', '员工': 'employee'}
ORGANIZATION_HEADERS = {'company', 'company_id', 'company_name', 'company_code', 'org', 'org_id', 'org_name', 'org_code', 'organization', 'organization_id', 'organization_name', 'organization_code', '企业', '企业编码', '企业id', '企业名称', '公司', '公司编码', '公司id', '公司名称', '组织', '组织编码', '组织id', '组织名称', '机构名称', '机构编码', '账套名称'}
EXTERNAL_BOOK_HEADERS = {header.lower() for header in ALIASES['external_book_id']}


def signed_cents(value, allow_grouping=False, maximum=MAX_CENTS):
    """Legacy red entries use signed integer cents; current business stays nonnegative."""
    if type(value) is not str:
        raise ValueError('金额必须是精确十进制字符串，不接受浮点或数字类型')
    value = value.strip()
    if ',' in value:
        if not allow_grouping or not re.fullmatch(r'-?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?', value):
            raise ValueError('金额千位分隔符须明确指定，不能猜测小数分隔符')
        value = value.replace(',', '')
    if not re.fullmatch(r'-?[0-9]+(?:\.[0-9]{1,2})?', value):
        raise ValueError('金额须为最多两位小数，不接受指数、舍入或模糊符号')
    negative = value.startswith('-')
    whole, _, fraction = value.lstrip('-').partition('.')
    cents = int(whole) * 100 + int(fraction.ljust(2, '0') or '0')
    if cents > maximum:
        raise ValueError('金额超出允许范围')
    return -cents if negative else cents


def signed_money(cents):
    if type(cents) is not int or abs(cents) > MAX_CENTS:
        raise ValueError('金额超出允许范围')
    return ('-' if cents < 0 else '') + f'{abs(cents) // 100}.{abs(cents) % 100:02d}'


def canonical_payload(payload):
    return json.dumps({key: value for key, value in payload.items() if key != '_evidence'}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _issue(code, message, kind='', external_id='', row_number=0, severity='blocking'):
    return {'code': code, 'severity': severity, 'message': message, 'kind': kind, 'external_id': external_id, 'row_number': row_number}


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('JSON 含重复字段，不能确定原记录')
        result[key] = value
    return result


def _loads(text):
    def forbidden(value):
        raise ValueError('JSON 不接受浮点数、指数或非有限数字；金额请使用字符串')
    return json.loads(text, object_pairs_hook=_json_object, parse_float=forbidden, parse_constant=forbidden)


def _safe_xml(archive, filename):
    with archive.open(filename) as stream:
        content = stream.read(MAX_XML + 1)
    if len(content) > MAX_XML:
        raise AccountingError('Excel 单项 XML 超过限制', 413)
    # NUL rejection also covers UTF-16/32 entity declarations, before XML parsing.
    if b'\x00' in content or re.search(br'<!\s*(?:DOCTYPE|ENTITY)', content, re.I):
        raise AccountingError('Excel 含不允许的实体定义或编码', 422)
    return ET.fromstring(content)


def _table(rows, positions, encoding, sheet_name=None):
    if len(rows) < 2:
        raise AccountingError('文件须包含表头和至少一条记录', 422)
    headers = [str(value).strip() for value in rows[0]]
    if not headers or len(headers) > MAX_COLUMNS or any(not value for value in headers) or len(set(headers)) != len(headers):
        raise AccountingError('表头不能为空、重复或超过 64 列', 422)
    if len(rows) > MAX_RECORDS + 1:
        raise AccountingError('单次最多 50,000 条原始记录', 413)
    if any(len(row) != len(headers) or any(len(cell) > MAX_CELL for cell in row) for row in rows[1:]):
        raise AccountingError('行列数量不一致或单元格超过 4,000 字', 422)
    return {'headers': headers, 'rows': rows[1:], 'positions': positions[1:], 'encoding': encoding, 'sheet_name': sheet_name}


def _csv(data):
    try:
        content, encoding = data.decode('utf-8-sig'), 'UTF-8'
    except UnicodeDecodeError:
        try:
            content, encoding = data.decode('gb18030'), 'GB18030 / GBK'
        except UnicodeDecodeError:
            raise AccountingError('CSV 请使用 UTF-8 或 GBK 编码', 422) from None
    if '\x00' in content:
        raise AccountingError('CSV 含无效控制字符', 422)
    rows, positions, previous = [], [], 0
    try:
        for row in (reader := csv.reader(io.StringIO(content), strict=True)):
            first, previous = previous + 1, reader.line_num
            if not any(value.strip() for value in row):
                continue
            rows.append(row)
            positions.append(first)
            if len(rows) > MAX_RECORDS + 1 or len(row) > MAX_COLUMNS:
                raise AccountingError('文件超过 50,000 行或 64 列，请拆分', 413)
        return _table(rows, positions, encoding)
    except csv.Error:
        raise AccountingError('CSV 引号或分隔符无效', 422) from None


def _xlsx(data, sheet_name=None):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 300 or sum(entry.file_size for entry in entries) > MAX_EXPANDED:
                raise AccountingError('Excel 解压大小或内部文件数超过限制', 413)
            if len(set(names)) != len(names) or any(PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or '\\' in name for name in names):
                raise AccountingError('Excel 内部路径无效', 422)
            if any(entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED} or entry.flag_bits & 1 or (entry.file_size > 100_000 and entry.file_size > max(entry.compress_size, 1) * 150) for entry in entries):
                raise AccountingError('Excel 含异常压缩或加密内容', 422)
            if any('vbaproject' in name.lower() or 'externallinks' in name.lower() for name in names):
                raise AccountingError('Excel 请移除宏和外部链接', 422)
            for name in names:
                if name.endswith('.xml') or name.endswith('.rels'):
                    element = _safe_xml(archive, name)
                    if any(node.get('TargetMode', '').lower() == 'external' for node in element.iter()):
                        raise AccountingError('Excel 请移除外部链接', 422)
                    if name.startswith('xl/worksheets/') and any(node.tag == '{' + NS['s'] + '}f' for node in element.iter()):
                        raise AccountingError('Excel 含公式；请粘贴为原始数值后上传', 422)
            workbook = _safe_xml(archive, 'xl/workbook.xml')
            props = workbook.find('s:workbookPr', NS)
            if props is not None and props.get('date1904') in {'1', 'true'}:
                raise AccountingError('1904 日期制请先将日期转为明确文本', 422)
            sheets = workbook.findall('s:sheets/s:sheet', NS)
            selected = [sheet for sheet in sheets if sheet.get('name') == sheet_name] if sheet_name else sheets
            if len(selected) != 1:
                raise AccountingError('多工作表须明确指定工作表名称', 422)
            relation = selected[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            rels = _safe_xml(archive, 'xl/_rels/workbook.xml.rels')
            target = next((node.get('Target') for node in rels if node.get('Id') == relation), None)
            if not target:
                raise AccountingError('Excel 工作表关系无效', 422)
            path = target.lstrip('/') if target.startswith('/') else str(PurePosixPath('xl') / target)
            if path not in names or '..' in PurePosixPath(path).parts:
                raise AccountingError('Excel 工作表路径无效', 422)
            shared = []
            if 'xl/sharedStrings.xml' in names:
                for item in _safe_xml(archive, 'xl/sharedStrings.xml').findall('s:si', NS):
                    value = ''.join(node.text or '' for node in item.iterfind('.//s:t', NS))
                    if len(value) > MAX_CELL or len(shared) >= MAX_RECORDS * MAX_COLUMNS:
                        raise AccountingError('Excel 文本内容超过限制', 413)
                    shared.append(value)
            rows, positions, cells_count = [], [], 0
            seen_rows = set()
            for element in _safe_xml(archive, path).findall('s:sheetData/s:row', NS):
                row_number = element.get('r', '')
                if not re.fullmatch(r'[1-9][0-9]{0,6}', row_number):
                    raise AccountingError('Excel 行号无效', 422)
                if row_number in seen_rows:
                    raise AccountingError('Excel 含重复行位置', 422)
                seen_rows.add(row_number)
                cells = {}
                for cell in element.findall('s:c', NS):
                    cells_count += 1
                    position = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]*)', cell.get('r', ''))
                    if not position or int(position[2]) != int(row_number) or cells_count > (MAX_RECORDS + 1) * MAX_COLUMNS:
                        raise AccountingError('Excel 单元格数量或位置无效', 413)
                    column = 0
                    for char in position[1]:
                        column = column * 26 + ord(char) - 64
                    if column > MAX_COLUMNS or column in cells:
                        raise AccountingError('Excel 列数超过限制或重复位置', 422)
                    value = cell.findtext('s:v', '', NS)
                    if cell.get('t') == 's':
                        if not re.fullmatch(r'[0-9]+', value) or int(value) >= len(shared):
                            raise AccountingError('Excel 共享文本索引无效', 422)
                        value = shared[int(value)]
                    elif cell.get('t') == 'inlineStr':
                        value = ''.join(node.text or '' for node in cell.iterfind('.//s:t', NS))
                    elif cell.get('t') in {'e', 'b'}:
                        raise AccountingError('Excel 错误值和布尔单元格须转为明确文本', 422)
                    cells[column] = value
                if cells and any(value.strip() for value in cells.values()):
                    rows.append([cells.get(index, '') for index in range(1, max(cells) + 1)])
                    positions.append(int(row_number))
                if len(rows) > MAX_RECORDS + 1:
                    raise AccountingError('Excel 超过 50,000 条原始记录', 413)
            if rows:
                width = len(rows[0])
                rows = [row + [''] * max(0, width - len(row)) for row in rows]
            return _table(rows, positions, 'Excel 原始单元格值', selected[0].get('name'))
    except AccountingError:
        raise
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError, IndexError, OSError, RuntimeError, OverflowError):
        raise AccountingError('Excel 无法安全解析，请另存为 CSV', 422) from None


def inspect_file(filename, data, mapping=None):
    """Inspect bounded original format without converting any financial values."""
    mapping = mapping.model_dump() if hasattr(mapping, 'model_dump') else mapping
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise AccountingError('旧账文件不能为空，单文件最多 10 MB', 413)
    extension = filename.lower().rsplit('.', 1)[-1]
    if extension == 'csv':
        table = _csv(data)
    elif extension == 'xlsx' and data[:2] == b'PK':
        table = _xlsx(data, (mapping or {}).get('sheet_name'))
    elif extension == 'json':
        try:
            package = _loads(data.decode('utf-8-sig'))
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise AccountingError('JSON 无法解析：请使用精确字符串金额和唯一字段', 422) from None
        if not isinstance(package, dict) or package.get('schema') != SCHEMA or not isinstance(package.get('context'), dict) or not isinstance(package.get('records'), list):
            raise AccountingError('JSON 须采用 nexus.legacy/v1 中性包规范，包含 context 和 records', 422)
        if len(package['records']) > MAX_RECORDS:
            raise AccountingError('单文件最多 50,000 条记录', 413)
        weighted = sum(len(item.get('payload', {}).get('lines', [])) if isinstance(item, dict) and item.get('kind') == 'voucher' and isinstance(item.get('payload'), dict) and isinstance(item['payload'].get('lines'), list) else 1 for item in package['records'])
        if weighted > MAX_RECORDS:
            raise AccountingError('单文件含分录在内最多 50,000 条原始记录', 413)
        return {'format': 'json', 'encoding': 'UTF-8', 'package': package, 'schema': SCHEMA}
    else:
        raise AccountingError('支持中性 JSON、CSV、.xlsx；尚未验证厂商原生备份或 XML 格式', 422)
    suggested, ambiguous = {}, []
    for field, aliases in ALIASES.items():
        matches = [index for index, header in enumerate(table['headers']) if header.lower() in {alias.lower() for alias in aliases}]
        if len(matches) == 1:
            suggested[field] = matches[0]
        elif matches:
            ambiguous.append(field)
    return {**table, 'format': extension, 'suggested_columns': suggested, 'ambiguous_fields': ambiguous}


def _text(value, name, required=True, maximum=500):
    if type(value) is not str or len(value.strip()) > maximum or (required and not value.strip()):
        raise ValueError(f'{name} 须为' + ('非空' if required else '') + f'文本，最多 {maximum} 字')
    return value.strip()


def _bool(value, name):
    if type(value) is bool:
        return value
    known = {'true': True, 'false': False, '是': True, '否': False, '1': True, '0': False}
    if type(value) is str and value.strip().lower() in known:
        return known[value.strip().lower()]
    raise ValueError(f'{name} 须明确为 true/false 或 是/否')


def _list(value, name):
    if isinstance(value, str):
        value = [item.strip() for item in re.split(r'[;,，；]', value) if item.strip()]
    if not isinstance(value, list) or any(type(item) is not str for item in value):
        raise ValueError(f'{name} 须为文本列表')
    if len(set(value)) != len(value):
        raise ValueError(f'{name} 不得重复')
    return value


def _aux(value):
    if type(value) is str:
        value = _loads(value or '{}')
    if not isinstance(value, dict) or any(key not in AUX_KINDS or type(item) is not str or not item.strip() for key, item in value.items()):
        raise ValueError('辅助核算须为维度到原始档案编码的对象')
    return {key: _text(item, '辅助档案编码', maximum=100) for key, item in value.items()}


def _normalize_payload(kind, raw, mapping, context):
    grouping = mapping.get('allow_grouping', False)
    money = lambda value: signed_money(signed_cents(value, grouping))
    def take(field, default=None):
        if field not in raw or raw[field] == '':
            if default is not None:
                return default
            raise ValueError(f'缺少字段 {field}，不能自动补值')
        return raw[field]
    currency = raw.get('currency', context['currency'])
    if currency not in {'CNY', '人民币'} or raw.get('original_currency', 'CNY') not in {'CNY', '人民币'}:
        raise ValueError('MVP 仅支持人民币；外币记录不能丢弃币种后迁入')
    if any(raw.get(field) not in {None, '', '0', '0.00'} for field in ('quantity', 'exchange_rate', 'unit_price', 'foreign_amount', 'unit')):
        raise ValueError('数量核算或外币汇率尚未支持，须单独验收')
    if kind == 'account':
        categories = {'资产': 'asset', '负债': 'liability', '权益': 'equity', '所有者权益': 'equity', '成本': 'cost', '收入': 'income', '费用': 'expense', '其他': 'other'}
        category = categories.get(take('category'), take('category'))
        normal = {'借': 'debit', '借方': 'debit', '贷': 'credit', '贷方': 'credit'}.get(take('normal_balance'), take('normal_balance'))
        requirements = [DIMENSIONS.get(item, item) for item in _list(take('auxiliary_requirements', []), '辅助维度')]
        if category not in {'asset', 'liability', 'equity', 'cost', 'income', 'expense', 'other'} or normal not in {'debit', 'credit'} or any(item not in AUX_KINDS for item in requirements):
            raise ValueError('科目类别、余额方向或辅助维度无效')
        return {'code': _text(take('code'), '科目编码', maximum=100), 'name': _text(take('name'), '科目名称'), 'parent_code': _text(take('parent_code', ''), '上级编码', False, 100), 'posting': _bool(take('posting'), '末级科目标志'), 'category': category, 'normal_balance': normal, 'auxiliary_requirements': requirements}
    if kind == 'auxiliary':
        dimension = DIMENSIONS.get(take('kind'), take('kind'))
        if dimension not in AUX_KINDS:
            raise ValueError('辅助档案类型无效')
        return {'kind': dimension, 'code': _text(take('code'), '档案编码', maximum=100), 'name': _text(take('name'), '档案名称')}
    if kind == 'voucher':
        voucher_date = convert_date(_text(take('date'), '凭证日期'), mapping.get('date_format', 'ymd'))
        period = validate_period(take('period', voucher_date[:7]))
        if voucher_date[:7] != period:
            raise ValueError('凭证日期与期间不一致，跨期不能自动改日期')
        original_status = _text(take('status'), '原凭证状态', maximum=100)
        status = mapping.get('status_values', {}).get(original_status, STATUS.get(original_status))
        if status not in {'posted', 'unposted', 'void'}:
            raise ValueError('凭证状态未明确映射为已记账、未记账或作废')
        raw_lines = take('lines')
        if not isinstance(raw_lines, list) or not 1 <= len(raw_lines) <= MAX_LINES:
            raise ValueError('单凭证须含 1–500 条分录')
        lines = []
        for raw_line in raw_lines:
            if not isinstance(raw_line, dict):
                raise ValueError('分录须为对象')
            ordinal = raw_line.get('ordinal')
            if isinstance(ordinal, str) and re.fullmatch(r'[1-9][0-9]{0,2}', ordinal):
                ordinal = int(ordinal)
            if type(ordinal) is not int or not 1 <= ordinal <= MAX_LINES:
                raise ValueError('分录序号须为 1–500 的整数')
            original_currency = raw_line.get('original_currency', 'CNY')
            if original_currency not in {'CNY', '人民币'} or any(raw_line.get(field) not in {None, '', '0', '0.00'} for field in ('quantity', 'exchange_rate', 'unit_price', 'foreign_amount', 'unit')):
                raise ValueError('分录含尚未支持的外币或数量核算')
            lines.append({'ordinal': ordinal, 'account_code': _text(raw_line.get('account_code'), '分录科目编码', maximum=100), 'description': _text(raw_line.get('description', ''), '摘要', False, 1000), 'debit': money(raw_line.get('debit')), 'credit': money(raw_line.get('credit')), 'auxiliaries': _aux(raw_line.get('auxiliaries', {})), 'original_currency': 'CNY'})
        if len({line['ordinal'] for line in lines}) != len(lines):
            raise ValueError('凭证分录序号重复')
        lines.sort(key=lambda line: line['ordinal'])
        if [line['ordinal'] for line in lines] != list(range(1, len(lines) + 1)):
            raise ValueError('分录序号必须从 1 连续排列；缺行不能自动重编号')
        return {'external_id': _text(take('external_id'), '凭证原始 ID', maximum=200), 'date': voucher_date, 'period': period, 'voucher_type': _text(take('voucher_type'), '凭证字', maximum=100), 'number': _text(take('number'), '凭证号', maximum=100), 'status': status, 'original_status': original_status, 'is_reversal': _bool(take('is_reversal', False), '红字冲销标志'), 'lines': lines}
    if kind == 'balance':
        if mapping.get('balance_mode', 'net_debit') == 'split':
            opening = signed_money(signed_cents(take('opening_debit'), grouping) - signed_cents(take('opening_credit'), grouping))
            closing = signed_money(signed_cents(take('closing_debit'), grouping) - signed_cents(take('closing_credit'), grouping))
            if any(signed_cents(take(field), grouping) < 0 for field in ('opening_debit', 'opening_credit', 'closing_debit', 'closing_credit')):
                raise ValueError('借贷余额栏须为非负数；负余额请使用明确净额口径')
        else:
            opening, closing = money(take('opening')), money(take('closing'))
        scope = take('scope')
        if scope not in {'posted', '仅已记账'}:
            raise ValueError('余额表须明确仅包含已记账凭证，其他口径不能混算')
        result = {'account_code': _text(take('account_code'), '余额科目', maximum=100), 'auxiliaries': _aux(take('auxiliaries', {})), 'opening': opening, 'period_debit': money(take('period_debit')), 'period_credit': money(take('period_credit')), 'closing': closing, 'scope': 'posted'}
        for field in ('year_debit', 'year_credit'):
            if raw.get(field) not in {None, ''}:
                result[field] = money(raw[field])
        if ('year_debit' in result) != ('year_credit' in result):
            raise ValueError('本年累计借贷字段须同时提供，不能猜补另一栏')
        return result
    if kind == 'open_item':
        item_kind = {'应收': 'receivable', '应付': 'payable'}.get(take('kind'), take('kind'))
        if item_kind not in {'receivable', 'payable'}:
            raise ValueError('未结往来须明确为应收或应付')
        occurred = convert_date(take('occurred_on'), mapping.get('date_format', 'ymd'))
        due = convert_date(raw['due_on'], mapping.get('date_format', 'ymd')) if raw.get('due_on') else None
        return {'kind': item_kind, 'account_code': _text(take('account_code', ''), '未结科目编码', False, 100), 'counterparty_code': _text(take('counterparty_code'), '往来方编码', maximum=100), 'original_amount': money(take('original_amount')), 'settled_amount': money(take('settled_amount')), 'remaining_amount': money(take('remaining_amount')), 'occurred_on': occurred, 'due_on': due, 'voucher_ids': _list(take('voucher_ids', []), '关联凭证')}
    raise ValueError('记录类型不受支持')


def _context(context):
    if not isinstance(context, dict) or context.get('currency') != 'CNY':
        raise AccountingError('迁移范围须明确币种 CNY', 422)
    try:
        _text(context.get('external_book_id'), '外部账套 ID', maximum=100)
        start, end = validate_date(context['history_start']), validate_date(context['history_end'])
        cutover = validate_date(context['cutover_date'])
        for value in (start, end, cutover):
            validate_period(value[:7])
        if start[8:] != '01' or int(end[8:]) != calendar.monthrange(int(end[:4]), int(end[5:7]))[1]:
            raise ValueError('历史范围须从月首至月末')
        if start > end or cutover != (date.fromisoformat(end) + timedelta(days=1)).isoformat():
            raise ValueError('历史范围必须有序，接管日期须为历史结束日的次日（月首）')
    except (ValueError, KeyError) as error:
        raise AccountingError(str(error), 422) from None


def normalize_records(filename, data, category, mapping, context):
    """Return immutable normalized facts, row issues and original-file metadata."""
    _context(context)
    category = {'accounts': 'account', 'auxiliaries': 'auxiliary', 'vouchers': 'voucher', 'balances': 'balance', 'open_items': 'open_item'}.get(category, category)
    mapping = mapping.model_dump() if hasattr(mapping, 'model_dump') else dict(mapping or {})
    parsed = inspect_file(filename, data, mapping)
    sha = hashlib.sha256(data).hexdigest()
    metadata = {key: value for key, value in parsed.items() if key not in {'package', 'rows', 'positions'}}
    metadata.update({'filename': filename, 'sha256': sha, 'size_bytes': len(data), 'adapter': SCHEMA, 'adapter_verified': True, 'native_vendor_format_verified': False})
    issues, inputs = [], []
    if parsed['format'] == 'json':
        package = parsed['package']
        for key in ('external_book_id', 'currency', 'history_start', 'history_end', 'cutover_date'):
            if package['context'].get(key) != context.get(key):
                issues.append(_issue('PACKAGE_SCOPE_MISMATCH', f'中性包 {key} 与迁移项目范围不一致'))
        for index, item in enumerate(package['records'], 1):
            if not isinstance(item, dict) or item.get('kind') not in KINDS or not isinstance(item.get('payload'), dict):
                issues.append(_issue('RECORD_FORMAT_INVALID', '记录须包含 kind、external_id、period 和 payload', row_number=index))
                continue
            row_scope_mismatch = False
            unverified_owner = False
            for section in (item, item['payload']):
                for key, value in section.items():
                    label = key.strip().lower()
                    if label in EXTERNAL_BOOK_HEADERS and value not in (None, '') and (type(value) is not str or value.strip() != context['external_book_id']):
                        row_scope_mismatch = True
                    if label in ORGANIZATION_HEADERS and value not in (None, ''):
                        unverified_owner = True
            if row_scope_mismatch:
                issues.append(_issue('ROW_SCOPE_MISMATCH', 'JSON 记录显式所属外部账套与迁移项目不一致，请按单账套拆分', item['kind'], str(item.get('external_id', '')), index))
                continue
            if unverified_owner:
                issues.append(_issue('ORGANIZATION_SCOPE_UNVERIFIED', 'JSON 记录含企业/组织标识但无法按外部账套核实，不能静默忽略归入当前企业', item['kind'], str(item.get('external_id', '')), index))
                continue
            if category not in {'package', item['kind']}:
                issues.append(_issue('CATEGORY_MISMATCH', '文件记录类型与指定类别不一致', item['kind'], str(item.get('external_id', '')), index))
                continue
            if item['kind'] == 'voucher' and ((item['payload'].get('external_id') and item.get('external_id') != item['payload']['external_id']) or (item['payload'].get('period') and item.get('period') != item['payload']['period'])):
                issues.append(_issue('VOUCHER_WRAPPER_CONFLICT', '凭证头 ID/期间与记录外层标识不同，不能覆盖后导入', item['kind'], str(item.get('external_id', '')), index))
                continue
            inputs.append((item['kind'], {**item['payload'], 'external_id': item.get('external_id', item['payload'].get('external_id')), 'period': item.get('period', item['payload'].get('period', context['history_start'][:7]))}, index, {'json_pointer': f'/records/{index - 1}'}))
    else:
        if category not in KINDS:
            raise AccountingError('CSV/Excel 须明确选择科目、辅助档案、凭证、余额或未结事项', 422)
        columns = mapping.get('columns') or parsed['suggested_columns']
        resolved = {}
        for field, column in columns.items():
            if type(column) is str:
                if column not in parsed['headers']:
                    raise AccountingError(f'映射表头不存在：{column}', 422)
                column = parsed['headers'].index(column)
            if type(column) is not int or not 0 <= column < len(parsed['headers']):
                raise AccountingError('列映射位置无效', 422)
            resolved[field] = column
        for raw, position in zip(parsed['rows'], parsed['positions']):
            item = {field: raw[column].strip() for field, column in resolved.items()}
            scope_column = parsed['suggested_columns'].get('external_book_id')
            if scope_column is not None and raw[scope_column].strip() and raw[scope_column].strip() != context['external_book_id']:
                issues.append(_issue('ROW_SCOPE_MISMATCH', '原表行所属外部账套与迁移项目不一致，请按单账套拆分', category, item.get('external_id', ''), position))
                continue
            unverified_organization_columns = [index for index, header in enumerate(parsed['headers']) if header.lower() in ORGANIZATION_HEADERS]
            if any(raw[index].strip() for index in unverified_organization_columns):
                issues.append(_issue('ORGANIZATION_SCOPE_UNVERIFIED', '原表含企业/组织标识但无法按外部账套核实，请提供单账套资料及明确范围', category, item.get('external_id', ''), position))
                continue
            unsafe_fields = []
            for field in ('quantity', 'exchange_rate', 'unit_price', 'foreign_amount', 'unit', 'currency', 'original_currency'):
                original_column = parsed['suggested_columns'].get(field)
                if original_column is not None:
                    original_value = raw[original_column].strip()
                    accepted = {'', 'CNY', '人民币'} if field in {'currency', 'original_currency'} else {'', '0', '0.00'}
                    if original_value not in accepted:
                        unsafe_fields.append(field)
            if unsafe_fields:
                issues.append(_issue('UNSUPPORTED_ACCOUNTING_DIMENSION', '原表含尚未支持的外币或数量信息，不能通过遗漏列绕过：' + '、'.join(unsafe_fields), category, item.get('external_id', ''), position))
                continue
            if 'auxiliaries' not in item:
                item['auxiliaries'] = {dimension: item[dimension] for dimension in AUX_KINDS if item.get(dimension)}
            if category == 'voucher':
                item['lines'] = [{field: item.get(field, '' if field in {'description'} else None) for field in ('ordinal', 'account_code', 'description', 'debit', 'credit')} | {'auxiliaries': item['auxiliaries'], 'original_currency': item.get('original_currency', 'CNY')} | {field: item[field] for field in ('quantity', 'exchange_rate', 'unit_price', 'foreign_amount', 'unit') if field in item}]
            inputs.append((category, item, position, {'headers': parsed['headers'], 'values': raw, 'sheet_name': parsed.get('sheet_name')}))
        if category == 'voucher':
            grouped = {}
            for kind, item, position, evidence in inputs:
                key = (item.get('period'), item.get('external_id'))
                if not item.get('external_id'):
                    issues.append(_issue('VOUCHER_ID_MISSING', '多行凭证必须有原系统稳定凭证 ID，不能只凭凭证号合并', kind, '', position))
                    continue
                if key not in grouped:
                    grouped[key] = (kind, item, position, {'original_rows': [dict(evidence, row_number=position)]})
                else:
                    previous = grouped[key][1]
                    if any(previous.get(field) != item.get(field) for field in ('date', 'number', 'voucher_type', 'status', 'is_reversal')):
                        issues.append(_issue('VOUCHER_HEADER_CONFLICT', '同一凭证 ID 的日期、号码或状态不一致', kind, item['external_id'], position))
                    previous['lines'].extend(item['lines'])
                    grouped[key][3]['original_rows'].append(dict(evidence, row_number=position))
            inputs = list(grouped.values())
    records = []
    for kind, raw, position, evidence in inputs:
        identifier = str(raw.get('external_id') or raw.get('code') or '')
        if raw.get('external_book_id') not in (None, '') and raw['external_book_id'] != context['external_book_id']:
            issues.append(_issue('ROW_SCOPE_MISMATCH', '明确映射的原表账套与迁移项目不一致，请按单账套拆分', kind, identifier, position))
            continue
        try:
            if kind == 'balance' and not raw.get('period') and context['history_start'][:7] != context['history_end'][:7]:
                raise ValueError('多月余额表必须提供每行期间，不能全部归到首月')
            payload = _normalize_payload(kind, raw, mapping, context)
            period = validate_period(raw.get('period') or (payload['period'] if kind == 'voucher' else context['history_end'][:7] if kind == 'open_item' else context['history_start'][:7]))
            if not context['history_start'][:7] <= period <= context['history_end'][:7]:
                raise ValueError('记录期间在所选历史范围之外')
            if kind == 'open_item' and payload['occurred_on'] >= context['cutover_date']:
                raise ValueError('未结事项发生日期必须早于接管日期')
            if not identifier and kind == 'balance':
                identifier = payload['account_code'] + ':' + hashlib.sha256(json.dumps(payload['auxiliaries'], sort_keys=True).encode()).hexdigest()[:16]
            original_identifier = identifier
            if kind == 'auxiliary':
                identifier = payload['kind'] + ':' + identifier
            identifier = _text(identifier, '原始稳定 ID', maximum=200)
            payload['_evidence'] = {'filename': filename, 'sha256': sha, 'row_number': position, **evidence}
            if kind == 'auxiliary':
                payload['_evidence']['original_external_id'] = original_identifier
            records.append({'kind': kind, 'external_id': identifier, 'period': period, 'payload': payload, 'row_number': position})
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            issues.append(_issue('NORMALIZATION_INVALID', str(error), kind, identifier, position))
    metadata['record_count'] = len(records)
    metadata['voucher_line_count'] = sum(len(row['payload']['lines']) for row in records if row['kind'] == 'voucher')
    metadata['raw_row_count'] = len(parsed.get('rows', parsed.get('package', {}).get('records', [])))
    metadata['normalization_fingerprint'] = hashlib.sha256(json.dumps({'sha': sha, 'category': category, 'mapping': mapping, 'context': context}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    metadata['source_controls_verified'] = False
    if parsed['format'] == 'json' and 'controls' in parsed['package']:
        controls = parsed['package']['controls']
        metadata['source_controls'] = controls
        try:
            if not isinstance(controls, dict) or set(controls) - {'record_counts', 'voucher_line_count', 'posted_voucher_count', 'posted_debit', 'posted_credit'}:
                raise ValueError('中性包 controls 字段不符合规范')
            if not controls:
                raise ValueError('controls 不能为空；未提供控制总数请省略此字段')
            record_counts = controls.get('record_counts', {})
            if not isinstance(record_counts, dict) or any(kind not in KINDS or type(count) is not int or count < 0 for kind, count in record_counts.items()):
                raise ValueError('record_counts 须为记录类型到非负整数数量')
            actual_counts = {kind: sum(row['kind'] == kind for row in records) for kind in KINDS}
            for kind, count in record_counts.items():
                if count != actual_counts[kind]:
                    raise ValueError(f'源控制总数 {kind}={count} 与解析数 {actual_counts[kind]} 不一致')
            posted = [row for row in records if row['kind'] == 'voucher' and row['payload']['status'] == 'posted']
            count_values = {'voucher_line_count': metadata['voucher_line_count'], 'posted_voucher_count': len(posted)}
            for field, actual in count_values.items():
                if field in controls and (type(controls[field]) is not int or controls[field] != actual):
                    raise ValueError(f'源控制总数 {field} 与解析数量不一致')
            for field, side in (('posted_debit', 'debit'), ('posted_credit', 'credit')):
                if field in controls and signed_cents(controls[field], maximum=MAX_CENTS * MAX_RECORDS) != sum(signed_cents(line[side]) for row in posted for line in row['payload']['lines']):
                    raise ValueError(f'源控制总额 {field} 与原已记账凭证金额不一致')
            metadata['source_control_fields_verified'] = list(controls)
            metadata['source_controls_verified'] = not issues and set(controls) == {'record_counts', 'voucher_line_count', 'posted_voucher_count', 'posted_debit', 'posted_credit'} and set(record_counts) == KINDS
        except (ValueError, TypeError, KeyError) as error:
            issues.append(_issue('SOURCE_CONTROL_MISMATCH', str(error)))
    return records, issues, metadata
