"""Monthly statements remain private originals without fictitious business amounts."""

import hashlib
import io
from zipfile import ZipFile

import pytest

from app.accounting.domain import AccountingError
from app.accounting.directory import DirectoryService
from app.accounting.models import FirmMemberGrant, BookMemberGrant
from app.service_plans.attachments import XLSX, validate_material_original
from app.service_plans.attachments import MaterialAttachmentService
from app.service_plans.models import MaterialSubmit, MaterialReview
from app.business.storage import AttachmentStore
from test_business import business  # noqa: F401
from test_business_postgres import ADMIN_URL, business_case, business_postgres  # noqa: F401
from test_stage17_monthly_workbench import month_case  # noqa: F401
from test_stage25_service_plans import generated, plans  # noqa: F401
from test_business_api import auth, client  # noqa: F401


def request_fixture(client):
    owner, employee, finance = (
        auth(client, name) for name in ("accountant", "employee", "finance")
    )
    company = client.get("/api/business/context", headers=employee).json()["companies"][0]
    response = client.post(
        "/api/books",
        headers=owner,
        json={"name": "独立资料原件验证", "company_id": company["id"], "period": "2026-10"},
    )
    assert response.status_code == 201, response.text
    book = response.json()["id"]
    response = client.post(
        f"/api/books/{book}/material-templates",
        headers=owner,
        json={
            "name": "本月银行资料",
            "reason": "收集本月原始银行对账单",
            "items": [
                {
                    "key": "statement",
                    "title": "银行对账单",
                    "assignee_user_id": "local-business-employee",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    template = response.json()
    response = client.post(
        f"/api/books/{book}/material-templates/{template['id']}/generate",
        headers=owner,
        json={"expected_version": 1, "reason": "明确生成本月资料要求"},
    )
    assert response.status_code == 200, response.text
    material = client.get(f"/api/books/{book}/material-requests", headers=owner).json()["rows"][0]
    return owner, employee, finance, book, material["id"]


def test_statement_upload_reload_submit_review_keeps_original_and_creates_no_business(client):
    owner, employee, finance, book, identifier = request_fixture(client)
    root = f"/api/material-requests/{identifier}"
    original = "日期,收入,支出\n2026-10-01,100.00,0.00\n".encode("utf-8-sig")
    with client.app.state.service.db.transaction() as conn:
        before = [
            conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            for table in ("business_events", "sources", "drafts")
        ]
    response = client.post(
        root + "/attachments?expected_version=1",
        headers=employee,
        files={"file": ("本月银行对账单.csv", original, "text/csv")},
    )
    assert response.status_code == 200, response.text
    uploaded = response.json()
    attachment = uploaded["attachment"]
    assert uploaded["version"] == 1 and "storage_key" not in attachment
    assert attachment["sha256"] == hashlib.sha256(original).hexdigest()
    mine = client.get(f"/api/material-requests?request_id={identifier}", headers=employee).json()[
        "rows"
    ][0]
    assert [item["id"] for item in mine["attachments"]] == [attachment["id"]]
    path = root + f"/attachments/{attachment['id']}/content"
    assert client.get(path, headers=owner).status_code == 404  # not yet submitted
    assert (
        client.post(
            root + "/attachments?expected_version=1",
            headers=finance,
            files={"file": ("银行.csv", original, "text/csv")},
        ).status_code
        == 404
    )
    repeated = client.post(
        root + "/attachments?expected_version=1",
        headers=employee,
        files={"file": ("本月银行对账单.csv", original, "text/csv")},
    )
    assert repeated.status_code == 200 and repeated.json()["attachment"]["id"] == attachment["id"]
    response = client.post(
        root + "/submit",
        headers=employee,
        json={
            "expected_version": 1,
            "mode": "provided",
            "note": "已上传本月银行对账单，请独立核对",
            "attachment_ids": [attachment["id"]],
        },
    )
    assert response.status_code == 200, response.text
    content = client.get(path, headers=owner)
    assert content.status_code == 200 and content.content == original
    assert content.headers["cache-control"] == "private, no-store"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert (
        client.post(
            root + "/attachments?expected_version=2",
            headers=employee,
            files={"file": ("银行补充.csv", original, "text/csv")},
        ).status_code
        == 409
    )
    response = client.post(
        root + "/review",
        headers=owner,
        json={
            "expected_version": 2,
            "action": "accept",
            "reason": "独立核对本月完整原件与说明",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "accepted"
    with client.app.state.service.db.transaction() as conn:
        after = [
            conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            for table in ("business_events", "sources", "drafts")
        ]
        assert after == before
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM material_attachments WHERE request_id=?", (identifier,)
            ).fetchone()[0]
            == 1
        )


def test_tampered_material_original_is_never_downloaded_or_accepted(client):
    owner, employee, _, _, identifier = request_fixture(client)
    root = f"/api/material-requests/{identifier}"
    original = b"date,amount\n2026-10-01,1.00\n"
    response = client.post(
        root + "/attachments?expected_version=1",
        headers=employee,
        files={"file": ("statement.csv", original, "text/csv")},
    )
    assert response.status_code == 200, response.text
    attachment = response.json()["attachment"]
    assert (
        client.post(
            root + "/submit",
            headers=employee,
            json={
                "expected_version": 1,
                "mode": "provided",
                "note": "提交本月银行原件等待验收",
                "attachment_ids": [attachment["id"]],
            },
        ).status_code
        == 200
    )
    with client.app.state.service.db.transaction() as conn:
        key = conn.execute(
            "SELECT storage_key FROM material_attachments WHERE id=?", (attachment["id"],)
        ).fetchone()[0]
    client.app.state.attachment_store.backend.path(key).write_bytes(b"tampered")
    assert (
        client.get(root + f"/attachments/{attachment['id']}/content", headers=owner).status_code
        == 503
    )
    response = client.post(
        root + "/review",
        headers=owner,
        json={
            "expected_version": 2,
            "action": "accept",
            "reason": "尝试验收前核对原件完整性",
        },
    )
    assert response.status_code == 503, response.text


def workbook(extra=None):
    output = io.BytesIO()
    parts = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="资料" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>',
    }
    if extra:
        parts[extra[0]] = extra[1]
    with ZipFile(output, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return output.getvalue()


def test_standalone_original_accepts_encoded_csv_and_bounded_xlsx_without_processing():
    assert (
        validate_material_original(
            "中文.csv", "text/csv", "日期,金额\n2026-10-01,1".encode("gb18030")
        )[1]
        == "text/csv"
    )
    assert validate_material_original("工资资料.xlsx", XLSX, workbook())[1] == XLSX


@pytest.mark.parametrize(
    "name,mime,data",
    [
        ("bad.xlsx", XLSX, b"not a workbook"),
        ("macro.xlsx", XLSX, workbook(("xl/vbaProject.bin", b"macro"))),
        ("traversal.xlsx", XLSX, workbook(("../outside", b"bad"))),
        (
            "entity.xlsx",
            XLSX,
            workbook(
                (
                    "xl/workbook.xml",
                    b'<!DOCTYPE x [<!ENTITY a SYSTEM "file:///private">]><x>&a;</x>',
                )
            ),
        ),
        ("bad.csv", "image/png", b"date,amount\n1,2"),
        ("bad.csv", "text/csv", b"\x00binary"),
    ],
)
def test_invalid_material_containers_rejected(name, mime, data):
    with pytest.raises(AccountingError):
        validate_material_original(name, mime, data)


def test_standalone_original_native_roles_preserve_hashes_and_latest_submission_acl(
    plans, tmp_path
):
    c = plans
    store = AttachmentStore(tmp_path / "monthly-originals")
    c.plans.attachments = MaterialAttachmentService(c.plans, store)
    _, material = generated(c)
    original = b"date,income,expense\n2026-10-01,0,100\n"
    saved = store.put_original("statement.csv", "text/csv", original)
    uploaded = c.plans.attachments.add(c.employee, material["id"], 1, saved)
    identifier = uploaded["attachment"]["id"]
    assert len(c.plans.my_requests(c.employee)["rows"][0]["attachments"]) == 1
    with pytest.raises(AccountingError):
        c.plans.attachments.content(c.owner, material["id"], identifier)
    submitted = c.plans.submit(
        c.employee,
        material["id"],
        MaterialSubmit(
            expected_version=1,
            mode="provided",
            note="本月银行原件独立归档验收",
            attachment_ids=[identifier],
        ),
    )
    assert submitted["evidence_current"] and submitted["state"] == "submitted"
    assert c.plans.attachments.content(c.owner, material["id"], identifier)[1] == original
    accepted = c.plans.review(
        c.reviewer,
        material["id"],
        MaterialReview(expected_version=2, action="accept", reason="独立核对银行原件及资料月份"),
    )
    assert accepted["state"] == "accepted"
    if c.db.is_postgres:
        import psycopg

        with pytest.raises(psycopg.Error):
            with c.db.transaction(write=True, actor=c.owner) as conn:
                conn.execute(
                    "UPDATE material_attachments SET sha256=? WHERE id=?", ("0" * 64, identifier)
                )
    else:
        import sqlite3

        with pytest.raises(sqlite3.DatabaseError):
            with c.db.transaction(write=True, actor=c.owner) as conn:
                conn.execute(
                    "UPDATE material_attachments SET sha256=? WHERE id=?", ("0" * 64, identifier)
                )
    assert store.read({**saved}) == original


def test_revoked_material_contact_keeps_independent_book_role_but_not_unsubmitted_original(
    plans, tmp_path
):
    c = plans
    store = AttachmentStore(tmp_path / "private-unsubmitted")
    c.plans.attachments = MaterialAttachmentService(c.plans, store)
    _, material = generated(c)
    saved = store.put_original("private.csv", "text/csv", b"date,amount\n2026-10-01,100\n")
    c.plans.attachments.add(c.employee, material["id"], 1, saved)
    directory = DirectoryService(c.db)
    if c.db.is_postgres:
        # The native SQL fixture deliberately omits email; provision its synthetic
        # identity before exercising the normal directory grant API.
        import psycopg

        with psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "UPDATE accounting.users SET email=%s WHERE id=%s",
                (
                    c.employee.user_id + "@example.test",
                    c.employee.user_id,
                ),
            )
    with c.db.transaction(actor=c.owner) as conn:
        email = conn.execute(
            "SELECT email FROM users WHERE id=?", (c.employee.user_id,)
        ).fetchone()[0]
    directory.grant_firm_member(c.owner, FirmMemberGrant(email=email, role="operator"))
    directory.grant_book_member(c.owner, c.book, c.employee.user_id, BookMemberGrant(role="editor"))
    directory.revoke_company_member(c.owner, c.company, c.employee.user_id)
    with c.db.transaction(actor=c.employee) as conn:
        assert c.accounting.access.book(conn, c.employee, c.book)["permissions"]["edit"]
    requests = c.plans.book_requests(c.employee, c.book)["rows"]
    assert requests[0]["attachments"] == []
    with pytest.raises(AccountingError):
        c.plans.attachments.content(c.employee, material["id"], saved["id"])
