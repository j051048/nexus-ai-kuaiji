"""Batch 05：应收核销可回溯到具体银行流水行。"""

from test_business import business  # noqa: F401  (pytest fixture re-export)


def test_allocation_can_point_at_a_bank_statement_line(business):
    with business.db.transaction(write=True) as conn:
        columns = {
            row["name"]: row
            for row in conn.execute("PRAGMA table_info(fee_receivable_allocations)")
        }
        foreign = {
            row["from"]: row["table"]
            for row in conn.execute("PRAGMA foreign_key_list(fee_receivable_allocations)")
        }
    assert "bank_statement_line_id" in columns
    assert columns["bank_statement_line_id"]["notnull"] == 0
    assert foreign["bank_statement_line_id"] == "bank_statement_lines"
