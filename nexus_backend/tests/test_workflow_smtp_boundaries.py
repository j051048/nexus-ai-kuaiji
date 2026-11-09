"""Local pre-send failures must not become unknown deliveries or extra recipients."""

import pytest

from app.delivery.followups import ChannelDisabled, DeliveryNotSent
from app.delivery.smtp_channel import SmtpChannel
from test_dcp07_smtp_channel import (  # noqa: F401 - reuse complete business fixtures
    business,
    close_case,
    ready_case,
    case,
    grant,
    queue,
)


@pytest.mark.parametrize("field", ["port", "timeout"])
def test_smtp_rejects_boolean_configuration(field):
    with pytest.raises(ChannelDisabled):
        SmtpChannel("127.0.0.1", base_url="http://127.0.0.1", **{field: True})


@pytest.mark.parametrize(
    "recipient",
    ["first@example.invalid,second@example.invalid", "bad\r\nBcc: other@example.invalid", 42],
)
def test_invalid_bound_recipient_is_provably_unsent(monkeypatch, recipient):
    channel = SmtpChannel("127.0.0.1", base_url="http://127.0.0.1")
    calls = []
    monkeypatch.setattr(channel, "_connect", lambda: calls.append("connect"))
    with pytest.raises(DeliveryNotSent):
        channel.deliver({"notification_id": "test", "recipient_email": recipient})
    assert calls == []


def test_invalid_local_header_is_unsent_without_disclosing_input(monkeypatch):
    channel = SmtpChannel("127.0.0.1", base_url="http://127.0.0.1")
    calls = []
    monkeypatch.setattr(channel, "_connect", lambda: calls.append("connect"))
    with pytest.raises(DeliveryNotSent) as failure:
        channel.deliver(
            {
                "recipient_email": "one@example.invalid",
                "period": "secret\r\nBcc: other@example.invalid",
            }
        )
    assert "secret" not in str(failure.value)
    assert calls == []


def test_dispatch_records_invalid_recipient_as_unsent_not_unknown(case, monkeypatch):
    with case.db.transaction(write=True, actor=case.reviewer) as conn:
        conn.execute(
            "UPDATE users SET email='one@example.invalid,two@example.invalid' WHERE id='finance'"
        )
    grant(case)
    queue(case)
    channel = SmtpChannel("127.0.0.1", base_url="http://127.0.0.1")
    calls = []
    monkeypatch.setattr(channel, "_connect", lambda: calls.append("connect"))
    outcome = case.followups.dispatch(channel, limit=1)
    assert outcome["failed"] == 1 and outcome["unknown"] == 0 and outcome["sent"] == 0
    item = case.followups.notifications(case.reviewer, case.book["id"], "2026-10")["items"][0]
    assert item["state"] == "failed"
    assert case.followups.dispatch(channel, limit=1)["sent"] == 0
    assert calls == []
