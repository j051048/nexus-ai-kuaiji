from period_fixtures import restore_period_state
"""Bank identity cannot be bound to a closed or mismatched source period."""
import json
from uuid import uuid4
import pytest

from app.accounting.models import ImportRequest, SourceRow
from app.accounting.service import fingerprint
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用 PostgreSQL 管理员和受限API测试连接')


@pytest.mark.parametrize('kind', ['closed', 'wrong_month'])
def test_sql_bank_identity_requires_current_open_source_period(business_case, kind):
    case = business_case
    source_id = uuid4().hex
    source_date = '2026-11-03' if kind == 'wrong_month' else '2026-10-03'
    raw = {'date':source_date,'description':'银行身份SQL边界','counterparty':'合成商店','amount':'113.00','direction':'expense','evidence_ref':'合成票据'}
    digest = fingerprint(raw)
    # Source legacy policy allows staging rows; the NEW identity capability
    # must independently reject binding an invalid or closed target period.
    with case.db.transaction(write=True, actor=case.owner) as conn:
        conn.execute("INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint) VALUES(?, ?, ?, '独立SQL身份检查', ?, '银行身份SQL边界', '合成商店', 11300, 'expense', '合成票据', ?)", (source_id,case.book,'bank:v1:'+source_id,source_date,digest))
    if kind == 'closed':
        restore_period_state(case.service.db, case.book)
    with pytest.raises(case.psycopg.Error):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("INSERT INTO bank_import_keys(source_id,book_id,firm_id,company_id,namespace,bank_account,stable_transaction_id,original_external_id,content_fingerprint,created_by,created_at) VALUES(?,?,?,?,'stable-bank','configured-account','immutable-txn','original-row',?,?,'2026-10-12T00:00:00+00:00')", (source_id,case.book,case.owner.firm_id,case.company,digest,case.owner.user_id))
    with case.db.transaction(actor=case.owner) as conn:
        assert not conn.execute('SELECT source_id FROM bank_import_keys WHERE source_id=?', (source_id,)).fetchall()
