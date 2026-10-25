"""Real restricted-role PG16/17 gates; explicit disposable loopback only."""

import os
import json
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError, Actor
from app.accounting.directory import DirectoryService
from app.accounting.models import BookMemberGrant
from app.b04_close.models import CloseCommand
from app.delivery.monthly_models import MonthlyPackageCreate, MonthlyPackageAcknowledge
from app.delivery.monthly_package import MonthlyPackageService
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from app.service_plans.models import PlanSave
from app.service_plans.service import ServicePlansService
from app.subledger.models import ControlApplicability
from app.subledger.service import SubledgerService
from test_p0_close_upload_postgres import p0_case, _reviewed_close


@pytest.fixture(scope="module")
def p0_pg():
    admin = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
    runtime = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
    if not admin or not runtime or os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1":
        pytest.skip("Requires explicit isolated AK05 PostgreSQL runner")
    targets = [urlsplit(value) for value in (admin, runtime)]
    for target in targets:
        if (
            target.scheme not in {"postgres", "postgresql"}
            or target.hostname != "127.0.0.1"
            or not target.port
            or target.port == 5432
            or not target.path.startswith("/ak01_local_ak05_")
            or target.query
            or target.fragment
        ):
            pytest.fail("Only disposable loopback AK05 targets accepted")
    assert (targets[0].hostname, targets[0].port, targets[0].path) == (
        targets[1].hostname,
        targets[1].port,
        targets[1].path,
    )
    import psycopg

    with psycopg.connect(admin) as conn:
        # The isolated runner proves the target; container data paths differ
        # from extracted local clusters and are not an authorization boundary.
        assert conn.execute("SELECT current_database()").fetchone()[0] == targets[0].path[1:]
    db = Database(runtime)
    yield SimpleNamespace(admin=admin, runtime=runtime, db=db, psycopg=psycopg)
    db.close()


@pytest.fixture
def package_case(p0_case, monkeypatch):
    c = p0_case
    # The old helper creates a genuinely zero opening. Complete C01 applicability
    # through the real service, including the native trigger (forward fix 156).
    original_create = ReportService.create

    def create_after_applicability(service, actor, book_id, period):
        SubledgerService(c.db).set_control_applicability(
            c.manager,
            ControlApplicability(
                book_id=book_id,
                status="not_applicable",
                effective_from="2026-10",
                reason="合成零余额企业本期无应收应付控制科目发生",
            ),
        )
        return original_create(service, actor, book_id, period)

    with monkeypatch.context() as patch:
        patch.setattr(ReportService, "create", create_after_applicability)
        ready = _reviewed_close(c)
    ServicePlansService(c.db, c.accounting, c.business).save_plan(
        c.manager,
        c.book["id"],
        PlanSave(
            expected_version=0,
            contact_user_id=c.manager.user_id,
            due_on="2026-11-10",
            reason="本机客户联系人配置",
        ),
    )
    ready.close.lock(
        c.reviewer,
        ready.run["id"],
        CloseCommand(expected_version=ready.run["version"], reason="本机独立月结锁期"),
    )
    ReportService(c.db).publish(
        c.reviewer, ready.report_id, ReportCommand(expected_version=1, reason="本机发布正式报表")
    )
    c.monthly = MonthlyPackageService(c.db)
    c.request = MonthlyPackageCreate(
        idempotency_key="pg-monthly-" + uuid4().hex,
        report_id=ready.report_id,
        expected_report_version=1,
        reason="本机复核冻结交付",
    )
    return c


def test_pg_freeze_download_and_independent_exact_hash_acknowledgement(package_case):
    c = package_case
    readiness = c.monthly.readiness(c.reviewer, c.book["id"])
    assert readiness["ready"], readiness["blockers"]
    package = c.monthly.create(c.reviewer, c.book["id"], c.request)
    assert c.monthly.download(c.manager, c.book["id"], package["id"]).startswith(b"PK")
    assert c.monthly.create(c.reviewer, c.book["id"], c.request)["id"] == package["id"]
    ack = MonthlyPackageAcknowledge(
        manifest_sha256=package["manifest_sha256"], reason="客户已收本版交付"
    )
    assert (
        c.monthly.acknowledge(c.manager, c.book["id"], package["id"], ack)[
            "customer_receipt_status"
        ]
        == "acknowledged"
    )
    assert (
        c.monthly.acknowledge(c.manager, c.book["id"], package["id"], ack)["external_import_status"]
        == "separate_workflow"
    )
    with c.db.transaction(actor=c.foreign) as conn:
        assert (
            conn.execute(
                "SELECT id FROM monthly_delivery_packages WHERE id=?", (package["id"],)
            ).fetchall()
            == []
        )
    with pytest.raises(AccountingError) as error:
        c.monthly.download(c.manager, c.other_book["id"], package["id"])
    assert error.value.status == 404


def test_pg_package_roles_are_append_only_and_hash_guarded(package_case):
    c = package_case
    package = c.monthly.create(c.reviewer, c.book["id"], c.request)
    with c.db.transaction(actor=c.reviewer) as conn:
        row = dict(
            conn.execute(
                "SELECT * FROM monthly_delivery_packages WHERE id=?", (package["id"],)
            ).fetchone()
        )
        for table in ("monthly_delivery_packages", "monthly_delivery_acknowledgements"):
            for privilege in ("UPDATE", "DELETE"):
                assert not conn.execute(
                    "SELECT has_table_privilege(current_user,?,?)",
                    ("accounting." + table, privilege),
                ).fetchone()[0]
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(
            write=True, actor=c.reviewer, company_id=c.book["company_id"], write_scope="company"
        ) as conn:
            conn.execute("DELETE FROM monthly_delivery_packages WHERE id=?", (package["id"],))
    row.update(
        id=str(uuid4()),
        version=2,
        idempotency_key="forged-hash-" + uuid4().hex,
        manifest_sha256="a" * 64,
    )
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with c.db.transaction(
            write=True, actor=c.reviewer, company_id=c.book["company_id"], write_scope="company"
        ) as conn:
            conn.execute(
                "INSERT INTO monthly_delivery_packages("
                + ",".join(row)
                + ") VALUES("
                + ",".join("?" for _ in row)
                + ")",
                tuple(row.values()),
            )


def test_pg_customer_cannot_self_ack_or_forge_hash_and_revocation_is_current(package_case):
    c = package_case
    package = c.monthly.create(c.manager, c.book["id"], c.request)
    assert package["can_acknowledge"] is False
    ack = MonthlyPackageAcknowledge(
        manifest_sha256=package["manifest_sha256"], reason="不允许自建自签收"
    )
    with pytest.raises(AccountingError) as error:
        c.monthly.acknowledge(c.manager, c.book["id"], package["id"], ack)
    assert error.value.status == 403
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(
            write=True, actor=c.manager, company_id=c.book["company_id"], write_scope="company"
        ) as conn:
            conn.execute(
                "INSERT INTO monthly_delivery_acknowledgements(package_id,book_id,firm_id,company_id,manifest_sha256,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    package["id"],
                    c.book["id"],
                    c.manager.firm_id,
                    c.book["company_id"],
                    package["manifest_sha256"],
                    "直接SQL自签收",
                    c.manager.user_id,
                    "2026-10-25",
                ),
            )
    second = c.monthly.create(c.reviewer, c.book["id"], c.request)
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "DELETE FROM accounting.company_memberships WHERE company_id=%s AND user_id=%s",
            (c.book["company_id"], c.manager.user_id),
        )
    with pytest.raises(AccountingError) as error:
        c.monthly.acknowledge(
            c.manager,
            c.book["id"],
            second["id"],
            MonthlyPackageAcknowledge(
                manifest_sha256=second["manifest_sha256"], reason="失去客户身份后签收"
            ),
        )
    assert error.value.status == 403


def test_pg_read_only_client_can_ack_despite_staff_only_service_plan_rls(package_case):
    c = package_case
    person = "monthly-client-" + uuid4().hex
    client = Actor(person, c.manager.firm_id)
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
            (person, "本机只读客户", person + "@example.test"),
        )
        conn.execute(
            "INSERT INTO accounting.company_memberships(company_id,user_id,role) VALUES(%s,%s,'finance')",
            (c.book["company_id"], person),
        )
    DirectoryService(c.db).grant_book_member(
        c.manager, c.book["id"], person, BookMemberGrant(role="viewer")
    )
    ServicePlansService(c.db, c.accounting, c.business).save_plan(
        c.manager,
        c.book["id"],
        PlanSave(
            expected_version=1,
            contact_user_id=person,
            due_on="2026-11-10",
            reason="只读客户独立签收",
        ),
    )
    package = c.monthly.create(c.reviewer, c.book["id"], c.request)
    readiness = c.monthly.readiness(client, c.book["id"])
    assert readiness["permissions"]["generate"] is False
    assert readiness["packages"][0]["can_acknowledge"] is True
    with c.db.transaction(actor=client) as conn:
        assert (
            conn.execute(
                "SELECT * FROM service_plan_versions WHERE book_id=?", (c.book["id"],)
            ).fetchall()
            == []
        )
    ack = c.monthly.acknowledge(
        client,
        c.book["id"],
        package["id"],
        MonthlyPackageAcknowledge(
            manifest_sha256=package["manifest_sha256"], reason="客户核对本版收到"
        ),
    )
    assert ack["customer_receipt_status"] == "acknowledged"
