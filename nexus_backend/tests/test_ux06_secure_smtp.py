import smtplib
from urllib.parse import parse_qs, urlsplit
import pytest
from app.delivery.followups import ChannelDisabled, DeliveryNotSent
from app.delivery.smtp_channel import SmtpChannel


def payload():
    return dict(
        notification_id="notice",
        book_id="book",
        period="2026-11",
        package_id="a" * 32,
        package_version=3,
        manifest_sha256="b" * 64,
        recipient_email="fake@example.invalid",
    )


def test_mail_links_to_exact_package_and_version():
    channel = SmtpChannel("127.0.0.1", base_url="http://127.0.0.1:1234")
    message, _ = channel.message(payload())
    link = next(word for word in message.get_content().split() if "/workspace/" in word)
    params = parse_qs(urlsplit(link).query)
    assert params["package"] == ["a" * 32]
    assert params["package_version"] == ["3"]
    assert params["manifest_sha256"] == ["b" * 64]


def test_secure_channel_verifies_tls_before_auth_and_preserves_unknown(monkeypatch):
    from app.delivery.smtp_channel import SecureSmtpChannel

    calls = []

    class Transport:
        def __init__(self, host, port, *, timeout, context):
            assert context.check_hostname
            calls.append("tls")

        def login(self, user, password):
            calls.append("auth")

        def send_message(self, *args, **kwargs):
            calls.append("send")
            raise TimeoutError("synthetic-after-send")

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP_SSL", Transport)
    channel = SecureSmtpChannel(
        "smtp.example.invalid",
        sender="fake@example.invalid",
        base_url="https://app.example.invalid",
        username="fake",
        password="synthetic",
    )
    with pytest.raises(TimeoutError):
        channel.deliver(payload())
    assert calls == ["tls", "auth", "send"]


def test_secure_handshake_failure_proves_unsent(monkeypatch):
    from app.delivery.smtp_channel import SecureSmtpChannel

    monkeypatch.setattr(
        smtplib, "SMTP_SSL", lambda *a, **kw: (_ for _ in ()).throw(OSError("secret-do-not-show"))
    )
    channel = SecureSmtpChannel(
        "smtp.example.invalid",
        sender="fake@example.invalid",
        base_url="https://app.example.invalid",
        username="fake",
        password="synthetic",
    )
    with pytest.raises(DeliveryNotSent) as caught:
        channel.deliver(payload())
    assert "secret-do-not-show" not in str(caught.value)
    with pytest.raises(ChannelDisabled):
        SecureSmtpChannel(
            "smtp.example.invalid",
            base_url="http://app.example.invalid",
            username="fake",
            password="synthetic",
        )
