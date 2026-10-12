"""Synthetic local workbench benchmark; no production data or services.

This measures one process's summary query, not concurrent or real-world capacity.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus_backend"))
from app.accounting.database import Database
from app.accounting.seed import LOCAL_ACTOR, bootstrap
from app.accounting.service import AccountingService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--books", type=int, default=500)
    parser.add_argument("--sources", type=int, default=100, help="Sources per book")
    args = parser.parse_args()
    if not 1 <= args.books <= 1000 or not 1 <= args.sources <= 1000:
        parser.error("Use 1–1000 books and 1–1000 sources per book")
    with tempfile.TemporaryDirectory(prefix="accounting-benchmark-") as temporary:
        db = Database(Path(temporary) / "synthetic.db")
        service = AccountingService(db)
        bootstrap(service, seed_examples=False)
        # Fixture writes intentionally skip import/review work; only summary is timed.
        with db.transaction(write=True) as conn:
            for book_number in range(args.books):
                book_id = f"bench-book-{book_number}"
                company_id = f"bench-company-{book_number}"
                conn.execute("INSERT INTO companies(id,name) VALUES(?,?)", (company_id, f"合成企业 {book_number:04d}"))
                conn.execute("INSERT INTO firm_clients(firm_id,company_id) VALUES(?,?)", (LOCAL_ACTOR.firm_id, company_id))
                conn.execute("INSERT INTO books(id,firm_id,name,period,company_id) VALUES(?,?,?,?,?)", (book_id, LOCAL_ACTOR.firm_id, f"合成企业 {book_number:04d}", "2026-10", company_id))
                conn.execute("INSERT INTO book_access(book_id,firm_id,user_id) VALUES(?,?,?)", (book_id, LOCAL_ACTOR.firm_id, LOCAL_ACTOR.user_id))
                conn.execute("INSERT INTO periods VALUES(?,?,'open')", (book_id, "2026-10"))
                conn.executemany("INSERT INTO accounts(book_id,code,name) VALUES(?,?,?)", [(book_id, "1002", "银行存款"), (book_id, "6602.02", "示例办公费")])
                for row in range(args.sources):
                    source_id = f"{book_id}-source-{row}"
                    draft_id = f"{book_id}-draft-{row}"
                    evidence = "" if row % 5 == 0 else "合成测试引用"
                    conn.execute("INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (source_id, book_id, str(row), "benchmark", "2026-10-01", "合成办公费用", "合成对方", 12345, "expense", evidence, source_id))
                    conn.execute("INSERT INTO drafts(id,book_id,source_id,description,explanation) VALUES(?,?,?,?,?)", (draft_id, book_id, source_id, "合成办公费用", "基准测试构造数据"))
                    conn.executemany("INSERT INTO draft_lines VALUES(?,?,?,?,?,?)", [(draft_id, book_id, 0, "6602.02", 12345, 0), (draft_id, book_id, 1, "1002", 0, 12345)])
        statement_count = 0
        original_connect = db.connect

        def count_statement(_statement):
            nonlocal statement_count
            statement_count += 1

        def traced_connect():
            conn = original_connect()
            conn.set_trace_callback(count_statement)
            return conn

        db.connect = traced_connect
        start = perf_counter()
        result = service.workbench(LOCAL_ACTOR)
        elapsed = perf_counter() - start
        expected = args.books * args.sources
        assert result["stats"]["book_count"] == args.books
        assert result["stats"]["pending_count"] == expected
        assert result["stats"]["exception_count"] == args.books * ((args.sources + 4) // 5)
        print(json.dumps({"books": args.books, "sources": expected, "draft_lines": expected * 2, "sql_statements": statement_count, "summary_ms": round(elapsed * 1000, 2), "scope": "synthetic, single process, local SQLite"}, ensure_ascii=False))
        if statement_count > 20:
            raise SystemExit("Workbench summary still makes per-book/per-source queries")


if __name__ == "__main__":
    main()
