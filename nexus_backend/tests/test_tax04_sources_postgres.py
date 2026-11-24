from __future__ import annotations

import pytest

from app.accounting.domain import AccountingError
from app.tax.sources import TaxSourceAdapter
from test_tax04_sources import COLUMNS, ROWS, xlsx
from test_tax_workpaper_postgres import case, pg  # noqa: F401
import os

pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="需要显式 AK05 四角色隔离 PostgreSQL 运行器",
)


def test_source_import_is_scoped_and_versioned_on_postgres(case):
    adapter = TaxSourceAdapter(case.db)
    first = adapter.import_source(
        case.maker, case.book["id"], filename="运输台账.xlsx", data=xlsx({"运输明细": ROWS}),
        source_system="客户运输台账", external_id="PG-S1", version=1,
        sheet_name="运输明细", column_map=COLUMNS, rule_version="tax-source-v1",
    )
    assert first["amount_total_cents"] == 3000
    assert first["quantity_milli"] == 3500
    replay = adapter.import_source(
        case.maker, case.book["id"], filename="运输台账.xlsx", data=xlsx({"运输明细": ROWS}),
        source_system="客户运输台账", external_id="PG-S1", version=1,
        sheet_name="运输明细", column_map=COLUMNS, rule_version="tax-source-v1",
    )
    assert replay["id"] == first["id"] and replay["replayed"] is True
    newer = adapter.import_source(
        case.maker, case.book["id"], filename="运输台账.xlsx", data=xlsx({"运输明细": ROWS}),
        source_system="客户运输台账", external_id="PG-S1", version=2,
        sheet_name="运输明细", column_map=COLUMNS, rule_version="tax-source-v1",
    )
    assert newer["status"] == "active"
    items = adapter.list_sources(case.maker, case.book["id"])["items"]
    assert next(item for item in items if item["version"] == 1)["status"] == "superseded"
    bound = adapter.bind_output(case.maker, case.book["id"], newer["id"], output_kind="workpaper", output_id="PG-W1", output_version=1)
    checked = adapter.validate_output(case.maker, case.book["id"], output_kind="workpaper", output_id="PG-W1", output_version=1)
    assert bound["frozen"] is False and checked["valid"] is True
    with pytest.raises(AccountingError):
        adapter.import_source(
            case.viewer, case.book["id"], filename="运输台账.xlsx", data=xlsx({"运输明细": ROWS}),
            source_system="客户运输台账", external_id="PG-S2", version=1,
            sheet_name="运输明细", column_map=COLUMNS, rule_version="tax-source-v1",
        )

    contributions = [
        {"row_number": row["row_number"], "amount_cents": row["amount_cents"], "quantity_milli": row["quantity_milli"]}
        for row in newer["rows"] if row["status"] == "adopted"
    ]
    split_row = next(row for row in newer["rows"] if row["external_id"] == "L2")
    next(item for item in contributions if item["row_number"] == split_row["row_number"])["lines"] = [
        {"output_line_id": "pg-line-1", "amount_cents": 1200, "quantity_milli": 1500},
        {"output_line_id": "pg-line-2", "amount_cents": 800, "quantity_milli": 1000},
    ]
    checked_contrib = adapter.record_contributions(
        case.maker, case.book["id"], newer["id"], output_kind="workpaper",
        output_id="PG-W1", output_version=1, contributions=contributions,
    )
    assert checked_contrib == {"rows": 2, "amount_total_cents": 3000, "quantity_milli": 3500}
    stored_contrib = adapter.list_contributions(case.maker, case.book["id"], output_kind="workpaper", output_id="PG-W1")
    assert len(stored_contrib) == 2
    split_stored = next(item for item in stored_contrib if item["row_number"] == split_row["row_number"])
    assert len(split_stored["lines"]) == 2

    adopted = next(row for row in newer["rows"] if row["status"] == "adopted")
    reviewed = adapter.review_row(
        case.maker, case.book["id"], newer["id"], adopted["row_number"],
        status="excluded", reason="PG 人工核对后排除",
    )
    assert next(row for row in reviewed["rows"] if row["row_number"] == adopted["row_number"])["status"] == "excluded"
    with pytest.raises(AccountingError) as stale:
        adapter.validate_output(case.maker, case.book["id"], output_kind="workpaper", output_id="PG-W1", output_version=1)
    assert stale.value.status == 409
