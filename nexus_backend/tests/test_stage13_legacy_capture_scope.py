"""Historical debt clearance cannot become ordinary classifier training."""
from uuid import uuid4
from pathlib import Path
import json

import pytest

from app.accounting.models import ApprovalRequest
from app.learning.service import LearningService
from test_business import business
from test_stage13_legacy_settlements import legacy_case,bank,preview,confirm


def approved_clearance(c):
    source=bank(c);receipt=confirm(c,preview(c,source))
    draft=c.ledger.get_draft(c.actors['accountant'],c.book['id'],source['draft_id'])
    approved=c.ledger.approve(c.actors['accountant'],c.book['id'],draft['id'],ApprovalRequest(expected_version=draft['version']))
    return source,approved


def forged_sample(c,source,approved):
    return (uuid4().hex,c.manager.firm_id,c.company['id'],c.book['id'],'2026-10',approved['id'],source['id'],approved['version']-1,approved['version'],'{}','{}','{}','[]','a'*64,'b'*64,0,c.actors['accountant'].user_id,'2026-10-13T00:00:00+00:00')


def test_standalone_learning_without_optional_injection_skips_clearance_capture(legacy_case):
    c=legacy_case;source,approved=approved_clearance(c)
    ordinary=LearningService(c.db)
    with c.db.transaction(write=True,actor=c.actors['accountant']) as conn:
        draft=conn.execute('SELECT * FROM drafts WHERE id=?',(approved['id'],)).fetchone()
        result=ordinary.capture_in_transaction(conn,c.actors['accountant'],c.book,draft,approved,approved['version']-1)
        assert not result['captured'] and result['id'] is None
        assert conn.execute('SELECT count(*) FROM learning_samples WHERE source_id=?',(source['id'],)).fetchone()[0]==0


def test_sqlite_raw_sample_insert_cannot_bypass_optional_capture_hook(legacy_case):
    c=legacy_case;source,approved=approved_clearance(c)
    with pytest.raises(Exception,match='Historical clearance'):
        with c.db.transaction(write=True,actor=c.actors['accountant']) as conn:
            conn.execute('INSERT INTO learning_samples VALUES('+','.join('?' for _ in range(18))+')',forged_sample(c,source,approved))


def test_sqlite_forward_guards_cover_version_evaluation_and_publication(legacy_case):
    c=legacy_case;source,approved=approved_clearance(c);ordinary=LearningService(c.db)
    # Simulate a valid reviewed trace created before this forward upgrade. No
    # original financial record is changed; guards are restored in the same txn.
    ddl=Path(__file__).resolve().parents[1].joinpath('migrations/sqlite/050_legacy_learning_capture_scope.sql').read_text(encoding='utf-8')
    statements={name:ddl[ddl.index('CREATE TRIGGER '+name):].split('\nCREATE TRIGGER ',1)[0] for name in ('learning_legacy_capture','learning_legacy_version','learning_legacy_evaluation')}
    ordinary._legacy_source_allocated=lambda *args:False
    actor=c.actors['accountant'];rule_id=uuid4().hex;stamp='2026-10-13T00:00:00+00:00'
    with c.db.transaction(write=True,actor=actor) as conn:
        conn.execute('DROP TRIGGER learning_legacy_capture')
        draft=conn.execute('SELECT * FROM drafts WHERE id=?',(approved['id'],)).fetchone()
        sample=ordinary.capture_in_transaction(conn,actor,c.book,draft,approved,approved['version']-1)['id']
        conn.execute(statements['learning_legacy_capture'])
        assert not ordinary._ordinary_sample(conn,c.book['id'],sample)
        conn.execute('INSERT INTO learning_rules VALUES(?,?,?,?,?,1,1,\'draft\',NULL,?,?,?)',(rule_id,actor.firm_id,c.company['id'],c.book['id'],'2026-10',actor.user_id,stamp,stamp))
    definition=json.dumps({'name':'不得发布的旧清偿分类器','source_kind':'bank','conditions':{'business_type':'daily_cashflow','direction':'expense','payment_status':'paid','category':None,'counterparty':'模拟往来方'},'lines':[{'account_code':'2202.HIST','side':'debit','amount_field':'gross'},{'account_code':'1002','side':'credit','amount_field':'gross'}]})
    values=(rule_id,c.book['id'],1,definition,'a'*64,json.dumps([sample]),'模拟升级前已有清偿样例',actor.user_id,stamp)
    with pytest.raises(Exception,match='Historical clearance'):
        with c.db.transaction(write=True,actor=actor) as conn:conn.execute('INSERT INTO learning_rule_versions VALUES(?,?,?,?,?,?,?,?,?)',values)
    with c.db.transaction(write=True,actor=actor) as conn:
        conn.execute('DROP TRIGGER learning_legacy_version');conn.execute('INSERT INTO learning_rule_versions VALUES(?,?,?,?,?,?,?,?,?)',values);conn.execute(statements['learning_legacy_version'])
    evaluation=(uuid4().hex,rule_id,c.book['id'],1,json.dumps([sample]),'b'*64,'{"passed":true}',actor.user_id,stamp)
    with pytest.raises(Exception,match='Historical clearance'):
        with c.db.transaction(write=True,actor=actor) as conn:conn.execute('INSERT INTO learning_rule_evaluations VALUES(?,?,?,?,?,?,?,?,?)',evaluation)
    with c.db.transaction(write=True,actor=actor) as conn:
        conn.execute('DROP TRIGGER learning_legacy_evaluation');conn.execute('INSERT INTO learning_rule_evaluations VALUES(?,?,?,?,?,?,?,?,?)',evaluation);conn.execute(statements['learning_legacy_evaluation'])
    with pytest.raises(Exception,match='Historical clearance'):
        with c.db.transaction(write=True,actor=actor) as conn:conn.execute("UPDATE learning_rules SET status='published',state_version=state_version+1,evaluation_id=? WHERE id=?",(evaluation[0],rule_id))
