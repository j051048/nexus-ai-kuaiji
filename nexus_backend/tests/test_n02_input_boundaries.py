"""N02 HTTP and malformed-source boundaries with no model or external services.

Raw invoice batches deliberately represent stored legacy/corrupt input. They do
not bypass formal posting or review. Parser cases use the actual CSV parser and
an explicit synthetic direction, rather than inventing a counterparty field.
"""

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from app.accounting.invariants import MAX_CENTS
from app.invoices.parser import parse_invoice_export
from test_business import business  # noqa: F401 - fixture dependency
from test_n02_source_contract import tax_http  # noqa: F401 - shared real HTTP router
from test_tax_workpaper import profile, row, tax  # noqa: F401 - fixture dependency


@pytest.fixture
def boundary_http(tax, tax_http):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    # Reuse the real router/authentication fixture, observing HTTP 500 responses
    # instead of letting Starlette propagate the underlying exception to pytest.
    with TestClient(tax_http.app, raise_server_exceptions=False) as client:
        yield client


def _raw_batch(case, payload):
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO invoice_import_batches(id,book_id,firm_id,company_id,filename,"
            "storage_key,sha256,size_bytes,row_count,direction,rows_json,notes_json,"
            "created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "n02-input-boundary-batch",
                case.book["id"],
                case.manager.firm_id,
                case.company["id"],
                "n02-synthetic-invoices.csv",
                "n02/input-boundary.csv",
                "c" * 64,
                1024,
                len(payload) if isinstance(payload, list) else 1,
                "mixed",
                json.dumps(payload, ensure_ascii=False),
                "[]",
                case.manager.user_id,
                "2026-10-05T00:00:00+00:00",
            ),
        )


def _create(client, case, period="2026-10"):
    return client.post(
        "/api/tax/workpapers",
        json={
            "book_id": case.book["id"],
            "period": period,
            "reason": "N02 本地输入边界",
            "vat_accounts": [],
        },
    )


def _paper_count(case):
    with case.db.transaction(actor=case.manager) as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM tax_workpapers WHERE book_id=?", (case.book["id"],)
        ).fetchone()["n"]


def _assert_no_paper(case):
    assert _paper_count(case) == 0


def _parsed_row(direction="output", *, buyer="真实解析字段购方", seller="真实解析字段销方"):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        [
            "发票号码",
            "开票日期",
            "销售方名称",
            "销售方纳税人识别号",
            "购买方名称",
            "购买方纳税人识别号",
            "金额",
            "税额",
            "价税合计",
            "发票状态",
        ]
    )
    writer.writerow(
        [
            "000000001",
            "2026-10-05",
            seller,
            "N02SELLER",
            buyer,
            "N02BUYER",
            "1000.00",
            "30.00",
            "1030.00",
            "正常",
        ]
    )
    parsed = parse_invoice_export("n02-input.csv", stream.getvalue().encode("utf-8-sig"))
    assert len(parsed["rows"]) == 1
    result = parsed["rows"][0]
    assert "counterparty" not in result
    return {**result, "direction": direction}


@pytest.mark.parametrize("period", ["2026-13", "2026-1"])
def test_http_invalid_period_returns_422_without_creating_a_workpaper(tax, boundary_http, period):
    _raw_batch(tax, [row("output", 100_000, 3_000)])
    response = _create(boundary_http, tax, period)
    assert response.status_code == 422, response.text
    _assert_no_paper(tax)


@pytest.mark.parametrize(
    "payload",
    [{"unexpected": []}, "not-an-array", None, [None], ["not-an-object"], [1]],
    ids=["object-root", "string-root", "null-root", "null-row", "string-row", "number-row"],
)
def test_malformed_stored_invoice_shape_is_a_controlled_409(tax, boundary_http, payload):
    _raw_batch(tax, payload)
    response = _create(boundary_http, tax)
    assert response.status_code == 409, response.text
    _assert_no_paper(tax)


@pytest.mark.parametrize("shape", ["one-row", "sum-of-valid-rows"])
def test_workpaper_amounts_outside_shared_money_limit_are_rejected(tax, boundary_http, shape):
    if shape == "one-row":
        source = [row("output", MAX_CENTS + 1, 0)]
    else:
        each = MAX_CENTS // 2 + 1
        source = [row("output", each, 0), row("output", each, 0, day="06")]
        assert all(item["amount_cents"] <= MAX_CENTS for item in source)
    _raw_batch(tax, source)
    response = _create(boundary_http, tax)
    persisted = _paper_count(tax)
    assert response.status_code == 409, (
        "Amounts outside the shared MAX_CENTS contract must fail before persistence; "
        f"status={response.status_code}, persisted={persisted}"
    )
    assert persisted == 0


def test_exact_shared_money_upper_bound_remains_supported(tax, boundary_http):
    _raw_batch(tax, [row("output", MAX_CENTS, 0)])
    response = _create(boundary_http, tax)
    assert response.status_code == 201, response.text
    assert response.json()["output_net"] == "999999999999.99"


def test_amount_above_int32_but_within_shared_limit_remains_supported(tax, boundary_http):
    # PostgreSQL storage is being widened to BIGINT; int32 is not a product limit.
    _raw_batch(tax, [row("output", 2_147_483_648, 0)])
    response = _create(boundary_http, tax)
    assert response.status_code == 201, response.text
    assert response.json()["output_net"] == "21474836.48"


@pytest.mark.parametrize("direction,party", [("output", "buyer"), ("input", "seller")])
def test_actual_parser_party_fields_are_presented_for_each_direction(
    tax, boundary_http, direction, party
):
    parsed = _parsed_row(direction)
    _raw_batch(tax, [parsed])
    response = _create(boundary_http, tax)
    assert response.status_code == 201, response.text
    paper = response.json()
    assert paper["lines"][0]["counterparty"] == parsed[party]
    assert paper["source_snapshot"]["invoices"][0]["original"][party] == parsed[party]


@pytest.mark.parametrize("formula", ["=1+1", "+SUM(1,2)", "-1+2", "@SUM(1,2)", " \t=1+1"])
def test_reviewed_csv_prefixes_formula_counterparty_as_text(tax, boundary_http, formula):
    parsed = _parsed_row(buyer=formula)
    _raw_batch(tax, [parsed])
    created = _create(boundary_http, tax)
    assert created.status_code == 201, created.text
    paper = created.json()
    reviewed = boundary_http.post(
        f"/api/tax/workpapers/{paper['id']}/review",
        headers={"X-Test-Actor": "owner"},
        json={
            "book_id": tax.book["id"],
            "expected_version": paper["version"],
            "reason": "不同人员核对合成发票及导出文本",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    result = boundary_http.get(
        f"/api/tax/workpapers/{paper['id']}/csv",
        params={
            "book_id": tax.book["id"],
            "expected_version": paper["version"],
            "source_digest": paper["source_digest"],
        },
    )
    assert result.status_code == 200, result.text
    rows = list(csv.reader(io.StringIO(result.text.lstrip("\ufeff"))))
    party_index = rows[0].index("对方")
    assert rows[1][party_index] == "'" + parsed["buyer"]
    assert rows[1][party_index][0] == "'"
