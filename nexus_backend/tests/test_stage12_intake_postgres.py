import pytest
from uuid import uuid4

from app.accounting.domain import AccountingError
from app.intake.models import IntakeRecognize
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_stage12_intake import attach_intake, confirmed, create_intake, intake
import test_stage12_intake as shared_intake_tests

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用 PostgreSQL 管理员及受限运行测试连接')


def test_pg_intake_private_scope_and_worker_has_no_original_capability(pg_ai):
    c = pg_ai; service = intake(c); draft = attach_intake(c, service, create_intake(c, service))
    for name in ('peer', 'approver', 'finance', 'accountant', 'outsider'):
        with c.db.transaction(actor=c.actors[name]) as conn:
            assert conn.execute('SELECT * FROM intake_drafts WHERE id=?', (draft['id'],)).fetchall() == []
            assert conn.execute('SELECT * FROM intake_attachments WHERE intake_id=?', (draft['id'],)).fetchall() == []
        with pytest.raises(AccountingError): service.get(c.actors[name], draft['id'])
    with c.psycopg.connect(ADMIN_URL) as conn:
        for table in ('intake_drafts', 'intake_attachments', 'intake_runs'):
            assert conn.execute('SELECT relforcerowsecurity FROM pg_class WHERE oid=%s::regclass', ('accounting.' + table,)).fetchone()[0]
            assert not conn.execute('SELECT has_table_privilege(%s,%s,%s)', ('accounting_worker', 'accounting.' + table, 'SELECT')).fetchone()[0]


def test_pg_atomic_completion_uses_existing_independent_approval(pg_ai):
    shared_intake_tests.test_complete_is_atomic_independent_approval_and_idempotent(pg_ai)


def test_pg_recognition_transfers_without_second_fee(pg_ai):
    shared_intake_tests.test_ai_suggestions_never_write_facts_and_transfer_does_not_charge_twice(pg_ai)


def test_pg_intake_held_send_blocks_ordinary_event_ocr_budget(pg_ai):
    shared_intake_tests.test_intake_and_existing_event_ocr_share_one_quota(pg_ai)


def test_pg_employee_unknown_send_check_survives_hidden_cost_rls(pg_ai):
    shared_intake_tests.test_unknown_send_remains_held_and_explicit_retry_waits_for_cost_reconciliation(pg_ai)


def test_pg_native_cost_origin_is_immutable_and_cannot_forge_other_intake(pg_ai):
    c = pg_ai; service = intake(c); draft = attach_intake(c, service, create_intake(c, service))
    run = service.recognize(c.actors['employee'], draft['id'], draft['attachments'][0]['id'], IntakeRecognize(expected_version=draft['version'], idempotency_key=uuid4().hex))
    with c.db.transaction(actor=c.actors['employee']) as conn:
        assert not conn.execute('SELECT * FROM model_costs WHERE id=?', (run['id'],)).fetchall()
        assert conn.execute('SELECT accounting.intake_request_unresolved(?,?)', (draft['id'], draft['attachments'][0]['id'])).fetchone()[0]
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with c.psycopg.connect(ADMIN_URL) as conn:
            conn.execute('UPDATE accounting.model_costs SET intake_run_id=NULL,version=version+1 WHERE id=%s', (run['id'],))


def test_pg_invalid_evidence_remains_accounting_exception_after_confirmed_intake(pg_ai):
    shared_intake_tests.test_ocr_evidence_findings_survive_transfer_and_never_enter_validated_cache(pg_ai)
