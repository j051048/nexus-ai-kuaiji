"""Current source workflow metadata never opens employee ledger information."""
from uuid import uuid4
import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest
from app.cases.models import CaseCreate
from app.cases.service import CaseService
from test_business_postgres import ADMIN_URL,APP_URL,business_case,business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_stage9_reconciliation_postgres import prepare
from test_stage9_reconciliation import command,selection

pytestmark=pytest.mark.skipif(not ADMIN_URL or not APP_URL,reason='需要专用PostgreSQL管理员与受限运行测试连接')


def test_pg_linked_bank_case_is_current_blocker_without_employee_metadata_access(pg_ai):
    c=pg_ai
    reconciliation,mapped,result=prepare(c)
    reviewer=c.actors['accountant']
    reconciliation.confirm(reviewer,command(selection(result)))
    bank_id=result['candidates'][0]['source_id']
    with c.db.transaction(actor=reviewer) as conn:
        business_source=conn.execute('SELECT source_id FROM drafts WHERE id=? AND book_id=?',(mapped['draft_id'],c.book['id'])).fetchone()[0]
        bank_draft=conn.execute('SELECT id FROM drafts WHERE source_id=? AND book_id=?',(bank_id,c.book['id'])).fetchone()[0]
        assert not conn.execute('SELECT accounting.source_workflow_blocked(?,?)',(c.book['id'],business_source)).fetchone()[0]
    CaseService(c.db,c.business).create(reviewer,CaseCreate(book_id=c.book['id'],draft_id=bank_draft,title='内部付款异常',reason='内部备注不可透过boolean开放',idempotency_key=uuid4().hex))
    with c.db.transaction(actor=reviewer) as conn:
        assert conn.execute('SELECT accounting.source_workflow_blocked(?,?)',(c.book['id'],business_source)).fetchone()[0]
    for source_id in (bank_id,business_source):
        with pytest.raises(c.psycopg.errors.InsufficientPrivilege,match='scope denied'):
            with c.db.transaction(actor=c.actors['employee']) as conn:
                conn.execute('SELECT accounting.source_workflow_blocked(?,?)',(c.book['id'],source_id))
    draft=c.ledger.get_draft(reviewer,c.book['id'],mapped['draft_id'])
    with pytest.raises(AccountingError,match='已关联银行流水'):
        c.ledger.approve(reviewer,c.book['id'],draft['id'],ApprovalRequest(expected_version=draft['version']))


def test_pg_source_workflow_unknown_or_foreign_book_is_not_a_boolean_oracle(pg_ai):
    c=pg_ai
    for actor,book_id,source_id in ((c.actors['employee'],c.book['id'],'unknown-source'),(c.actors['accountant'],c.other_book['id'],'unknown-source')):
        with pytest.raises(c.psycopg.errors.InsufficientPrivilege,match='scope denied'):
            with c.db.transaction(actor=actor) as conn:
                conn.execute('SELECT accounting.source_workflow_blocked(?,?)',(book_id,source_id))
