"""PostgreSQL primitives execute the same fixture set as Python and HTTP."""
import pytest

from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_stage10_domain import CASES

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用 PostgreSQL 管理及受限运行连接')


def test_postgres_money_and_period_match_shared_contract(business_case):
    c = business_case
    for case in CASES['money']:
        if case['valid']:
            with c.db.transaction(actor=c.owner) as conn:
                assert conn.execute('SELECT accounting.domain_money_cents(?)', (case['value'],)).fetchone()[0] == case['cents']
                assert conn.execute('SELECT accounting.learning_money_cents(?)', (case['value'],)).fetchone()[0] == case['cents']
        else:
            with pytest.raises(c.psycopg.Error):
                with c.db.transaction(actor=c.owner) as conn:
                    conn.execute('SELECT accounting.domain_money_cents(?)', (case['value'],)).fetchone()
            if isinstance(case['value'], str):
                with c.db.transaction(actor=c.owner) as conn:
                    assert conn.execute('SELECT accounting.learning_money_cents(?)', (case['value'],)).fetchone()[0] is None
    with c.db.transaction(actor=c.owner) as conn:
        for case in CASES['period']:
            assert conn.execute('SELECT accounting.domain_period_valid(?)', (case['value'],)).fetchone()[0] is case['valid']
