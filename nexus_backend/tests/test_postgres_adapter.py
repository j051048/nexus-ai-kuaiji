import sqlite3
from decimal import Decimal

from app.accounting.database import CompatibleRow, Database, migration_files, postgres_sql


def test_postgres_row_preserves_mapping_index_and_exact_sum():
    row = CompatibleRow(["id", "amount_cents"], ["source", Decimal("99999999999999")])
    assert row[0] == row["id"] == "source"
    assert row[1] == row["amount_cents"] == 99999999999999
    assert isinstance(row[1], int)
    assert dict(row) == {"id": "source", "amount_cents": 99999999999999}


def test_postgres_bind_translation_skips_literals_comments_and_dollar_quotes():
    sql = "SELECT ?, '?' AS mark, \"?column\", $$?$$, $body$?$body$ -- ?\n/* ? */ WHERE x=?"
    assert postgres_sql(sql) == "SELECT %s, '?' AS mark, \"?column\", $$?$$, $body$?$body$ -- ?\n/* ? */ WHERE x=%s"
    assert postgres_sql("INSERT OR IGNORE INTO users(id,name) VALUES(?,?)") == "INSERT INTO users(id,name) VALUES(%s,%s) ON CONFLICT DO NOTHING"


def test_legacy_sqlite_upgrade_preserves_sources_reviews_exports_and_authorizations(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(migration_files("sqlite")[0].read_text(encoding="utf-8"))
    conn.executescript("""
     INSERT INTO firms VALUES('f','旧事务所');
     INSERT INTO users(id,name) VALUES('u','旧会计');
     INSERT INTO memberships VALUES('f','u','reviewer');
     INSERT INTO books VALUES('b','f','旧客户账套','2026-10');
     INSERT INTO book_access VALUES('b','f','u');
     INSERT INTO periods VALUES('b','2026-10','closed');
     INSERT INTO accounts(book_id,code,name) VALUES('b','1002','银行存款');
     INSERT INTO sources VALUES('s','b','bank-001','旧文件','2026-10-01','旧事项','旧供应商',30,'expense','旧发票','hash');
     INSERT INTO drafts(id,book_id,source_id,description,status,version,explanation,approved_by,approved_at,exported_at)
       VALUES('d','b','s','旧事项','exported',3,'旧审核依据','u','approved-time','exported-time');
     INSERT INTO draft_lines VALUES('d','b',0,'1002',0,30);
     INSERT INTO source_evidence(source_id,book_id,evidence_ref,created_at,created_by) VALUES('s','b','旧补证','evidence-time','u');
     INSERT INTO exports VALUES('e','b','old-idempotency','request-hash','old-csv',1,'old.csv','exported-time','u');
     INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES('f','b','u','draft.approve','d','old-audit','approved-time');
    """)
    conn.commit()
    conn.close()
    db = Database(path)
    with db.transaction() as conn:
        assert conn.execute("SELECT company_id FROM books WHERE id='b'").fetchone()[0] == "legacy-company-b"
        assert dict(conn.execute("SELECT * FROM book_access").fetchone()) == {"book_id": "b", "firm_id": "f", "user_id": "u", "role": "reviewer"}
        assert conn.execute("SELECT role FROM memberships").fetchone()[0] == "reviewer"
        assert conn.execute("SELECT amount_cents FROM sources").fetchone()[0] == 30
        assert conn.execute("SELECT version,approved_by,status FROM drafts").fetchone()[:] == (3, "u", "exported")
        assert conn.execute("SELECT csv FROM exports").fetchone()[0] == "old-csv"
        assert conn.execute("SELECT details FROM audit_events").fetchone()[0] == "old-audit"
        assert conn.execute("SELECT evidence_ref FROM source_evidence").fetchone()[0] == "旧补证"
        assert conn.execute("SELECT status FROM periods").fetchone()[0] == "closed"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    Database(path)
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 1
        expected = {int(path.name.split("_", 1)[0]) for path in migration_files("sqlite")}
        assert {row[0] for row in conn.execute("SELECT version FROM schema_migrations")} == expected
