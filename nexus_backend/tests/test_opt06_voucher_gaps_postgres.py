"""OPT06-D: PostgreSQL generate_series branch detects missing voucher numbers."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.posting.integrity import VoucherGapService

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 隔离连接")


def test_postgres_gap_scan_uses_generate_series():
    import psycopg

    schema = "opt06d_" + uuid4().hex[:10]
    with psycopg.connect(ADMIN_URL) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
        admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO PUBLIC")
        admin.execute(
            f"CREATE TABLE {schema}.vouchers("
            "id TEXT PRIMARY KEY, book_id TEXT, voucher_type TEXT, voucher_no INTEGER)"
        )
        admin.execute(
            f"INSERT INTO {schema}.vouchers VALUES ('a','book','记',1),('c','book','记',3)"
        )
        admin.execute(f"GRANT SELECT ON {schema}.vouchers TO PUBLIC")
        admin.commit()
    db = Database(APP_URL)
    conn = db.connect()
    try:
        conn.execute(f"SET search_path TO {schema}")
        service = VoucherGapService(db)
        result = service.scan_in_transaction(conn, "book")
        assert result["missing"] == [{"voucher_type": "记", "voucher_no": 2}]
        assert result["types"][0]["contiguous"] is False
    finally:
        conn.close()
        db.close()
        with psycopg.connect(ADMIN_URL) as admin:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")
            admin.commit()
