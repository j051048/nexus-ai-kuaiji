"""Actual worker/SQL checks for historical learning and formula-only disclosure."""
import os
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import Actor
from app.legacy_migration.storage import LegacyOriginalStore
from test_business_postgres import ADMIN_URL,APP_URL,business_case,business_postgres
from test_stage13_legacy_learning import make_case,published,reviewed,candidate,shadowed,current_operator
import test_stage13_legacy_learning as shared

WORKER_URL=os.getenv('ACCOUNTING_TEST_WORKER_DATABASE_URL')
pytestmark=pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL,reason='需要真实 PG 管理员、受限 API 和独立 worker 连接')


@pytest.fixture
def pg_legacy_learning(business_case,tmp_path_factory):
    c=business_case
    try:
        yield make_case(c.db,c.owner,c.reviewer,{'id':c.book,'company_id':c.company},LegacyOriginalStore(tmp_path_factory.getbasetemp()/'stage13-shared-originals'),worker_db=Database(WORKER_URL))
    finally:
        # This UUID firm belongs solely to business_case, including setup failure.
        with c.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("UPDATE accounting.migration_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=accounting.ocr_utc_now(),finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status IN ('queued','running','retry_wait')",(c.owner.firm_id,))


def test_pg_historical_review_replay_and_formula_only_share(pg_legacy_learning):
    shared.test_historical_bank_rule_needs_independent_review_and_negative_replay(pg_legacy_learning)


def test_pg_operator_uses_formula_without_archive_permission(pg_legacy_learning):
    shared.test_book_operator_can_use_published_formula_without_reading_history(pg_legacy_learning)


def test_pg_shadow_amount_lines_and_catalog_changes(pg_legacy_learning):
    shared.test_mapped_line_difference_fails_shadow_and_cannot_publish(pg_legacy_learning)
    shared.test_master_or_account_change_invalidates_shadow_and_published_formula(pg_legacy_learning)


def test_pg_archive_acl_and_current_close_guard(pg_legacy_learning):
    shared.test_revoke_history_or_book_scope_and_closed_period_block_commands(pg_legacy_learning)


def test_pg_native_samples_cannot_fake_current_approval_or_replayed_answer(pg_legacy_learning):
    c=pg_legacy_learning;row=candidate(c)
    import psycopg
    for sql,args in [("UPDATE accounting.migration_learning_samples SET final_json='{}' WHERE candidate_id=%s",(row['id'],)),("UPDATE accounting.migration_learning_candidates SET status='reviewed',version=version+1 WHERE id=%s",(row['id'],))]:
        with pytest.raises(psycopg.Error):
            if 'candidates' in sql:
                with c.db.transaction(write=True,actor=c.owner) as conn:conn.execute(sql.replace('%s','?'),args)
            else:
                with psycopg.connect(ADMIN_URL) as conn:conn.execute(sql,args)


def test_pg_native_copied_passed_shadow_and_scope_fail(pg_legacy_learning):
    c=pg_legacy_learning;row=reviewed(c,candidate(c));shadow=shadowed(c,row)
    import psycopg
    with c.db.transaction(actor=c.reviewer) as conn:
        original=dict(conn.execute('SELECT * FROM migration_learning_shadows WHERE id=?',(shadow['id'],)).fetchone())
    original.update(id=uuid4().hex,report_json='{"passed":true}',request_key=uuid4().hex)
    columns=list(original)
    with pytest.raises(psycopg.Error):
        with c.db.transaction(write=True,actor=c.reviewer) as conn:
            conn.execute('INSERT INTO migration_learning_shadows('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',[original[key] for key in columns])
    with c.db.transaction(actor=c.owner) as conn:
        assert conn.execute('SELECT count(*) FROM migration_learning_shadows WHERE candidate_id=?',(row['id'],)).fetchone()[0]==1


def test_pg_no_raw_history_select_for_worker_or_current_operator(pg_legacy_learning):
    c=pg_legacy_learning;published(c);operator=current_operator(c,'本期规则经办员')
    import psycopg
    with c.db.transaction(actor=operator) as conn:
        for table in ('migration_learning_candidates','migration_learning_samples','migration_learning_shadows','migration_learning_rules'):
            assert conn.execute('SELECT id FROM '+table+' WHERE book_id=?',(c.book['id'],)).fetchall()==[]
        formulas=conn.execute('SELECT * FROM accounting.migration_learning_public_rules(?)',(c.book['id'],)).fetchall()
        assert len(formulas)==1 and set(formulas[0])=={'id','version','definition_json'}
    with psycopg.connect(ADMIN_URL) as conn:
        for table in ('migration_learning_candidates','migration_learning_samples','migration_learning_reviews','migration_learning_shadows','migration_learning_rules','migration_learning_retirements'):
            assert conn.execute('SELECT relforcerowsecurity FROM pg_class WHERE oid=%s::regclass',('accounting.'+table,)).fetchone()[0]
            assert not conn.execute('SELECT has_table_privilege(%s,%s,%s)',('accounting_worker','accounting.'+table,'SELECT')).fetchone()[0]
