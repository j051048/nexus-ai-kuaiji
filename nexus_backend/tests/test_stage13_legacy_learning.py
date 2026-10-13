"""Synthetic old-book examples require independent judgment and negative replay."""
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.catalog import CatalogService
from app.accounting.catalog_models import AccountUpdate
from app.accounting.domain import AccountingError,Actor
from app.accounting.models import BookCreate
from app.accounting.seed import LOCAL_ACTOR,bootstrap
from app.accounting.service import AccountingService
from app.legacy_migration.learning import (LegacyCandidateCreate,LegacyLearningPublish,
    LegacyLearningRetire,LegacyLearningReview,LegacyLearningService,LegacyLearningShadow)
from app.legacy_migration.models import MappingSave,MigrationConfirm,ProjectCreate,VersionCommand
from app.legacy_migration.service import LegacyMigrationService
from app.legacy_migration.storage import LegacyOriginalStore
from app.legacy_migration.worker import MigrationWorker


def bank_package(external_id):
    context={'external_book_id':external_id,'currency':'CNY','history_start':'2026-09-01','history_end':'2026-09-30','cutover_date':'2026-10-01'}
    records=[]
    for code,name,category,side,aux in [('1002','模拟银行','asset','debit',[]),('2001','模拟借款','liability','credit',[]),('6602','模拟费用','expense','debit',['counterparty'])]:
        records.append({'kind':'account','external_id':code,'period':'2026-09','payload':{'code':code,'name':name,'parent_code':'','posting':True,'category':category,'normal_balance':side,'auxiliary_requirements':aux}})
    for code,name in [('A','模拟供应商甲'),('B','模拟供应商乙')]:
        records.append({'kind':'auxiliary','external_id':code,'period':'2026-09','payload':{'kind':'counterparty','code':code,'name':name}})
        records.append({'kind':'voucher','external_id':'V'+code,'period':'2026-09','payload':{'external_id':'V'+code,'date':'2026-09-06','period':'2026-09','voucher_type':'记','number':code,'status':'posted','is_reversal':False,'lines':[{'ordinal':1,'account_code':'6602','description':'模拟采购付款','debit':'100.00','credit':'0.00','auxiliaries':{'counterparty':code}},{'ordinal':2,'account_code':'1002','description':'模拟银行付款','debit':'0.00','credit':'100.00','auxiliaries':{}}]}})
    for code,opening,debit,credit,closing,aux,identifier in [('1002','1000.00','0.00','200.00','800.00',{},'BAL-BANK'),('2001','-1000.00','0.00','0.00','-1000.00',{},'BAL-LOAN'),('6602','0.00','200.00','0.00','200.00',{},'BAL-EXPENSE'),('6602','0.00','100.00','0.00','100.00',{'counterparty':'A'},'BAL-A'),('6602','0.00','100.00','0.00','100.00',{'counterparty':'B'},'BAL-B')]:
        records.append({'kind':'balance','external_id':identifier,'period':'2026-09','payload':{'account_code':code,'auxiliaries':aux,'opening':opening,'period_debit':debit,'period_credit':credit,'closing':closing,'scope':'posted'}})
    return {'schema':'nexus.legacy/v1','context':context,'records':records}


def run_target_phase(worker,service,actor,book_id,project_id,phase):
    """A shared fair dispatcher need not claim this fixture's company first."""
    processed=[]
    deadline=time.monotonic()+150
    while len(processed)<100 and time.monotonic()<deadline:
        detail=service.get(actor,book_id,project_id)
        jobs=[j for j in detail['jobs'] if j['phase']==phase]
        assert jobs,'该项目没有已入队的阶段任务'
        if all(j['status']=='succeeded' for j in jobs):return detail
        failures=[{'id':j['id'],'status':j['status'],'error_code':j['error_code'],'error_detail':j['error_detail']} for j in jobs if j['status'] in {'failed','stale','cancelled'}]
        assert not failures,{'target_project':project_id,'phase':phase,'jobs':failures,'processed':processed}
        job=worker.run_once()
        if not job:
            # A prior abandoned lease can occupy the independent two-job lane;
            # ordinary recovery, without changing another firm's identity, clears it.
            time.sleep(0.2)
            continue
        processed.append({'target':job['project_id']==project_id,'phase':job['phase']})
    raise AssertionError({'target_project':project_id,'phase':phase,'reason':'目标项目在100次公平调度或150秒正常租约恢复后仍未结束','processed':processed})


def current_operator(c,label='本期经办员'):
    from app.accounting.directory import DirectoryService
    actor=Actor('current-operator-'+uuid4().hex,c.owner.firm_id)
    DirectoryService(c.db).register_identity(SimpleNamespace(user_id=actor.user_id,display_name=label,email=None))
    with c.db.transaction(write=True,actor=c.owner) as conn:
        conn.execute("INSERT INTO memberships VALUES(?,?,'operator')",(actor.firm_id,actor.user_id))
        conn.execute("INSERT INTO book_access VALUES(?,?,?,'editor')",(c.book['id'],actor.firm_id,actor.user_id))
    return actor


def make_case(db,owner,reviewer,book,storage,worker_db=None,package_transform=None):
    service=LegacyMigrationService(db,storage);learning=LegacyLearningService(db,service)
    catalog=CatalogService(db)
    account=next(item for item in catalog.get(owner,book['id'])['accounts'] if item['code']=='6602.02')
    catalog.update_account(owner,book['id'],account['code'],AccountUpdate(**{key:account[key] for key in ('code','name','category','normal_balance','active')},auxiliary_requirements=['counterparty'],expected_version=account['version']))
    external='SYNTHETIC-LEARNING-'+uuid4().hex
    detail=service.create(owner,book['id'],ProjectCreate(target_book_id=book['id'],source_product='generic',product_version='中性模板v1',external_book_id=external,source_system_key='synthetic-'+uuid4().hex,history_start='2026-09-01',history_end='2026-09-30',cutover_date='2026-10-01',idempotency_key=uuid4().hex,reason='仅用于自动测试的虚拟旧账'))
    project=detail['project']['id'];service.grant_access(owner,book['id'],project,reviewer.user_id,'reviewer','独立旧账及历史样例核对')
    package=bank_package(external)
    if package_transform:package_transform(package)
    service.upload(owner,book['id'],project,'SYNTHETIC历史银行.json',json.dumps(package,ensure_ascii=False).encode(),'package',{},detail['project']['version'],uuid4().hex)
    detail=service.get(owner,book['id'],project)
    detail=service.save_mapping(owner,book['id'],project,MappingSave(expected_version=detail['project']['version'],accounts=[{'source_code':'1002','target_code':'1002','action':'use_existing'},{'source_code':'2001','target_code':'2202','action':'use_existing'},{'source_code':'6602','target_code':'6602.02','action':'use_existing'}],auxiliaries=[{'kind':'counterparty','source_code':code,'action':'create'} for code in ('A','B')],reason='明确科目及往来方映射',idempotency_key=uuid4().hex))
    worker=MigrationWorker(LegacyMigrationService(worker_db or db,storage))
    for phase in ('parse','validate'):
        detail=service.enqueue(owner,book['id'],project,phase,VersionCommand(expected_version=detail['project']['version'],reason='核对测试资料',idempotency_key=uuid4().hex))
        detail=run_target_phase(worker,service,owner,book['id'],project,phase)
    assert detail['project']['status']=='ready',detail
    validation=detail['latest_validation']
    body=MigrationConfirm(expected_version=detail['project']['version'],validation_id=validation['id'],basis_token=validation['basis_token'],confirmed=True,reason='独立核对虚拟原件及金额',idempotency_key=uuid4().hex)
    service.accept(reviewer,book['id'],project,body)
    body=body.model_copy(update={'idempotency_key':uuid4().hex})
    detail=service.activate(owner,book['id'],project,body)
    rows=service.records(owner,book['id'],project,kind='voucher')['records']
    record_ids={row['external_id']:row['id'] for row in rows}
    with db.transaction(actor=owner) as conn:
        cps={r['code']:r['id'] for r in conn.execute("SELECT id,code FROM company_master_entities WHERE firm_id=? AND company_id=? AND kind='counterparty'",(owner.firm_id,book['company_id']))}
    return SimpleNamespace(db=db,service=service,learning=learning,owner=owner,reviewer=reviewer,book=book,project=project,record_ids=record_ids,cps=cps,storage=storage,worker_db=worker_db)


@pytest.fixture
def legacy_learning(tmp_path):
    db=Database(tmp_path/'learning.db');accounting=AccountingService(db);bootstrap(accounting,seed_examples=False)
    book=accounting.create_book(LOCAL_ACTOR,BookCreate(name='历史学习虚拟企业',period='2026-10'))
    reviewer=Actor('historical-reviewer-'+uuid4().hex,LOCAL_ACTOR.firm_id)
    with db.transaction(write=True,actor=LOCAL_ACTOR) as conn:
        conn.execute('INSERT INTO users(id,name) VALUES(?,?)',(reviewer.user_id,'独立测试会计'))
        conn.execute("INSERT INTO memberships VALUES(?,?,'reviewer')",(LOCAL_ACTOR.firm_id,reviewer.user_id))
        conn.execute("INSERT INTO book_access VALUES(?,?,?,'reviewer')",(book['id'],LOCAL_ACTOR.firm_id,reviewer.user_id))
    return make_case(db,LOCAL_ACTOR,reviewer,book,LegacyOriginalStore(tmp_path/'originals'))


def candidate(c,**changes):
    value={'definition':{'name':'模拟银行付费规则','source_kind':'bank','conditions':{'business_type':'daily_cashflow','direction':'expense','category':None,'payment_status':'paid','counterparty':'模拟供应商甲','counterparty_id':c.cps['A']},'lines':[{'account_code':'6602.02','side':'debit','amount_field':'gross'},{'account_code':'1002','side':'credit','amount_field':'gross'}]},'positive_record_ids':[c.record_ids['VA']],'reason':'从原件中独立识别银行付款，旧记账标志不能替代本系统复核','idempotency_key':uuid4().hex}
    value.update(changes)
    return c.learning.create(c.owner,c.book['id'],c.project,LegacyCandidateCreate(**value))


def reviewed(c,row):
    return c.learning.review(c.reviewer,c.book['id'],c.project,row['id'],LegacyLearningReview(expected_version=row['version'],decision='approve',evidence_confirmed=True,reason='另一名会计已核对原件及对应分录',idempotency_key=uuid4().hex))


def shadowed(c,row,negative=None):
    return c.learning.shadow(c.reviewer,c.book['id'],c.project,row['id'],LegacyLearningShadow(expected_version=row['version'],negative_record_ids=negative or [c.record_ids['VB']],evidence_confirmed=True,reason='独立确认正例命中且不同往来方负例不命中',idempotency_key=uuid4().hex))


def published(c):
    row=reviewed(c,candidate(c));shadow=shadowed(c,row)
    assert shadow['passed'],shadow
    row=c.learning.publish(c.reviewer,c.book['id'],c.project,row['id'],LegacyLearningPublish(expected_version=row['version'],shadow_id=shadow['id'],reason='经过原件复核及正负回放后仅用于草稿',idempotency_key=uuid4().hex))
    return row


def test_historical_bank_rule_needs_independent_review_and_negative_replay(legacy_learning):
    c=legacy_learning;row=candidate(c)
    assert row['status']=='candidate' and row['eligibility']['eligible']
    with pytest.raises(AccountingError,match='另一名会计'):
        c.learning.review(c.owner,c.book['id'],c.project,row['id'],LegacyLearningReview(expected_version=1,decision='approve',evidence_confirmed=True,reason='不能自审',idempotency_key=uuid4().hex))
    result=published(c)
    assert result['status']=='published' and result['version']==3
    with c.db.transaction(actor=c.owner) as conn:
        for table in ('sources','drafts','exports','learning_samples','learning_rules'):
            assert conn.execute('SELECT count(*) FROM '+table+' WHERE book_id=?',(c.book['id'],)).fetchone()[0]==0
        samples=conn.execute('SELECT quality_json FROM migration_learning_samples WHERE book_id=?',(c.book['id'],)).fetchall()
    assert all(json.loads(row[0])['vendor_posted_is_not_approval'] for row in samples)


def test_positive_and_negative_overlap_or_matching_negative_never_passes(legacy_learning):
    c=legacy_learning;row=reviewed(c,candidate(c))
    with pytest.raises(AccountingError,match='不能重叠'):shadowed(c,row,[c.record_ids['VA']])
    bad=candidate(c,positive_record_ids=[c.record_ids['VA'],c.record_ids['VB']]);bad=reviewed(c,bad)
    # A second supplier cannot be silently generalized into this exact identity.
    with pytest.raises(AccountingError,match='不能重叠'):shadowed(c,bad,[c.record_ids['VB']])


def test_published_formula_only_generates_current_bank_draft_lines(legacy_learning):
    c=legacy_learning;row=published(c)
    source={'book_id':c.book['id'],'source_kind':'bank','date':'2026-10-05','amount_cents':2300,'direction':'expense','counterparty':'模拟供应商甲'}
    with c.db.transaction(actor=c.owner) as conn:
        result=c.learning.render_in_transaction(conn,c.owner,c.book['id'],source)
        assert result['lines']==[('6602.02',2300,0),('1002',0,2300)]
        assert 'migration_rule_id' in result and 'rule_id' not in result
        assert c.learning.render_in_transaction(conn,c.owner,c.book['id'],source|{'counterparty':'模拟供应商乙'}) is None
        assert c.learning.render_in_transaction(conn,c.owner,c.book['id'],source|{'source_kind':'business'}) is None
    result=c.learning.retire(c.reviewer,c.book['id'],c.project,row['id'],LegacyLearningRetire(expected_version=row['version'],reason='停止建议但保留历史',idempotency_key=uuid4().hex))
    assert result['status']=='retired'
    with c.db.transaction(actor=c.owner) as conn:assert c.learning.render_in_transaction(conn,c.owner,c.book['id'],source) is None


def test_master_or_account_change_invalidates_shadow_and_published_formula(legacy_learning):
    c=legacy_learning;row=reviewed(c,candidate(c));shadow=shadowed(c,row)
    with c.db.transaction(write=True,actor=c.owner) as conn:
        conn.execute('UPDATE accounts SET active=0,version=version+1 WHERE book_id=? AND code=?',(c.book['id'],'6602.02'))
    with pytest.raises(AccountingError,match='改变'):
        c.learning.publish(c.reviewer,c.book['id'],c.project,row['id'],LegacyLearningPublish(expected_version=row['version'],shadow_id=shadow['id'],reason='不接受旧回放',idempotency_key=uuid4().hex))


def test_revoke_history_or_book_scope_and_closed_period_block_commands(legacy_learning):
    c=legacy_learning;row=candidate(c)
    c.service.revoke_access(c.owner,c.book['id'],c.project,c.reviewer.user_id,'试点撤回历史权限')
    with pytest.raises(AccountingError):c.learning.list(c.reviewer,c.book['id'],c.project)
    from app.closing.models import CloseRequest,PreflightRequest,SnapshotReview
    from app.closing.service import ClosingService
    closing=ClosingService(c.db);preflight=closing.preflight(c.owner,c.book['id'],PreflightRequest(idempotency_key=uuid4().hex))
    closing.review(c.owner,c.book['id'],preflight['id'],SnapshotReview(reason='明确核实本期没有业务且资料完整',materials_confirmed=True))
    closing.close(c.owner,c.book['id'],CloseRequest(snapshot_id=preflight['id'],reason='依据已复核快照关期'))
    with pytest.raises(AccountingError,match='开放期间'):candidate(c)


def test_idempotent_creation_and_true_actionable_permissions(legacy_learning):
    c=legacy_learning;body=LegacyCandidateCreate(definition={'name':'幂等银行规则','source_kind':'bank','conditions':{'business_type':'daily_cashflow','direction':'expense','payment_status':'paid','category':None,'counterparty':'模拟供应商甲','counterparty_id':c.cps['A']},'lines':[{'account_code':'6602.02','side':'debit','amount_field':'gross'},{'account_code':'1002','side':'credit','amount_field':'gross'}]},positive_record_ids=[c.record_ids['VA']],reason='独立证据',idempotency_key=uuid4().hex)
    first=c.learning.create(c.owner,c.book['id'],c.project,body);second=c.learning.create(c.owner,c.book['id'],c.project,body)
    assert first['id']==second['id'] and not first['permissions']['review']
    overview=c.learning.list(c.reviewer,c.book['id'],c.project)
    assert overview['candidates'][0]['permissions']['review']
    assert set(item['id'] for item in overview['counterparties'])==set(c.cps.values())


def test_mapped_line_difference_fails_shadow_and_cannot_publish(legacy_learning):
    c=legacy_learning
    original=candidate(c)['definition']
    original['lines'][0]['account_code']='6602.99'
    row=reviewed(c,candidate(c,definition=original));shadow=shadowed(c,row)
    assert not shadow['passed']
    positive=next(item for item in shadow['report']['outcomes'] if item['polarity']=='positive')
    assert positive['matched'] and not positive['equal']
    with pytest.raises(AccountingError,match='回放未通过'):
        c.learning.publish(c.reviewer,c.book['id'],c.project,row['id'],LegacyLearningPublish(expected_version=row['version'],shadow_id=shadow['id'],reason='金额正确也不能忽略科目差异',idempotency_key=uuid4().hex))


def test_complex_original_remains_candidate_and_never_publishes(legacy_learning,tmp_path):
    original=legacy_learning;accounting=AccountingService(original.db)
    book=accounting.create_book(original.owner,BookCreate(name='复杂历史虚拟企业',period='2026-10'))
    with original.db.transaction(write=True,actor=original.owner) as conn:
        conn.execute("INSERT INTO book_access VALUES(?,?,?,'reviewer')",(book['id'],original.owner.firm_id,original.reviewer.user_id))
    def transform(package):
        row=next(r for r in package['records'] if r['kind']=='voucher' and r['external_id']=='VA')
        row['payload']['lines'].append({'ordinal':3,'account_code':'1002','description':'原系统保留的额外分录','debit':'0.00','credit':'0.00','auxiliaries':{}})
    c=make_case(original.db,original.owner,original.reviewer,book,LegacyOriginalStore(tmp_path/'complex'),worker_db=original.worker_db,package_transform=transform)
    row=candidate(c)
    assert not row['eligibility']['eligible'] and row['eligibility']['issues']
    row=reviewed(c,row);shadow=shadowed(c,row)
    assert not shadow['passed']


def test_book_operator_can_use_published_formula_without_reading_history(legacy_learning):
    c=legacy_learning;published(c);operator=current_operator(c)
    with pytest.raises(AccountingError):c.learning.list(operator,c.book['id'],c.project)
    source={'book_id':c.book['id'],'source_kind':'bank','date':'2026-10-05','amount_cents':2300,'direction':'expense','counterparty':'模拟供应商甲'}
    with c.db.transaction(actor=operator) as conn:
        result=c.learning.render_in_transaction(conn,operator,c.book['id'],source)
        assert result and 'migration_rule_id' in result
        assert set(result)<= {'lines','auxiliaries','issues','explanation','migration_rule_id','migration_rule_version'}


def test_missing_or_changed_original_blocks_new_learning_actions(legacy_learning):
    c=legacy_learning
    with c.db.transaction(actor=c.owner) as conn:
        storage_key=conn.execute('SELECT storage_key FROM migration_files WHERE project_id=?',(c.project,)).fetchone()[0]
    # This path is inside the fixture's private temporary originals directory.
    c.storage._path(storage_key).write_bytes(b'changed synthetic original')
    with pytest.raises(AccountingError):candidate(c)
    with c.db.transaction(actor=c.owner) as conn:
        assert conn.execute('SELECT count(*) FROM migration_learning_candidates WHERE project_id=?',(c.project,)).fetchone()[0]==0


def test_public_formula_policy_fingerprint_changes_only_after_consumable_publication(legacy_learning):
    c=legacy_learning
    with c.db.transaction(actor=c.owner) as conn:first=c.learning.policy_fingerprint_in_transaction(conn,c.owner,c.book['id'])
    row=reviewed(c,candidate(c));shadow=shadowed(c,row)
    with c.db.transaction(actor=c.owner) as conn:assert c.learning.policy_fingerprint_in_transaction(conn,c.owner,c.book['id'])==first
    row=c.learning.publish(c.reviewer,c.book['id'],c.project,row['id'],LegacyLearningPublish(expected_version=row['version'],shadow_id=shadow['id'],reason='明确发布可消费公式',idempotency_key=uuid4().hex))
    with c.db.transaction(actor=c.owner) as conn:second=c.learning.policy_fingerprint_in_transaction(conn,c.owner,c.book['id'])
    assert second!=first
    c.learning.retire(c.reviewer,c.book['id'],c.project,row['id'],LegacyLearningRetire(expected_version=row['version'],reason='明确停用公式',idempotency_key=uuid4().hex))
    with c.db.transaction(actor=c.owner) as conn:assert c.learning.policy_fingerprint_in_transaction(conn,c.owner,c.book['id'])==first
