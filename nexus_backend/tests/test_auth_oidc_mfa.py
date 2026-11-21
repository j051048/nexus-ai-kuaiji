"""OIDC/AAL2 守卫：高危业务动作与管理员动作共用同一道门。

这里只测谓词本身，因为守卫的全部判断都收敛在一个纯函数里；中间件的其余
部分（取 token、验签、把 403 写回）已有既有测试覆盖，重复搭一遍套未必更可信。
"""

import pytest

from app.auth.admin_security import HIGH_RISK_ACTIONS, mfa_enforcement, requires_admin_mfa


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/treasury/payment-orders/po-1/approve"),
        ("POST", "/api/b04-close/run-1/lock"),
        ("POST", "/api/reports/report-1/publish"),
        ("POST", "/api/books/book-1/review-delegations"),
        ("POST", "/api/firm/batch-operations/review-export"),
    ],
)
def test_high_risk_business_actions_require_aal2(method, path):
    assert requires_admin_mfa(path, method) is True


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/treasury/payment-orders/po-1/approve"),
        ("OPTIONS", "/api/reports/report-1/publish"),
        ("POST", "/api/treasury/payment-orders/po-1/submit"),
        ("POST", "/api/b04-close/preflight"),
        ("POST", "/api/firm/batch-operations/review-export/preview"),
        ("GET", "/api/books/book-1/review-delegations"),
        ("POST", "/api/books/book-1/review-delegations/delegation-1"),
    ],
)
def test_reads_and_near_misses_stay_open(method, path):
    assert requires_admin_mfa(path, method) is False


def test_admin_paths_still_require_aal2_and_trailing_slash_is_normalised():
    assert requires_admin_mfa("/api/admin/users/", "POST") is True
    assert requires_admin_mfa("/api/firm/members/", "POST") is True
    assert requires_admin_mfa("/api/treasury/payment-orders/po-1/approve/", "POST") is True


def test_recipient_acceptance_stays_reachable_without_step_up():
    # 收件人还没加入事务所，此处校验的是待审请求本身，不是管理权。
    assert (
        requires_admin_mfa("/api/admin/account-requests/req-1/accept", "POST") is False
    )


def test_the_five_high_risk_actions_are_exactly_what_the_doc_claims():
    assert len(HIGH_RISK_ACTIONS) == 5


def test_enforcement_defaults_to_warn_so_a_deploy_cannot_lock_everyone_out():
    assert mfa_enforcement({}) == "warn"
    assert mfa_enforcement({"ACCOUNTING_MFA_ENFORCE": "enforce"}) == "enforce"
    assert mfa_enforcement({"ACCOUNTING_MFA_ENFORCE": " OFF "}) == "off"


def test_unknown_enforcement_mode_refuses_to_start():
    import pytest

    with pytest.raises(RuntimeError):
        mfa_enforcement({"ACCOUNTING_MFA_ENFORCE": "sometimes"})
