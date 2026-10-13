import pytest
from app.accounting.domain import AccountingError
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_stage10_revision_history import cross_period

pytestmark=pytest.mark.skipif(not ADMIN_URL or not APP_URL,reason='需要专用 PostgreSQL 管理员和受限运行连接')


def test_pg_history_cap_does_not_open_current_facts_or_later_attachment(pg_ai):
    c=pg_ai;event,old,new,later,history=cross_period(c)
    actor=c.actors['accountant']
    value=history.get(actor,old['book_id'],old['source_id'])
    assert value['payload']['amount']=='100.00' and value['payload']['occurred_on']=='2026-10-03'
    assert later['id'] not in str(value) and new['id'] not in str(value)
    assert history.download(actor,old['book_id'],old['source_id'],event['attachments'][0]['id'])[0]
    with c.db.transaction(actor=actor) as conn:
        assert conn.execute('SELECT * FROM business_events WHERE id=?',(event['id'],)).fetchall()==[]
        assert conn.execute('SELECT * FROM business_attachments WHERE event_id=?',(event['id'],)).fetchall()==[]
        assert conn.execute('SELECT accounting.revision_historical_attachment(?,?,?)',(old['book_id'],old['source_id'],later['id'])).fetchone()[0] is None
    with pytest.raises(AccountingError):
        c.business.get_event(actor,event['id'])
    with c.psycopg.connect(ADMIN_URL) as conn:
        for fn in ('revision_source_history(text,text)','revision_historical_attachment(text,text,text)'):
            assert not conn.execute('SELECT has_function_privilege(%s,%s,%s)',('accounting_worker','accounting.'+fn,'EXECUTE')).fetchone()[0]


def test_pg_history_native_cap_rechecks_book_authorization_after_revocation(pg_ai):
    c=pg_ai;_,old,_,_,history=cross_period(c)
    c.directory.revoke_book_member(c.manager,old['book_id'],c.actors['accountant'].user_id)
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(actor=c.actors['accountant']) as conn:
            conn.execute('SELECT accounting.revision_source_history(?,?)',(old['book_id'],old['source_id']))
