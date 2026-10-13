"""Synthetic migration facts only: exact amounts, original evidence, no real firms."""
import copy
import io
import json
import zipfile

import pytest
from pydantic import ValidationError

from app.accounting.domain import AccountingError
from app.accounting.invariants import MAX_CENTS
from app.legacy_migration.models import FileMapping, MigrationConfirm, ProjectCreate
from app.legacy_migration.normalization import (MAX_BYTES, SCHEMA, inspect_file,
                                               normalize_records, signed_cents, signed_money)
from app.legacy_migration.storage import LegacyOriginalStore
from app.legacy_migration.validation import deduplicate_records, validate_records

CONTEXT = {'external_book_id': 'SYNTHETIC-TPLUS-BOOK', 'currency': 'CNY', 'history_start': '2026-08-01', 'history_end': '2026-08-31', 'cutover_date': '2026-09-01'}


def synthetic_package():
    records = []
    for code, name, category, side in [('1001', '模拟现金', 'asset', 'debit'), ('2001', '模拟借款', 'liability', 'credit'), ('6602', '模拟管理费用', 'expense', 'debit')]:
        records.append({'kind': 'account', 'external_id': code, 'period': '2026-08', 'payload': {'code': code, 'name': name, 'parent_code': '', 'posting': True, 'category': category, 'normal_balance': side, 'auxiliary_requirements': []}})
    records.append({'kind': 'voucher', 'external_id': 'SYNTHETIC-V1', 'period': '2026-08', 'payload': {'external_id': 'SYNTHETIC-V1', 'date': '2026-08-05', 'period': '2026-08', 'voucher_type': '记', 'number': '0001', 'status': 'posted', 'is_reversal': False, 'lines': [{'ordinal': 1, 'account_code': '6602', 'description': '仅用于测试', 'debit': '100.00', 'credit': '0.00', 'auxiliaries': {}, 'original_currency': 'CNY'}, {'ordinal': 2, 'account_code': '1001', 'description': '仅用于测试', 'debit': '0.00', 'credit': '100.00', 'auxiliaries': {}, 'original_currency': 'CNY'}]}})
    for code, opening, debit, credit, closing in [('1001', '1000.00', '0.00', '100.00', '900.00'), ('2001', '-1000.00', '0.00', '0.00', '-1000.00'), ('6602', '0.00', '100.00', '0.00', '100.00')]:
        records.append({'kind': 'balance', 'external_id': code, 'period': '2026-08', 'payload': {'account_code': code, 'auxiliaries': {}, 'opening': opening, 'period_debit': debit, 'period_credit': credit, 'closing': closing, 'scope': 'posted'}})
    return {'schema': SCHEMA, 'context': copy.deepcopy(CONTEXT), 'records': records}


def normalized(package=None, context=None):
    return normalize_records('SYNTHETIC-中性账务包.json', json.dumps(package or synthetic_package(), ensure_ascii=False).encode(), 'package', {}, context or CONTEXT)


def codes(report):
    return {issue['code'] for issue in report['issues']}


def test_synthetic_full_month_has_zero_unexplained_amounts_and_traceable_original():
    records, issues, metadata = normalized()
    assert issues == []
    assert metadata['native_vendor_format_verified'] is False
    assert metadata['record_count'] == 7
    assert records[3]['payload']['_evidence']['json_pointer'] == '/records/3'
    assert len(records[3]['payload']['_evidence']['sha256']) == 64
    report = validate_records(records, CONTEXT)
    assert report['blocking_count'] == 0
    assert report['coverage']['complete']
    assert report['summary']['totals']['posted_debit'] == '100.00'
    assert report['summary']['totals']['cutover_net_balance'] == '0.00'
    assert not report['coverage']['balance_and_vouchers_accumulated_together']


@pytest.mark.parametrize('value,expected', [('0', 0), ('001.20', 120), ('-0.01', -1), ('-999999999999.99', -MAX_CENTS)])
def test_signed_legacy_amounts_exact(value, expected):
    assert signed_cents(value) == expected
    assert signed_cents(signed_money(expected)) == expected


@pytest.mark.parametrize('value', [1, 0.01, True, None, '1e2', '1.001', 'NaN', '+1.00', '(1.00)', '1,23', '1000000000000.00'])
def test_legacy_money_rejects_guessing_rounding_and_floats(value):
    with pytest.raises(ValueError):
        signed_cents(value)


def test_optional_grouping_is_explicit():
    with pytest.raises(ValueError):
        signed_cents('1,234.56')
    assert signed_cents('-1,234.56', True) == -123456


@pytest.mark.parametrize('field,value', [('currency', 'USD'), ('external_book_id', 'OTHER-BOOK'), ('history_end', '2026-07-31')])
def test_json_package_scope_cannot_be_silently_reinterpreted(field, value):
    package = synthetic_package()
    package['context'][field] = value
    _, issues, _ = normalized(package)
    assert 'PACKAGE_SCOPE_MISMATCH' in {issue['code'] for issue in issues}


def test_duplicate_json_object_keys_rejected():
    with pytest.raises(AccountingError):
        inspect_file('x.json', b'{"schema":"nexus.legacy/v1","context":{},"records":[],"records":[]}')


def test_float_json_not_coerced_into_money():
    package = synthetic_package()
    package['records'][3]['payload']['lines'][0]['debit'] = 100.01
    with pytest.raises(AccountingError):
        normalized(package)


def test_nonstandard_vendor_backup_not_claimed_supported():
    with pytest.raises(AccountingError, match='尚未验证'):
        inspect_file('TPLUS-backup.xml', b'<AccountBook/>')


def test_duplicate_same_content_skipped_but_origins_remain_in_service_inputs():
    records, _, _ = normalized()
    another = copy.deepcopy(records)
    another[3]['payload']['_evidence']['filename'] = '第二次上传.json'
    unique, count, issues = deduplicate_records(records + another)
    assert len(unique) == 7 and count == 7 and issues == []
    report = validate_records(records + another, CONTEXT)
    assert report['summary']['totals']['posted_debit'] == '100.00'
    assert report['summary']['deduplicated_count'] == 7


def test_same_id_changed_content_blocks_instead_of_overwriting():
    records, _, _ = normalized()
    changed = copy.deepcopy(records[3])
    changed['payload']['number'] = '9999'
    report = validate_records(records + [changed], CONTEXT)
    assert 'DUPLICATE_CONFLICT' in codes(report)
    assert not report['coverage']['complete']


def test_unknown_account_and_missing_balance_are_blocking():
    records, _, _ = normalized()
    records[3]['payload']['lines'][0]['account_code'] = 'UNKNOWN'
    records.pop()
    report = validate_records(records, CONTEXT)
    assert {'VOUCHER_ACCOUNT_UNKNOWN', 'MONTH_ACCOUNT_BALANCE_MISSING'} <= codes(report)


def test_missing_balances_never_claims_complete():
    records, _, _ = normalized()
    report = validate_records([row for row in records if row['kind'] != 'balance'], CONTEXT)
    assert not report['coverage']['complete']
    assert not report['coverage']['has_balances']


def test_unbalanced_voucher_and_source_balance_mismatch():
    records, _, _ = normalized()
    records[3]['payload']['lines'][0]['debit'] = '99.99'
    report = validate_records(records, CONTEXT)
    assert {'VOUCHER_UNBALANCED', 'PERIOD_MOVEMENT_MISMATCH'} <= codes(report)


def test_red_entries_require_explicit_flag_and_reduce_totals():
    package = synthetic_package()
    red = copy.deepcopy(package['records'][3])
    red['external_id'] = red['payload']['external_id'] = 'SYNTHETIC-RED'
    red['payload']['number'] = '0002'
    red['payload']['is_reversal'] = True
    red['payload']['lines'][0]['debit'] = '-10.00'
    red['payload']['lines'][1]['credit'] = '-10.00'
    package['records'].append(red)
    package['records'][4]['payload'].update(period_credit='90.00', closing='910.00')
    package['records'][6]['payload'].update(period_debit='90.00', closing='90.00')
    records, issues, _ = normalized(package)
    assert not issues
    report = validate_records(records, CONTEXT)
    assert report['blocking_count'] == 0
    assert report['summary']['totals']['posted_debit'] == '90.00'
    records[-1]['payload']['is_reversal'] = False
    assert 'RED_ENTRY_NOT_DECLARED' in codes(validate_records(records, CONTEXT))


@pytest.mark.parametrize('status', ['unposted', 'void'])
def test_nonposted_history_kept_and_excluded_from_ledger_totals(status):
    package = synthetic_package()
    other = copy.deepcopy(package['records'][3])
    other['external_id'] = other['payload']['external_id'] = 'SYNTHETIC-OTHER'
    other['payload']['number'] = '0002'
    other['payload']['status'] = status
    package['records'].append(other)
    records, issues, _ = normalized(package)
    report = validate_records(records, CONTEXT)
    assert not issues and report['blocking_count'] == 0
    assert report['summary']['counts']['voucher'] == 2
    assert report['summary']['totals']['posted_debit'] == '100.00'
    assert 'NONPOSTED_VOUCHER_RETAINED' in codes(report)


def test_month_continuity_and_complete_coverage():
    package = synthetic_package()
    context = dict(CONTEXT, history_end='2026-09-30', cutover_date='2026-10-01')
    package['context'] = context
    for previous in list(package['records'][4:]):
        next_row = copy.deepcopy(previous)
        next_row['period'] = '2026-09'
        next_row['payload'].update(opening=previous['payload']['closing'], period_debit='0.00', period_credit='0.00')
        package['records'].append(next_row)
    records, issues, _ = normalized(package, context)
    assert not issues
    assert validate_records(records, context)['blocking_count'] == 0
    records[-1]['payload']['opening'] = '99.00'
    report = validate_records(records, context)
    assert 'BALANCE_CONTINUITY_MISMATCH' in codes(report)


def test_account_parent_totals_not_double_counted():
    package = synthetic_package()
    package['records'][0]['payload']['parent_code'] = '10'
    package['records'].append({'kind': 'account', 'external_id': '10', 'period': '2026-08', 'payload': {'code': '10', 'name': '测试汇总现金', 'parent_code': '', 'posting': False, 'category': 'asset', 'normal_balance': 'debit', 'auxiliary_requirements': []}})
    parent_balance = copy.deepcopy(package['records'][4])
    parent_balance['external_id'] = '10'
    parent_balance['payload']['account_code'] = '10'
    package['records'].append(parent_balance)
    records, issues, _ = normalized(package)
    report = validate_records(records, CONTEXT)
    assert not issues and report['blocking_count'] == 0
    assert report['summary']['totals']['cutover_net_balance'] == '0.00'


def test_auxiliary_requirement_and_unknown_reference_block():
    records, _, _ = normalized()
    records[2]['payload']['auxiliary_requirements'] = ['department']
    assert 'AUXILIARY_REQUIRED_MISSING' in codes(validate_records(records, CONTEXT))
    records[3]['payload']['lines'][0]['auxiliaries'] = {'department': 'UNKNOWN'}
    assert 'AUXILIARY_REFERENCE_UNKNOWN' in codes(validate_records(records, CONTEXT))


def test_mapping_merge_is_not_silent():
    records, _, _ = normalized()
    mapping = {'accounts': [{'source_code': '1001', 'target_code': '1001'}, {'source_code': '2001', 'target_code': '1001'}, {'source_code': '6602', 'target_code': '6602'}], 'auxiliaries': []}
    assert 'ACCOUNT_MAPPING_MERGE' in codes(validate_records(records, CONTEXT, mapping))


def test_open_item_math_and_original_voucher_references():
    package = synthetic_package()
    package['records'].append({'kind': 'auxiliary', 'external_id': 'SYNTHETIC-C', 'period': '2026-08', 'payload': {'kind': 'counterparty', 'code': 'SYNTHETIC-C', 'name': '虚拟测试供应商'}})
    package['records'].append({'kind': 'open_item', 'external_id': 'SYNTHETIC-O', 'period': '2026-08', 'payload': {'kind': 'payable', 'counterparty_code': 'SYNTHETIC-C', 'original_amount': '100.00', 'settled_amount': '20.00', 'remaining_amount': '80.00', 'occurred_on': '2026-08-05', 'voucher_ids': ['SYNTHETIC-V1']}})
    records, issues, _ = normalized(package)
    report = validate_records(records, CONTEXT)
    assert not issues and 'OPEN_ITEM_SCOPE_UNVERIFIED' in codes(report)
    assert report['summary']['totals']['open_payable'] == '80.00'
    records[-1]['payload']['remaining_amount'] = '79.99'
    assert 'OPEN_ITEM_AMOUNT_MISMATCH' in codes(validate_records(records, CONTEXT))


def test_chinese_csv_voucher_grouping_preserves_physical_rows():
    content = '凭证ID,期间,凭证日期,凭证字,凭证号,凭证状态,是否红字,分录序号,科目编码,摘要,借方金额,贷方金额\nV1,2026-08,2026-08-05,记,0001,已记账,否,1,6602,模拟,100.00,0.00\nV1,2026-08,2026-08-05,记,0001,已记账,否,2,1001,模拟,0.00,100.00\n'
    records, issues, _ = normalize_records('模拟凭证.csv', content.encode('gb18030'), 'voucher', {}, CONTEXT)
    assert issues == [] and len(records) == 1
    assert records[0]['payload']['original_status'] == '已记账'
    assert [row['row_number'] for row in records[0]['payload']['_evidence']['original_rows']] == [2, 3]


def test_csv_status_not_assumed_posted():
    content = 'external_id,date,voucher_type,number,ordinal,account_code,debit,credit\nV1,2026-08-05,记,1,1,1001,100.00,0.00\n'
    records, issues, _ = normalize_records('x.csv', content.encode(), 'voucher', {}, CONTEXT)
    assert not records and issues[0]['severity'] == 'blocking'
    assert 'status' in issues[0]['message']


def xlsx_blob(cell_content='测试', extra=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>code</t></is></c></row><row r="2"><c r="A2" t="inlineStr">' + cell_content + '</c></row></sheetData></worksheet>')
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.parametrize('extra,cell', [({}, '<f>SUM(A1)</f><v>1</v>'), ({'xl/vbaProject.bin': 'macro'}, '<is><t>x</t></is>'), ({'xl/externalLinks/externalLink1.xml': '<x/>'}, '<is><t>x</t></is>'), ({'evil.xml': '<!DOCTYPE x [<!ENTITY y "z">]><x/>'}, '<is><t>x</t></is>'), ({'evil.xml': '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x><x/>'.encode('utf-16')}, '<is><t>x</t></is>'), ({'../unsafe.xml': '<x/>'}, '<is><t>x</t></is>'), ({'xl/_rels/external.rels': '<Relationships><Relationship TargetMode="External" Target="https://example.test"/></Relationships>'}, '<is><t>x</t></is>')])
def test_excel_formulas_macros_entities_and_external_paths_rejected(extra, cell):
    with pytest.raises(AccountingError):
        inspect_file('x.xlsx', xlsx_blob(cell, extra))


def test_file_and_compression_limits():
    with pytest.raises(AccountingError):
        inspect_file('x.csv', b'a' * (MAX_BYTES + 1))
    with pytest.raises(AccountingError):
        inspect_file('x.xlsx', xlsx_blob('<is><t>x</t></is>', {'bomb.xml': '<x>' + ' ' * 200000 + '</x>'}))


def test_original_storage_integrity(tmp_path):
    store = LegacyOriginalStore(tmp_path)
    content = json.dumps(synthetic_package()).encode()
    metadata = store.put('SYNTHETIC.json', content)
    assert store.read(metadata) == content
    store._path(metadata['storage_key']).write_bytes(b'changed')
    with pytest.raises(AccountingError, match='完整性'):
        store.read(metadata)


def test_commands_require_explicit_complete_periods_and_human_ack():
    body = {'target_book_id': 'test-book', 'source_product': 'generic', 'product_version': 'neutral-v1', 'external_book_id': CONTEXT['external_book_id'], 'source_system_key': 'SYNTHETIC-source', **{key: CONTEXT[key] for key in ('history_start', 'history_end', 'cutover_date')}, 'reason': '模拟接管', 'idempotency_key': 'synthetic-project-001'}
    assert ProjectCreate(**body).currency == 'CNY'
    with pytest.raises(ValidationError):
        ProjectCreate(**dict(body, history_start='2026-08-02'))
    with pytest.raises(ValidationError):
        ProjectCreate(**dict(body, cutover_date='2026-09-02'))
    with pytest.raises(ValidationError):
        MigrationConfirm(expected_version=1, validation_id='v1', basis_token='a' * 64, confirmed=False, reason='模拟', idempotency_key='synthetic-confirm-001')
    with pytest.raises(ValidationError):
        FileMapping(columns={'code': True})


def test_wrapper_mismatch_never_overwrites_original_voucher_identity():
    package = synthetic_package()
    package['records'][3]['payload']['external_id'] = 'OTHER-ID'
    records, issues, _ = normalized(package)
    assert 'VOUCHER_WRAPPER_CONFLICT' in {item['code'] for item in issues}
    assert not any(row['kind'] == 'voucher' for row in records)


def test_csv_omitted_foreign_currency_column_cannot_bypass_rejection():
    content = 'code,name,parent_code,posting,category,normal_balance,币种\n1001,模拟,,true,asset,debit,USD\n'
    records, issues, _ = normalize_records('x.csv', content.encode(), 'account', {'columns': {'code': 0, 'name': 1, 'parent_code': 2, 'posting': 3, 'category': 4, 'normal_balance': 5}}, CONTEXT)
    assert not records
    assert issues[0]['code'] == 'UNSUPPORTED_ACCOUNTING_DIMENSION'


def test_voucher_missing_line_number_not_silently_renumbered():
    package = synthetic_package()
    package['records'][3]['payload']['lines'][1]['ordinal'] = 3
    records, issues, _ = normalized(package)
    assert not any(row['kind'] == 'voucher' for row in records)
    assert '缺行' in issues[0]['message']


def test_split_balance_requires_explicit_nonnegative_debit_credit_columns():
    content = 'account_code,opening_debit,opening_credit,period_debit,period_credit,closing_debit,closing_credit,scope\n2001,0.00,1000.00,0.00,0.00,0.00,1000.00,posted\n'
    records, issues, _ = normalize_records('x.csv', content.encode(), 'balance', {'balance_mode': 'split'}, CONTEXT)
    assert not issues
    assert records[0]['payload']['opening'] == '-1000.00'
    assert records[0]['payload']['closing'] == '-1000.00'


def test_auxiliary_aggregate_and_detail_not_double_counted():
    package = synthetic_package()
    package['records'][2]['payload']['auxiliary_requirements'] = ['department']
    package['records'][3]['payload']['lines'][0]['auxiliaries'] = {'department': 'SYNTHETIC-D'}
    package['records'].append({'kind': 'auxiliary', 'external_id': 'SYNTHETIC-D', 'period': '2026-08', 'payload': {'kind': 'department', 'code': 'SYNTHETIC-D', 'name': '模拟部门'}})
    detail = copy.deepcopy(package['records'][6])
    detail['external_id'] = 'SYNTHETIC-AUX-BALANCE'
    detail['payload']['auxiliaries'] = {'department': 'SYNTHETIC-D'}
    package['records'].append(detail)
    records, issues, _ = normalized(package)
    report = validate_records(records, CONTEXT)
    assert not issues and report['blocking_count'] == 0
    assert report['summary']['totals']['cutover_net_balance'] == '0.00'
    records[-1]['payload']['closing'] = '99.99'
    assert 'AUXILIARY_BALANCE_SUM_MISMATCH' in codes(validate_records(records, CONTEXT))


def test_batch_limit_counts_voucher_lines_not_just_headers():
    package = {'schema': SCHEMA, 'context': CONTEXT, 'records': [{'kind': 'voucher', 'payload': {'lines': [{}] * 500}}] * 101}
    with pytest.raises(AccountingError, match='分录'):
        inspect_file('x.json', json.dumps(package).encode())


def test_excel_raw_text_reads_original_row_positions():
    parsed = inspect_file('x.xlsx', xlsx_blob('<is><t>SYNTHETIC-CODE</t></is>'))
    assert parsed['headers'] == ['code']
    assert parsed['rows'] == [['SYNTHETIC-CODE']]
    assert parsed['positions'] == [2]


def test_large_aggregate_totals_preserve_precision_above_line_limit():
    records, _, _ = normalized()
    voucher = copy.deepcopy(records[3])
    voucher['payload']['lines'][0]['debit'] = '999999999999.99'
    voucher['payload']['lines'][1]['credit'] = '999999999999.99'
    second = copy.deepcopy(voucher)
    second['external_id'] = second['payload']['external_id'] = 'SYNTHETIC-MAX-2'
    report = validate_records([row for row in records if row['kind'] != 'voucher'] + [voucher, second], CONTEXT)
    assert report['summary']['totals']['posted_debit'] == '1999999999999.98'
    assert 'PERIOD_MOVEMENT_MISMATCH' in codes(report)


def settlement_package(item_kind='payable'):
    package = synthetic_package()
    account = '2001' if item_kind == 'payable' else '1001'
    amount = '1000.00' if item_kind == 'payable' else '900.00'
    selected = package['records'][1 if item_kind == 'payable' else 0]
    selected['payload']['auxiliary_requirements'] = ['counterparty']
    if item_kind == 'receivable':
        package['records'][3]['payload']['lines'][1]['auxiliaries'] = {'counterparty': 'SYNTHETIC-C'}
    package['records'].append({'kind': 'auxiliary', 'external_id': 'SYNTHETIC-C', 'period': '2026-08', 'payload': {'kind': 'counterparty', 'code': 'SYNTHETIC-C', 'name': '模拟往来方'}})
    detail = copy.deepcopy(package['records'][5 if item_kind == 'payable' else 4])
    detail['external_id'] = 'SYNTHETIC-DETAIL'
    detail['payload']['auxiliaries'] = {'counterparty': 'SYNTHETIC-C'}
    package['records'].append(detail)
    package['records'].append({'kind': 'open_item', 'external_id': 'SYNTHETIC-O', 'period': '2026-08', 'payload': {'kind': item_kind, 'account_code': account, 'counterparty_code': 'SYNTHETIC-C', 'original_amount': '1000.00', 'settled_amount': '0.00' if item_kind == 'payable' else '100.00', 'remaining_amount': amount, 'occurred_on': '2026-07-05', 'voucher_ids': []}})
    mapping = {'accounts': [{'source_code': code, 'target_code': code, 'action': 'use_existing'} for code in ('1001', '2001', '6602')], 'auxiliaries': [{'kind': 'counterparty', 'source_code': 'SYNTHETIC-C', 'action': 'create'}], 'settlement_accounts': {item_kind: [account]}}
    return package, mapping


@pytest.mark.parametrize('item_kind', ['receivable', 'payable'])
def test_open_item_exact_account_counterparty_balance_reconciliation(item_kind):
    package, mapping = settlement_package(item_kind)
    records, issues, _ = normalized(package)
    report = validate_records(records, CONTEXT, mapping)
    assert not issues and report['blocking_count'] == 0
    assert report['coverage']['open_item_reconciliation_complete']
    assert report['open_item_bindings'][0]['account_code'] == ('2001' if item_kind == 'payable' else '1001')


def test_open_item_scope_must_have_source_counterparty_balance_not_aggregate():
    package, mapping = settlement_package()
    package['records'] = [row for row in package['records'] if row['external_id'] != 'SYNTHETIC-DETAIL']
    records, _, _ = normalized(package)
    report = validate_records(records, CONTEXT, mapping)
    assert 'OPEN_ITEM_SCOPE_UNVERIFIED' in codes(report)
    assert not report['coverage']['open_item_reconciliation_complete']


def test_open_item_missing_scope_or_mismatched_remainder_blocks_acceptance():
    package, mapping = settlement_package()
    records, _, _ = normalized(package)
    missing = validate_records(records, CONTEXT, dict(mapping, settlement_accounts={}))
    assert 'OPEN_ITEM_SCOPE_UNVERIFIED' in codes(missing)
    records[-1]['payload'].update(original_amount='999.99', remaining_amount='999.99')
    mismatch = validate_records(records, CONTEXT, mapping)
    assert 'OPEN_ITEM_BALANCE_MISMATCH' in codes(mismatch)
    assert not mismatch['coverage']['complete']


def test_open_item_single_explicit_scope_can_bind_without_guessing_multiple_accounts():
    package, mapping = settlement_package()
    package['records'][-1]['payload'].pop('account_code')
    records, _, _ = normalized(package)
    assert validate_records(records, CONTEXT, mapping)['open_item_bindings'][0]['account_code'] == '2001'
    report = validate_records(records, CONTEXT, dict(mapping, settlement_accounts={'payable': ['2001', 'OTHER']}))
    assert 'OPEN_ITEM_SCOPE_UNVERIFIED' in codes(report)


def test_cutover_cannot_skip_unimported_intermediate_months():
    with pytest.raises(AccountingError, match='次日'):
        normalize_records('x.json', json.dumps(synthetic_package()).encode(), 'package', {}, dict(CONTEXT, cutover_date='2026-11-01'))


def test_declared_source_controls_compare_counts_lines_and_exact_totals():
    package = synthetic_package()
    package['controls'] = {'record_counts': {'account': 3, 'auxiliary': 0, 'voucher': 1, 'balance': 3, 'open_item': 0}, 'voucher_line_count': 2, 'posted_voucher_count': 1, 'posted_debit': '100.00', 'posted_credit': '100.00'}
    _, issues, metadata = normalized(package)
    assert not issues and metadata['source_controls_verified']
    package['controls']['voucher_line_count'] = 3
    _, issues, metadata = normalized(package)
    assert issues[-1]['code'] == 'SOURCE_CONTROL_MISMATCH'
    assert not metadata['source_controls_verified']


def test_partial_source_controls_do_not_claim_complete_source_reconciliation():
    package = synthetic_package()
    package['controls'] = {'record_counts': {'account': 3}}
    records, issues, metadata = normalized(package)
    assert not issues and not metadata['source_controls_verified']
    report = validate_records(records, CONTEXT)
    assert not report['coverage']['source_controls_verified']
    assert 'SOURCE_CONTROLS_NOT_AVAILABLE' in codes(report)


def test_year_cumulative_movement_rolls_forward_without_guessing_prior_months():
    package = synthetic_package()
    context = dict(CONTEXT, history_end='2026-09-30', cutover_date='2026-10-01')
    package['context'] = context
    for previous in package['records'][4:]:
        previous['payload']['year_debit'] = previous['payload']['period_debit']
        previous['payload']['year_credit'] = previous['payload']['period_credit']
    for previous in list(package['records'][4:]):
        next_row = copy.deepcopy(previous)
        next_row['period'] = '2026-09'
        next_row['payload'].update(opening=previous['payload']['closing'], period_debit='0.00', period_credit='0.00')
        package['records'].append(next_row)
    records, issues, _ = normalized(package, context)
    assert not issues and validate_records(records, context)['blocking_count'] == 0
    records[-1]['payload']['year_debit'] = '99.99'
    assert 'YEAR_MOVEMENT_CONTINUITY_MISMATCH' in codes(validate_records(records, context))


@pytest.mark.parametrize('bad', [None, 'bad', {'kind': 'balance', 'external_id': 'x', 'period': '2026-08', 'payload': {}}, {'kind': 'voucher', 'external_id': 'x', 'period': '2026-08', 'payload': {'lines': None}}])
def test_invalid_stored_record_fails_closed_with_a_reviewable_report(bad):
    report = validate_records([bad], CONTEXT)
    assert report['blocking_count'] > 0
    assert not report['coverage']['complete']


def test_distinct_ids_cannot_hide_same_period_voucher_number_collision():
    records, _, _ = normalized()
    duplicate = copy.deepcopy(records[3])
    duplicate['external_id'] = duplicate['payload']['external_id'] = 'OTHER-VOUCHER-ID'
    report = validate_records(records + [duplicate], CONTEXT)
    assert 'VOUCHER_NUMBER_COLLISION' in codes(report)


def test_auxiliary_identity_namespaces_source_table_even_when_raw_ids_repeat():
    package = {'schema': SCHEMA, 'context': CONTEXT, 'records': [{'kind': 'auxiliary', 'external_id': '001', 'period': '2026-08', 'payload': {'kind': dimension, 'code': '001', 'name': '模拟'+dimension}} for dimension in ('department', 'counterparty')]}
    records, issues, _ = normalized(package)
    assert not issues
    assert {row['external_id'] for row in records} == {'department:001', 'counterparty:001'}
    assert all(row['payload']['code'] == '001' and row['payload']['_evidence']['original_external_id'] == '001' for row in records)
    assert len(deduplicate_records(records)[0]) == 2


def test_omitted_external_book_column_cannot_mix_another_book():
    csv = 'code,name,posting,category,normal_balance,账套编码\n1001,模拟,true,asset,debit,OTHER-BOOK\n'
    records, issues, _ = normalize_records('SYNTHETIC.csv', csv.encode(), 'account', {'columns': {'code': 0, 'name': 1, 'posting': 2, 'category': 3, 'normal_balance': 4}}, CONTEXT)
    assert not records and issues[0]['code'] == 'ROW_SCOPE_MISMATCH'


def test_unknown_organization_scope_is_not_dropped_by_column_mapping():
    csv = 'code,name,posting,category,normal_balance,company_id\n1001,模拟,true,asset,debit,UNVERIFIED\n'
    records, issues, _ = normalize_records('SYNTHETIC.csv', csv.encode(), 'account', {}, CONTEXT)
    assert not records and issues[0]['code'] == 'ORGANIZATION_SCOPE_UNVERIFIED'


def test_mapping_confirmation_is_explicit_strict_boolean():
    assert FileMapping().confirmed is False
    with pytest.raises(ValidationError):
        FileMapping(confirmed='true')


@pytest.mark.parametrize('header',['company_name','company_code','org_name','org_code','organization_name','organization_code','公司名称','企业名称','组织名称','机构编码','账套名称'])
def test_unverified_owner_organization_names_cannot_be_dropped(header):
    csv = 'code,name,posting,category,normal_balance,'+header+'\n1001,模拟,true,asset,debit,MIXED-OWNER\n'
    records, issues, _ = normalize_records('SYNTHETIC.csv',csv.encode(),'account',{'columns':{'code':0,'name':1,'posting':2,'category':3,'normal_balance':4}},CONTEXT)
    assert not records and issues[0]['code']=='ORGANIZATION_SCOPE_UNVERIFIED'


@pytest.mark.parametrize('section,field',[('outer','external_book_id'),('payload','账套编码'),('payload','账套ID')])
def test_json_record_explicit_external_book_cannot_contradict_package_context(section,field):
    package=synthetic_package()
    destination=package['records'][0] if section=='outer' else package['records'][0]['payload']
    destination[field]='OTHER-EXTERNAL-BOOK'
    records,issues,_=normalized(package)
    assert 'ROW_SCOPE_MISMATCH' in {item['code'] for item in issues}
    assert not any(row['kind']=='account' and row['payload']['code']=='1001' for row in records)


def test_json_owner_org_is_blocked_but_vendor_and_generic_metadata_remain_allowed():
    package=synthetic_package()
    package['records'][0]['payload'].update(vendor_name='SYNTHETIC-vendor',counterparty_name='SYNTHETIC-supplier',custom_note='保留一般未知字段原件，不推断为所属企业')
    records,issues,_=normalized(package)
    assert not issues and len(records)==7
    package['records'][0]['company_name']='UNVERIFIED-OWNER'
    _,issues,_=normalized(package)
    assert 'ORGANIZATION_SCOPE_UNVERIFIED' in {item['code'] for item in issues}


def test_explicit_custom_book_column_mapping_cannot_bypass_row_scope():
    csv = 'code,name,posting,category,normal_balance,custom_book_number\n1001,模拟,true,asset,debit,OTHER-BOOK\n'
    profile = {'columns': {'code': 0, 'name': 1, 'posting': 2, 'category': 3, 'normal_balance': 4, 'external_book_id': 5}}
    records, issues, _ = normalize_records('SYNTHETIC-custom.csv', csv.encode(), 'account', profile, CONTEXT)
    assert not records and 'ROW_SCOPE_MISMATCH' in {item['code'] for item in issues}
