import pytest

from app.learning.service import LearningService
from test_business_postgres import ADMIN_URL,APP_URL,business_case,business_postgres
from test_stage13_legacy_settlements_postgres import legacy_pg_case,WORKER_URL
from test_stage13_legacy_capture_scope import approved_clearance,forged_sample

pytestmark=pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL,reason='需要专用真实 PG 管理员、API 与独立 worker')


def test_pg_ordinary_capture_is_blocked_even_without_service_injection(legacy_pg_case):
    c=legacy_pg_case;source,approved=approved_clearance(c)
    ordinary=LearningService(c.db)
    with c.db.transaction(write=True,actor=c.actors['accountant']) as conn:
        draft=conn.execute('SELECT * FROM drafts WHERE id=?',(approved['id'],)).fetchone()
        assert not ordinary.capture_in_transaction(conn,c.actors['accountant'],c.book,draft,approved,approved['version']-1)['captured']
    with pytest.raises(c.psycopg.errors.CheckViolation,match='Historical clearance'):
        with c.db.transaction(write=True,actor=c.actors['accountant']) as conn:
            conn.execute('INSERT INTO learning_samples VALUES('+','.join('?' for _ in range(18))+')',forged_sample(c,source,approved))
    with c.db.transaction(actor=c.actors['accountant']) as conn:
        assert conn.execute('SELECT count(*) FROM learning_samples WHERE source_id=?',(source['id'],)).fetchone()[0]==0


def test_pg_hidden_legacy_allocation_does_not_restore_ordinary_capture_eligibility(legacy_pg_case):
    c=legacy_pg_case;source,approved=approved_clearance(c);actor=c.actors['accountant']
    c.migration.revoke_access(c.manager,c.book['id'],c.project,actor.user_id,'撤回历史授权，保留当前银行复核职责')
    with c.db.transaction(actor=actor) as conn:
        assert conn.execute('SELECT id FROM migration_clearance_allocations WHERE bank_source_id=?',(source['id'],)).fetchall()==[]
        current=conn.execute('SELECT * FROM sources WHERE id=?',(source['id'],)).fetchone()
        assert LearningService(c.db)._legacy_source_allocated(conn,actor,current)
    with pytest.raises(c.psycopg.errors.CheckViolation,match='Historical clearance'):
        with c.db.transaction(write=True,actor=actor) as conn:
            conn.execute('INSERT INTO learning_samples VALUES('+','.join('?' for _ in range(18))+')',forged_sample(c,source,approved))
