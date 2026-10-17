"""电子税务局发票导出表导入：解析有界、批内批间去重、银行候选与原件完整性。"""

import hashlib
import io
import zipfile
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate, BookMemberGrant, CompanyCreate
from app.business.storage import AttachmentStore
from app.invoices.models import InvoiceAccrualItem, InvoiceAccrualRequest
from app.invoices.parser import parse_invoice_export, validate_invoice_export
from app.invoices.router import build_invoice_imports_router
from app.invoices.service import InvoiceImportService
from app.uploads import UploadCreate, UploadIntentService
from test_business import business


COMPANY_TAX = "91320100MA1TEST01X"
HEADERS = (
    "发票种类,发票代码,发票号码,开票日期,销方名称,销方纳税人识别号,"
    "购方名称,购方纳税人识别号,金额,税额,价税合计,发票状态"
)


def csv_bytes(rows, headers=HEADERS, encoding="utf-8-sig"):
    return ("\n".join([headers, *rows]) + "\n").encode(encoding)


def row(
    index,
    day="2026-10-05",
    seller="客户企业",
    seller_tax=COMPANY_TAX,
    buyer="供应商",
    buyer_tax="91320100SUPPLIER01",
    amount="1000.00",
    tax="130.00",
    total="1130.00",
    status="正常",
    kind="数电票（增值税专用发票）",
):
    return f"{kind},{''},{index},{day},{seller},{seller_tax},{buyer},{buyer_tax},{amount},{tax},{total},{status}"


def xlsx(sheet):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="发票" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def text_cell(ref, value):
    return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'


def pdf(payload=b"x" * 4096):
    return b"%PDF-1.4\n" + payload + b"\n%%EOF"


@pytest.fixture
def invoices(business, tmp_path):
    c = business
    company = c.directory.create_company(
        c.manager, CompanyCreate(name="发票客户企业", tax_id=COMPANY_TAX)
    )
    book = c.ledger.create_book(
        c.manager,
        BookCreate(name="发票客户账套", period="2026-10", company_id=company["id"]),
    )
    c.directory.grant_book_member(
        c.manager, book["id"], "accountant", BookMemberGrant(role="reviewer")
    )
    c.company, c.book = company, book
    store = AttachmentStore(tmp_path / "invoice-originals")
    uploads = UploadIntentService(c.db, store)
    service = InvoiceImportService(c.db, store, uploads)
    c.store, c.uploads, c.invoices = store, uploads, service
    return c


def upload(
    c,
    actor,
    data,
    filename="全量发票查询导出表.csv",
    media_type="text/csv",
    purpose="invoice_import",
):
    created = c.uploads.create(
        actor,
        UploadCreate(
            purpose=purpose,
            filename=filename,
            media_type=media_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        ),
    )
    c.uploads.store_content(actor, created["id"], data)
    return created


def add_source(c, actor, source_id, counterparty, amount_cents, direction, day="2026-10-06"):
    with c.db.transaction(write=True, actor=actor) as conn:
        conn.execute(
            "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,"
            "amount_cents,direction,evidence_ref,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                c.book["id"],
                f"ext-{source_id}",
                "测试银行",
                day,
                "发票勾稽测试",
                counterparty,
                amount_cents,
                direction,
                "银行流水",
                hashlib.sha256(source_id.encode()).hexdigest(),
            ),
        )


def add_tax_accounts(c, actor):
    with c.db.transaction(write=True, actor=actor) as conn:
        conn.executemany(
            "INSERT INTO accounts(book_id,code,name,category,normal_balance) VALUES(?,?,?,?,?)",
            [
                (c.book["id"], "2221.01", "应交税费—应交增值税（销项税额）", "liability", "credit"),
                (c.book["id"], "2221.02", "应交税费—应交增值税（进项税额）", "asset", "debit"),
            ],
        )


def test_csv_export_parses_gbk_void_red_and_in_file_duplicates():
    rows = [
        row("25442000000000000001"),
        row(
            "25442000000000000002",
            seller="供应商",
            seller_tax="91320100SUPPLIER01",
            buyer="客户企业",
            buyer_tax=COMPANY_TAX,
        ),
        row("25442000000000000003", status="作废"),
        row("25442000000000000001"),
        row("", day="2026-10-04"),
    ]
    parsed = parse_invoice_export("全量发票导出.csv", csv_bytes(rows, encoding="gb18030"))
    assert parsed["source"] == "CSV (GB18030)"
    assert parsed["row_count"] == 4 and parsed["skipped"] == 1
    first, second, void, duplicate = parsed["rows"]
    assert first["number"] == "25442000000000000001"
    assert first["date"] == "2026-10-05" and first["total_cents"] == 113000
    assert first["buyer_tax"] == "91320100SUPPLIER01" and not first["void"]
    assert second["seller"] == "供应商" and second["seller_tax"] == "91320100SUPPLIER01"
    assert void["void"] and not void["duplicate"]
    assert duplicate["duplicate"] and duplicate["key"] == first["key"]
    assert any("重复" in warning for warning in parsed["warnings"])
    assert any("缺少发票号码" in warning for warning in parsed["warnings"])


def test_parser_derives_total_amount_and_rejects_statement_or_xls():
    rows = [row("25442000000000000011", amount="200.00", tax="26.00", total="")]
    parsed = parse_invoice_export("导出.csv", csv_bytes(rows))
    assert parsed["rows"][0]["total_cents"] == 22600
    statement = csv_bytes(["交易日期,交易对手,金额,方向"], headers="交易日期,交易对手,金额,方向")
    with pytest.raises(AccountingError, match="未识别"):
        parse_invoice_export("流水.csv", statement)
    with pytest.raises(AccountingError, match=".xls"):
        parse_invoice_export("旧版.xls", b"AAAB")
    with pytest.raises(AccountingError, match="表头"):
        parse_invoice_export("空.csv", HEADERS.encode())
    with pytest.raises(AccountingError, match="10 MB"):
        parse_invoice_export("大.csv", b"x" * (10 * 1024 * 1024 + 1))


def test_parser_rejects_export_with_no_valid_invoice_rows():
    with pytest.raises(AccountingError, match="没有可导入"):
        parse_invoice_export("空号码.csv", csv_bytes([row("")]))


def test_parser_rejects_row_overflow_and_bad_money_becomes_warning():
    rows = [row(f"25442000000{i:011d}") for i in range(5001)]
    with pytest.raises(AccountingError, match="5,000"):
        parse_invoice_export("超量.csv", csv_bytes(rows))
    parsed = parse_invoice_export(
        "导出.csv", csv_bytes([row("25442000000000000021", total='"1,130.00"', amount="￥1000.00")])
    )
    assert parsed["rows"][0]["total_cents"] == 113000
    assert parsed["rows"][0]["amount_cents"] == 100000
    parsed = parse_invoice_export(
        "导出.csv",
        csv_bytes([row("25442000000000000022", amount="", tax="", total="不是金额")]),
    )
    assert parsed["rows"][0]["total_cents"] is None
    assert any("价税合计" in warning for warning in parsed["warnings"])


def test_xlsx_export_parses_numeric_money_and_serial_date():
    serial = (datetime(2026, 10, 5) - datetime(1899, 12, 30)).days
    header = "".join(
        text_cell(f"{column}1", name)
        for column, name in zip(
            "ABCDEFGHIJKL",
            (
                "发票种类",
                "发票代码",
                "发票号码",
                "开票日期",
                "销方名称",
                "销方纳税人识别号",
                "购方名称",
                "购方纳税人识别号",
                "金额",
                "税额",
                "价税合计",
                "发票状态",
            ),
        )
    )
    values = [
        text_cell("A2", "数电票"),
        text_cell("B2", ""),
        text_cell("C2", "25442000000000000031"),
        f'<c r="D2"><v>{serial}</v></c>',
        text_cell("E2", "客户企业"),
        text_cell("F2", COMPANY_TAX),
        text_cell("G2", "供应商"),
        text_cell("H2", "91320100SUPPLIER01"),
        '<c r="I2"><v>1000.00</v></c>',
        '<c r="J2"><v>130.00</v></c>',
        '<c r="K2"><v>1130.00</v></c>',
        text_cell("L2", "正常"),
    ]
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData><row r="1">{header}</row><row r="2">{"".join(values)}</row></sheetData></worksheet>'
    )
    parsed = parse_invoice_export("全量发票.xlsx", xlsx(sheet))
    assert parsed["source"] == "Excel"
    line = parsed["rows"][0]
    assert line["date"] == "2026-10-05" and line["total_cents"] == 113000
    assert line["number"] == "25442000000000000031"
    with pytest.raises(AccountingError, match="公式"):
        parse_invoice_export(
            "全量发票.xlsx", xlsx(sheet.replace("<v>1130.00</v>", "<f>1+1</f><v>1130.00</v>"))
        )


def test_validate_invoice_export_returns_detected_media_and_rejects_claims():
    data = csv_bytes([row("25442000000000000041")])
    assert validate_invoice_export("导出.CSV", "text/csv", data)[1] == "text/csv"
    assert validate_invoice_export("导出.csv", "", data)[0] == "导出.csv"
    with pytest.raises(AccountingError, match="声明格式"):
        validate_invoice_export("导出.csv", "application/pdf", data)
    with pytest.raises(AccountingError, match="1–150"):
        validate_invoice_export("", "text/csv", data)


def test_import_batch_records_evidence_dedup_and_bank_candidates(invoices):
    c = invoices
    actor = c.actors["accountant"]
    add_source(c, actor, "source-out", "采购方甲公司", 113000, "income")
    add_source(c, actor, "source-in", "供应商乙公司", 226000, "expense")
    payload = csv_bytes(
        [
            row(
                "25442000000000000051",
                seller="第一客户企业",
                seller_tax=COMPANY_TAX,
                buyer="采购方甲公司",
                buyer_tax="91320100BUYER0001X",
            ),
            row(
                "25442000000000000052",
                seller="供应商乙公司",
                seller_tax="91320100SUPPLIER01",
                buyer="第一客户企业",
                buyer_tax=COMPANY_TAX,
                amount="2000.00",
                tax="260.00",
                total="2260.00",
            ),
            row("25442000000000000051"),
        ]
    )
    created = upload(c, actor, payload)
    batch = c.invoices.create(actor, c.book["id"], created["id"])
    assert batch["row_count"] == 3 and batch["duplicates"] == 1 and batch["matched"] == 2
    assert batch["direction"] == "mixed"
    with c.db.transaction(actor=actor) as conn:
        intent = conn.execute(
            "SELECT state,consumed_ref FROM upload_intents WHERE id=?", (created["id"],)
        ).fetchone()
        audit = conn.execute(
            "SELECT * FROM audit_events WHERE action='invoice.import' AND object_id=?",
            (batch["id"],),
        ).fetchone()
    assert intent["state"] == "consumed" and intent["consumed_ref"] == "invoice_import"
    assert audit is not None and audit["book_id"] == c.book["id"]
    detail = c.invoices.get_batch(actor, c.book["id"], batch["id"])
    matched_total = sum(1 for line in detail["rows"] if line["match"])
    assert matched_total == 2
    matched = next(line for line in detail["rows"] if line["match"])
    expected = "source-in" if matched["direction"] == "input" else "source-out"
    assert matched["match"]["source_id"] == expected
    assert detail["notes"]["source"] == "CSV"
    listing = c.invoices.list_batches(actor, c.book["id"])
    assert listing["total"] == 1 and listing["items"][0]["duplicates"] == 1
    filename, original = c.invoices.original(actor, c.book["id"], batch["id"])
    assert filename == "全量发票查询导出表.csv" and original == payload


def test_preview_is_non_destructive_and_reports_accrual_candidates(invoices):
    c = invoices
    actor = c.actors["accountant"]
    add_source(c, actor, "preview-source", "供应商", 113000, "income")
    payload = csv_bytes(
        [
            row("25442000000000000055"),
            row(
                "25442000000000000056",
                seller="供应商乙公司",
                seller_tax="91320100SUPPLIER02",
                buyer="客户企业",
                buyer_tax=COMPANY_TAX,
                amount="2000.00",
                tax="260.00",
                total="2260.00",
            ),
            row("25442000000000000057", status="作废"),
        ]
    )
    created = upload(c, actor, payload)
    preview = c.invoices.preview(actor, c.book["id"], created["id"])
    assert preview["row_count"] == 3 and preview["direction"] == "mixed"
    assert preview["matched"] == 1
    assert preview["unmatched_eligible"] == 1
    assert preview["void"] == 1 and preview["unknown_direction"] == 0
    assert preview["company_tax_linked"] is True
    assert preview["company_tax_matched"] == 2
    assert c.invoices.list_batches(actor, c.book["id"])["total"] == 0
    with c.db.transaction(actor=actor) as conn:
        state = conn.execute(
            "SELECT state FROM upload_intents WHERE id=?", (created["id"],)
        ).fetchone()["state"]
    assert state == "stored"
    outsider = c.actors["employee"]
    with pytest.raises(AccountingError):
        c.invoices.preview(outsider, c.book["id"], created["id"])
    batch = c.invoices.create(actor, c.book["id"], created["id"])
    assert batch["row_count"] == 3


def test_http_preview_uses_upload_id_without_consuming_it(invoices):
    c = invoices
    actor = c.actors["accountant"]
    created = upload(c, actor, csv_bytes([row("25442000000000000058")]))
    app = FastAPI()
    app.state.invoice_imports = c.invoices
    app.include_router(build_invoice_imports_router(lambda: actor))
    client = TestClient(app)
    response = client.post(
        f"/api/books/{c.book['id']}/invoice-imports/preview",
        json={"upload_id": created["id"]},
    )
    assert response.status_code == 200
    assert response.json()["unmatched_eligible"] == 1
    with c.db.transaction(actor=actor) as conn:
        state = conn.execute(
            "SELECT state FROM upload_intents WHERE id=?", (created["id"],)
        ).fetchone()["state"]
    assert state == "stored"


def test_accrual_creates_balanced_ar_ap_drafts_with_source_lineage(invoices):
    c = invoices
    actor = c.actors["accountant"]
    add_tax_accounts(c, actor)
    payload = csv_bytes(
        [
            row("25442000000000000091", buyer="应收客户"),
            row(
                "25442000000000000092",
                seller="应付供应商",
                seller_tax="91320100SUPPLIER03",
                buyer="客户企业",
                buyer_tax=COMPANY_TAX,
                amount="2000.00",
                tax="260.00",
                total="2260.00",
            ),
        ]
    )
    created = upload(c, actor, payload)
    batch = c.invoices.create(actor, c.book["id"], created["id"])
    detail = c.invoices.get_batch(actor, c.book["id"], batch["id"])
    by_direction = {line["direction"]: line for line in detail["rows"]}
    request = InvoiceAccrualRequest(
        items=[
            InvoiceAccrualItem(
                line=by_direction["output"]["line"],
                settlement_account="1122",
                profit_account="6001",
                tax_account="2221.01",
            ),
            InvoiceAccrualItem(
                line=by_direction["input"]["line"],
                settlement_account="2202",
                profit_account="6602.99",
                tax_account="2221.02",
            ),
        ],
        reason="已开具或已认证但本期未收付款，按权责发生制挂账",
    )
    receipt = c.invoices.accrue(actor, c.book["id"], batch["id"], request)
    assert receipt["created"] == 2 and receipt["replayed"] is False
    drafts = {
        item["line"]: c.ledger.get_draft(actor, c.book["id"], item["draft_id"])
        for item in receipt["items"]
    }
    output = drafts[by_direction["output"]["line"]]
    input_ = drafts[by_direction["input"]["line"]]
    assert output["issues"] == [] and input_["issues"] == []
    assert [(line["account_code"], line["debit"], line["credit"]) for line in output["lines"]] == [
        ("1122", "1130.00", "0.00"),
        ("6001", "0.00", "1000.00"),
        ("2221.01", "0.00", "130.00"),
    ]
    assert [(line["account_code"], line["debit"], line["credit"]) for line in input_["lines"]] == [
        ("6602.99", "2000.00", "0.00"),
        ("2221.02", "260.00", "0.00"),
        ("2202", "0.00", "2260.00"),
    ]
    refreshed = c.invoices.get_batch(actor, c.book["id"], batch["id"])
    assert all(line["accrual"]["status"] == "draft" for line in refreshed["rows"])
    with c.db.transaction(actor=actor) as conn:
        audit = conn.execute(
            "SELECT COUNT(*) AS count FROM audit_events WHERE action='invoice.accrual' "
            "AND book_id=?",
            (c.book["id"],),
        ).fetchone()["count"]
    assert audit == 2
    replay = c.invoices.accrue(actor, c.book["id"], batch["id"], request)
    assert replay["created"] == 0 and replay["replayed"] is True


def test_accrual_rechecks_current_bank_candidates_before_writing(invoices):
    c = invoices
    actor = c.actors["accountant"]
    add_tax_accounts(c, actor)
    created = upload(c, actor, csv_bytes([row("25442000000000000093", buyer="后来收款客户")]))
    batch = c.invoices.create(actor, c.book["id"], created["id"])
    add_source(c, actor, "late-bank", "后来收款客户", 113000, "income")
    detail = c.invoices.get_batch(actor, c.book["id"], batch["id"])
    with pytest.raises(AccountingError, match="已匹配银行流水"):
        c.invoices.accrue(
            actor,
            c.book["id"],
            batch["id"],
            InvoiceAccrualRequest(
                items=[
                    InvoiceAccrualItem(
                        line=detail["rows"][0]["line"],
                        settlement_account="1122",
                        profit_account="6001",
                        tax_account="2221.01",
                    )
                ],
                reason="测试当前银行候选重查",
            ),
        )


def test_accrual_binds_unique_counterparty_auxiliary_and_never_guesses_ambiguity(invoices):
    c = invoices
    actor = c.actors["accountant"]
    add_tax_accounts(c, actor)
    unique_id = uuid4().hex
    with c.db.transaction(write=True, actor=actor) as conn:
        conn.execute(
            "INSERT INTO company_master_entities(id,firm_id,company_id,kind,code,name,"
            "recorded_book_id,created_by,created_at) VALUES(?,?,?,'counterparty','C1',?,?,?,?)",
            (
                unique_id,
                actor.firm_id,
                c.company["id"],
                "唯一应收客户",
                c.book["id"],
                actor.user_id,
                "2026-10-17T00:00:00+00:00",
            ),
        )
        conn.execute(
            "UPDATE accounts SET auxiliary_requirements='[\"counterparty\"]' "
            "WHERE book_id=? AND code='1122'",
            (c.book["id"],),
        )
    created = upload(
        c,
        actor,
        csv_bytes([row("25442000000000000101", buyer="唯一应收客户")]),
    )
    batch = c.invoices.create(actor, c.book["id"], created["id"])
    detail = c.invoices.get_batch(actor, c.book["id"], batch["id"])
    item = InvoiceAccrualItem(
        line=detail["rows"][0]["line"],
        settlement_account="1122",
        profit_account="6001",
        tax_account="2221.01",
    )
    receipt = c.invoices.accrue(
        actor,
        c.book["id"],
        batch["id"],
        InvoiceAccrualRequest(items=[item], reason="唯一往来主数据自动绑定"),
    )
    draft = c.ledger.get_draft(actor, c.book["id"], receipt["items"][0]["draft_id"])
    assert draft["issues"] == []
    assert draft["lines"][0]["auxiliary_values"] == {"counterparty": unique_id}

    with c.db.transaction(write=True, actor=actor) as conn:
        for code, name in (("C2", "歧义客户"), ("C3", "歧义客户")):
            conn.execute(
                "INSERT INTO company_master_entities(id,firm_id,company_id,kind,code,name,"
                "recorded_book_id,created_by,created_at) VALUES(?,?,?,'counterparty',?,?,?,?,?)",
                (
                    uuid4().hex,
                    actor.firm_id,
                    c.company["id"],
                    code,
                    name,
                    c.book["id"],
                    actor.user_id,
                    "2026-10-17T00:00:00+00:00",
                ),
            )
    ambiguous = upload(
        c,
        actor,
        csv_bytes([row("25442000000000000102", buyer="歧义客户")]),
    )
    batch = c.invoices.create(actor, c.book["id"], ambiguous["id"])
    detail = c.invoices.get_batch(actor, c.book["id"], batch["id"])
    receipt = c.invoices.accrue(
        actor,
        c.book["id"],
        batch["id"],
        InvoiceAccrualRequest(
            items=[item.model_copy(update={"line": detail["rows"][0]["line"]})],
            reason="同名主数据不得自动猜测",
        ),
    )
    draft = c.ledger.get_draft(actor, c.book["id"], receipt["items"][0]["draft_id"])
    assert draft["lines"][0]["auxiliary_values"] == {}
    assert any("交易对方辅助值缺失" in issue for issue in draft["issues"])


def test_second_batch_flags_previous_invoices_as_duplicates(invoices):
    c = invoices
    actor = c.actors["accountant"]
    first = upload(c, actor, csv_bytes([row("25442000000000000061")]))
    c.invoices.create(actor, c.book["id"], first["id"])
    second = upload(c, actor, csv_bytes([row("25442000000000000061"), row("25442000000000000062")]))
    batch = c.invoices.create(actor, c.book["id"], second["id"])
    assert batch["duplicates"] == 1 and batch["row_count"] == 2
    detail = c.invoices.get_batch(actor, c.book["id"], batch["id"])
    flags = {line["number"]: line["duplicate"] for line in detail["rows"]}
    assert flags["25442000000000000061"] is True and flags["25442000000000000062"] is False


def test_import_requires_book_access_and_releases_on_purpose_mismatch(invoices):
    c = invoices
    actor = c.actors["accountant"]
    data = csv_bytes([row("25442000000000000071")])
    wrong = upload(
        c,
        actor,
        pdf(),
        filename="发票.pdf",
        media_type="application/pdf",
        purpose="event_attachment",
    )
    with pytest.raises(AccountingError) as mismatch:
        c.invoices.create(actor, c.book["id"], wrong["id"])
    assert mismatch.value.status == 409
    with c.db.transaction(actor=actor) as conn:
        state = conn.execute(
            "SELECT state FROM upload_intents WHERE id=?", (wrong["id"],)
        ).fetchone()["state"]
    assert state == "stored"
    outsider = c.actors["employee"]
    valid = upload(c, actor, data)
    with pytest.raises(AccountingError) as denied:
        c.invoices.create(outsider, c.book["id"], valid["id"])
    assert denied.value.status in {403, 404}
    with pytest.raises(AccountingError) as missing:
        c.invoices.create(actor, "book-missing", valid["id"])
    assert missing.value.status == 404
    with pytest.raises(AccountingError, match="分页"):
        c.invoices.list_batches(actor, c.book["id"], limit=0)
    batch = c.invoices.create(actor, c.book["id"], valid["id"])
    with pytest.raises(AccountingError) as gone:
        c.invoices.create(actor, c.book["id"], valid["id"])
    assert gone.value.status in {404, 409}
    assert c.invoices.get_batch(actor, c.book["id"], batch["id"])["id"] == batch["id"]


def test_upload_validator_binds_invoice_export_to_intent(invoices):
    c = invoices
    actor = c.actors["accountant"]
    statement = csv_bytes(["x"], headers="交易日期,交易对手,金额,方向")
    created = c.uploads.create(
        actor,
        UploadCreate(
            purpose="invoice_import",
            filename="流水.csv",
            media_type="text/csv",
            size_bytes=len(statement),
            sha256=hashlib.sha256(statement).hexdigest(),
        ),
    )
    with pytest.raises(AccountingError, match="未识别"):
        c.uploads.store_content(actor, created["id"], statement)
    with c.db.transaction(actor=actor) as conn:
        state = conn.execute(
            "SELECT state FROM upload_intents WHERE id=?", (created["id"],)
        ).fetchone()["state"]
    assert state == "pending"
    valid = upload(c, actor, csv_bytes([row("25442000000000000081")]))
    batch = c.invoices.create(actor, c.book["id"], valid["id"])
    assert batch["row_count"] == 1 and batch["direction"] == "output"
