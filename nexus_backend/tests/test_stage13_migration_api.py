"""Real HTTP migration flow with private synthetic originals and explicit roles."""
import copy
import json
import secrets
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.accounting.seed import LOCAL_ACTOR
from app.main import create_app
from app.auth import Identity
from app.accounting.models import FirmMemberGrant, BookMemberGrant
from app.legacy_migration.worker import MigrationWorker
from test_business_api import auth
from test_stage13_legacy_settlements import drain_project_phase


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'migration-api.db', seed=False, attachment_root=tmp_path / 'private', queue_worker_enabled=False), base_url='http://127.0.0.1', client=('127.0.0.1', 1351)) as value:
        yield value


def assert_response(response, expected=200):
    assert response.status_code == expected, response.text
    return response.json()


def prepare_http(client, package=None, source_product='generic'):
    owner, finance, employee = [auth(client, role) for role in ('accountant', 'finance', 'employee')]
    # Verified local personas are explicit synthetic identities. Register through
    # DirectoryService, then grant actual company/book/project roles via HTTP.
    for headers in (finance, employee):
        identity = client.app.state.token_users[headers['Authorization'][7:]]
        client.app.state.directory.register_identity(identity)
    book = assert_response(client.post('/api/books', headers=owner, json={'name': 'SYNTHETIC-旧账接管企业', 'period': '2026-10'}), 201)
    assert_response(client.put('/api/companies/' + book['company_id'] + '/members', headers=owner, json={'email': 'finance@demo.example.invalid', 'role': 'finance'}))
    # Customer finance may review the source migration project, but must not
    # receive the firm's formal-voucher review capability.
    assert_response(client.put('/api/books/' + book['id'] + '/members/local-business-finance', headers=owner, json={'role': 'editor'}))
    reviewer_id = 'legacy-http-reviewer-' + uuid4().hex
    reviewer_identity = Identity(reviewer_id, reviewer_id + '@example.invalid', '虚构独立复核员')
    client.app.state.directory.register_identity(reviewer_identity)
    client.app.state.directory.grant_firm_member(LOCAL_ACTOR, FirmMemberGrant(email=reviewer_identity.email, role='reviewer'))
    client.app.state.directory.grant_book_member(LOCAL_ACTOR, book['id'], reviewer_id, BookMemberGrant(role='reviewer'))
    token = secrets.token_urlsafe(32)
    client.app.state.tokens[token] = time.monotonic() + 7200
    client.app.state.token_users[token] = reviewer_identity
    reviewer = {'Authorization': 'Bearer ' + token}
    base = '/api/books/' + book['id'] + '/legacy-migrations'
    template_response = client.get(base + '/template', headers=owner, params={'external_book_id': 'SYNTHETIC-HTTP-BOOK', 'history_start': '2026-09-01', 'history_end': '2026-09-30', 'cutover_date': '2026-10-01'})
    template = assert_response(template_response)
    assert template['synthetic'] is True
    assert '不是T+' in template['notice']
    assert 'attachment;' in template_response.headers['content-disposition']
    content = copy.deepcopy(package or template)
    context = content['context']
    project = assert_response(client.post(base, headers=owner, json={'target_book_id': book['id'], 'source_product': source_product, 'product_version': 'SYNTHETIC-中性 v1', 'external_book_id': context['external_book_id'], 'source_system_key': 'SYNTHETIC-'+uuid4().hex, 'history_start': context['history_start'], 'history_end': context['history_end'], 'cutover_date': context['cutover_date'], 'reason': '隔离环境合成旧账验收', 'idempotency_key': uuid4().hex}), 201)
    project_id = project['project']['id']
    path = base + '/' + project_id
    assert_response(client.post(path + '/access', headers=owner, json={'user_id': 'local-business-finance', 'role': 'viewer', 'reason': '客户核对旧账范围'}))
    assert_response(client.post(path + '/access', headers=owner, json={'user_id': reviewer_id, 'role': 'reviewer', 'reason': '事务所独立审核合成旧账'}))
    c = SimpleNamespace(client=client, owner=owner, finance=finance, reviewer=reviewer, reviewer_id=reviewer_id, employee=employee, book=book, base=base, path=path, project=project_id, package=content, db=client.app.state.service.db, manager=LOCAL_ACTOR, worker=MigrationWorker(client.app.state.legacy_migration))
    return c


def detail(c, headers=None):
    return assert_response(c.client.get(c.path, headers=headers or c.owner))


def upload(c, data=None, filename='SYNTHETIC-旧账.json', category='package', mapping=None, replaces=None):
    response = c.client.post(c.path + '/files', headers=c.owner, data={'category': category, 'mapping_json': json.dumps(mapping or {}), 'expected_version': detail(c)['project']['version'], 'idempotency_key': uuid4().hex, **({'replaces_file_id': replaces} if replaces else {})}, files={'file': (filename, data or json.dumps(c.package, ensure_ascii=False).encode(), 'application/json' if filename.endswith('.json') else 'text/csv')})
    return assert_response(response, 201)


def run_phase(c, phase):
    version = detail(c)['project']['version']
    response = c.client.post(c.path + '/' + phase, headers=c.owner, json={'expected_version': version, 'reason': '明确执行合成' + phase, 'idempotency_key': uuid4().hex})
    assert_response(response, 202)
    drain_project_phase(c, c.worker, phase, version)
    return detail(c)


def mapping(c):
    return {'expected_version': detail(c)['project']['version'], 'idempotency_key': uuid4().hex, 'reason': '确认合成科目及稳定辅助档案', 'accounts': [{'source_code': '1002', 'target_code': '1002', 'action': 'use_existing'}, {'source_code': '2001', 'target_code': '2001', 'action': 'create'}, {'source_code': '6602.02', 'target_code': '6602.HIST', 'action': 'create'}], 'auxiliaries': [{'kind': 'counterparty', 'source_code': 'SYNTHETIC-PARTY', 'action': 'create'}]}


def ready(c):
    original = upload(c)
    run_phase(c, 'parse')
    assert_response(c.client.post(c.path + '/mapping', headers=c.owner, json=mapping(c)), 201)
    run_phase(c, 'validate')
    report = assert_response(c.client.get(c.path + '/report', headers=c.finance))
    return original, report


def confirmation(c, report=None):
    report = report or assert_response(c.client.get(c.path + '/report', headers=c.finance))
    return {'expected_version': detail(c)['project']['version'], 'validation_id': report['validation_id'], 'basis_token': report['basis_token'], 'confirmed': True, 'reason': '核对原软件范围、状态、原件、金额及映射', 'idempotency_key': uuid4().hex}


def test_http_create_template_multipart_parse_mapping_independent_accept_activate(client):
    c = prepare_http(client, source_product='chanjet_tplus')
    original, report = ready(c)
    assert report['report']['blocking_count'] == 0
    assert report['report']['coverage']['source_controls_verified'] is True
    assert report['report']['source']['native_vendor_format_verified'] is False
    assert report['report']['source']['source_controls_are_user_supplied'] is True
    body = confirmation(c, report)
    assert client.post(c.path + '/accept', headers=c.owner, json=body).status_code == 403
    assert client.post(c.path + '/accept', headers=c.finance, json=body).status_code == 403
    assert_response(client.post(c.path + '/accept', headers=c.reviewer, json=body), 201)
    active = assert_response(client.post(c.path + '/activate', headers=c.owner, json=confirmation(c, report)), 201)
    assert active['project']['status'] == 'active' and active['cutover']['snapshot']['opening_is_reference']
    assert active['cutover']['snapshot']['automatic_export'] is False
    assert active['vendor_native_verified'] is False and active['official_ledger'] == 'source_software'
    original_id = original['file']['id']
    downloaded = client.get(c.path + '/files/' + original_id, headers=c.finance)
    assert downloaded.status_code == 200 and downloaded.json() == c.package
    assert downloaded.headers['cache-control'] == 'no-store'
    records = assert_response(client.get(c.path + '/records?kind=voucher&limit=1', headers=c.finance))
    assert records['records'][0]['payload']['status'] == 'posted'
    assert records['pagination']['total'] == 1
    with c.db.transaction(actor=c.manager) as conn:
        for table in ('sources', 'drafts', 'exports'):
            assert conn.execute('SELECT count(*) FROM ' + table + ' WHERE book_id=?', (c.book['id'],)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM business_events WHERE company_id=?', (c.book['company_id'],)).fetchone()[0] == 0


def test_http_auth_and_cross_enterprise_history_resources_never_fall_back(client):
    c = prepare_http(client)
    original, _ = ready(c)
    other = assert_response(client.post('/api/books', headers=c.owner, json={'name': 'SYNTHETIC-另一企业', 'period': '2026-10'}), 201)
    wrong = '/api/books/' + other['id'] + '/legacy-migrations/' + c.project
    for suffix in ('', '/files/' + original['file']['id'], '/records', '/issues', '/report'):
        assert client.get(c.path + suffix).status_code == 401
        assert client.get(c.path + suffix, headers=c.employee).status_code in {403, 404}
        assert client.get(wrong + suffix, headers=c.owner).status_code == 404
    assert client.get(c.path + '/records?limit=0', headers=c.owner).status_code == 422


def test_http_column_mapping_unconfirmed_cannot_enqueue_parse(client):
    c = prepare_http(client)
    content = '科目编码,科目名称,末级科目,科目类别,余额方向\n1002,模拟银行,是,资产,借方\n'
    uploaded = upload(c, content.encode(), 'SYNTHETIC-科目.csv', 'accounts')
    inspected = assert_response(client.get(c.path + '/files/' + uploaded['file']['id'] + '/inspection', headers=c.owner))
    assert inspected['headers'] == ['科目编码', '科目名称', '末级科目', '科目类别', '余额方向']
    response = client.post(c.path + '/parse', headers=c.owner, json={'expected_version': detail(c)['project']['version'], 'reason': '不能跳过列确认', 'idempotency_key': uuid4().hex})
    assert response.status_code == 409 and '列映射' in response.text
    response = client.post(c.path + '/files/' + uploaded['file']['id'] + '/remap', headers=c.owner, json={'expected_version': detail(c)['project']['version'], 'reason': '明确确认中文字段、日期及状态', 'idempotency_key': uuid4().hex, 'mapping': {'confirmed': True, 'columns': inspected['suggested_columns']}})
    replaced = assert_response(response, 201)
    assert replaced['file']['id'] != uploaded['file']['id']
    run_phase(c, 'parse')
    assert assert_response(client.get(c.path + '/records?kind=account', headers=c.owner))['pagination']['total'] == 1


def test_http_revocation_prevents_accept_and_original_download(client):
    c = prepare_http(client)
    original, report = ready(c)
    body = confirmation(c, report)
    assert_response(client.delete('/api/books/' + c.book['id'] + '/members/local-business-finance', headers=c.owner))
    assert_response(client.delete('/api/books/' + c.book['id'] + '/members/' + c.reviewer_id, headers=c.owner))
    assert client.post(c.path + '/accept', headers=c.reviewer, json=body).status_code in {403, 404}
    assert client.get(c.path + '/files/' + original['file']['id'], headers=c.finance).status_code in {403, 404}
    assert detail(c)['project']['status'] == 'ready'


def test_http_source_controls_difference_is_blocking_and_no_activation(client):
    c = prepare_http(client)
    c.package['controls']['posted_debit'] = '0.31'
    _, report = ready(c)
    assert report['report']['blocking_count'] > 0
    assert 'SOURCE_CONTROL_MISMATCH' in {item['code'] for item in report['report']['issues']}
    assert client.post(c.path + '/accept', headers=c.reviewer, json=confirmation(c, report)).status_code == 409
    assert not detail(c)['can_activate']


def test_http_mixed_external_books_blocks_even_with_mapping_omitted(client):
    c = prepare_http(client)
    content = '科目编码,科目名称,末级科目,科目类别,余额方向,账套编码\n1002,模拟银行,是,资产,借方,ANOTHER-BOOK\n'
    upload(c, content.encode(), 'SYNTHETIC-混账科目.csv', 'accounts', {'confirmed': True, 'columns': {'code': 0, 'name': 1, 'posting': 2, 'category': 3, 'normal_balance': 4}})
    run_phase(c, 'parse')
    run_phase(c, 'validate')
    report = assert_response(client.get(c.path + '/report', headers=c.owner))['report']
    assert 'ROW_SCOPE_MISMATCH' in {item['code'] for item in report['issues']}
    assert report['blocking_count'] > 0 and not report['coverage']['complete']


def test_http_json_record_scope_conflicts_block_acceptance_without_discarding_generic_vendor_fields(client):
    c=prepare_http(client)
    c.package['records'][0]['external_book_id']='ANOTHER-BOOK'
    c.package['records'][1]['payload']['company_name']='UNVERIFIED-OWNER'
    c.package['records'][2]['payload'].update(vendor_name='SYNTHETIC-供应商',custom_note='一般扩展字段不作为所属企业')
    _,report=ready(c)
    issue_codes={item['code'] for item in report['report']['issues']}
    assert {'ROW_SCOPE_MISMATCH','ORGANIZATION_SCOPE_UNVERIFIED'} <= issue_codes
    assert report['report']['blocking_count']>0 and not report['report']['coverage']['complete']
    assert client.post(c.path+'/accept',headers=c.reviewer,json=confirmation(c,report)).status_code==409
    records=assert_response(client.get(c.path+'/records?kind=account',headers=c.owner))['records']
    assert any(row['payload']['code']=='6602.02' for row in records)
    assert not any(row['payload']['code']=='1002' for row in records)
