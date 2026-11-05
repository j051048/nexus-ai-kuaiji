"""Customer acknowledgment filter is bound to the latest package, not download/open."""

from uuid import uuid4

from app.cases.service import CaseService
from app.delivery.monthly_models import MonthlyPackageAcknowledge
from app.monthly.workbench import MonthlyWorkbenchService
from test_monthly_delivery_postgres import package_case, p0_case, p0_pg  # noqa: F401


def test_monthly_signature_focus_requires_latest_version_acknowledgment(package_case):
    c = package_case
    work = MonthlyWorkbenchService(c.db, c.accounting, c.business, CaseService(c.db, c.business))

    def pending():
        return [row["book_id"] for row in work.list(c.reviewer, "2026-10", focus="receipt")["rows"]]

    package = c.monthly.create(c.reviewer, c.book["id"], c.request)
    assert c.book["id"] in pending()
    c.monthly.download(c.manager, c.book["id"], package["id"])
    assert c.book["id"] in pending()
    c.monthly.acknowledge(
        c.manager,
        c.book["id"],
        package["id"],
        MonthlyPackageAcknowledge(
            manifest_sha256=package["manifest_sha256"], reason="核对本版资料后签收"
        ),
    )
    assert c.book["id"] not in pending()
    newer = c.monthly.create(
        c.reviewer, c.book["id"], c.request.model_copy(update={"idempotency_key": uuid4().hex})
    )
    assert newer["version"] > package["version"]
    assert c.book["id"] in pending()
    assert not work.list(c.foreign, "2026-10", focus="receipt")["rows"]
