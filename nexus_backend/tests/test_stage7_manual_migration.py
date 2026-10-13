"""Upgrade protects earlier human edits; the marker cannot be cleared by SQL."""
import json
import sqlite3

import pytest

from app.accounting import database
from app.accounting.seed import LOCAL_ACTOR


def test_pre_stage7_manual_edit_is_backfilled_and_cannot_be_cleared(tmp_path,monkeypatch):
    original=database.migration_files
    monkeypatch.setattr(database,'migration_files',lambda dialect:[path for path in original(dialect) if int(path.name.split('_')[0])<16])
    path=tmp_path/'before-manual-guard.db'
    db=database.Database(path)
    # Build only records the historical schema understood. Calling today's
    # domain methods here would incorrectly query later case/learning tables.
    book,source,draft='historical-book','historical-source','historical-draft'
    lines=[{'account_code':'6602.02','debit':'113.00','credit':'0.00'}, {'account_code':'1002','debit':'0.00','credit':'113.00'}]
    with db.transaction(write=True) as conn:
        conn.execute("INSERT INTO users(id,name,email) VALUES(?,'历史复核会计','historical@example.invalid')",(LOCAL_ACTOR.user_id,))
        conn.execute("INSERT INTO firms(id,name,created_by) VALUES(?,'历史事务所',?)",(LOCAL_ACTOR.firm_id,LOCAL_ACTOR.user_id))
        conn.execute("INSERT INTO memberships(firm_id,user_id,role) VALUES(?,?,'owner')",(LOCAL_ACTOR.firm_id,LOCAL_ACTOR.user_id))
        conn.execute("INSERT INTO companies(id,name) VALUES('historical-company','历史客户企业')")
        conn.execute("INSERT INTO firm_clients(firm_id,company_id) VALUES(?,'historical-company')",(LOCAL_ACTOR.firm_id,))
        conn.execute("INSERT INTO books(id,firm_id,company_id,name,period) VALUES(?,?,'historical-company','历史人工草稿','2026-10')",(book,LOCAL_ACTOR.firm_id))
        conn.execute("INSERT INTO book_access(book_id,firm_id,user_id,role) VALUES(?,?,?,'reviewer')",(book,LOCAL_ACTOR.firm_id,LOCAL_ACTOR.user_id))
        conn.execute("INSERT INTO periods VALUES(?,'2026-10','open')",(book,))
        conn.executemany('INSERT INTO accounts(book_id,code,name) VALUES(?,?,?)',[(book,'6602.02','办公费用'),(book,'1002','银行存款')])
        conn.execute("INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint) VALUES(?,?,'historical-external','历史来源','2026-10-03','历史原始摘要','历史供应商',11300,'expense','历史归档票据',?)",(source,book,'f'*64))
        conn.execute("INSERT INTO drafts(id,book_id,source_id,description,version,explanation,last_manual_edit_version) VALUES(?,?,?,'历史人工修订',2,'历史规则建议',0)",(draft,book,source))
        conn.executemany('INSERT INTO draft_lines VALUES(?,?,?,?,?,?)',[(draft,book,0,'6602.02',11300,0),(draft,book,1,'1002',0,11300)])
        details={'before':{'version':1,'description':'历史原始摘要','lines':lines},'after':{'version':2,'description':'历史人工修订','lines':lines}}
        conn.execute("INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,'draft.update',?,?,'2026-10-11T00:00:00+00:00')",(LOCAL_ACTOR.firm_id,book,LOCAL_ACTOR.user_id,draft,json.dumps(details,ensure_ascii=False)))
    monkeypatch.setattr(database,'migration_files',original)
    upgraded=database.Database(path)
    with upgraded.transaction() as conn:
        row=conn.execute('SELECT version,last_manual_edit_version,description FROM drafts WHERE id=?',(draft,)).fetchone()
        assert row['version']==row['last_manual_edit_version']==2 and row['description']=='历史人工修订'
        assert conn.execute("SELECT count(*) FROM audit_events WHERE object_id=? AND action='draft.update'",(draft,)).fetchone()[0]==1
    with pytest.raises(sqlite3.IntegrityError,match='cannot be cleared'):
        with upgraded.transaction(write=True) as conn:
            conn.execute('UPDATE drafts SET last_manual_edit_version=0 WHERE id=?',(draft,))
