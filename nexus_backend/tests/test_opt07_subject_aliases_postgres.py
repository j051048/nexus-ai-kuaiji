import os
from uuid import uuid4

import pytest

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")


def test_subject_alias_unique_key_and_company_scope():
    import psycopg
    from psycopg.errors import UniqueViolation
    from scripts.migrate_accounting import migrate

    assert ADMIN_URL
    migrate(ADMIN_URL)
    firm, company, other = (uuid4().hex for _ in range(3))
    alias = "旧牌-PG"
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.subject_aliases WHERE firm_id=%s", (firm,))
        sql = (
            "INSERT INTO accounting.subject_aliases(id,firm_id,company_id,subject_type,subject_id,alias,source,status,effective_from,created_by,created_at) "
            "VALUES(%s,%s,%s,'transport_subject',%s,%s,'test','approved',%s,'tester','2026-10-01T00:00:00+00:00')"
        )
        conn.execute(sql, (uuid4().hex, firm, company, "s1", alias, "2026-10-01"))
        with pytest.raises(UniqueViolation), conn.transaction():
            conn.execute(sql, (uuid4().hex, firm, company, "s2", alias, "2026-10-01"))
        conn.execute(sql, (uuid4().hex, firm, company, "s2", alias, "2026-11-01"))
        conn.execute(sql, (uuid4().hex, firm, other, "s3", alias, "2026-10-01"))
        conn.execute(
            "INSERT INTO accounting.subject_aliases(id,firm_id,company_id,subject_type,subject_id,alias,source,status,effective_from,created_by,created_at) "
            "VALUES(%s,%s,%s,'counterparty',%s,%s,'test','approved',%s,'tester','2026-10-01T00:00:00+00:00')",
            (uuid4().hex, firm, company, "c1", "客商旧称", "2026-10-01"),
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM accounting.subject_aliases WHERE firm_id=%s",
            (firm,),
        ).fetchone()[0]
        assert count == 4
        conn.execute("DELETE FROM accounting.subject_aliases WHERE firm_id=%s", (firm,))
