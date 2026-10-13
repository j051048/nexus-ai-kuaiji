"""Real restricted SQL checks: history never inherits current-month access."""
import json
import os
from uuid import uuid4

import pytest

from app.accounting.database import Database
from test_business_postgres import ADMIN_URL, APP_URL, STAMP, business_case, business_postgres

WORKER_URL = os.getenv('ACCOUNTING_TEST_WORKER_DATABASE_URL')
pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用 PostgreSQL 管理员和受限运行连接')


@pytest.fixture
def migration_case(business_case):
    c = business_case
    with c.db.transaction(actor=c.owner) as conn:
        c.ledger = conn.execute('SELECT ledger_id FROM ledger_periods WHERE book_id=?', (c.book,)).fetchone()[0]
    c.project = uuid4().hex
    with c.db.transaction(write=True, actor=c.owner) as conn:
        conn.execute("INSERT INTO migration_projects(id,firm_id,company_id,ledger_id,book_id,external_book_id,source_system_key,source_product,product_version,history_start,history_end,cutover_date,create_key,create_fingerprint,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,'tplus','T+','标准模板 v1','2026-01-01','2026-09-30','2026-10-01',?,?,?,?,?)", (c.project,c.owner.firm_id,c.company,c.ledger,c.book,'native-'+c.project,c.project,'a'*64,c.owner.user_id,STAMP,STAMP))
        conn.execute("INSERT INTO migration_project_access VALUES(?,?,'manager',?,'明确迁移管理责任',?)", (c.project,c.owner.user_id,c.owner.user_id,STAMP))
    yield c
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.migration_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status IN ('queued','running','retry_wait')", (c.owner.firm_id,))


def _file(c):
    key=uuid4().hex
    with c.db.transaction(write=True,actor=c.owner) as conn:
        conn.execute("INSERT INTO migration_files(id,project_id,firm_id,company_id,category,filename,storage_key,sha256,size_bytes,media_type,create_key,created_by,created_at) VALUES(?,?,?,?,'vouchers','历史凭证.csv',?, ?,10,'text/csv',?,?,?)",(key,c.project,c.owner.firm_id,c.company,key[:2]+'/'+key,'a'*64,key,c.owner.user_id,STAMP))
    return key


def _job(c, file_id=None, phase='parse'):
    key=uuid4().hex
    with c.db.transaction(write=True,actor=c.owner) as conn:
        conn.execute("INSERT INTO migration_jobs(id,project_id,file_id,firm_id,company_id,phase,execution_user_id,expected_version,available_at,request_key,request_fingerprint,created_at,updated_at) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?)",(key,c.project,file_id,c.owner.firm_id,c.company,phase,c.owner.user_id,STAMP,key,'a'*64,STAMP,STAMP))
    return key


def _grant(c, actor, role='reviewer'):
    with c.db.transaction(write=True,actor=c.owner) as conn:
        conn.execute('INSERT INTO migration_project_access VALUES(?,?,?,?,?,?)',(c.project,actor.user_id,role,c.owner.user_id,'独立核对旧账授权',STAMP))


def _worker(c, sql, params=()):
    from psycopg.rows import dict_row
    with c.psycopg.connect(WORKER_URL,row_factory=dict_row) as conn:
        conn.execute('SET LOCAL ROLE accounting_worker')
        return conn.execute(sql,params).fetchall()


def test_native_current_book_grant_does_not_reveal_archive(migration_case):
    c=migration_case; file_id=_file(c)
    for actor in (c.reviewer,c.viewer,c.employee,c.unassigned):
        with c.db.transaction(actor=actor) as conn:
            assert conn.execute('SELECT id FROM migration_projects WHERE id=?',(c.project,)).fetchall()==[]
            assert conn.execute('SELECT id FROM migration_files WHERE id=?',(file_id,)).fetchall()==[]
    _grant(c,c.reviewer)
    with c.db.transaction(actor=c.reviewer) as conn:
        assert conn.execute('SELECT id FROM migration_projects WHERE id=?',(c.project,)).fetchone()[0]==c.project
    with c.db.transaction(write=True,actor=c.owner) as conn:
        conn.execute('DELETE FROM book_access WHERE book_id=? AND user_id=?',(c.book,c.reviewer.user_id))
    with c.db.transaction(actor=c.reviewer) as conn:
        assert conn.execute('SELECT id FROM migration_projects WHERE id=?',(c.project,)).fetchall()==[]


def test_native_project_grant_never_adds_book_permission(migration_case):
    c=migration_case
    with pytest.raises(c.psycopg.Error):
        _grant(c,c.unassigned)
    with c.db.transaction(actor=c.owner) as conn:
        assert not conn.execute('SELECT user_id FROM migration_project_access WHERE project_id=? AND user_id=?',(c.project,c.unassigned.user_id)).fetchall()


def test_native_api_cannot_forge_ready_report_or_lease(migration_case):
    c=migration_case;job=_job(c,_file(c))
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True,actor=c.owner) as conn:
            conn.execute("UPDATE migration_projects SET status='ready' WHERE id=?",(c.project,))
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(actor=c.owner) as conn:
            conn.execute('SELECT lease_token FROM migration_jobs WHERE id=?',(job,)).fetchall()
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(actor=c.owner) as conn:
            conn.execute('SELECT accounting.migration_claim(?)',('api-forgery',)).fetchall()
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True,actor=c.owner) as conn:
            conn.execute('SELECT set_config(\'app.migration_job_id\',?,true),set_config(\'app.migration_token\',?,true)',(job,'fake-token'))
            conn.execute("INSERT INTO migration_records VALUES(?,?,?,?,? ,?,'voucher','old-1','2026-01','{}',?,1,?)",(uuid4().hex,c.project,conn.execute('SELECT file_id FROM migration_jobs WHERE id=?',(job,)).fetchone()[0],job,c.owner.firm_id,c.company,'a'*64,STAMP))


def test_native_originals_and_external_identity_are_immutable(migration_case):
    c=migration_case;file_id=_file(c)
    for sql,params in [("UPDATE migration_files SET sha256=? WHERE id=?",('b'*64,file_id)),('DELETE FROM migration_files WHERE id=?',(file_id,)),("UPDATE migration_projects SET external_book_id='other' WHERE id=?",(c.project,))]:
        with pytest.raises(c.psycopg.Error):
            with c.db.transaction(write=True,actor=c.owner) as conn: conn.execute(sql,params)
    with c.db.transaction(actor=c.owner) as conn:
        assert conn.execute('SELECT sha256 FROM migration_files WHERE id=?',(file_id,)).fetchone()[0]=='a'*64


def test_native_worker_has_no_global_originals_or_history_select(migration_case):
    c=migration_case
    with c.psycopg.connect(ADMIN_URL) as conn:
        for table in ('migration_projects','migration_files','migration_records','migration_validations','migration_external_keys','migration_source_bindings','migration_file_replacements'):
            assert conn.execute('SELECT relforcerowsecurity FROM pg_class WHERE oid=%s::regclass',('accounting.'+table,)).fetchone()[0]
            assert not conn.execute('SELECT has_table_privilege(%s,%s,%s)',('accounting_worker','accounting.'+table,'SELECT')).fetchone()[0]


@pytest.mark.skipif(not WORKER_URL,reason='需要真实独立 worker 连接')
def test_native_real_worker_records_fenced_by_live_lease_and_replacement(migration_case):
    c=migration_case;file_id=_file(c);job_id=_job(c,file_id)
    claimed=_worker(c,'SELECT * FROM accounting.migration_claim(%s)',('stage13-native',))[0]
    assert claimed['id']==job_id
    worker_db=Database(WORKER_URL)
    values=(uuid4().hex,c.project,file_id,job_id,c.owner.firm_id,c.company,'voucher','V001','2026-01',json.dumps({'lines':[]}), 'a'*64,1,STAMP)
    with worker_db.transaction(write=True,actor=c.owner) as conn:
        conn.execute("SELECT set_config('app.migration_job_id',?,true),set_config('app.migration_token',?,true)",(job_id,claimed['lease_token']))
        conn.execute('INSERT INTO migration_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',values)
    new_file=_file(c)
    with c.db.transaction(write=True,actor=c.owner) as conn:
        conn.execute('INSERT INTO migration_file_replacements VALUES(?,?,?,?,?,?,?,?,?,?)',(uuid4().hex,c.project,file_id,new_file,c.owner.firm_id,c.company,uuid4().hex,'重新上传修订原表',c.owner.user_id,STAMP))
    assert _worker(c,'SELECT accounting.migration_check(%s,%s) AS valid',(job_id,claimed['lease_token']))[0]['valid'] is False
    with pytest.raises(c.psycopg.Error):
        with worker_db.transaction(write=True,actor=c.owner) as conn:
            conn.execute("SELECT set_config('app.migration_job_id',?,true),set_config('app.migration_token',?,true)",(job_id,claimed['lease_token']))
            conn.execute('INSERT INTO migration_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(uuid4().hex,*values[1:7],'V002',*values[8:]))
    assert _worker(c,"SELECT accounting.migration_finish(%s,%s,'succeeded') AS finished",(job_id,claimed['lease_token']))[0]['finished']
    with c.db.transaction(actor=c.owner) as conn:
        assert conn.execute('SELECT status FROM migration_jobs WHERE id=?',(job_id,)).fetchone()[0]=='stale'


@pytest.mark.skipif(not WORKER_URL,reason='需要真实独立 worker 连接')
def test_native_report_needs_real_worker_and_independent_fresh_acceptance(migration_case):
    c=migration_case;_grant(c,c.reviewer);job_id=_job(c,phase='validate')
    claimed=_worker(c,'SELECT * FROM accounting.migration_claim(%s)',('stage13-validation',))[0]
    validation_id=uuid4().hex
    with c.db.transaction(actor=c.owner) as conn:
        basis=conn.execute('SELECT accounting.migration_basis(?)',(c.project,)).fetchone()[0]
    assert len(basis)==64
    worker_db=Database(WORKER_URL)
    with worker_db.transaction(write=True,actor=c.owner) as conn:
        conn.execute("SELECT set_config('app.migration_job_id',?,true),set_config('app.migration_token',?,true)",(job_id,claimed['lease_token']))
        report=json.dumps({'blocking_count':0,'warning_count':0,'issues':[],'coverage':{'complete':True}})
        conn.execute('INSERT INTO migration_validations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(validation_id,c.project,job_id,1,0,c.owner.firm_id,c.company,basis,report,0,0,STAMP))
    assert _worker(c,"SELECT accounting.migration_finish(%s,%s,'succeeded') AS finished",(job_id,claimed['lease_token']))[0]['finished']
    def accept(actor,token):
        with c.db.transaction(write=True,actor=actor) as conn:
            conn.execute('INSERT INTO migration_acceptances VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uuid4().hex,c.project,validation_id,c.owner.firm_id,c.company,1,token,uuid4().hex,actor.user_id,'独立核对后确认',STAMP))
    with pytest.raises(c.psycopg.Error): accept(c.owner,basis)
    with pytest.raises(c.psycopg.Error): accept(c.reviewer,'b'*64)
    accept(c.reviewer,basis)
    with c.db.transaction(actor=c.owner) as conn:
        assert conn.execute('SELECT status FROM migration_projects WHERE id=?',(c.project,)).fetchone()[0]=='accepted'


@pytest.mark.skipif(not WORKER_URL,reason='需要真实独立 worker 连接')
def test_native_expired_response_cannot_publish_after_recovery(migration_case):
    c=migration_case;file_id=_file(c);job_id=_job(c,file_id)
    first=_worker(c,'SELECT * FROM accounting.migration_claim(%s)',('stage13-first',))[0]
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.migration_jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=%s",(job_id,))
    assert _worker(c,'SELECT accounting.migration_recover() AS recovered')[0]['recovered']>=1
    second=_worker(c,'SELECT * FROM accounting.migration_claim(%s)',('stage13-restarted',))[0]
    assert second['id']==job_id and second['lease_token']!=first['lease_token']
    assert second['execution_user_id']==first['execution_user_id']
    assert second['attempt_count']==2
    assert _worker(c,"SELECT accounting.migration_finish(%s,%s,'succeeded') AS finished",(job_id,first['lease_token']))[0]['finished'] is False
    with pytest.raises(c.psycopg.Error):
        with Database(WORKER_URL).transaction(write=True,actor=c.owner) as conn:
            conn.execute("SELECT set_config('app.migration_job_id',?,true),set_config('app.migration_token',?,true)",(job_id,first['lease_token']))
            conn.execute("INSERT INTO migration_records VALUES(?,?,?,?,?,?,'voucher','LATE','2026-01','{}',?,1,?)",(uuid4().hex,c.project,file_id,job_id,c.owner.firm_id,c.company,'a'*64,STAMP))
    assert _worker(c,'SELECT accounting.migration_check(%s,%s) AS valid',(job_id,second['lease_token']))[0]['valid']


@pytest.mark.skipif(not WORKER_URL,reason='需要真实独立 worker 连接')
def test_native_revoked_executor_is_not_replaced_during_restart(migration_case):
    c=migration_case;file_id=_file(c);job_id=_job(c,file_id)
    first=_worker(c,'SELECT * FROM accounting.migration_claim(%s)',('stage13-revocation',))[0]
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute('DELETE FROM accounting.ledger_managers WHERE ledger_id=%s AND user_id=%s',(c.ledger,c.owner.user_id))
        conn.execute("UPDATE accounting.migration_jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=%s",(job_id,))
    _worker(c,'SELECT accounting.migration_recover() AS recovered')
    with c.psycopg.connect(ADMIN_URL) as conn:
        status,executor=conn.execute('SELECT status,execution_user_id FROM accounting.migration_jobs WHERE id=%s',(job_id,)).fetchone()
    assert status=='stale' and executor==c.owner.user_id
    assert _worker(c,"SELECT accounting.migration_finish(%s,%s,'succeeded') AS finished",(job_id,first['lease_token']))[0]['finished'] is False


@pytest.mark.skipif(not WORKER_URL,reason='需要真实独立 worker 连接')
def test_native_dispatch_has_company_fairness_and_separate_two_job_limit(migration_case):
    from copy import copy
    c=migration_case;other=copy(c);other.book=c.other_book;other.company=c.other_company;other.project=uuid4().hex
    with c.db.transaction(write=True,actor=c.owner) as conn:
        other.ledger=conn.execute('SELECT ledger_id FROM ledger_periods WHERE book_id=?',(other.book,)).fetchone()[0]
        conn.execute("INSERT INTO migration_projects(id,firm_id,company_id,ledger_id,book_id,external_book_id,source_system_key,source_product,history_start,history_end,cutover_date,create_key,create_fingerprint,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,'tplus','T+','2026-01-01','2026-09-30','2026-10-01',?,?,?,?,?)",(other.project,c.owner.firm_id,other.company,other.ledger,other.book,'native-'+other.project,other.project,'a'*64,c.owner.user_id,STAMP,STAMP))
        conn.execute("INSERT INTO migration_project_access VALUES(?,?,'manager',?,'其他企业独立管理授权',?)",(other.project,c.owner.user_id,c.owner.user_id,STAMP))
    _job(c,_file(c));_job(c,_file(c));_job(other,_file(other))
    first=_worker(c,'SELECT * FROM accounting.migration_claim(%s)',('fair-1',))[0]
    second=_worker(c,'SELECT * FROM accounting.migration_claim(%s)',('fair-2',))[0]
    assert {first['company_id'],second['company_id']}=={c.company,other.company}
    assert _worker(c,'SELECT * FROM accounting.migration_claim(%s)',('fair-3',))==[]
    with c.psycopg.connect(ADMIN_URL) as conn:
        rows=conn.execute("SELECT company_id,count(*) FROM accounting.migration_jobs WHERE firm_id=%s AND status='running' GROUP BY company_id",(c.owner.firm_id,)).fetchall()
    assert len(rows)==2 and all(count==1 for _,count in rows)


def test_native_workspace_count_limit_obeys_current_project_and_book_grants(migration_case):
    from app.business.service import BusinessService
    from app.accounting.review import ReviewService
    from app.cases.service import CaseService
    from app.workspace.service import WorkspaceService
    c=migration_case;business=BusinessService(c.db)
    workspace=WorkspaceService(c.db,c.service,business,ReviewService(c.db,c.service),CaseService(c.db,business))
    assert workspace.list(c.reviewer,kind='migration',bucket='all',limit=1)['pagination']['total']==0
    _grant(c,c.reviewer)
    result=workspace.list(c.reviewer,kind='migration',bucket='all',limit=1)
    assert result['pagination']['total']==1 and result['tasks'][0]['ref']==c.project and result['tasks'][0]['book_id']==c.book
    assert workspace.list(c.reviewer,kind='migration',bucket='all',limit=1,offset=1)['tasks']==[]
    with c.db.transaction(write=True,actor=c.owner) as conn:
        conn.execute('DELETE FROM book_access WHERE book_id=? AND user_id=?',(c.book,c.reviewer.user_id))
    assert workspace.list(c.reviewer,kind='migration',bucket='all',limit=1)['pagination']['total']==0
