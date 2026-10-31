"""Monthly client delivery is frozen evidence, separate from software import receipts."""

import hashlib
import io
import json
import sqlite3
from zipfile import ZipFile
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import BookMemberGrant
from app.delivery.monthly_models import MonthlyPackageCreate, MonthlyPackageAcknowledge
from app.delivery.monthly_package import MonthlyPackageService, readable_files, money
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from app.service_plans.models import (
    PlanSave,
    TemplateCreate,
    TemplateItem,
    Generate,
    MaterialSubmit,
    MaterialReview,
)
from app.service_plans.service import ServicePlansService
from test_business import business
from test_p0_close_dependencies import close_case, command


@pytest.fixture
def ready_case(close_case):
    c = close_case
    ServicePlansService(c.db, c.ledger, c.service).save_plan(
        c.manager,
        c.book["id"],
        PlanSave(
            expected_version=0,
            contact_user_id="finance",
            due_on="2026-11-10",
            reason="明确客户签收人",
        ),
    )
    c.close.review(c.reviewer, c.run["close"]["id"], command())
    c.close.lock(c.reviewer, c.run["close"]["id"], command())
    ReportService(c.db).publish(
        c.reviewer,
        c.report["report"]["id"],
        ReportCommand(expected_version=c.report["report"]["version"], reason="发布本月已复核报表"),
    )
    c.packages = MonthlyPackageService(c.db)
    return c


def request(c, **changes):
    return MonthlyPackageCreate(
        **{
            "idempotency_key": "monthly-test-1",
            "report_id": c.report["report"]["id"],
            "expected_report_version": c.report["report"]["version"],
            "reason": "会计复核后冻结交付",
            **changes,
        }
    )


def freeze(c):
    return c.packages.create(c.reviewer, c.book["id"], request(c))


def test_readiness_missing_contact_and_published_report(close_case):
    c = close_case
    data = MonthlyPackageService(c.db).readiness(c.manager, c.book["id"])
    assert not data["ready"]
    assert {"contact_missing", "published_report_missing", "period_not_locked"} <= {
        x["code"] for x in data["blockers"]
    }
    assert data["permissions"]["generate"] is True


def test_frozen_zip_hashes_scope_and_restart(ready_case):
    c = ready_case
    data = c.packages.readiness(c.manager, c.book["id"])
    assert data["ready"], data["blockers"]
    assert data["reports"][0]["current"]
    package = freeze(c)
    assert package["status"] == "frozen" and package["version"] == 1
    assert package["customer_receipt_status"] == "not_recorded"
    assert package["external_import_status"] == "separate_workflow"
    raw = c.packages.download(c.actors["finance"], c.book["id"], package["id"])
    assert raw == MonthlyPackageService(c.db).download(c.manager, c.book["id"], package["id"])
    with ZipFile(io.BytesIO(raw)) as archive:
        manifest_bytes = archive.read("manifest.json")
        assert hashlib.sha256(manifest_bytes).hexdigest() == package["manifest_sha256"]
        manifest = json.loads(manifest_bytes)
        assert manifest["book_id"] == c.book["id"] and manifest["company_id"] == c.company["id"]
        assert manifest["period"] == "2026-10"
        assert set(archive.namelist()) == {"manifest.json", *[e["path"] for e in manifest["files"]]}
        for item in manifest["files"]:
            content = archive.read(item["path"])
            assert hashlib.sha256(content).hexdigest() == item["sha256"]
            assert len(content) == item["size_bytes"]
            assert b"storage_key" not in content
        assert (
            json.loads(archive.read("formal_vouchers.json"))[0]["id"] == c.payment["voucher"]["id"]
        )
        assert (
            json.loads(archive.read("published_report.json"))["report"]["id"]
            == package["report_id"]
        )


def test_idempotency_and_version_are_frozen(ready_case):
    c = ready_case
    first = freeze(c)
    assert freeze(c)["id"] == first["id"]
    with pytest.raises(AccountingError) as e:
        c.packages.create(c.reviewer, c.book["id"], request(c, reason="不同内容不得重用"))
    assert e.value.status == 409
    second = c.packages.create(
        c.reviewer, c.book["id"], request(c, idempotency_key="monthly-test-2")
    )
    assert second["version"] == 2 and second["manifest_sha256"] != first["manifest_sha256"]


@pytest.mark.parametrize("kind", ["reader", "foreign", "report_version"])
def test_generation_requires_current_review_and_exact_report(ready_case, kind):
    c = ready_case
    actor = c.actors["finance"] if kind == "reader" else c.reviewer
    book_id = c.other_book["id"] if kind == "foreign" else c.book["id"]
    req = request(c, expected_report_version=99) if kind == "report_version" else request(c)
    with pytest.raises(AccountingError) as e:
        c.packages.create(actor, book_id, req)
    assert e.value.status in {403, 404, 409}


def test_download_cross_company_and_revoked_grant(ready_case):
    c = ready_case
    p = freeze(c)
    with pytest.raises(AccountingError) as e:
        c.packages.download(c.manager, c.other_book["id"], p["id"])
    assert e.value.status == 404
    with c.db.transaction(write=True) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE book_id=? AND user_id=?", (c.book["id"], "finance")
        )
    with pytest.raises(AccountingError) as e:
        c.packages.download(c.actors["finance"], c.book["id"], p["id"])
    assert e.value.status == 404


def test_customer_acknowledges_exact_hash_idempotently_and_not_import(ready_case):
    c = ready_case
    p = freeze(c)
    body = MonthlyPackageAcknowledge(
        manifest_sha256=p["manifest_sha256"], reason="客户确认收到本版"
    )
    assert c.packages.detail(c.actors["finance"], c.book["id"], p["id"])["package"][
        "can_acknowledge"
    ]
    with pytest.raises(AccountingError) as e:
        c.packages.acknowledge(c.reviewer, c.book["id"], p["id"], body)
    assert e.value.status == 403
    acknowledged = c.packages.acknowledge(c.actors["finance"], c.book["id"], p["id"], body)
    assert acknowledged["customer_receipt_status"] == "acknowledged"
    assert acknowledged["external_import_status"] == "separate_workflow"
    assert c.packages.acknowledge(c.actors["finance"], c.book["id"], p["id"], body) == acknowledged
    for changed in (
        dict(manifest_sha256="a" * 64, reason=body.reason),
        dict(manifest_sha256=p["manifest_sha256"], reason="不同客户说明"),
    ):
        with pytest.raises(AccountingError) as e:
            c.packages.acknowledge(
                c.actors["finance"], c.book["id"], p["id"], MonthlyPackageAcknowledge(**changed)
            )
        assert e.value.status == 409
    with c.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM delivery_receipts").fetchone()[0] == 0


def test_revoked_customer_membership_prevents_acknowledgement(ready_case):
    c = ready_case
    p = freeze(c)
    with c.db.transaction(write=True) as conn:
        conn.execute(
            "DELETE FROM company_memberships WHERE company_id=? AND user_id=?",
            (c.company["id"], "finance"),
        )
    with pytest.raises(AccountingError):
        c.packages.acknowledge(
            c.actors["finance"],
            c.book["id"],
            p["id"],
            MonthlyPackageAcknowledge(
                manifest_sha256=p["manifest_sha256"], reason="已失去企业权限"
            ),
        )


def test_snapshot_and_ack_are_append_only(ready_case):
    c = ready_case
    p = freeze(c)
    c.packages.acknowledge(
        c.actors["finance"],
        c.book["id"],
        p["id"],
        MonthlyPackageAcknowledge(manifest_sha256=p["manifest_sha256"], reason="客户签收完整内容"),
    )
    for table in ("monthly_delivery_packages", "monthly_delivery_acknowledgements"):
        for sql in (f"DELETE FROM {table}", f"UPDATE {table} SET created_at=created_at"):
            with pytest.raises(sqlite3.IntegrityError):
                with c.db.transaction(write=True) as conn:
                    conn.execute(sql)


def test_reviewer_who_is_also_contact_cannot_self_acknowledge(ready_case):
    c = ready_case
    from app.accounting.models import FirmMemberGrant
    c.directory.grant_firm_member(c.manager, FirmMemberGrant(email="finance@example.test", role="reviewer"))
    c.directory.grant_book_member(
        c.manager, c.book["id"], "finance", BookMemberGrant(role="reviewer")
    )
    p = c.packages.create(c.actors["finance"], c.book["id"], request(c))
    assert p["can_acknowledge"] is False
    with pytest.raises(AccountingError) as error:
        c.packages.acknowledge(
            c.actors["finance"],
            c.book["id"],
            p["id"],
            MonthlyPackageAcknowledge(
                manifest_sha256=p["manifest_sha256"], reason="不得生成后自行签收"
            ),
        )
    assert error.value.status == 403
    with pytest.raises(sqlite3.IntegrityError, match="client contact"):
        with c.db.transaction(write=True) as conn:
            conn.execute(
                "INSERT INTO monthly_delivery_acknowledgements(package_id,book_id,firm_id,company_id,manifest_sha256,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    p["id"],
                    c.book["id"],
                    c.manager.firm_id,
                    c.company["id"],
                    p["manifest_sha256"],
                    "绕过服务自行签收",
                    "finance",
                    "2026-10-25",
                ),
            )


def test_material_missing_unreviewed_and_current_accepted(ready_case):
    c = ready_case
    plans = c.packages.plans
    template = plans.create_template(
        c.manager,
        c.book["id"],
        TemplateCreate(
            name="必需资料",
            reason="明确月度资料要求",
            items=[
                TemplateItem(
                    key="bank", title="银行材料", required=True, assignee_user_id="finance"
                )
            ],
        ),
    )
    plans.generate(
        c.manager, c.book["id"], template["id"], Generate(expected_version=1, reason="生成资料请求")
    )
    with c.db.transaction() as conn:
        material = conn.execute(
            "SELECT id FROM material_requests WHERE book_id=?", (c.book["id"],)
        ).fetchone()[0]
    for state in ("pending", "submitted"):
        readiness = c.packages.readiness(c.reviewer, c.book["id"])
        assert "required_materials_unaccepted" in {x["code"] for x in readiness["blockers"]}
        with pytest.raises(AccountingError) as error:
            freeze(c)
        assert error.value.status == 409
        if state == "pending":
            plans.submit(
                c.actors["finance"],
                material,
                MaterialSubmit(expected_version=1, mode="no_activity", note="本期该项目无发生"),
            )
    plans.review(
        c.reviewer,
        material,
        MaterialReview(expected_version=2, action="accept", reason="独立审核无发生说明"),
    )
    assert c.packages.readiness(c.reviewer, c.book["id"])["ready"]
    freeze(c)


def test_stale_report_blocks_new_package_but_frozen_download_unchanged(ready_case):
    c = ready_case
    p = freeze(c)
    before = c.packages.download(c.manager, c.book["id"], p["id"])
    with c.db.transaction(write=True) as conn:
        conn.execute("UPDATE report_templates SET active=0")
    assert not c.packages.readiness(c.reviewer, c.book["id"])["ready"]
    with pytest.raises(AccountingError) as error:
        c.packages.create(c.reviewer, c.book["id"], request(c, idempotency_key="stale-report"))
    assert error.value.status == 409
    assert c.packages.download(c.manager, c.book["id"], p["id"]) == before


def test_html_injection_and_csv_formula_are_escaped_without_float():
    malicious = "<script>alert(1)</script>"
    files = readable_files(
        {"company_name": malicious, "id": "book", "period": "2026-10"},
        1,
        {
            "id": "report",
            "version": 1,
            "content_json": json.dumps({"profit_statement": {"name": malicious}}),
        },
        {
            "rows": [
                {
                    "code": "=1+2",
                    "name": " @SUM(1)",
                    "opening_cents": -1,
                    "debit_cents": 99999999999999,
                    "credit_cents": 0,
                    "closing_cents": -100,
                }
            ]
        },
        "client",
    )
    assert "<script>" not in files["README.html"] and "<script>" not in files["report.html"]
    assert "&lt;script&gt;" in files["README.html"] and "&lt;script&gt;" in files["report.html"]
    assert "'=1+2" in files["account_balances.csv"] and "' @SUM(1)" in files["account_balances.csv"]
    assert "-0.01,999999999999.99,0.00,-1.00" in files["account_balances.csv"]
    assert money(-1) == "-0.01"


def test_company_scope_and_authorization_are_rechecked_inside_write(ready_case, monkeypatch):
    from contextlib import contextmanager

    c = ready_case
    original = c.db.transaction
    called = []

    @contextmanager
    def observed(*args, **kwargs):
        if kwargs.get("write"):
            called.append(kwargs)
        with original(*args, **kwargs) as conn:
            yield conn

    monkeypatch.setattr(c.db, "transaction", observed)
    package = freeze(c)
    c.packages.acknowledge(
        c.actors["finance"],
        c.book["id"],
        package["id"],
        MonthlyPackageAcknowledge(
            manifest_sha256=package["manifest_sha256"], reason="客户核对本月交付"
        ),
    )
    assert len(called) == 2
    assert all(x["write_scope"] == "company" and x["company_id"] == c.company["id"] for x in called)


def test_read_only_customer_lists_and_acknowledges_frozen_package(ready_case):
    c = ready_case
    package = freeze(c)
    c.directory.grant_book_member(
        c.manager, c.book["id"], "finance", BookMemberGrant(role="viewer")
    )
    data = c.packages.readiness(c.actors["finance"], c.book["id"])
    assert data["permissions"]["generate"] is False
    assert data["packages"][0]["can_acknowledge"] is True
    result = c.packages.acknowledge(
        c.actors["finance"],
        c.book["id"],
        package["id"],
        MonthlyPackageAcknowledge(
            manifest_sha256=package["manifest_sha256"], reason="只读客户确认收到"
        ),
    )
    assert result["customer_receipt_status"] == "acknowledged"


def test_changed_contact_cannot_sign_another_recipients_frozen_version(ready_case):
    c = ready_case
    package = freeze(c)
    c.directory.grant_book_member(
        c.manager, c.book["id"], "company_admin", BookMemberGrant(role="viewer")
    )
    c.packages.plans.save_plan(
        c.manager,
        c.book["id"],
        PlanSave(
            expected_version=1,
            contact_user_id="company_admin",
            due_on="2026-11-10",
            reason="更换本月客户联系人",
        ),
    )
    for actor in (c.actors["finance"], c.actors["company_admin"]):
        assert not c.packages.detail(actor, c.book["id"], package["id"])["package"][
            "can_acknowledge"
        ]
        with pytest.raises(AccountingError) as error:
            c.packages.acknowledge(
                actor,
                c.book["id"],
                package["id"],
                MonthlyPackageAcknowledge(
                    manifest_sha256=package["manifest_sha256"], reason="更换联系人后不能代签"
                ),
            )
        assert error.value.status == 403


@pytest.mark.parametrize("tamper", ["manifest_hash", "file_hash", "impersonation"])
def test_native_sqlite_snapshot_hashes_and_actor_cannot_be_forged(ready_case, tamper):
    from app.delivery.service import encode

    c = ready_case
    package = freeze(c)
    with c.db.transaction(actor=c.reviewer) as conn:
        row = dict(
            conn.execute(
                "SELECT * FROM monthly_delivery_packages WHERE id=?", (package["id"],)
            ).fetchone()
        )
    row.update(id=str(uuid4()), version=2, idempotency_key="native-" + tamper)
    snapshot = json.loads(row["snapshot_json"])
    snapshot["manifest"].update(package_id=row["id"], version=2)
    snapshot["manifest_json"] = encode(snapshot["manifest"])
    row["manifest_sha256"] = hashlib.sha256(snapshot["manifest_json"].encode()).hexdigest()
    if tamper == "manifest_hash":
        row["manifest_sha256"] = "a" * 64
    elif tamper == "file_hash":
        snapshot["files"]["README.html"] += "unexpected change"
    row["snapshot_json"] = encode(snapshot)
    actor = c.manager if tamper == "impersonation" else c.reviewer
    with pytest.raises(sqlite3.IntegrityError):
        with c.db.transaction(write=True, actor=actor) as conn:
            conn.create_function(
                "monthly_delivery_sha256",
                1,
                lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest(),
                deterministic=True,
            )
            conn.execute(
                "INSERT INTO monthly_delivery_packages("
                + ",".join(row)
                + ") VALUES("
                + ",".join("?" for _ in row)
                + ")",
                tuple(row.values()),
            )
