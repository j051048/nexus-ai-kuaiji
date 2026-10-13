import json
import pytest
from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate, ExportRequest
from app.accounting.seed import LOCAL_ACTOR
from app.integrations.models import BridgeConfiguration, BridgeExport, BridgeImport, BridgeImportConfirm
from app.integrations.service import FinancialBridgeService
from test_accounting import service, book, make_draft, approve, add_actor


def config(**overrides):
    return BridgeConfiguration(**{'expected_version': 0, 'external_book_id': 'tplus-demo', 'accounts': [{'local_code': '1002', 'external_code': '100201'}, {'local_code': '6602.02', 'external_code': '660202'}], 'reason': '试点映射核验', 'idempotency_key': 'config-stage10', **overrides})


def reference(bridge, book_id, **overrides):
    value = bridge.template(LOCAL_ACTOR, book_id)
    value['records'][0].update({'external_id': 'external-account-1', **overrides})
    return value


def request(body, key='import-stage10'):
    return BridgeImport(format='json', content=json.dumps(body, ensure_ascii=False), reason='客户导出参考资料', idempotency_key=key)


def test_reference_preview_confirm_replay_does_not_create_sources(service, book):
    bridge = FinancialBridgeService(service.db)
    bridge.configure(LOCAL_ACTOR, book, config())
    original = reference(bridge, book)
    preview = bridge.preview_import(LOCAL_ACTOR, book, request(original))
    result = bridge.confirm_import(LOCAL_ACTOR, book, preview['id'], BridgeImportConfirm(fingerprint=preview['fingerprint']))
    assert result['state'] == 'confirmed'
    assert bridge.confirm_import(LOCAL_ACTOR, book, preview['id'], BridgeImportConfirm(fingerprint=preview['fingerprint'])) == result
    repeated = bridge.preview_import(LOCAL_ACTOR, book, request(original, 'import-stage10-again'))
    assert repeated['duplicates'] == 1
    assert service.get_book(LOCAL_ACTOR, book)['sources'] == []
    with service.db.transaction() as conn:
        assert conn.execute('SELECT COUNT(*) FROM bridge_reference_records').fetchone()[0] == 1
    original['records'][0]['name'] = '被改写的外部名称'
    conflict = bridge.preview_import(LOCAL_ACTOR, book, request(original, 'import-stage10-conflict'))
    assert conflict['conflicts'] == 1
    with pytest.raises(AccountingError, match='冲突'):
        bridge.confirm_import(LOCAL_ACTOR, book, conflict['id'], BridgeImportConfirm(fingerprint=conflict['fingerprint']))


def test_cross_company_files_and_revocation_rejected(service, book):
    bridge = FinancialBridgeService(service.db)
    bridge.configure(LOCAL_ACTOR, book, config())
    payload = reference(bridge, book)
    payload['company_id'] = 'another-company'
    with pytest.raises(AccountingError, match='范围'):
        bridge.preview_import(LOCAL_ACTOR, book, request(payload))
    stranger = add_actor(service)
    with pytest.raises(AccountingError):
        bridge.overview(stranger, book)
    editor = add_actor(service, user_id='bridge-editor', firm_id=LOCAL_ACTOR.firm_id, role='operator', book_id=book)
    with pytest.raises(AccountingError):
        bridge.configure(editor, book, config(idempotency_key='editor-config'))


def test_changed_mapping_invalidates_preview_and_external_identity_cannot_rebind(service, book):
    bridge = FinancialBridgeService(service.db)
    bridge.configure(LOCAL_ACTOR, book, config())
    payload = reference(bridge, book)
    preview = bridge.preview_import(LOCAL_ACTOR, book, request(payload))
    bridge.configure(LOCAL_ACTOR, book, config(expected_version=1, idempotency_key='config-stage10-v2'))
    with pytest.raises(AccountingError, match='变化'):
        bridge.confirm_import(LOCAL_ACTOR, book, preview['id'], BridgeImportConfirm(fingerprint=preview['fingerprint']))
    preview = bridge.preview_import(LOCAL_ACTOR, book, request(payload, 'new-preview-stage10'))
    bridge.confirm_import(LOCAL_ACTOR, book, preview['id'], BridgeImportConfirm(fingerprint=preview['fingerprint']))
    with pytest.raises(AccountingError, match='不能重新绑定'):
        bridge.configure(LOCAL_ACTOR, book, config(expected_version=2, external_book_id='new-external-book', idempotency_key='bad-rebinding'))


def test_frozen_export_package_maps_once_and_live_dispatch_disabled(service, book):
    bridge = FinancialBridgeService(service.db)
    bridge.configure(LOCAL_ACTOR, book, config())
    draft = make_draft(service, book)
    approve(service, book, draft)
    exported = service.export(LOCAL_ACTOR, book, ExportRequest(draft_ids=[draft['id']], idempotency_key='bridge-source-export'))
    req = BridgeExport(export_id=exported['id'], configuration_version=1, reason='交付核验', idempotency_key='bridge-intent-stage10')
    intent = bridge.prepare_export(LOCAL_ACTOR, book, req)
    assert bridge.prepare_export(LOCAL_ACTOR, book, req) == intent
    assert intent['state'] == 'awaiting_adapter'
    assert {x['account_code'] for x in intent['package']['vouchers'][0]['lines']} == {'100201', '660202'}
    with pytest.raises(AccountingError, match='未启用'):
        bridge.dispatch(LOCAL_ACTOR, book, intent['id'])
    bridge.configure(LOCAL_ACTOR, book, config(expected_version=1, idempotency_key='config-later-version'))
    assert bridge.get_outbox(LOCAL_ACTOR, book, intent['id']) == intent
    with pytest.raises(AccountingError, match='冻结交付'):
        bridge.prepare_export(LOCAL_ACTOR, book, req.model_copy(update={'configuration_version': 2}))


def test_missing_mapping_blocks_package_and_no_partial_intent(service, book):
    bridge = FinancialBridgeService(service.db)
    bridge.configure(LOCAL_ACTOR, book, config(accounts=[{'local_code': '1002', 'external_code': '1002'}]))
    draft = make_draft(service, book)
    approve(service, book, draft)
    exported = service.export(LOCAL_ACTOR, book, ExportRequest(draft_ids=[draft['id']], idempotency_key='missing-map-export'))
    with pytest.raises(AccountingError, match='缺少外部映射'):
        bridge.prepare_export(LOCAL_ACTOR, book, BridgeExport(export_id=exported['id'], configuration_version=1, reason='测试', idempotency_key='missing-map-intent'))
    assert bridge.overview(LOCAL_ACTOR, book)['outbox'] == []
