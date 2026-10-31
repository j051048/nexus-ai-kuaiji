import pytest

from app.accounting.domain import AccountingError
from app.accounting.access_policy import AccessPolicyService, ContactAccess
from app.delivery.monthly_models import MonthlyPackageAcknowledge
from test_business import business  # noqa: F401
from test_p0_close_dependencies import close_case  # noqa: F401
from test_monthly_delivery_package import ready_case, freeze  # noqa: F401


def test_contact_only_can_open_exact_package_without_ledger(ready_case):
    c = ready_case
    p = freeze(c)
    c.directory.revoke_book_member(c.manager, c.book["id"], "finance")
    policy = AccessPolicyService(c.db)
    policy.contact_access(
        c.reviewer, c.book["id"], ContactAccess(user_id="finance", reason="仅签收本月资料包")
    )
    actor = c.actors["finance"]
    with pytest.raises(AccountingError):
        c.ledger.get_book(actor, c.book["id"])
    assert policy.recipient_packages(actor)["packages"][0]["id"] == p["id"]
    assert c.packages.open(actor, c.book["id"], p["id"])["can_acknowledge"]
    assert c.packages.download(actor, c.book["id"], p["id"]).startswith(b"PK")
    c.packages.acknowledge(
        actor,
        c.book["id"],
        p["id"],
        MonthlyPackageAcknowledge(
            manifest_sha256=p["manifest_sha256"], reason="核对本版本资料后签收"
        ),
    )
    policy.contact_access(
        c.reviewer,
        c.book["id"],
        ContactAccess(user_id="finance", reason="撤销资料包访问授权"),
        revoke=True,
    )
    assert policy.recipient_packages(actor)["packages"] == []
    with pytest.raises(AccountingError):
        c.packages.download(actor, c.book["id"], p["id"])
