"""文件底座的真实 PostgreSQL 边界，不连接畅捷通或客户生产库。"""
import json
from uuid import uuid4
import pytest
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest, ImportRequest, SourceRow
from app.integrations.models import BridgeConfiguration, BridgeExport, BridgeImport, BridgeImportConfirm
from app.integrations.service import FinancialBridgeService
from app.ledgers.models import ManagerGrant, PeriodCreate
from app.ledgers.service import LedgersService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用 PostgreSQL 管理员和受限运行连接')


def config(version=0, external='synthetic-tplus-company', **changes):
    return BridgeConfiguration(expected_version=version, external_book_id=external,
        accounts=[{'local_code':'1002','external_code':'100201'}, {'local_code':'6602.02','external_code':'660202'}],
        reason='合成客户导出格式及科目映射已核对', idempotency_key=uuid4().hex, **changes)


def prepare(c):
    service = FinancialBridgeService(c.db)
    service.configure(c.owner, c.book, config())
    body = service.template(c.owner, c.book)
    body['records'][0]['external_id'] = 'source-file-account-1'
    request = BridgeImport(format='json', content=json.dumps(body, ensure_ascii=False), reason='合成客户原始参考文件留存', idempotency_key=uuid4().hex)
    preview = service.preview_import(c.owner, c.book, request)
    return service, request, preview


def exported(c):
    c.service.import_sources(c.owner, c.book, ImportRequest(source_name='合成交付源', rows=[SourceRow(external_id=uuid4().hex,
        date='2026-10-03', description='办公用品', counterparty='合成供应商', amount='80.00', direction='expense', evidence_ref='合成归档票据已核对')]))
    c.service.suggestions(c.owner, c.book)
    draft = c.service.get_book(c.owner, c.book)['drafts'][0]
    c.service.approve(c.reviewer, c.book, draft['id'], ApprovalRequest(expected_version=draft['version']))
    return c.service.export(c.reviewer, c.book, ExportRequest(draft_ids=[draft['id']], idempotency_key=uuid4().hex))


def test_pg_reference_import_is_idempotent_and_never_creates_ledger_sources(business_case):
    c = business_case; svc, request, preview = prepare(c)
    assert svc.preview_import(c.owner, c.book, request)['id'] == preview['id']
    confirmed = svc.confirm_import(c.reviewer, c.book, preview['id'], BridgeImportConfirm(fingerprint=preview['fingerprint']))
    assert confirmed['state'] == 'confirmed'
    assert svc.confirm_import(c.reviewer, c.book, preview['id'], BridgeImportConfirm(fingerprint=preview['fingerprint']))['state'] == 'confirmed'
    assert c.service.get_book(c.owner, c.book)['sources'] == []
    with c.db.transaction(actor=c.reviewer) as conn:
        assert conn.execute('SELECT COUNT(*) FROM bridge_reference_records WHERE book_id=?', (c.book,)).fetchone()[0] == 1
    with c.psycopg.connect(ADMIN_URL) as conn:
        for table in ('bridge_configurations','bridge_import_previews','bridge_reference_records','bridge_outbox'):
            assert conn.execute('SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass', ('accounting.'+table,)).fetchone() == (True,True)
            assert not conn.execute('SELECT has_table_privilege(%s,%s,%s)', ('accounting_worker','accounting.'+table,'SELECT')).fetchone()[0]


def test_pg_raw_reference_cannot_be_forged_or_copied_from_stale_configuration(business_case):
    c = business_case; svc, _, preview = prepare(c)
    record = preview['records'][0]
    row = svc.overview(c.owner, c.book)
    def insert(value):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute('INSERT INTO bridge_reference_records VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (uuid4().hex, row['ledger_id'], c.book, c.owner.firm_id, c.company, preview['id'], record['kind'], record['external_id'], record['period'], json.dumps(value), '2026-10-12'))
    with pytest.raises(c.psycopg.errors.RaiseException):
        insert({**record, 'name':'篡改客户原始科目名'})
    svc.configure(c.owner, c.book, config(version=1))
    with pytest.raises(c.psycopg.errors.RaiseException):
        insert(record)
    with c.db.transaction(actor=c.reviewer) as conn:
        assert conn.execute('SELECT * FROM bridge_reference_records WHERE book_id=?', (c.book,)).fetchall() == []


def test_pg_external_book_identity_cannot_rebind_from_new_month_with_old_month_hidden(business_case):
    c = business_case; svc, _, preview = prepare(c)
    svc.confirm_import(c.reviewer, c.book, preview['id'], BridgeImportConfirm(fingerprint=preview['fingerprint']))
    ledgers = LedgersService(c.db)
    ledgers.grant_manager(c.owner, c.book, ManagerGrant(user_id=c.reviewer.user_id, reason='明确授权长期账套配置维护'))
    new = ledgers.create_period(c.owner, c.book, PeriodCreate(period='2026-11', name='接入隔离次月', source_book_id=c.book, inherit_user_ids=[c.reviewer.user_id], idempotency_key=uuid4().hex))
    with c.db.transaction(write=True, actor=c.owner) as conn:
        conn.execute('DELETE FROM book_access WHERE book_id=? AND user_id=?', (c.book,c.reviewer.user_id))
    with c.db.transaction(actor=c.reviewer) as conn:
        assert conn.execute('SELECT * FROM bridge_reference_records WHERE book_id=?', (c.book,)).fetchall() == []
    with pytest.raises(AccountingError, match='不能重新绑定'):
        svc.configure(c.reviewer, new['book_id'], config(version=1, external='wrong-tplus-company'))


def test_pg_preview_and_intent_require_current_book_reviewer_after_revocation(business_case):
    c = business_case; svc, request, preview = prepare(c)
    for actor in (c.employee,c.finance,c.viewer):
        with pytest.raises(AccountingError):
            svc.confirm_import(actor,c.book,preview['id'],BridgeImportConfirm(fingerprint=preview['fingerprint']))
    with c.db.transaction(write=True, actor=c.owner) as conn:
        conn.execute('DELETE FROM book_access WHERE book_id=? AND user_id=?',(c.book,c.reviewer.user_id))
    with pytest.raises(AccountingError):
        svc.get_preview(c.reviewer,c.book,preview['id'])
    with c.db.transaction(actor=c.reviewer) as conn:
        assert conn.execute('SELECT * FROM bridge_import_previews WHERE id=?',(preview['id'],)).fetchall() == []


def test_pg_frozen_package_native_guard_rejects_amount_forgery_and_live_send_remains_disabled(business_case):
    c = business_case; svc, _, _ = prepare(c); export = exported(c)
    command = BridgeExport(export_id=export['id'],configuration_version=1,reason='已核对合成凭证交付清单',idempotency_key=uuid4().hex)
    intent = svc.prepare_export(c.reviewer,c.book,command)
    assert intent['state']=='awaiting_adapter' and not intent['live_enabled'] and not intent['automatic_resend']
    assert svc.prepare_export(c.reviewer,c.book,command)['id']==intent['id']
    with pytest.raises(AccountingError,match='未启用'):
        svc.dispatch(c.reviewer,c.book,intent['id'])
    original = intent['package']
    original['vouchers'][0]['lines'][0]['debit']='999.00'
    with pytest.raises(c.psycopg.errors.RaiseException):
        with c.db.transaction(write=True,actor=c.reviewer) as conn:
            conn.execute("INSERT INTO bridge_outbox SELECT ?,ledger_id,configuration_version,export_id,book_id,firm_id,company_id,?, ?,request_fingerprint,?,reason,created_by,created_at,state FROM bridge_outbox WHERE id=?",
                (uuid4().hex,'a'*40,json.dumps(original,ensure_ascii=False),uuid4().hex,intent['id']))
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True,actor=c.reviewer) as conn:
            conn.execute("UPDATE bridge_outbox SET package_json='{}' WHERE id=?",(intent['id'],))
    saved=svc.get_outbox(c.reviewer,c.book,intent['id'])
    assert saved['package']['vouchers'][0]['lines'][0]['debit']!='999.00'


def test_pg_wrong_company_envelope_is_not_a_backdoor_into_reference_storage(business_case):
    c=business_case;svc,request,_=prepare(c)
    body=json.loads(request.content);body['company_id']=c.other_company
    with pytest.raises(AccountingError,match='范围'):
        svc.preview_import(c.owner,c.book,request.model_copy(update={'content':json.dumps(body),'idempotency_key':uuid4().hex}))
