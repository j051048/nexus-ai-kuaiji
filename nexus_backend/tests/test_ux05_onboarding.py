import pytest

from app.accounting.domain import AccountingError, Actor
from test_admin_identity import accounts, request, recipient  # noqa: F401
from test_directory import case  # noqa: F401


def test_bulk_preview_is_read_only_and_does_not_grant_books(accounts):
    a, c = accounts, accounts.case
    payload = request(a).model_dump(exclude={"initial_password"})
    result = a.service.preview_accounts(c.actor, [payload])
    assert result["items"][0]["email"] == payload["email"]
    assert result["items"][0]["book_access"] == "none"
    assert not a.provider.calls
    with c.db.transaction(actor=c.actor) as conn:
        assert conn.execute("SELECT count(*) FROM admin_account_requests").fetchone()[0] == 0


def test_preview_duplicate_roles_department_and_foreign_scope(accounts):
    a, c = accounts, accounts.case
    payload = request(a).model_dump(exclude={"initial_password"})
    result = a.service.preview_accounts(c.actor, [payload, payload])
    assert result["ready"] is False
    assert result["items"][1]["code"] == "duplicate_email"
    for changed in [
        dict(company_role="owner"),
        dict(company_id="other"),
        dict(department_id="other"),
    ]:
        result = a.service.preview_accounts(c.actor, [{**payload, **changed}])
        assert result["ready"] is False
    assert not a.provider.calls


def test_preview_rechecks_current_admin_authority_and_limits(accounts):
    a, c = accounts, accounts.case
    actor = Actor(c.user("employee").user_id, c.actor.firm_id)
    payload = request(a).model_dump(exclude={"initial_password"})
    result = a.service.preview_accounts(actor, [payload])
    assert not result["ready"]
    with pytest.raises(AccountingError):
        a.service.preview_accounts(c.actor, [payload] * 51)


def test_expired_invitation_is_displayed_but_never_accepted(accounts):
    a, c = accounts, accounts.case
    response = a.service.create(c.actor, request(a))
    identity, token = recipient(response)
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute(
            "UPDATE admin_account_requests SET expires_at=1 WHERE id=?",
            (response["request"]["id"],),
        )
    row = a.service.list_requests(c.actor)["requests"][0]
    assert row["activation_expired"] is True
    with pytest.raises(AccountingError):
        a.service.accept(identity, row["id"], token)


def test_preview_flags_existing_pending_invitation_without_reissuing(accounts):
    a, c = accounts, accounts.case
    original = request(a)
    a.service.create(c.actor, original)
    before = len(a.provider.calls)
    result = a.service.preview_accounts(
        c.actor, [original.model_dump(exclude={"initial_password"})]
    )
    assert not result["ready"]
    assert result["items"][0]["code"] == "existing_request"
    assert len(a.provider.calls) == before
