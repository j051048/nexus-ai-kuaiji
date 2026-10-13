import io
from uuid import uuid4

import pytest
from PIL import Image
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.accounting.domain import AccountingError
from app.intake.models import IntakeComplete, IntakeCreate, IntakeRecognize, IntakeUpdate
from app.intake.router import build_intake_router
from app.intake.service import IntakeService
from app.intelligence.provider import AISettings, ProviderError
from app.intelligence.cache import config_key
from app.operations.meter import MeterService
from app.operations.requests import ReconcileRequest as CostReconcile, PolicyRequest as PolicyUpdate
from app.operations.pricing import Pricing, Rate
from app.operations.service import OperationsService
from test_intelligence_workflow import ai_case, create_event, extract
from test_stage10_backup_recovery import recovery


def intake(c):
    return IntakeService(c.db, c.storage, c.provider, business=c.business, intelligence=c.intelligence, meter=c.intelligence._meter())


def create_intake(c, service=None, **changes):
    service = service or intake(c)
    context = c.business.context(c.actors['employee'])
    department = next(row for row in context['companies'] if row['id'] == c.company['id'])['department_id']
    return service.create(c.actors['employee'], IntakeCreate(company_id=c.company['id'], department_id=department, idempotency_key=uuid4().hex, **changes))


def attach_intake(c, service, draft):
    output = io.BytesIO(); Image.new('RGB', (14, 12), 'white').save(output, 'PNG')
    return service.add_attachment(c.actors['employee'], draft['id'], draft['version'], c.storage.put('原始业务票据.png', 'image/png', output.getvalue()))


def confirmed(draft, **changes):
    payload = {'occurred_on': '2026-10-03', 'description': '员工办公采购', 'counterparty': '耗材商店', 'amount': '0.30', 'direction': 'expense', 'payment_status': 'unpaid', 'category': 'office'}
    return IntakeComplete(expected_version=draft['version'], payload=payload, fields_confirmed=True, idempotency_key=uuid4().hex, **changes)


def test_incomplete_draft_has_no_fake_business_or_accounting_facts(ai_case):
    c = ai_case; service = intake(c); draft = create_intake(c, service, fields={'description': '金额暂未确认'})
    assert draft['fields']['amount'] == '' and service.get(c.actors['employee'], draft['id'])['fields']['description'] == '金额暂未确认'
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT count(*) FROM business_events WHERE company_id=?', (c.company['id'],)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM sources WHERE book_id=?', (c.book['id'],)).fetchone()[0] == 0


@pytest.mark.parametrize('name', ['peer', 'approver', 'finance', 'accountant', 'outsider'])
def test_unsubmitted_intake_and_original_are_private_to_current_employee(ai_case, name):
    c = ai_case; service = intake(c); draft = attach_intake(c, service, create_intake(c, service))
    assert not service.list(c.actors[name])['drafts']
    with pytest.raises(AccountingError) as error:
        service.get(c.actors[name], draft['id'])
    assert error.value.status == 404
    with pytest.raises(AccountingError):
        service.download(c.actors[name], draft['attachments'][0]['id'])


def test_membership_revocation_removes_resume_and_download(ai_case):
    c = ai_case; service = intake(c); draft = attach_intake(c, service, create_intake(c, service))
    c.directory.revoke_company_member(c.manager, c.company['id'], c.actors['employee'].user_id)
    assert service.list(c.actors['employee'])['drafts'] == []
    with pytest.raises(AccountingError): service.download(c.actors['employee'], draft['attachments'][0]['id'])


def test_complete_is_atomic_independent_approval_and_idempotent(ai_case):
    c = ai_case; service = intake(c); draft = attach_intake(c, service, create_intake(c, service)); request = confirmed(draft)
    result = service.complete(c.actors['employee'], draft['id'], request)
    event = result['event']; assert event['status'] == 'submitted' and len(event['attachments']) == 1
    assert not event['permissions']['department_approve'] and not event['permissions']['finance_approve']
    assert service.complete(c.actors['employee'], draft['id'], request)['event']['id'] == event['id']
    assert service.list(c.actors['employee'])['drafts'] == []
    assert c.business.get_event(c.actors['approver'], event['id'])['permissions']['department_approve']
    with c.db.transaction(actor=c.actors['employee']) as conn: assert conn.execute('SELECT count(*) FROM business_events WHERE id=? AND company_id=?', (event['id'], c.company['id'])).fetchone()[0] == 1


def test_failed_submission_retains_only_original_intake_no_partial_event(ai_case):
    c = ai_case; service = intake(c); draft = attach_intake(c, service, create_intake(c, service))
    c.business.revoke_department_approver(c.manager, c.company['id'], draft['department_id'], c.actors['approver'].user_id)
    with pytest.raises(AccountingError, match='审批人'): service.complete(c.actors['employee'], draft['id'], confirmed(draft))
    assert service.get(c.actors['employee'], draft['id'])['status'] == 'draft'
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT count(*) FROM business_events WHERE company_id=?', (c.company['id'],)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM business_attachments WHERE company_id=?', (c.company['id'],)).fetchone()[0] == 0
    assert service.download(c.actors['employee'], draft['attachments'][0]['id'])[0]


def test_old_version_and_missing_human_confirmation_are_rejected(ai_case):
    c = ai_case; service = intake(c); old = create_intake(c, service); draft = attach_intake(c, service, old)
    with pytest.raises(AccountingError, match='已更新'): service.complete(c.actors['employee'], draft['id'], confirmed(old))
    request = confirmed(draft); request.fields_confirmed = False
    with pytest.raises(AccountingError, match='确认'): service.complete(c.actors['employee'], draft['id'], request)
    with c.db.transaction(actor=c.manager) as conn: assert conn.execute('SELECT count(*) FROM business_events WHERE company_id=?', (c.company['id'],)).fetchone()[0] == 0


def test_manual_path_available_without_ai_and_original_survives_backup(ai_case, tmp_path):
    c = ai_case; c.provider.settings = AISettings(); service = intake(c); draft = attach_intake(c, service, create_intake(c, service))
    assert not draft['vision_ready']
    manifest = recovery.backup(c.db.path, c.storage.root, tmp_path / 'backup')
    assert len(manifest['files']) == 2 and recovery.restore(tmp_path / 'backup', tmp_path / 'restored')['verified_files'] == 2
    event = service.complete(c.actors['employee'], draft['id'], confirmed(draft))['event']
    assert event['status'] == 'submitted' and c.provider.extract_calls == 0


def test_ai_suggestions_never_write_facts_and_transfer_does_not_charge_twice(ai_case):
    c = ai_case; service = intake(c); draft = attach_intake(c, service, create_intake(c, service))
    request = IntakeRecognize(expected_version=draft['version'], idempotency_key=uuid4().hex)
    run = service.recognize(c.actors['employee'], draft['id'], draft['attachments'][0]['id'], request)
    assert run['result']['documents'][0]['gross_amount'] == '0.30' and run['checks'] == []
    assert service.get(c.actors['employee'], draft['id'])['fields']['amount'] == ''
    assert service.recognize(c.actors['employee'], draft['id'], draft['attachments'][0]['id'], request)['id'] == run['id']
    event = service.complete(c.actors['employee'], draft['id'], confirmed(draft))['event']
    detail = c.intelligence.reconcile(c.actors['finance'], event['id'])
    assert len(detail['runs']) == 1 and detail['runs'][0]['result']['documents'][0]['gross_amount'] == '0.30'
    assert extract(c, event, key='intake-transferred-cache-verification')['status'] == 'succeeded'
    assert c.provider.extract_calls == 1
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT count(*) FROM model_costs WHERE company_id=?', (c.company['id'],)).fetchone()[0] == 1
        assert conn.execute('SELECT intake_run_id,event_id FROM model_costs WHERE company_id=?', (c.company['id'],)).fetchone()['event_id'] is None


def test_unknown_send_remains_held_and_explicit_retry_waits_for_cost_reconciliation(ai_case):
    c = ai_case; c.provider.failure = ProviderError('timeout', '供应商结果未知'); service = intake(c)
    draft = attach_intake(c, service, create_intake(c, service)); request = IntakeRecognize(expected_version=draft['version'], idempotency_key=uuid4().hex)
    run = service.recognize(c.actors['employee'], draft['id'], draft['attachments'][0]['id'], request)
    assert run['status'] == 'failed'
    with c.db.transaction(actor=c.manager) as conn: assert conn.execute('SELECT state FROM model_costs WHERE id=?', (run['id'],)).fetchone()[0] == 'uncertain'
    with pytest.raises(AccountingError, match='尚未确认'):
        service.recognize(c.actors['employee'], draft['id'], draft['attachments'][0]['id'], IntakeRecognize(expected_version=draft['version'], idempotency_key=uuid4().hex))
    assert c.provider.extract_calls == 1
    operations = OperationsService(c.db, c.intelligence._meter(), c.intelligence)
    row = operations.overview(c.manager, c.company['id'])['costs'][0]
    assert row['can_reconcile']
    operations.reconcile(c.manager, run['id'], CostReconcile(expected_version=row['version'], confirmed_cost='0.00', reason='人工查询供应商回执确认没有计费'))
    c.provider.failure = None
    assert service.recognize(c.actors['employee'], draft['id'], draft['attachments'][0]['id'], IntakeRecognize(expected_version=draft['version'], idempotency_key=uuid4().hex))['status'] == 'succeeded'


def test_intake_and_existing_event_ocr_share_one_quota(ai_case):
    c = ai_case; c.intelligence.meter = MeterService(c.db, Pricing(ocr=Rate('1', '1'), proposal=Rate('1', '1')))
    operations = OperationsService(c.db, c.intelligence._meter(), c.intelligence)
    operations.policy(c.manager, c.company['id'], PolicyUpdate(expected_version=0, quota_enabled=True, monthly_limit='1.00', ocr_reserve='1.00', proposal_reserve='1.00'))
    service = intake(c); draft = attach_intake(c, service, create_intake(c, service))
    service.recognize(c.actors['employee'], draft['id'], draft['attachments'][0]['id'], IntakeRecognize(expected_version=draft['version'], idempotency_key=uuid4().hex))
    event = create_event(c)
    with pytest.raises(AccountingError, match='额度不足'): extract(c, event)
    assert c.provider.extract_calls == 1
    assert operations.overview(c.manager, c.company['id'])['usage']['exposure'] == '1.000000'


def test_disabled_company_and_revocation_during_send_never_publish_suggestions(ai_case):
    c = ai_case; service = intake(c); draft = attach_intake(c, service, create_intake(c, service))
    c.intelligence.company_settings(c.manager, c.company['id'], False)
    with pytest.raises(AccountingError): service.recognize(c.actors['employee'], draft['id'], draft['attachments'][0]['id'], IntakeRecognize(expected_version=draft['version'], idempotency_key=uuid4().hex))
    assert c.provider.extract_calls == 0
    c.intelligence.company_settings(c.manager, c.company['id'], True)
    c.provider.extract_hook = lambda: c.directory.revoke_company_member(c.manager, c.company['id'], c.actors['employee'].user_id)
    with pytest.raises(AccountingError): service.recognize(c.actors['employee'], draft['id'], draft['attachments'][0]['id'], IntakeRecognize(expected_version=draft['version'], idempotency_key=uuid4().hex))
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT status,result_json FROM intake_runs WHERE intake_id=?', (draft['id'],)).fetchone()['status'] == 'stale'
        assert conn.execute('SELECT state FROM model_costs WHERE company_id=?', (c.company['id'],)).fetchone()[0] == 'uncertain'


def test_http_upload_and_confirm_contract_with_real_file_validation(ai_case):
    c = ai_case; service = intake(c); app = FastAPI(); app.state.intake = service
    app.include_router(build_intake_router(lambda: c.actors['employee']))
    @app.exception_handler(AccountingError)
    async def handler(request: Request, error: AccountingError): return JSONResponse({'detail': str(error)}, status_code=error.status)
    client = TestClient(app)
    context = c.business.context(c.actors['employee']); dept = context['companies'][0]['department_id']
    result = client.post('/api/intake/drafts', json={'company_id': c.company['id'], 'department_id': dept, 'idempotency_key': uuid4().hex})
    assert result.status_code == 201; draft = result.json()
    invalid = client.post(f"/api/intake/drafts/{draft['id']}/attachments?expected_version=1", files={'file': ('fake.png', b'not an image', 'image/png')})
    assert invalid.status_code == 422
    request = confirmed(draft, submit=False).model_dump(); request['payload']['amount'] = 0.3
    assert client.post(f"/api/intake/drafts/{draft['id']}/complete", json=request).status_code == 422


def test_draft_update_keeps_partial_fields_and_concurrent_version_is_rejected(ai_case):
    c = ai_case; service = intake(c); draft = create_intake(c, service)
    request = IntakeUpdate(expected_version=draft['version'], template_id='daily_cashflow', fields={'description': '日期和金额待补', 'direction': 'income', 'payment_status': 'paid'})
    latest = service.update(c.actors['employee'], draft['id'], request)
    assert latest['fields']['amount'] == '' and latest['template_id'] == 'daily_cashflow'
    with pytest.raises(AccountingError, match='已更新'): service.update(c.actors['employee'], draft['id'], request)
    with c.db.transaction(actor=c.manager) as conn: assert conn.execute('SELECT count(*) FROM business_events WHERE company_id=?', (c.company['id'],)).fetchone()[0] == 0


def test_completion_saved_without_attachment_requires_final_valid_payload(ai_case):
    c = ai_case; service = intake(c); draft = create_intake(c, service)
    event = service.complete(c.actors['employee'], draft['id'], confirmed(draft, submit=False))['event']
    assert event['status'] == 'draft' and not event['attachments']
    with pytest.raises(AccountingError, match='已经保存'):
        service.update(c.actors['employee'], draft['id'], IntakeUpdate(expected_version=1, template_id='reimbursement', fields={'amount': '9.00'}))


def test_duplicate_upload_returns_original_attachment_without_version_churn(ai_case):
    c = ai_case; service = intake(c); draft = attach_intake(c, service, create_intake(c, service))
    duplicate = attach_intake(c, service, draft)
    assert duplicate['version'] == draft['version'] and duplicate['attachments'] == draft['attachments']


def test_ocr_evidence_findings_survive_transfer_and_never_enter_validated_cache(ai_case):
    c = ai_case; c.provider.document['evidence'] = []
    service = intake(c); draft = attach_intake(c, service, create_intake(c, service))
    result = service.recognize(c.actors['employee'], draft['id'], draft['attachments'][0]['id'], IntakeRecognize(expected_version=draft['version'], idempotency_key=uuid4().hex))
    assert any(item['code'] == 'document_1_evidence_gross_amount' for item in result['checks'])
    event = service.complete(c.actors['employee'], draft['id'], confirmed(draft))['event']
    detail = c.intelligence.reconcile(c.actors['finance'], event['id'])
    assert any(item['code'] == 'document_1_evidence_gross_amount' and not item['resolved'] for item in detail['findings'])
    with c.db.transaction(actor=c.actors['employee']) as conn:
        if c.db.is_postgres:
            assert conn.execute('SELECT accounting.read_document_extraction(?,?)', (draft['attachments'][0]['id'], config_key(c.provider.settings, draft['attachments'][0]['media_type']))).fetchone()[0] is None
        else:
            assert conn.execute('SELECT count(*) FROM document_extractions WHERE company_id=?', (c.company['id'],)).fetchone()[0] == 0
