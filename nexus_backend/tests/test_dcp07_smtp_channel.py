"""DCP07: the SMTP adapter speaks real protocol to a local server.

Every case uses a loopback server implemented in this file, fictional data and no
real recipient. Acceptance means the server took the message, never that a client
read it; ambiguity must stay ambiguous so the dispatcher records ``unknown``.
"""

import email
import email.message
import socket
import threading

import pytest

from app.delivery.followups import DeliveryFollowupService, DeliveryNotSent
from app.delivery.smtp_channel import SmtpChannel


class MiniSmtp:
    """Just enough SMTP to answer one session and keep what it was given."""

    def __init__(self, mode="ok"):
        self.mode, self.messages, self.sessions = mode, [], 0
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            self.sessions += 1
            self._session(conn)

    def _session(self, conn):
        stream = conn.makefile("rb")
        body, data_mode = [], False
        conn.sendall(b"220 mini smtp\r\n")
        try:
            while True:
                line = stream.readline()
                if not line:
                    return
                if data_mode:
                    if line in (b".\r\n", b".\n"):
                        data_mode = False
                        if self.mode == "drop_after_data":
                            conn.close()
                            return
                        self.messages.append(b"".join(body))
                        conn.sendall(b"250 queued\r\n")
                        continue
                    body.append(line)
                    continue
                command = line.upper()
                if command.startswith(b"EHLO") or command.startswith(b"HELO"):
                    conn.sendall(b"250-mini\r\n250 8BITMIME\r\n")
                elif command.startswith(b"MAIL FROM"):
                    conn.sendall(b"250 ok\r\n")
                elif command.startswith(b"RCPT TO"):
                    conn.sendall(b"550 5.1.1 denied\r\n" if self.mode == "refuse_rcpt" else b"250 ok\r\n")
                elif command.startswith(b"DATA"):
                    conn.sendall(b"354 go\r\n")
                    data_mode = True
                elif command.startswith(b"QUIT"):
                    conn.sendall(b"221 bye\r\n")
                    conn.close()
                    return
                else:
                    conn.sendall(b"250 ok\r\n")
        except OSError:
            return

    def close(self):
        self.server.close()


@pytest.fixture
def server():
    made = []

    def factory(mode="ok"):
        item = MiniSmtp(mode)
        made.append(item)
        return item

    yield factory
    for item in made:
        item.close()


def channel(port, **overrides):
    options = {
        "host": "127.0.0.1",
        "port": port,
        "sender": "firm@example.invalid",
        "base_url": "https://books.example.invalid",
    }
    options.update(overrides)
    return SmtpChannel(**options)


def payload(**overrides):
    value = {
        "notification_id": "n-1",
        "book_id": "b-1",
        "period": "2026-10",
        "package_version": 3,
        "recipient_email": "client@example.invalid",
    }
    value.update(overrides)
    return value


def test_accepted_message_keeps_only_a_login_reminder(server):
    smtp = server()
    reference = channel(smtp.port).deliver(payload())
    assert reference.startswith("smtp:") and len(smtp.messages) == 1
    message = email.message_from_bytes(smtp.messages[0])
    text = message.get_payload(decode=True).decode()
    assert "2026-10" in text and "/workspace/monthly" in text
    assert "不包含金额" in text and "¥" not in text and "附件" in text


def test_refused_recipient_is_provably_unsent(server):
    smtp = server("refuse_rcpt")
    with pytest.raises(DeliveryNotSent, match="拒收"):
        channel(smtp.port).deliver(payload())
    assert smtp.messages == []


def test_disconnect_after_data_is_not_called_unsent(server):
    smtp = server("drop_after_data")
    with pytest.raises(Exception) as failure:
        channel(smtp.port).deliver(payload())
    assert not isinstance(failure.value, DeliveryNotSent)
    assert smtp.sessions == 1


def test_missing_address_and_remote_host_are_refused():
    with pytest.raises(DeliveryNotSent):
        channel(25).deliver(payload(recipient_email=""))
    with pytest.raises(Exception) as failure:
        channel(25, host="smtp.example.invalid")
    assert "本机" in str(failure.value)


# ------------------------------------------------------------------ dispatch

from app.delivery.followup_models import DeliveryGrantRequest, DeliveryTaskRequest
from app.delivery.monthly_models import MonthlyPackageCreate
from app.delivery.monthly_package import MonthlyPackageService
from app.service_plans.service import ServicePlansService
from test_business import business  # noqa: F401 - pytest fixture chain
from test_p0_close_dependencies import close_case  # noqa: F401 - pytest fixture chain
from test_monthly_delivery_package import ready_case  # noqa: F401 - pytest fixture


@pytest.fixture
def case(ready_case):  # noqa: F811 - pytest fixture chain
    plans = ServicePlansService(ready_case.db, ready_case.ledger, ready_case.service)
    ready_case.packages = MonthlyPackageService(ready_case.db, plans)
    ready_case.followups = DeliveryFollowupService(ready_case.db, ready_case.packages, plans)
    with ready_case.db.transaction(write=True, actor=ready_case.reviewer) as conn:
        conn.execute("UPDATE users SET email='finance@example.invalid' WHERE id='finance'")
    return ready_case


def grant(case):
    case.packages.create(
        case.reviewer,
        case.book["id"],
        MonthlyPackageCreate(
            idempotency_key="dcp07-package",
            report_id=case.report["report"]["id"],
            expected_report_version=case.report["report"]["version"],
            reason="会计复核后冻结交付",
        ),
    )
    return case.followups.configure(
        case.reviewer,
        case.book["id"],
        DeliveryGrantRequest(period="2026-10", recipient_user_id="finance", reason="客户签收人"),
    )


def queue(case):
    return case.followups.queue(
        case.reviewer,
        case.book["id"],
        DeliveryTaskRequest(period="2026-10", kind="signature_reminder", reason="到期提醒签收"),
    )


def test_dispatch_marks_server_acceptance_without_claiming_receipt(case, server):
    grant(case)
    queued = queue(case)
    smtp = server()
    result = case.followups.dispatch(channel(smtp.port), limit=5)
    assert result["sent"] == 1 and result["failed"] == 0 and result["unknown"] == 0
    with case.db.connect() as conn:
        row = conn.execute(
            "SELECT state,sink_ref FROM delivery_notifications WHERE id=?",
            (queued["notification_id"],),
        ).fetchone()
    assert row["state"] == "sink_written" and row["sink_ref"].startswith("smtp:")
    again = case.followups.dispatch(channel(smtp.port), limit=5)
    assert again["sent"] == 0 and len(smtp.messages) == 1


def test_unknown_outcome_survives_redispatch(case, server):
    grant(case)
    queued = queue(case)
    smtp = server("drop_after_data")
    assert case.followups.dispatch(channel(smtp.port), limit=5)["unknown"] == 1
    second = case.followups.dispatch(channel(smtp.port), limit=5)
    assert second["sent"] == 0 and smtp.sessions == 1
    with case.db.connect() as conn:
        row = conn.execute(
            "SELECT state FROM delivery_notifications WHERE id=?",
            (queued["notification_id"],),
        ).fetchone()
    assert row["state"] == "unknown"
