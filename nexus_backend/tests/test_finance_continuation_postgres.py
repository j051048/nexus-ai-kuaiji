"""Same workflows on restricted PostgreSQL roles, including concurrent acceptance."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.accounting.domain import AccountingError
from test_business_postgres import ADMIN_URL, APP_URL
from test_intelligence_workflow_postgres import pg_ai, business_case, business_postgres  # noqa: F401
from test_finance_agent_reuse_postgres import finance  # noqa: F401
import test_finance_continuation as flows

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需显式隔离 PG 配置")


@pytest.mark.parametrize(
    "case",
    [
        flows.test_settlement_detail_partial_reverse_and_stale_version,
        flows.test_settlement_detail_never_crosses_scope_or_grants_employee_access,
        flows.test_settlement_preview_fingerprint_changes_on_other_allocation,
        flows.test_asset_keeps_identity_across_year_and_blocks_unfinished_prior,
        flows.test_asset_account_change_invalidates_review,
        flows.test_native_asset_run_cannot_reference_another_ledger,
        flows.test_asset_three_months_cross_year_posts_exact_residual,
        flows.test_reversed_asset_history_requires_manual_reconciliation,
    ],
    ids=[
        "partial_reverse",
        "scope",
        "fingerprint",
        "asset_carry",
        "account_version",
        "native_scope",
        "cross_year",
        "reversed_history",
    ],
)
def test_workflow(finance, case):
    case(finance)


@pytest.mark.parametrize("boundary", ["projection", "api", "native"])
def test_corrected_payment(finance, boundary):
    flows.test_reversed_payment_is_not_available_for_new_allocation(finance, boundary)


def test_competing_settlement_preview_accepts_once(finance):
    c = finance
    sub, item, voucher = flows.settlement_case(c)
    view = sub.item_workspace(c.manager, c.book["id"], item["id"])

    def execute(_):
        try:
            flows.allocate(c, sub, item, voucher, view["fingerprint"])
            return "accepted"
        except AccountingError as error:
            assert error.status == 409
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(execute, range(2))) == ["accepted", "stale"]
    assert (
        sub.item_workspace(c.manager, c.book["id"], item["id"])["item"]["remaining_cents"] == 700000
    )


def test_revoked_intermediate_period_never_disappears_from_asset_history(finance):
    from app.accounting.models import BookMemberGrant
    from test_finance_agent_reuse import asset_case

    c = finance
    service, asset = asset_case(c, start="2026-12")
    middle, target = flows.next_period(c, "2026-11"), flows.next_period(c, "2026-12")
    reviewer = c.actors["accountant"]
    for book in (middle, target):
        c.directory.grant_book_member(
            c.manager, book, reviewer.user_id, BookMemberGrant(role="reviewer")
        )
    assert service.workspace(reviewer, target)["assets"][0]["id"] == asset["id"]
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (middle, reviewer.user_id),
        )
    for operation in (
        lambda: service.workspace(reviewer, target),
        lambda: service.generate_asset(reviewer, asset["id"], book_id=target),
    ):
        with pytest.raises(AccountingError) as denied:
            operation()
        assert denied.value.status == 403
