"""Migration todo ACL is applied in SQL before COUNT/LIMIT and precise routing."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.directory import DirectoryService
from app.accounting.domain import Actor
from app.accounting.models import BookCreate
from app.accounting.review import ReviewService
from app.accounting.seed import LOCAL_ACTOR,bootstrap
from app.accounting.service import AccountingService
from app.jobs.visibility import book_edit_scope
from app.legacy_migration.models import ProjectCreate
from app.legacy_migration.service import LegacyMigrationService
from app.legacy_migration.storage import LegacyOriginalStore
from app.legacy_migration.workspace import workspace_branch


@pytest.fixture
def migration_workspace(tmp_path):
    db=Database(tmp_path/'workspace.db');accounting=AccountingService(db);bootstrap(accounting,seed_examples=False)
    book=accounting.create_book(LOCAL_ACTOR,BookCreate(name='待办虚拟企业',period='2026-10'))
    service=LegacyMigrationService(db,LegacyOriginalStore(tmp_path/'originals'))
    detail=service.create(LOCAL_ACTOR,book['id'],ProjectCreate(target_book_id=book['id'],external_book_id='SYNTHETIC-'+uuid4().hex,source_system_key=uuid4().hex,history_start='2026-09-01',history_end='2026-09-30',cutover_date='2026-10-01',idempotency_key=uuid4().hex,reason='虚拟企业迁移待办授权验证'))
    c=SimpleNamespace(db=db,accounting=accounting,service=service,owner=LOCAL_ACTOR,book=book,project=detail['project']['id'])
    def person(firm_role='reviewer',book_role='reviewer',project_role=None):
        actor=Actor('migration-task-person-'+uuid4().hex,c.owner.firm_id)
        DirectoryService(db).register_identity(SimpleNamespace(user_id=actor.user_id,display_name='待办测试人员',email=None))
        with db.transaction(write=True,actor=c.owner) as conn:
            if firm_role:conn.execute('INSERT INTO memberships VALUES(?,?,?)',(actor.firm_id,actor.user_id,firm_role))
            if book_role:conn.execute('INSERT INTO book_access VALUES(?,?,?,?)',(book['id'],actor.firm_id,actor.user_id,book_role))
        if project_role:service.grant_access(c.owner,book['id'],c.project,actor.user_id,project_role,'明确旧账资料授权')
        return actor
    c.person=person
    return c


def query(c,actor,count=False,limit=None,offset=0):
    with c.db.transaction(actor=actor) as conn:
        reviews,rp,_=ReviewService(c.db,c.accounting)._review_scope(conn,actor)
        edits,ep=book_edit_scope(actor)
        sql,args=workspace_branch(c.db,actor)
        ctes='WITH review_books AS ('+reviews+'),edit_books AS (SELECT b.* FROM books b WHERE '+edits+'),tasks AS ('+sql+') '
        tail='SELECT count(*) FROM tasks' if count else 'SELECT * FROM tasks ORDER BY ref'
        parameters=[*rp,*ep,*args]
        if limit is not None:tail+=' LIMIT ? OFFSET ?';parameters += [limit,offset]
        result=conn.execute(ctes+tail,parameters)
        return result.fetchone()[0] if count else [dict(row) for row in result.fetchall()]


def state(c,value):
    # This fixture projects status only; financial publication is tested elsewhere.
    with c.db.transaction(write=True,actor=c.owner) as conn:
        conn.execute('UPDATE migration_projects SET status=? WHERE id=?',(value,c.project))


def test_migration_creator_collecting_can_prepare_and_processing_waits(migration_workspace):
    c=migration_workspace;row=query(c,c.owner)[0]
    assert row['kind']=='migration' and row['ref']==c.project and row['book_id']==c.book['id']
    assert row['bucket']=='action' and row['action_label']=='继续准备资料'
    assert set(row)=={'kind','ref','company_id','book_id','event_id','draft_id','batch_id','title','state','priority','due_on','updated_at','bucket','action_label','responsible'}
    state(c,'processing');row=query(c,c.owner)[0]
    assert row['bucket']=='waiting' and row['action_label']=='查看后台进度'
    state(c,'needs_attention');row=query(c,c.owner)[0]
    assert row['bucket']=='action' and row['priority']==1 and row['action_label']=='处理迁移差异'


def test_history_permission_and_book_permission_are_both_required_before_count(migration_workspace):
    c=migration_workspace;without_history=c.person();viewer=c.person('viewer','viewer','viewer')
    assert query(c,without_history,count=True)==0
    assert query(c,viewer,count=True)==1
    assert query(c,viewer)[0]['bucket']=='waiting'
    with c.db.transaction(write=True,actor=c.owner) as conn:
        conn.execute('DELETE FROM book_access WHERE book_id=? AND user_id=?',(c.book['id'],viewer.user_id))
    assert query(c,viewer,count=True)==0


def test_ready_requires_independent_reviewer_and_accepted_requires_manager(migration_workspace):
    c=migration_workspace;reviewer=c.person(project_role='reviewer');state(c,'ready')
    assert query(c,c.owner)[0]['bucket']=='waiting'
    row=query(c,reviewer)[0]
    assert row['bucket']=='action' and row['action_label']=='独立验收'
    state(c,'accepted')
    assert query(c,reviewer)[0]['bucket']=='waiting'
    assert query(c,c.owner)[0]['action_label']=='确认接管'


def test_latest_mapping_author_cannot_be_the_independent_reviewer(migration_workspace):
    c=migration_workspace;reviewer=c.person(project_role='reviewer')
    with c.db.transaction(write=True,actor=c.owner) as conn:
        conn.execute('INSERT INTO migration_mapping_versions(project_id,version,firm_id,company_id,payload_json,request_key,request_fingerprint,created_by,created_at) VALUES(?,1,?,?,\'{}\',?,?,?,?)',(c.project,c.owner.firm_id,c.book['company_id'],uuid4().hex,'a'*64,reviewer.user_id,'2026-10-13T00:00:00+00:00'))
    state(c,'ready')
    assert query(c,reviewer)[0]['bucket']=='waiting'


def test_manager_needs_current_ledger_manager_and_close_locks_actions(migration_workspace):
    c=migration_workspace
    with c.db.transaction(write=True,actor=c.owner) as conn:
        conn.execute('DELETE FROM ledger_managers WHERE user_id=? AND ledger_id=(SELECT ledger_id FROM ledger_periods WHERE book_id=?)',(c.owner.user_id,c.book['id']))
    assert query(c,c.owner)[0]['bucket']=='waiting'
    from app.closing.models import CloseRequest,PreflightRequest,SnapshotReview
    from app.closing.service import ClosingService
    closing=ClosingService(c.db);p=closing.preflight(c.owner,c.book['id'],PreflightRequest(idempotency_key=uuid4().hex))
    closing.review(c.owner,c.book['id'],p['id'],SnapshotReview(reason='本期无新业务且资料完整已核实',materials_confirmed=True))
    closing.close(c.owner,c.book['id'],CloseRequest(snapshot_id=p['id'],reason='按复核快照锁期'))
    row=query(c,c.owner)[0]
    assert row['bucket']=='waiting' and row['action_label']=='查看已关期迁移'


def test_suspended_client_hides_task(migration_workspace):
    c=migration_workspace
    with c.db.transaction(write=True,actor=c.owner) as conn:
        conn.execute("UPDATE firm_clients SET status='suspended' WHERE firm_id=? AND company_id=?",(c.owner.firm_id,c.book['company_id']))
    assert query(c,c.owner,count=True)==0


@pytest.mark.parametrize('status',['active','cancelled'])
def test_completed_or_cancelled_project_is_not_an_open_todo(migration_workspace,status):
    c=migration_workspace;state(c,status)
    assert query(c,c.owner,count=True)==0


def test_pagination_never_counts_another_companys_ungranted_history(migration_workspace):
    c=migration_workspace;reviewer=c.person(project_role='reviewer')
    hidden=c.accounting.create_book(c.owner,BookCreate(name='隐藏虚拟企业',period='2026-10'))
    hidden_project=c.service.create(c.owner,hidden['id'],ProjectCreate(target_book_id=hidden['id'],external_book_id=uuid4().hex,source_system_key=uuid4().hex,history_start='2026-09-01',history_end='2026-09-30',cutover_date='2026-10-01',idempotency_key=uuid4().hex,reason='另一企业保密历史'))
    assert query(c,reviewer,count=True)==1
    rows=query(c,reviewer,limit=1)
    assert len(rows)==1 and rows[0]['ref']==c.project
    assert hidden_project['project']['id'] not in {row['ref'] for row in rows}
    assert query(c,reviewer,limit=1,offset=1)==[]


def staged_parse(c):
    import json
    from app.legacy_migration.models import VersionCommand
    from test_stage13_legacy_learning import bank_package
    detail=c.service.get(c.owner,c.book['id'],c.project)
    data=json.dumps(bank_package(detail['project']['external_book_id']),ensure_ascii=False).encode()
    uploaded=c.service.upload(c.owner,c.book['id'],c.project,'SYNTHETIC进度.json',data,'package',{},detail['project']['version'],uuid4().hex)
    detail=c.service.get(c.owner,c.book['id'],c.project)
    detail=c.service.enqueue(c.owner,c.book['id'],c.project,'parse',VersionCommand(expected_version=detail['project']['version'],reason='核对虚拟资料',idempotency_key=uuid4().hex))
    return data,uploaded['file']['id'],detail


def test_real_worker_failure_retry_and_finished_parse_are_actionable(migration_workspace):
    from app.legacy_migration.models import VersionCommand
    from app.legacy_migration.worker import MigrationWorker
    c=migration_workspace;data,file_id,detail=staged_parse(c)
    with c.db.transaction(actor=c.owner) as conn:key=conn.execute('SELECT storage_key FROM migration_files WHERE id=?',(file_id,)).fetchone()[0]
    path=c.service.storage._path(key);path.write_bytes(b'changed synthetic blob')
    worker=MigrationWorker(c.service);job=worker.run_once()
    row=query(c,c.owner)[0]
    assert row['bucket']=='action' and row['priority']==1 and row['action_label']=='处理迁移任务'
    path.write_bytes(data)
    c.service.retry(c.owner,c.book['id'],c.project,job['id'],VersionCommand(expected_version=detail['project']['version'],reason='原件恢复后明确重试',idempotency_key=uuid4().hex))
    assert query(c,c.owner)[0]['bucket']=='waiting'
    assert worker.run_once()
    row=query(c,c.owner)[0]
    assert row['bucket']=='action' and row['action_label']=='确认映射并核验'


def test_cancelled_job_needs_human_but_new_queued_retry_waits(migration_workspace):
    from app.legacy_migration.models import VersionCommand
    c=migration_workspace;_,_,detail=staged_parse(c);job=detail['jobs'][0]
    assert query(c,c.owner)[0]['bucket']=='waiting'
    c.service.cancel_job(c.owner,c.book['id'],c.project,job['id'],VersionCommand(expected_version=detail['project']['version'],reason='明确暂停该任务',idempotency_key=uuid4().hex))
    assert query(c,c.owner)[0]['action_label']=='处理迁移任务'
    c.service.retry(c.owner,c.book['id'],c.project,job['id'],VersionCommand(expected_version=detail['project']['version'],reason='明确恢复取消任务',idempotency_key=uuid4().hex))
    assert query(c,c.owner)[0]['bucket']=='waiting'
