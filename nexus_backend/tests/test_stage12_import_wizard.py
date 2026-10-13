import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.accounting.domain import AccountingError
from app.accounting.models import ImportRequest
from app.accounting.seed import LOCAL_ACTOR
from app.business.storage import AttachmentStore
from app.import_wizard.models import MappingSettings, PreviewRequest
from app.import_wizard.parser import convert_date, convert_money, parse_file
from app.import_wizard.service import ImportWizardService
from test_accounting import service, book, add_actor, row

CONTENT = '交易编号,交易日期,摘要,交易对方,金额,方向,凭据引用\r\nCN001,2026/10/01,办公用品,文具店,0.30,支出,发票001\r\nCN002,2026年10月02日,服务收入,客户甲,10.00,收入,合同002\r\n'
MAPPING = MappingSettings(columns={'external_id': 0, 'date': 1, 'description': 2, 'counterparty': 3, 'amount': 4, 'direction': 5, 'evidence_ref': 6})


@pytest.fixture
def wizard(service, tmp_path):
    return ImportWizardService(service.db, AttachmentStore(tmp_path / 'originals'), service)


def preview(wizard, actor, book, content=CONTENT, mapping=MAPPING, account='工行-1234', namespace='statement'):
    item = wizard.upload(actor, book, '中文流水.csv', content.encode('utf-8'))
    return wizard.preview(actor, book, PreviewRequest(file_id=item['file_id'], namespace=namespace, bank_account=account, mapping=mapping)), item


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'gbk'])
def test_chinese_headers_and_encoding_are_deterministic(encoding):
    table = parse_file('流水.csv', CONTENT.encode(encoding))
    assert table['headers'][0] == '交易编号'
    assert table['suggested_columns'] == MAPPING.columns
    assert len(table['rows']) == 2


@pytest.mark.parametrize('value,expected', [('000.30', 30), ('¥1,234.56', 123456), ('0000', 0), ('-0.30', -30)])
def test_money_no_float_or_rounding(value, expected):
    assert convert_money(value, allow_grouping=True, signed=True) == expected


@pytest.mark.parametrize('value', ['1.001', '1e3', '1,23', '(30.00)', 'NaN', '1 200'])
def test_ambiguous_money_is_rejected(value):
    with pytest.raises(ValueError):
        convert_money(value, allow_grouping=True, signed=True)


def test_ambiguous_date_needs_explicit_order():
    with pytest.raises(ValueError):
        convert_date('01/02/2026', 'ymd')
    assert convert_date('01/02/2026', 'dmy') == '2026-02-01'
    assert convert_date('01/02/2026', 'mdy') == '2026-01-02'


def xlsx(sheet, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="流水" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', sheet)
        if extra:
            archive.writestr(*extra)
    return output.getvalue()


def test_xlsx_raw_numeric_value_not_float_and_rejects_formulas():
    sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>金额</t></is></c></row><row r="2"><c r="A2"><v>0.30</v></c></row></sheetData></worksheet>'
    assert parse_file('流水.xlsx', xlsx(sheet))['rows'][0][0] == '0.30'
    with pytest.raises(AccountingError, match='公式'):
        parse_file('流水.xlsx', xlsx(sheet.replace('<v>0.30</v>', '<f>1+2</f><v>3</v>')))


@pytest.mark.parametrize('sheet', [b'<!DOCTYPE x [<!ENTITY y "boom">]><x/>', '<!DOCTYPE x [<!ENTITY y "boom">]><x/>'.encode('utf-16')])
def test_xlsx_xml_entities_rejected_including_utf16(sheet):
    with pytest.raises(AccountingError, match='XML'):
        parse_file('流水.xlsx', xlsx(sheet))


def test_xlsx_external_link_and_zip_path_rejected():
    sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>'
    for extra in [('xl/externalLinks/link.xml', 'x'), ('../outside.xml', 'x')]:
        with pytest.raises(AccountingError):
            parse_file('流水.xlsx', xlsx(sheet, extra))


def test_preview_confirm_exact_sums_original_lineage_and_repeat(wizard, service, book):
    result, item = preview(wizard, LOCAL_ACTOR, book)
    assert result['totals'] == {'income': '10.00', 'expense': '0.30'}
    assert result['error_count'] == 0 and result['new_count'] == 2
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: wizard.confirm(LOCAL_ACTOR, book, result['id'], True), range(2)))
    assert sorted(bool(x.get('replayed')) for x in receipts) == [False, True]
    assert len(service.get_book(LOCAL_ACTOR, book)['sources']) == 2
    source = service.get_book(LOCAL_ACTOR, book)['sources'][0]
    evidence = wizard.evidence(LOCAL_ACTOR, book, source['id'])['originals'][0]
    assert evidence['filename'] == '中文流水.csv' and evidence['headers'][0] == '交易编号'
    assert wizard.original(LOCAL_ACTOR, book, item['file_id'])['data'] == CONTENT.encode('utf-8')
    assert wizard.profiles(LOCAL_ACTOR, book)['profiles'][0]['mapping'] == MAPPING.model_dump()
    repeated, _ = preview(wizard, LOCAL_ACTOR, book)
    assert repeated['new_count'] == 0 and repeated['duplicate_count'] == 2
    assert wizard.confirm(LOCAL_ACTOR, book, repeated['id'])['duplicates'] == 2


def test_bad_row_conflicting_identity_and_preview_race_never_partially_import(wizard, service, book):
    bad, _ = preview(wizard, LOCAL_ACTOR, book, CONTENT.replace('10.00', '1.001'))
    assert bad['error_count'] == 1
    with pytest.raises(AccountingError, match='存在错误'):
        wizard.confirm(LOCAL_ACTOR, book, bad['id'])
    assert not service.get_book(LOCAL_ACTOR, book)['sources']
    ready, _ = preview(wizard, LOCAL_ACTOR, book)
    service.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name='同时导入', namespace='statement', bank_account='工行-1234', rows=[row(external_id='CN002', stable_transaction_id='CN002', amount='99.00')]))
    with pytest.raises(AccountingError, match='整批未导入'):
        wizard.confirm(LOCAL_ACTOR, book, ready['id'])
    assert len(service.get_book(LOCAL_ACTOR, book)['sources']) == 1


def test_scope_and_revocation_rechecked_for_original_profiles_confirmation(wizard, service, book):
    actor = add_actor(service, user_id='operator-import', firm_id=LOCAL_ACTOR.firm_id, role='operator', book_id=book)
    result, item = preview(wizard, actor, book)
    stranger = add_actor(service)
    for who in (stranger,):
        for call in (lambda: wizard.profiles(who, book), lambda: wizard.original(who, book, item['file_id']), lambda: wizard.confirm(who, book, result['id'])):
            with pytest.raises(AccountingError):
                call()
    with service.db.transaction(write=True) as conn:
        conn.execute('DELETE FROM book_access WHERE user_id=? AND book_id=?', (actor.user_id, book))
    with pytest.raises(AccountingError):
        wizard.confirm(actor, book, result['id'])
    with pytest.raises(AccountingError):
        wizard.original(actor, book, item['file_id'])


def test_same_bank_other_company_profiles_and_originals_not_shared(wizard, service, book):
    from app.accounting.models import BookCreate
    result, item = preview(wizard, LOCAL_ACTOR, book)
    wizard.confirm(LOCAL_ACTOR, book, result['id'], True)
    other = service.create_book(LOCAL_ACTOR, BookCreate(name='另一企业', period='2026-10'))['id']
    assert wizard.profiles(LOCAL_ACTOR, other)['profiles'] == []
    with pytest.raises(AccountingError):
        wizard.original(LOCAL_ACTOR, other, item['file_id'])


def test_no_transaction_id_warns_and_exact_file_rows_repeat_but_not_absolute_cross_file(wizard, service, book):
    mapping = MAPPING.model_copy(update={'columns': {k: v for k, v in MAPPING.columns.items() if k != 'external_id'}})
    result, _ = preview(wizard, LOCAL_ACTOR, book, mapping=mapping)
    assert all(any('跨文件' not in text and '人工辨别' in text for text in row['warnings']) for row in result['rows'])
    wizard.confirm(LOCAL_ACTOR, book, result['id'])
    repeated, _ = preview(wizard, LOCAL_ACTOR, book, mapping=mapping)
    assert repeated['duplicate_count'] == 2
    edited, _ = preview(wizard, LOCAL_ACTOR, book, CONTENT.replace('CN001', 'DISPLAY-EDIT'), mapping=mapping)
    assert edited['new_count'] == 2 and any('存在同日期' in message for message in edited['rows'][0]['warnings'])


def test_split_amount_both_nonzero_is_error_and_decimal_precision_preserved(wizard, book):
    content = '日期,摘要,收入,支出\n2026-10-01,服务收入,0.10,0\n2026-10-02,办公费用,0,0.20\n2026-10-03,混合金额,1,1\n'
    mapping = MappingSettings(columns={'date': 0, 'description': 1, 'income': 2, 'expense': 3}, amount_mode='split')
    result, _ = preview(wizard, LOCAL_ACTOR, book, content, mapping)
    assert result['totals'] == {'income': '0.10', 'expense': '0.20'}
    assert result['error_count'] == 1 and '只能有一侧' in result['rows'][2]['errors'][0]


def test_original_integrity_and_immutable_metadata(wizard, service, book):
    _, item = preview(wizard, LOCAL_ACTOR, book)
    with service.db.transaction() as conn:
        metadata = conn.execute('SELECT * FROM bank_import_files WHERE id=?', (item['file_id'],)).fetchone()
    wizard.storage._path(metadata['storage_key']).write_bytes(b'changed')
    with pytest.raises(AccountingError, match='完整性'):
        wizard.original(LOCAL_ACTOR, book, item['file_id'])
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        with service.db.transaction(write=True) as conn:
            conn.execute("UPDATE bank_import_files SET filename='changed.csv' WHERE id=?", (item['file_id'],))


def test_csv_original_physical_position_survives_blank_rows_and_multiline_description(wizard, book):
    content = '交易编号,交易日期,摘要,交易对方,金额,方向,凭据引用\n\nCN001,2026-10-01,"办公用品\n分两次采购",文具店,0.30,支出,发票001\n\nCN002,2026-10-02,服务收入,客户甲,10.00,收入,合同002\n'
    result, _ = preview(wizard, LOCAL_ACTOR, book, content)
    assert [r['line'] for r in result['rows']] == [2, 3]
    assert [r['original_position'] for r in result['rows']] == [3, 6]
    assert result['rows'][0]['raw'][2] == '办公用品\n分两次采购'


def test_batch_totals_above_single_transaction_ceiling_still_use_exact_cents(wizard, book):
    content = CONTENT.replace('0.30', '999999999999.99').replace('10.00', '999999999999.99').replace('收入,合同002', '支出,合同002')
    result, _ = preview(wizard, LOCAL_ACTOR, book, content)
    assert result['error_count'] == 0
    assert result['totals']['expense'] == '1999999999999.98'


def test_xlsx_nonstandard_lzma_compression_rejected_before_xml_or_decompression():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_LZMA) as archive:
        archive.writestr('xl/workbook.xml', '<workbook/>')
    with pytest.raises(AccountingError, match='非标准压缩'):
        parse_file('不规范压缩.xlsx', output.getvalue())
