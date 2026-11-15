from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.accounting.domain import AccountingError
from app.tax.sources import TaxSourceAdapter, validate_contributions
from test_business import business  # noqa: F401


def _sheet(rows, formula=False):
    body = []
    for index, values in enumerate(rows, 1):
        cells = []
        for col, value in enumerate(values):
            ref = chr(65 + col) + str(index)
            if isinstance(value, str):
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
        body.append(f'<row r="{index}">{"".join(cells)}</row>')
    if formula:
        body.append('<row r="99"><c r="A99"><f>1+1</f><v>2</v></c></row>')
    return (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(body)}</sheetData></worksheet>'
    )


def xlsx(sheets, formulas=False):
    output = BytesIO()
    names = list(sheets)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            + "".join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(names, 1))
            + "</sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(names) + 1))
            + "</Relationships>",
        )
        for i, name in enumerate(names, 1):
            archive.writestr(f"xl/worksheets/sheet{i}.xml", _sheet(sheets[name], formula=formulas and i == 1))
    return output.getvalue()


HEADER = ["行ID", "日期", "摘要", "金额", "数量", "目标"]
ROWS = [
    HEADER,
    ["L1", "2026-10-01", "机场接送", "10.00", "1", ""],
    ["L2", "2026-10-02", "包车", "20.00", "2.5", ""],
    ["L3", "2026-10-03", "零额", "0", "0", ""],
    ["L4", "2026-09-30", "上月", "99.00", "1", ""],
    ["L2", "2026-10-02", "包车", "20.00", "2.5", ""],
]
COLUMNS = {"external_id": "行ID", "date": "日期", "description": "摘要", "amount": "金额", "quantity": "数量", "target_id": "目标"}


def _import(case, data=None, version=1, book_id=None, sheet="运输明细", columns=None, key="S1"):
    return TaxSourceAdapter(case.db).import_source(
        case.manager,
        book_id or case.book["id"],
        filename="运输台账.xlsx",
        data=data or xlsx({"说明": [["说明"], ["合成数据"]], "运输明细": ROWS}),
        source_system="客户运输台账",
        external_id=key,
        version=version,
        sheet_name=sheet,
        column_map=columns or COLUMNS,
        rule_version="tax-source-v1",
    )


def test_multisheet_selects_named_sheet_and_keeps_amount_and_quantity_separate(business):
    result = _import(business)
    assert result["sheet_name"] == "运输明细"
    assert result["amount_total_cents"] == 3000
    assert result["quantity_milli"] == 3500
    statuses = {row["external_id"]: row["status"] for row in result["rows"]}
    assert statuses["L1"] == "adopted"
    assert statuses["L3"] == "excluded"
    assert statuses["L4"] == "excluded"
    assert sum(1 for row in result["rows"] if row["status"] == "needs_review") == 1


def test_same_version_replays_and_changed_payload_conflicts(business):
    data = xlsx({"运输明细": ROWS})
    first = _import(business, data=data)
    again = _import(business, data=data)
    assert again["id"] == first["id"] and again["replayed"] is True
    with pytest.raises(AccountingError) as error:
        _import(business, data=xlsx({"运输明细": ROWS[:2]}))
    assert error.value.status == 409


def test_new_version_supersedes_old_and_is_listed(business):
    _import(business, version=1)
    second = _import(business, data=xlsx({"运输明细": ROWS}), version=2)
    items = TaxSourceAdapter(business.db).list_sources(business.manager, business.book["id"])["items"]
    assert second["status"] == "active"
    assert next(item for item in items if item["version"] == 1)["status"] == "superseded"
    with pytest.raises(AccountingError) as error:
        _import(business, version=1)
    assert error.value.status == 409


def test_formula_and_missing_sheet_are_rejected(business):
    with pytest.raises(AccountingError):
        _import(business, data=xlsx({"运输明细": ROWS}, formulas=True))
    with pytest.raises(AccountingError):
        _import(business, sheet="不存在")


def test_zero_out_of_period_and_duplicate_rows_are_not_adopted(business):
    result = _import(business)
    assert all(row["reason"] for row in result["rows"] if row["status"] != "adopted")


def test_other_book_does_not_merge_same_external_id(business):
    first = _import(business, version=1)
    other = _import(business, book_id=business.other_book["id"], version=1)
    assert other["id"] != first["id"]
    assert other["amount_total_cents"] == 3000


def test_viewer_cannot_import_and_unknown_target_requires_review(business):
    with pytest.raises(AccountingError):
        TaxSourceAdapter(business.db).import_source(
            business.actors["employee"], business.book["id"], filename="x.xlsx", data=xlsx({"运输明细": ROWS}),
            source_system="客户运输台账", external_id="S1", version=1, sheet_name="运输明细",
            column_map=COLUMNS, rule_version="v1",
        )
    rows = [HEADER, ["L1", "2026-10-01", "机场接送", "10.00", "1", "missing-target"]]
    result = _import(business, data=xlsx({"运输明细": rows}), key="S2")
    assert result["rows"][0]["status"] == "needs_review"
    assert result["amount_total_cents"] == 0


def test_source_upgrade_invalidates_unfrozen_output_binding(business):
    adapter = TaxSourceAdapter(business.db)
    first = _import(business, version=1)
    bound = adapter.bind_output(
        business.manager, business.book["id"], first["id"],
        output_kind="workpaper", output_id="W-1", output_version=1, frozen=False,
    )
    assert bound["frozen"] is False
    _import(business, version=2)
    with pytest.raises(AccountingError) as error:
        adapter.validate_output(
            business.manager, business.book["id"],
            output_kind="workpaper", output_id="W-1", output_version=1,
        )
    assert error.value.status == 409


def test_frozen_output_keeps_historical_binding_after_source_upgrade(business):
    adapter = TaxSourceAdapter(business.db)
    first = _import(business, version=1)
    adapter.bind_output(
        business.manager, business.book["id"], first["id"],
        output_kind="approval", output_id="A-1", output_version=1, frozen=True,
    )
    _import(business, version=2)
    checked = adapter.validate_output(
        business.manager, business.book["id"],
        output_kind="approval", output_id="A-1", output_version=1,
    )
    assert checked["valid"] is True and checked["historical"] is True
    assert checked["source_fingerprint"] == first["fingerprint"]


def test_contributions_must_cover_adopted_rows_with_exact_values(business):
    adapter = TaxSourceAdapter(business.db)
    source = _import(business, version=1)
    adopted = [row for row in source["rows"] if row["status"] == "adopted"]
    contributions = [
        {"row_number": row["row_number"], "amount_cents": row["amount_cents"], "quantity_milli": row["quantity_milli"]}
        for row in adopted
    ]
    checked = validate_contributions(source, contributions)
    assert checked == {"rows": 2, "amount_total_cents": 3000, "quantity_milli": 3500}
    with pytest.raises(AccountingError) as mismatch:
        validate_contributions(source, [{**contributions[0], "amount_cents": 1}, contributions[1]])
    assert mismatch.value.status == 409
    with pytest.raises(AccountingError) as missing:
        validate_contributions(source, contributions[:1])
    assert missing.value.status == 409
    with pytest.raises(AccountingError) as duplicate:
        validate_contributions(source, contributions + contributions[:1])
    assert duplicate.value.status == 409
    excluded = next(row for row in source["rows"] if row["status"] == "excluded")
    with pytest.raises(AccountingError) as rejected:
        validate_contributions(source, contributions + [{"row_number": excluded["row_number"], "amount_cents": 0, "quantity_milli": 0}])
    assert rejected.value.status == 409


def test_manager_cannot_override_unsettled_facts_but_can_exclude_adopted_row(business):
    adapter = TaxSourceAdapter(business.db)
    source = _import(business, version=1)
    excluded = next(row for row in source["rows"] if row["status"] == "excluded")
    with pytest.raises(AccountingError) as unsettled:
        adapter.review_row(
            business.manager, business.book["id"], source["id"], excluded["row_number"],
            status="adopted", reason="客户确认零额行仍需保留",
        )
    assert unsettled.value.status == 409
    adopted = next(row for row in source["rows"] if row["status"] == "adopted")
    reviewed = adapter.review_row(
        business.manager, business.book["id"], source["id"], adopted["row_number"],
        status="excluded", reason="客户撤回该行",
    )
    reviewed_row = next(row for row in reviewed["rows"] if row["row_number"] == adopted["row_number"])
    assert reviewed_row["status"] == "excluded"
    assert reviewed["amount_total_cents"] == 2000
    assert reviewed["quantity_milli"] == 2500
    with pytest.raises(AccountingError) as rejected:
        adapter.review_row(
            business.actors["employee"], business.book["id"], source["id"], adopted["row_number"],
            status="excluded", reason="无权限",
        )
    assert rejected.value.status in {403, 404}


def test_reviewing_source_invalidates_unfrozen_binding_but_keeps_frozen(business):
    adapter = TaxSourceAdapter(business.db)
    source = _import(business, version=1)
    adapter.bind_output(business.manager, business.book["id"], source["id"],
                        output_kind="workpaper", output_id="W-2", output_version=1)
    adapter.bind_output(business.manager, business.book["id"], source["id"],
                        output_kind="approval", output_id="A-2", output_version=1, frozen=True)
    adopted = next(row for row in source["rows"] if row["status"] == "adopted")
    adapter.review_row(business.manager, business.book["id"], source["id"], adopted["row_number"],
                       status="excluded", reason="客户撤回")
    with pytest.raises(AccountingError) as stale:
        adapter.validate_output(business.manager, business.book["id"],
                                output_kind="workpaper", output_id="W-2", output_version=1)
    assert stale.value.status == 409
    frozen = adapter.validate_output(business.manager, business.book["id"],
                                     output_kind="approval", output_id="A-2", output_version=1)
    assert frozen["valid"] is True
    assert frozen["historical"] is True
    assert frozen["invalidated_at"] is None


def test_contributions_are_persisted_against_the_bound_output(business):
    service = TaxSourceAdapter(business.db)
    source = _import(business, version=1)
    service.bind_output(business.manager, business.book["id"], source["id"],
                        output_kind="workpaper", output_id="WP-CONTRIB", output_version=1)
    rows = [row for row in source["rows"] if row["status"] == "adopted"]
    by_ext = {row["external_id"]: row for row in rows}
    contributions = [
        {"row_number": by_ext["L1"]["row_number"], "amount_cents": 1000, "quantity_milli": 1000,
         "lines": [{"output_line_id": "line-1", "amount_cents": 1000, "quantity_milli": 1000}]},
        {"row_number": by_ext["L2"]["row_number"], "amount_cents": 2000, "quantity_milli": 2500,
         "lines": [
             {"output_line_id": "line-2", "amount_cents": 1200, "quantity_milli": 1500},
             {"output_line_id": "line-3", "amount_cents": 800, "quantity_milli": 1000},
         ]},
    ]
    checked = service.record_contributions(
        business.manager, business.book["id"], source["id"],
        output_kind="workpaper", output_id="WP-CONTRIB", output_version=1,
        contributions=contributions,
    )
    assert checked == {"rows": 2, "amount_total_cents": 3000, "quantity_milli": 3500}
    stored = service.list_contributions(business.manager, business.book["id"],
                                        output_kind="workpaper", output_id="WP-CONTRIB")
    assert [row["row_number"] for row in stored] == [row["row_number"] for row in rows]
    assert sum(row["amount_cents"] for row in stored) == 3000
    split = next(row for row in stored if row["row_number"] == by_ext["L2"]["row_number"])
    assert [line["output_line_id"] for line in split["lines"]] == ["line-2", "line-3"]
    assert sum(line["amount_cents"] for line in split["lines"]) == 2000

    with pytest.raises(AccountingError) as duplicate:
        service.record_contributions(
            business.manager, business.book["id"], source["id"],
            output_kind="workpaper", output_id="WP-CONTRIB", output_version=1,
            contributions=[{**contributions[1], "lines": [contributions[1]["lines"][0], contributions[1]["lines"][0]]}],
        )
    assert duplicate.value.status == 409
    with pytest.raises(AccountingError) as mismatch:
        service.record_contributions(
            business.manager, business.book["id"], source["id"],
            output_kind="workpaper", output_id="WP-CONTRIB", output_version=1,
            contributions=[{**contributions[1], "lines": [{"output_line_id": "line-2", "amount_cents": 1, "quantity_milli": 1}]}],
        )
    assert mismatch.value.status == 409
    with pytest.raises(AccountingError) as missing:
        service.record_contributions(
            business.manager, business.book["id"], source["id"],
            output_kind="workpaper", output_id="WP-CONTRIB", output_version=1,
            contributions=contributions[:1],
        )
    assert missing.value.status == 409



def test_multiple_sources_merge_into_same_output_version(business):
    adapter = TaxSourceAdapter(business.db)
    first = _import(business, key="S-MULTI-A")
    second = _import(business, key="S-MULTI-B")
    for source in (first, second):
        adapter.bind_output(
            business.manager, business.book["id"], source["id"],
            output_kind="workpaper", output_id="WP-MULTI", output_version=1,
        )
    checked = adapter.validate_output(
        business.manager, business.book["id"],
        output_kind="workpaper", output_id="WP-MULTI", output_version=1,
    )
    assert len(checked["sources"]) == 2
    for source in (first, second):
        adopted = [row for row in source["rows"] if row["status"] == "adopted"]
        adapter.record_contributions(
            business.manager, business.book["id"], source["id"],
            output_kind="workpaper", output_id="WP-MULTI", output_version=1,
            contributions=[{"row_number": row["row_number"], "amount_cents": row["amount_cents"],
                            "quantity_milli": row["quantity_milli"]} for row in adopted],
        )
    rows = adapter.list_contributions(
        business.manager, business.book["id"],
        output_kind="workpaper", output_id="WP-MULTI", output_version=1,
    )
    assert len(rows) == 4
    assert {row["source_file_id"] for row in rows} == {first["id"], second["id"]}


def test_aggregate_contributions_sums_multiple_sources(business):
    adapter = TaxSourceAdapter(business.db)
    sources = [_import(business, key=f"S-AGG-{index}") for index in (1, 2)]
    for source in sources:
        adapter.bind_output(
            business.manager, business.book["id"], source["id"],
            output_kind="workpaper", output_id="WP-AGG", output_version=1,
        )
        adopted = [row for row in source["rows"] if row["status"] == "adopted"]
        adapter.record_contributions(
            business.manager, business.book["id"], source["id"],
            output_kind="workpaper", output_id="WP-AGG", output_version=1,
            contributions=[{"row_number": row["row_number"], "amount_cents": row["amount_cents"],
                            "quantity_milli": row["quantity_milli"]} for row in adopted],
        )
    aggregate = adapter.aggregate_contributions(
        business.manager, business.book["id"],
        output_kind="workpaper", output_id="WP-AGG", output_version=1,
    )
    assert aggregate["source_count"] == 2
    assert aggregate["row_count"] == 4
    assert aggregate["amount_total_cents"] == 6000
    assert aggregate["quantity_milli"] == 7000


def test_batch_import_continues_after_one_file_fails(business):
    adapter = TaxSourceAdapter(business.db)
    good = {
        "filename": "good.xlsx", "data": xlsx({"运输明细": ROWS}),
        "source_system": "客户运输台账", "external_id": "S-BATCH-GOOD", "version": 1,
        "sheet_name": "运输明细", "column_map": COLUMNS, "rule_version": "tax-source-v1",
    }
    bad = {
        "filename": "bad.xlsx", "data": xlsx({"运输明细": ROWS}),
        "source_system": "客户运输台账", "external_id": "S-BATCH-BAD", "version": 1,
        "sheet_name": "不存在的页签", "column_map": COLUMNS, "rule_version": "tax-source-v1",
    }
    result = adapter.import_batch(business.manager, business.book["id"], [good, bad])
    assert result["imported"] == 1
    assert result["failed"] == 1
    assert [item["external_id"] for item in result["items"] if item["status"] == "imported"] == ["S-BATCH-GOOD"]
    assert [item["external_id"] for item in result["items"] if item["status"] == "failed"] == ["S-BATCH-BAD"]
    assert result["items"][1]["retryable"] is True
    sources = adapter.list_sources(business.manager, business.book["id"])["items"]
    assert [item["external_id"] for item in sources] == ["S-BATCH-GOOD"]
