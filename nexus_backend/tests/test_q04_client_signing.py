"""Q04: a client contact sees one frozen package and signs it from a phone.

The tests stay on the accounting invariants that matter to a client signature:

* readiness explains who configures a missing contact before anything is frozen;
* opening a package is evidence of a view, never a signature;
* the receipt binds one exact version and hash, and a lost response retries safely;
* a signature never widens access beyond the package and the client's book role.
"""

from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError, Actor
from app.accounting.models import BookMemberGrant, CompanyMemberGrant, FirmMemberGrant
from app.delivery.monthly_models import MonthlyPackageAcknowledge
from app.delivery.monthly_package import MonthlyPackageService
from app.service_plans.models import PlanSave
from test_business import business  # noqa: F401 - fixture base
from test_monthly_delivery_package import close_case, freeze, ready_case  # noqa: F401 - fixtures


def contact_request(c, **changes):
    from app.delivery.monthly_models import MonthlyPackageCreate

    return MonthlyPackageCreate(
        **{
            "idempotency_key": "q04-second-version",
            "report_id": c.report["report"]["id"],
            "expected_report_version": c.report["report"]["version"],
            "reason": "第二版交付",
            **changes,
        }
    )


def test_readiness_explains_missing_contact_and_who_configures(close_case):
    data = MonthlyPackageService(close_case.db).readiness(close_case.manager, close_case.book["id"])
    blocker = next(item for item in data["blockers"] if item["code"] == "contact_missing")
    assert data["contact"]["status"] == "missing"
    assert blocker["owner"] == "事务所管理员或服务会计"
    assert blocker["entry"] == "客户服务单 · 客户联系人"
    assert "联系人" in blocker["message"]


def test_revoked_contact_points_at_the_grant_that_is_missing(ready_case):
    c = ready_case
    with c.db.transaction(actor=c.manager) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE book_id=? AND user_id=?",
            (c.book["id"], c.actors["finance"].user_id),
        )
    data = c.packages.readiness(c.manager, c.book["id"])
    blocker = next(item for item in data["blockers"] if item["code"] == "contact_ineligible")
    assert blocker["owner"] == "事务所管理员"
    assert blocker["entry"] == "企业与账号 · 账套成员"
    assert data["contact"]["status"] == "ineligible"


def test_contact_with_review_rights_is_warned_and_cannot_self_sign(ready_case):
    c = ready_case
    # A one-person shop: the same person runs the books and signs for the client.
    identity = SimpleNamespace(
        user_id="q04-solo", display_name="一人兼两职", email="q04-solo@example.test"
    )
    c.directory.register_identity(identity)
    c.directory.grant_firm_member(c.manager, FirmMemberGrant(email=identity.email, role="admin"))
    c.directory.grant_company_member(
        c.manager, c.company["id"], CompanyMemberGrant(email=identity.email, role="admin")
    )
    solo = Actor(identity.user_id, c.manager.firm_id)
    c.packages.plans.save_plan(
        c.manager,
        c.book["id"],
        PlanSave(
            expected_version=1,
            contact_user_id=identity.user_id,
            due_on="2026-11-10",
            reason="一人同时负责生成与签收",
        ),
    )
    data = c.packages.readiness(c.manager, c.book["id"])
    warning = next(item for item in data["warnings"] if item["code"] == "contact_can_review")
    assert warning["owner"] == "事务所管理员或服务会计"
    assert warning["entry"] == "客户服务单 · 客户联系人"
    # Generation still works (the firm must be able to close the month)...
    package = c.packages.create(solo, c.book["id"], contact_request(c))
    # ...but the same person can never sign it.
    assert package["can_acknowledge"] is False
    with pytest.raises(AccountingError) as error:
        c.packages.acknowledge(
            solo,
            c.book["id"],
            package["id"],
            MonthlyPackageAcknowledge(
                manifest_sha256=package["manifest_sha256"], reason="自己生成自己签收"
            ),
        )
    assert error.value.status == 403


def test_package_detail_lists_contents_month_and_receipt_states(ready_case):
    package = freeze(ready_case)
    detail = ready_case.packages.detail(
        ready_case.actors["finance"], ready_case.book["id"], package["id"]
    )
    view = detail["package"]
    assert view["period"] == ready_case.book["period"]
    assert view["company_id"] == ready_case.company["id"]
    assert view["created_at"]
    assert view["file_count"] == 11
    assert {"README.html", "report.html", "account_balances.csv"} <= {
        item["path"] for item in view["files"]
    }
    assert view["customer_opening_status"] == "not_opened"
    assert view["customer_receipt_status"] == "not_recorded"
    assert view["acknowledgement"] is None
    assert view["superseded"] is False


def test_contact_opening_is_recorded_once_and_is_not_a_signature(ready_case):
    c = ready_case
    package = freeze(c)
    first = c.packages.open(c.actors["finance"], c.book["id"], package["id"])
    assert first["customer_opening_status"] == "opened"
    assert first["opening"]["opened_by"] == c.actors["finance"].user_id
    assert first["customer_receipt_status"] == "not_recorded"
    assert first["acknowledgement"] is None
    again = c.packages.open(c.actors["finance"], c.book["id"], package["id"])
    assert again["opening"] == first["opening"]
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM monthly_delivery_opens WHERE package_id=?", (package["id"],)
            ).fetchone()[0]
            == 1
        )


def test_only_the_configured_contact_can_open_a_package(ready_case):
    c = ready_case
    package = freeze(c)
    # A company administrator without a book grant cannot even see the book.
    with pytest.raises(AccountingError) as error:
        c.packages.open(c.actors["company_admin"], c.book["id"], package["id"])
    assert error.value.status == 404
    # The reviewer sees the book but is not the configured client contact.
    with pytest.raises(AccountingError) as error:
        c.packages.open(c.reviewer, c.book["id"], package["id"])
    assert error.value.status == 403


def test_package_of_another_company_is_not_reachable(ready_case):
    c = ready_case
    package = freeze(c)
    for action in (
        lambda: c.packages.detail(c.actors["other_employee"], c.book["id"], package["id"]),
        lambda: c.packages.open(c.actors["other_employee"], c.book["id"], package["id"]),
    ):
        with pytest.raises(AccountingError) as error:
            action()
        assert error.value.status == 404


def test_previous_version_stays_visible_and_signable(ready_case):
    c = ready_case
    first = freeze(c)
    second = c.packages.create(c.reviewer, c.book["id"], contact_request(c))
    assert second["version"] == first["version"] + 1
    stale = c.packages.detail(c.actors["finance"], c.book["id"], first["id"])["package"]
    assert stale["version"] == first["version"]
    assert stale["superseded"] is True
    assert stale["can_acknowledge"] is True
    signed = c.packages.acknowledge(
        c.actors["finance"],
        c.book["id"],
        first["id"],
        MonthlyPackageAcknowledge(
            manifest_sha256=first["manifest_sha256"], reason="客户签收第一版留存"
        ),
    )
    assert signed["manifest_sha256"] == first["manifest_sha256"]


def test_retry_after_a_lost_response_is_idempotent(ready_case):
    c = ready_case
    package = freeze(c)
    body = MonthlyPackageAcknowledge(
        manifest_sha256=package["manifest_sha256"], reason="客户确认收到本版"
    )
    first = c.packages.acknowledge(c.actors["finance"], c.book["id"], package["id"], body)
    retried = c.packages.acknowledge(c.actors["finance"], c.book["id"], package["id"], body)
    assert retried["acknowledgement"] == first["acknowledgement"]
    with pytest.raises(AccountingError) as error:
        c.packages.acknowledge(
            c.actors["finance"],
            c.book["id"],
            package["id"],
            MonthlyPackageAcknowledge(
                manifest_sha256=package["manifest_sha256"], reason="改口说没收到"
            ),
        )
    assert error.value.status == 409


def test_signing_never_widens_the_clients_book_role(ready_case):
    c = ready_case
    c.directory.grant_book_member(
        c.manager, c.book["id"], "finance", BookMemberGrant(role="viewer")
    )
    package = freeze(c)
    c.packages.acknowledge(
        c.actors["finance"],
        c.book["id"],
        package["id"],
        MonthlyPackageAcknowledge(
            manifest_sha256=package["manifest_sha256"], reason="只读客户签收"
        ),
    )
    with c.db.transaction(actor=c.actors["finance"]) as conn:
        book = c.packages.access.book(conn, c.actors["finance"], c.book["id"])
    assert book["permissions"]["export"] is False
    assert book["permissions"]["review"] is False
    assert "read" not in book["permissions"]  # reading the book itself is implied
