"""Recovery, evidence and input-generation invariants using real HTTP/SQLite."""
import copy
import json
from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.legacy_migration.worker import MigrationWorker
from test_stage13_migration_api import (assert_response, client, confirmation,
                                        detail, mapping, prepare_http, ready,
                                        run_phase, upload)


def test_invalid_original_inspection_leaves_no_unreferenced_private_blob(client):
    c = prepare_http(client)
    root = client.app.state.attachment_store.root
    before = {p.relative_to(root) for p in root.rglob('*') if p.is_file()}
    response = client.post(c.path + '/files', headers=c.owner,
        data={'category': 'package', 'mapping_json': '{}',
              'expected_version': detail(c)['project']['version'],
              'idempotency_key': uuid4().hex},
        files={'file': ('SYNTHETIC-invalid.json', b'{broken-json', 'application/json')})
    assert response.status_code == 422
    assert {p.relative_to(root) for p in root.rglob('*') if p.is_file()} == before
    assert detail(c)['files'] == []


def test_migration_task_is_exact_project_with_current_independent_acceptance_authority(client):
    c = prepare_http(client)
    task = assert_response(client.get('/api/workspace/tasks?kind=migration', headers=c.owner))
    assert task['pagination']['total'] == 1
    assert task['tasks'][0]['ref'] == c.project and task['tasks'][0]['book_id'] == c.book['id']
    assert task['tasks'][0]['action_label'] == '继续准备资料'
    _, report = ready(c)
    assert assert_response(client.get('/api/workspace/tasks?kind=migration', headers=c.owner))['tasks'] == []
    reviewer = assert_response(client.get('/api/workspace/tasks?kind=migration', headers=c.finance))
    assert reviewer['tasks'][0]['action_label'] == '独立验收'
    assert_response(client.post(c.path + '/accept', headers=c.finance, json=confirmation(c, report)), 201)
    assert assert_response(client.get('/api/workspace/tasks?kind=migration', headers=c.owner))['tasks'][0]['action_label'] == '确认接管'
    assert_response(client.post(c.path + '/activate', headers=c.owner, json=confirmation(c, report)), 201)
    assert assert_response(client.get('/api/workspace/tasks?kind=migration&bucket=all', headers=c.owner))['tasks'] == []


def test_original_corruption_blocks_download_acceptance_and_never_publishes_cutover(client):
    c = prepare_http(client)
    original, report = ready(c)
    with c.db.transaction(actor=c.manager) as conn:
        key = conn.execute('SELECT storage_key FROM migration_files WHERE id=?', (original['file']['id'],)).fetchone()[0]
    client.app.state.attachment_store._path(key).write_bytes(b'SYNTHETIC-original-corruption')
    response = client.get(c.path + '/files/' + original['file']['id'], headers=c.owner)
    assert response.status_code == 409 and '校验' in response.text
    assert client.post(c.path + '/accept', headers=c.finance, json=confirmation(c, report)).status_code == 409
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT count(*) FROM migration_acceptances WHERE project_id=?', (c.project,)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM migration_cutovers WHERE project_id=?', (c.project,)).fetchone()[0] == 0


def test_current_business_arriving_after_report_prevents_dirty_target_initialization(client):
    c = prepare_http(client)
    _, report = ready(c)
    assert_response(client.post('/api/books/' + c.book['id'] + '/imports', headers=c.owner, json={'source_name': 'SYNTHETIC-当期新银行流水', 'rows': [{'external_id': uuid4().hex, 'date': '2026-10-03', 'description': '当前业务不可覆盖', 'counterparty': 'SYNTHETIC-企业', 'amount': '0.01', 'direction': 'income', 'evidence_ref': 'SYNTHETIC-回单'}]}))
    assert_response(client.post(c.path + '/accept', headers=c.finance, json=confirmation(c, report)), 201)
    response = client.post(c.path + '/activate', headers=c.owner, json=confirmation(c, report))
    assert response.status_code == 409
    assert '新业务' in response.text or '目标月份' in response.text
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT count(*) FROM migration_cutovers WHERE project_id=?', (c.project,)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM sources WHERE book_id=?', (c.book['id'],)).fetchone()[0] == 1


def test_column_profile_replacement_preserves_original_and_invalidates_previous_report(client):
    c = prepare_http(client)
    original, report = ready(c)
    previous_body = confirmation(c, report)
    response = client.post(c.path + '/files/' + original['file']['id'] + '/remap', headers=c.owner, json={'expected_version': detail(c)['project']['version'], 'reason': '重新确认原始转换规则，必须重验', 'idempotency_key': uuid4().hex, 'mapping': {'confirmed': True, 'date_format': 'ymd'}})
    replaced = assert_response(response, 201)
    assert replaced['file']['id'] != original['file']['id']
    changed = detail(c)
    assert any(row['id'] == original['file']['id'] and row['superseded'] for row in changed['files'])
    assert client.get(c.path + '/report', headers=c.finance).status_code == 409
    assert client.post(c.path + '/accept', headers=c.finance, json=previous_body).status_code == 409
    assert client.get(c.path + '/files/' + original['file']['id'], headers=c.finance).status_code == 200
    run_phase(c, 'parse')
    run_phase(c, 'validate')
    fresh = assert_response(client.get(c.path + '/report', headers=c.finance))
    assert fresh['validation_id'] != report['validation_id'] and fresh['basis_token'] != report['basis_token']
    assert fresh['report']['blocking_count'] == 0


def test_same_old_voucher_identity_with_changed_amount_is_blocking_not_overwritten(client):
    c = prepare_http(client)
    other = copy.deepcopy(next(row for row in c.package['records'] if row['kind'] == 'voucher'))
    other['payload']['lines'][0]['debit'] = '0.31'
    other['payload']['lines'][1]['credit'] = '0.31'
    c.package['records'].append(other)
    c.package['controls'].update(voucher_line_count=4, posted_voucher_count=2, posted_debit='0.61', posted_credit='0.61')
    c.package['controls']['record_counts']['voucher'] = 2
    _, report = ready(c)
    assert report['report']['blocking_count'] > 0
    assert 'DUPLICATE_CONFLICT' in {issue['code'] for issue in report['report']['issues']}
    records = assert_response(client.get(c.path + '/records?kind=voucher', headers=c.owner))
    assert records['pagination']['total'] == 1
    assert records['records'][0]['payload']['lines'][0]['debit'] == '0.30'
    assert client.post(c.path + '/accept', headers=c.finance, json=confirmation(c, report)).status_code == 409


def test_backup_includes_every_historical_original_and_restores_exact_bytes(client, tmp_path):
    c = prepare_http(client)
    original, _ = ready(c)
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'backup_accounting.py'
    spec = spec_from_file_location('stage13_actual_recovery', script)
    recovery = module_from_spec(spec)
    spec.loader.exec_module(recovery)
    target, restored = tmp_path / 'backup', tmp_path / 'restored'
    manifest = recovery.backup(c.db.path, client.app.state.attachment_store.root, target)
    with c.db.transaction(actor=c.manager) as conn:
        blob = dict(conn.execute('SELECT storage_key,sha256,size_bytes FROM migration_files WHERE id=?', (original['file']['id'],)).fetchone())
    original_path = 'originals/' + blob['storage_key']
    assert any(entry['path'] == original_path and entry['sha256'] == blob['sha256'] and entry['size_bytes'] == blob['size_bytes'] for entry in manifest['files'])
    receipt = recovery.restore(target, restored)
    assert receipt['serving_enabled'] is False
    assert (restored / original_path).read_bytes() == json.dumps(c.package, ensure_ascii=False).encode()
    assert recovery.verify(target) == manifest


def test_real_worker_250_record_checkpoint_survives_expired_lease_and_restart(client, monkeypatch):
    c = prepare_http(client)
    # 300 independent synthetic vouchers exercise actual bounded publication,
    # including immutable rows and a persisted checkpoint at the first 250.
    base_voucher = copy.deepcopy(next(row for row in c.package['records'] if row['kind'] == 'voucher'))
    c.package['records'] = [row for row in c.package['records'] if row['kind'] != 'voucher']
    for index in range(300):
        voucher = copy.deepcopy(base_voucher)
        voucher['external_id'] = voucher['payload']['external_id'] = 'SYNTHETIC-V-'+str(index)
        voucher['payload']['number'] = str(index+1)
        c.package['records'].append(voucher)
    c.package.pop('controls')  # No invented source control totals in this recovery test.
    upload(c)
    version = detail(c)['project']['version']
    assert_response(client.post(c.path + '/parse', headers=c.owner, json={'expected_version': version, 'reason': '明确模拟解析中断和恢复', 'idempotency_key': uuid4().hex}), 202)
    worker = c.worker
    job = worker.claim()
    assert job and job['project_id'] == c.project
    original_connect = c.db.connect
    class CrashAfterDurableCheckpoint:
        def __init__(self, connection):
            self.connection, self.crash_after_commit = connection, False
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def execute(self, sql, *args, **kwargs):
            result = self.connection.execute(sql, *args, **kwargs)
            if sql.lstrip().upper().startswith('UPDATE MIGRATION_JOBS SET CHECKPOINT_JSON') and args[0][1] == 250:
                self.crash_after_commit = True
            return result
        def commit(self):
            self.connection.commit()
            if self.crash_after_commit:
                raise SystemExit('SYNTHETIC process death after first actual chunk committed')
    # The production worker writes all original records and its checkpoint.
    # Inject only the process-death boundary after the real commit succeeds.
    with monkeypatch.context() as patch:
        patch.setattr(c.db, 'connect', lambda: CrashAfterDurableCheckpoint(original_connect()))
        with pytest.raises(SystemExit):
            worker.execute(job)
    with c.db.transaction(write=True, actor=c.manager) as conn:
        saved = conn.execute('SELECT status,checkpoint_json FROM migration_jobs WHERE id=?', (job['id'],)).fetchone()
        assert saved['status'] == 'running' and json.loads(saved['checkpoint_json'])['next'] == 250
        assert conn.execute('SELECT count(*) FROM migration_records WHERE job_id=?', (job['id'],)).fetchone()[0] == 250
    restarted = MigrationWorker(client.app.state.legacy_migration)
    elapsed = datetime.now(timezone.utc) + timedelta(seconds=121)
    class ElapsedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return elapsed.astimezone(tz) if tz else elapsed.replace(tzinfo=None)
    # Advance only the test worker clock; no direct lease edits or guard bypass.
    with monkeypatch.context() as patch:
        patch.setattr('app.legacy_migration.worker.now', lambda: elapsed.isoformat())
        patch.setattr('app.legacy_migration.worker.datetime', ElapsedClock)
        resumed = restarted.claim()
        assert resumed['id'] == job['id'] and resumed['execution_user_id'] == job['execution_user_id']
        assert json.loads(resumed['checkpoint_json'])['next'] == 250
        restarted.execute(resumed)
    count = len(c.package['records'])
    with c.db.transaction(actor=c.manager) as conn:
        result = conn.execute('SELECT status,attempt_count,processed_count FROM migration_jobs WHERE id=?', (job['id'],)).fetchone()
        assert result['status'] == 'succeeded' and result['attempt_count'] == 2
        assert result['processed_count'] == count
        assert conn.execute('SELECT count(*) FROM migration_records WHERE job_id=?', (job['id'],)).fetchone()[0] == count
        assert conn.execute('SELECT count(DISTINCT id) FROM migration_records WHERE job_id=?', (job['id'],)).fetchone()[0] == count


def test_worker_fair_dispatches_another_enterprise_before_second_file_of_first(client):
    first, second = prepare_http(client), prepare_http(client)
    upload(first)
    accounts = {'schema': 'nexus.legacy/v1', 'context': first.package['context'], 'records': [row for row in first.package['records'] if row['kind'] == 'account']}
    upload(first, json.dumps(accounts,ensure_ascii=False).encode(), 'SYNTHETIC-另一个文件.json')
    upload(second)
    for c in (first,second):
        assert_response(client.post(c.path+'/parse',headers=c.owner,json={'expected_version':detail(c)['project']['version'],'reason':'验证多企业公平调度','idempotency_key':uuid4().hex}),202)
    worker = first.worker
    job_a = worker.claim()
    assert job_a
    worker.execute(job_a)
    job_b = worker.claim()
    assert job_b and job_b['company_id'] != job_a['company_id']
    worker.execute(job_b)
    while worker.run_once():
        pass
    with first.db.transaction(actor=first.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM migration_jobs WHERE status IN ('queued','running','retry_wait')").fetchone()[0] == 0


def test_lost_real_commit_ack_preserves_original_and_same_key_retry_is_idempotent(client, monkeypatch):
    from contextlib import contextmanager
    import hashlib
    import sqlite3
    c = prepare_http(client)
    original_transaction = c.db.transaction
    create_key = 'SYNTHETIC-lost-ack-'+uuid4().hex
    raw = json.dumps(c.package, ensure_ascii=False).encode()
    request = {'category':'package','mapping_json':'{}','expected_version':detail(c)['project']['version'],'idempotency_key':create_key}
    fired = False

    @contextmanager
    def durable_commit_then_lose_ack(*args, **kwargs):
        nonlocal fired
        with original_transaction(*args, **kwargs) as conn:
            yield conn
        # This executes after the actual Database.transaction.__exit__ commits.
        # Do not forge responses, edit evidence rows or suppress native guards.
        if kwargs.get('write') and not fired:
            with original_transaction(actor=c.manager) as check:
                exists = check.execute('SELECT 1 FROM migration_files WHERE project_id=? AND create_key=?', (c.project,create_key)).fetchone()
            if exists:
                fired = True
                raise sqlite3.OperationalError('SYNTHETIC acknowledgement lost after durable commit')
    with monkeypatch.context() as patch:
        patch.setattr(c.db, 'transaction', durable_commit_then_lose_ack)
        response = client.post(c.path+'/files',headers=c.owner,data=request,files={'file':('SYNTHETIC-lost-ack.json',raw,'application/json')})
    assert fired and response.status_code == 503
    with c.db.transaction(actor=c.manager) as conn:
        committed = dict(conn.execute('SELECT id,storage_key,sha256 FROM migration_files WHERE project_id=? AND create_key=?', (c.project,create_key)).fetchone())
        assert conn.execute('SELECT count(*) FROM migration_files WHERE project_id=?', (c.project,)).fetchone()[0] == 1
    assert client.app.state.attachment_store._path(committed['storage_key']).read_bytes() == raw
    assert committed['sha256'] == hashlib.sha256(raw).hexdigest()
    retried = assert_response(client.post(c.path+'/files',headers=c.owner,data=request,files={'file':('SYNTHETIC-lost-ack.json',raw,'application/json')}),201)
    assert retried['file']['id'] == committed['id']
    assert client.get(c.path+'/files/'+committed['id'],headers=c.finance).content == raw
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT count(*) FROM migration_files WHERE project_id=?', (c.project,)).fetchone()[0] == 1
    assert len([path for path in client.app.state.attachment_store.root.rglob('*') if path.is_file()]) == 1


def test_legacy_original_store_injection_accepts_private_png_attachment(client):
    import hashlib
    import io
    from PIL import Image
    from app.legacy_migration.storage import LegacyOriginalStore
    c = prepare_http(client)
    client.app.state.legacy_migration.storage = LegacyOriginalStore(client.app.state.attachment_store.root)
    content = io.BytesIO()
    Image.new('RGB',(24,18),'white').save(content,'PNG')
    raw = content.getvalue()
    saved = upload(c,raw,'SYNTHETIC-旧账附件.png','attachment')
    assert saved['file']['sha256'] == hashlib.sha256(raw).hexdigest()
    original = client.get(c.path+'/files/'+saved['file']['id'],headers=c.finance)
    assert original.status_code == 200 and original.content == raw
    assert original.headers['content-type'] == 'image/png' and original.headers['cache-control'] == 'no-store'
    response = client.post(c.path+'/parse',headers=c.owner,json={'expected_version':detail(c)['project']['version'],'reason':'图片不是结构化账务包','idempotency_key':uuid4().hex})
    assert response.status_code == 409


def test_validation_report_commit_before_finish_recovers_same_immutable_report(client, monkeypatch):
    from contextlib import contextmanager
    c = prepare_http(client)
    upload(c)
    run_phase(c,'parse')
    assert_response(client.post(c.path+'/mapping',headers=c.owner,json=mapping(c)),201)
    version = detail(c)['project']['version']
    assert_response(client.post(c.path+'/validate',headers=c.owner,json={'expected_version':version,'reason':'验证报告提交后进程中断的恢复','idempotency_key':uuid4().hex}),202)
    job = c.worker.claim()
    assert job and job['phase'] == 'validate' and job['project_id'] == c.project
    original_transaction = c.db.transaction
    durable_report_id = None

    @contextmanager
    def report_commit_then_process_death(*args,**kwargs):
        nonlocal durable_report_id
        with original_transaction(*args,**kwargs) as conn:
            yield conn
        if kwargs.get('write') and durable_report_id is None:
            with original_transaction(actor=c.manager) as check:
                report = check.execute('SELECT id FROM migration_validations WHERE job_id=?',(job['id'],)).fetchone()
            if report:
                durable_report_id = report[0]
                raise SystemExit('SYNTHETIC death after immutable validation committed and before finish')
    with monkeypatch.context() as patch:
        patch.setattr(c.db,'transaction',report_commit_then_process_death)
        with pytest.raises(SystemExit):
            c.worker.execute(job)
    assert durable_report_id
    with c.db.transaction(actor=c.manager) as conn:
        pending = conn.execute('SELECT status,checkpoint_json FROM migration_jobs WHERE id=?',(job['id'],)).fetchone()
        assert pending['status']=='running'
        assert json.loads(pending['checkpoint_json'])['validation_id']==durable_report_id
    elapsed = datetime.now(timezone.utc)+timedelta(seconds=121)
    class ElapsedClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return elapsed.astimezone(tz) if tz else elapsed.replace(tzinfo=None)
    restarted = MigrationWorker(client.app.state.legacy_migration)
    with monkeypatch.context() as patch:
        patch.setattr('app.legacy_migration.worker.now',lambda:elapsed.isoformat())
        patch.setattr('app.legacy_migration.worker.datetime',ElapsedClock)
        resumed = restarted.claim()
        assert resumed['id']==job['id'] and resumed['attempt_count']==2
        restarted.execute(resumed)
    with c.db.transaction(actor=c.manager) as conn:
        finished = conn.execute('SELECT status FROM migration_jobs WHERE id=?',(job['id'],)).fetchone()
        assert finished[0]=='succeeded'
        assert conn.execute('SELECT count(*) FROM migration_validations WHERE job_id=?',(job['id'],)).fetchone()[0]==1
        assert conn.execute('SELECT id FROM migration_validations WHERE job_id=?',(job['id'],)).fetchone()[0]==durable_report_id
    report = assert_response(client.get(c.path+'/report',headers=c.finance))
    assert report['validation_id']==durable_report_id and report['report']['blocking_count']==0
    assert detail(c)['project']['status']=='ready'


@pytest.mark.parametrize('target_code',['1002','2202','6602.02'])
def test_existing_target_category_direction_or_auxiliary_mismatch_blocks_acceptance(client,target_code):
    c = prepare_http(client)
    upload(c)
    run_phase(c,'parse')
    requested = mapping(c)
    # Keep distinct targets so this tests semantic compatibility rather than
    # accidentally passing because of the unrelated many-to-one merge guard.
    requested['accounts'][0].update(target_code='1002.SYNTHETIC',action='create')
    requested['accounts'][2].update(target_code=target_code,action='use_existing')
    assert_response(client.post(c.path+'/mapping',headers=c.owner,json=requested),201)
    run_phase(c,'validate')
    report = assert_response(client.get(c.path+'/report',headers=c.finance))
    assert report['report']['blocking_count']>0 and not report['report']['coverage']['complete']
    assert any(issue.get('kind')=='account' and '6602.02' in (issue.get('external_id','')+issue.get('message','')) and issue['severity']=='blocking' for issue in report['report']['issues'])
    assert client.post(c.path+'/accept',headers=c.finance,json=confirmation(c,report)).status_code==409


def test_historical_nonposting_parent_remains_archive_only_after_cutover(client):
    c = prepare_http(client)
    for row in c.package['records']:
        if row['kind']=='account' and row['payload']['code']=='6602.02':
            row['payload']['parent_code']='66'
    c.package['records'].append({'kind':'account','external_id':'66','period':'2026-09','payload':{'code':'66','name':'SYNTHETIC-历史汇总父科目','parent_code':'','posting':False,'category':'expense','normal_balance':'debit','auxiliary_requirements':[]}})
    c.package['controls']['record_counts']['account']+=1
    _, report = ready(c)
    assert report['report']['blocking_count']==0
    assert_response(client.post(c.path+'/accept',headers=c.finance,json=confirmation(c,report)),201)
    assert_response(client.post(c.path+'/activate',headers=c.owner,json=confirmation(c,report)),201)
    archived = assert_response(client.get(c.path+'/records?kind=account',headers=c.finance))
    assert any(row['payload']['code']=='66' and row['payload']['posting'] is False for row in archived['records'])
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute("SELECT 1 FROM accounts WHERE book_id=? AND code='66'",(c.book['id'],)).fetchone() is None
