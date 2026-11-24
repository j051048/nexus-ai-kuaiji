"""文件底座的真实 PostgreSQL 边界，不连接畅捷通或客户生产库。"""
import base64
import csv
import io
import json
from uuid import uuid4
import pytest
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest, ImportRequest, SourceRow
from app.integrations.models import (
    BridgeConfiguration,
    BridgeExport,
    BridgeImport,
    BridgeImportConfirm,
    BridgeManualReturn,
    BridgeReturnEvidence,
    BridgeReturnImport,
    BridgeReturnRowHandle,
)
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


def test_pg_return_file_marks_received_posted(business_case):
    c = business_case
    svc, _, _ = prepare(c)
    export = exported(c)
    intent = svc.prepare_export(
        c.reviewer,
        c.book,
        BridgeExport(
            export_id=export["id"],
            configuration_version=1,
            reason="合成返回文件核验",
            idempotency_key=uuid4().hex,
        ),
    )
    artifact = svc.create_artifact(c.reviewer, c.book, intent["id"])
    voucher = intent["package"]["vouchers"][0]
    amount = sum(float(line["debit"] or 0) for line in voucher["lines"])
    accounts_cell = "|".join(
        dict.fromkeys(str(line["account_code"]) for line in voucher["lines"] if line.get("account_code"))
    )
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(["批次标识", "凭证标识", "对方凭证号", "结果", "失败原因", "对方过账日期", "金额", "科目"])
    writer.writerow([intent["external_code"], f"T54-{voucher['external_code']}", "T-1", "accepted", "", "2026-10-31", f"{amount:.2f}", accounts_cell])
    result = svc.record_return(
        c.reviewer,
        c.book,
        artifact["id"],
        BridgeReturnImport(batch_code=intent["external_code"], content=buffer.getvalue()),
    )
    assert result["received"] is True and result["posted"] is True
    assert result["counts"]["accepted"] == 1
    with c.db.transaction(actor=c.reviewer) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bridge_return_rows WHERE import_id=%s", (result["id"],)).fetchone()[0] == 1


def test_pg_return_discrepancy_is_handled_with_evidence(business_case):
    c = business_case
    svc, _, _ = prepare(c)
    export = exported(c)
    intent = svc.prepare_export(
        c.reviewer,
        c.book,
        BridgeExport(
            export_id=export["id"],
            configuration_version=1,
            reason="合成差异返回核验",
            idempotency_key=uuid4().hex,
        ),
    )
    artifact = svc.create_artifact(c.reviewer, c.book, intent["id"])
    voucher = intent["package"]["vouchers"][0]
    amount = sum(float(line["debit"] or 0) for line in voucher["lines"])
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(["批次标识", "凭证标识", "对方凭证号", "结果", "失败原因", "对方过账日期", "金额", "科目"])
    writer.writerow([intent["external_code"], f"T54-{voucher['external_code']}", "T-2", "amount_mismatch", "客户少记", "2026-10-31", f"{amount + 0.01:.2f}", "100201"])
    result = svc.record_return(
        c.reviewer,
        c.book,
        artifact["id"],
        BridgeReturnImport(batch_code=intent["external_code"], content=buffer.getvalue()),
    )
    assert result["counts"]["mismatch"] == 1
    handled = svc.handle_return_row(
        c.reviewer,
        c.book,
        artifact["id"],
        result["id"],
        f"T54-{voucher['external_code']}",
        BridgeReturnRowHandle(
            action="resolved",
            reason_code="amount_mismatch",
            reason="客户确认手续费",
            evidence_ref="客户邮件 2026-11-02",
        ),
    )
    assert handled["rows"][0]["handled"] is True


def test_pg_manual_return_evidence_round_trip(business_case):
    c = business_case
    svc, _, _ = prepare(c)
    export = exported(c)
    intent = svc.prepare_export(
        c.reviewer, c.book,
        BridgeExport(export_id=export["id"], configuration_version=1,
                     reason="合成手工回执证据", idempotency_key=uuid4().hex),
    )
    artifact = svc.create_artifact(c.reviewer, c.book, intent["id"])
    manual = svc.record_manual_return(
        c.reviewer, c.book, artifact["id"],
        BridgeManualReturn(batch_code=intent["external_code"], received=True,
                           evidence_ref="客户邮件确认收到交付包"),
    )
    payload = BridgeReturnEvidence(
        filename="客户回执.pdf", media_type="application/pdf",
        content_base64=base64.b64encode(b"%PDF-1.4 pg manual receipt").decode(),
    )
    result = svc.attach_evidence(c.reviewer, c.book, artifact["id"], manual["id"], payload)
    assert result["size_bytes"] > 0
    stored = svc.evidence_for(c.reviewer, c.book, manual["id"], artifact["id"])
    assert bytes(stored["content"]).startswith(b"%PDF-")
