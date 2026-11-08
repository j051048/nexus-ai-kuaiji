"""Controlled invitation delivery reuses provisioning; no real identity or email calls."""

import json
import pytest
from app.accounting.admin_identity import AdminIdentityService

from app.accounting.domain import AccountingError
from app.delivery.followups import DeliveryNotSent
from test_admin_identity import accounts, case, request, recipient  # noqa: F401


class Mail:
    name = "test_mail"

    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback

    def deliver(self, payload):
        self.calls.append(dict(payload))
        if self.callback:
            self.callback()
        return "test:accepted"


def send(a, result, mail, key="invite-send-0001", url=None):
    return a.service.send_invitation(
        a.case.actor, result["request"]["id"], url or result["activation_url"], key, channel=mail
    )


def test_invite_send_is_bound_private_and_never_grants_membership(accounts):
    a, mail = accounts, Mail()
    result = a.service.create(a.case.actor, request(a))
    delivered = send(a, result, mail)
    assert delivered["state"] == "accepted"
    assert send(a, result, mail)["id"] == delivered["id"]
    assert len(mail.calls) == 1
    assert mail.calls[0]["recipient_email"] == result["request"]["email"]
    identity, token = recipient(result)
    with a.case.db.transaction(actor=a.case.actor) as conn:
        rows = [
            dict(r) for r in conn.execute("SELECT * FROM account_invitation_deliveries").fetchall()
        ]
        assert token not in json.dumps(rows) and "synthetic-otp-hash" not in json.dumps(rows)
        assert (
            conn.execute(
                "SELECT 1 FROM company_memberships WHERE user_id=?", (identity.user_id,)
            ).fetchone()
            is None
        )
    public = a.service.list_requests(a.case.actor)["requests"][0]
    assert public["invitation_delivery"]["state"] == "accepted"
    assert public["status"] == "awaiting_activation"
    assert "activation_url" not in json.dumps(public)


@pytest.mark.parametrize("fault", ["host", "provider_token", "rotated", "cancelled"])
def test_invite_tampering_or_stale_link_never_sends(accounts, fault):
    a, mail = accounts, Mail()
    result = a.service.create(a.case.actor, request(a))
    url = result["activation_url"]
    if fault == "host":
        url = url.replace("demo.example.test", "evil.example.test")
    if fault == "provider_token":
        url = url.replace("synthetic-otp-hash", "tampered-hash")
    if fault == "rotated":
        a.service.retry(a.case.actor, result["request"]["id"])
    if fault == "cancelled":
        a.service.cancel(a.case.actor, result["request"]["id"])
    with pytest.raises(AccountingError):
        send(a, result, mail, url=url)
    assert not mail.calls


def test_unknown_invite_is_not_resent_even_with_new_key(accounts):
    def timeout():
        raise TimeoutError("private transport error")

    a, mail = accounts, Mail(timeout)
    result = a.service.create(a.case.actor, request(a))
    assert send(a, result, mail)["state"] == "unknown"
    with pytest.raises(AccountingError):
        send(a, result, mail, key="invite-send-0002")
    assert len(mail.calls) == 1
    assert "private" not in json.dumps(a.service.list_requests(a.case.actor))


def test_proven_unsent_invite_allows_only_explicit_bounded_new_attempt(accounts):
    def refused():
        raise DeliveryNotSent("fixture refused")

    a, mail = accounts, Mail(refused)
    result = a.service.create(a.case.actor, request(a))
    assert send(a, result, mail)["state"] == "not_sent"
    assert send(a, result, mail)["state"] == "not_sent"
    assert len(mail.calls) == 1
    assert send(a, result, Mail(), key="invite-send-0002")["state"] == "accepted"


def test_restore_hold_blocks_new_invites_and_restart_keeps_unknown(accounts, monkeypatch, tmp_path):
    a, mail = accounts, Mail(lambda: (_ for _ in ()).throw(TimeoutError()))
    result = a.service.create(a.case.actor, request(a))
    assert send(a, result, mail)["state"] == "unknown"
    a.service = AdminIdentityService(a.case.db, a.provider)
    assert send(a, result, mail)["state"] == "unknown"
    assert len(mail.calls) == 1
    hold = tmp_path / "restore-hold.json"
    hold.write_text('{"hold":true}')
    monkeypatch.setenv("ACCOUNTING_RESTORE_HOLD_FILE", str(hold))
    with pytest.raises(AccountingError):
        send(a, result, mail, key="after-restore-key")
    assert len(mail.calls) == 1


def test_expired_and_budgeted_invites_never_send_again(accounts):
    a, mail = accounts, Mail(lambda: (_ for _ in ()).throw(DeliveryNotSent("fixture")))
    result = a.service.create(a.case.actor, request(a))
    for i in range(3):
        assert send(a, result, mail, key=f"bounded-{i}")["state"] == "not_sent"
    with pytest.raises(AccountingError):
        send(a, result, mail, key="bounded-fourth")
    assert len(mail.calls) == 3


def test_invitation_evidence_is_in_restore_inventory():
    from scripts.release_recovery_hold import INVENTORY_TABLES, REQUIRED_COLUMNS

    assert "account_invitation_deliveries" in INVENTORY_TABLES
    assert {
        "state",
        "request_attempt",
        "activation_digest",
        "recipient_email",
        "created_by",
    } <= set(REQUIRED_COLUMNS["account_invitation_deliveries"])


def test_crash_after_send_marker_is_not_retried_after_restart(accounts):
    a = accounts
    result = a.service.create(a.case.actor, request(a))

    def crash():
        raise SystemExit("synthetic process exit")

    mail = Mail(crash)
    with pytest.raises(SystemExit):
        send(a, result, mail)
    a.service = AdminIdentityService(a.case.db, a.provider)
    assert send(a, result, Mail())["state"] == "sending"
    with pytest.raises(AccountingError):
        send(a, result, Mail(), key="after-crash-new-key")
    assert len(mail.calls) == 1


def test_invitation_disabled_by_default_creates_no_attempt(accounts, monkeypatch):
    monkeypatch.delenv("ACCOUNTING_INVITATION_CHANNEL", raising=False)
    a = accounts
    result = a.service.create(a.case.actor, request(a))
    with pytest.raises(AccountingError) as error:
        a.service.send_invitation(
            a.case.actor, result["request"]["id"], result["activation_url"], "disabled-key"
        )
    assert error.value.status == 503
    assert a.service.invitation_status(a.case.actor, result["request"]["id"])["delivery"] is None
