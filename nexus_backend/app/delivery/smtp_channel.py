"""Loopback rehearsal and explicit opt-in authenticated TLS SMTP adapters.

The dispatcher's own fences decide retries; this module only classifies what the
server actually said. An accepted SMTP transaction proves the server took
the message, never that a client read it. Anything ambiguous stays an exception
so the caller records ``unknown`` instead of retrying.
"""

import socket
import smtplib
import ssl
import hashlib
from email.message import EmailMessage
from email.headerregistry import Address
from email.errors import HeaderParseError
from email.utils import make_msgid
from urllib.parse import urlparse, urlencode

from .followups import ChannelDisabled, DeliveryNotSent


LOOPBACK = {"127.0.0.1", "localhost", "::1", ""}


def _loopback(host):
    return (host or "").strip().lower() in LOOPBACK


def _mailbox(value):
    # A notification has exactly one bound recipient, never a comma-separated list.
    if not isinstance(value, str) or any(char in value for char in "\r\n"):
        raise ValueError("Invalid mailbox")
    value = value.strip()
    try:
        parsed = Address(addr_spec=value)
    except HeaderParseError:
        raise ValueError("Invalid mailbox") from None
    if not parsed.username or not parsed.domain:
        raise ValueError("Invalid mailbox")
    return value


class SmtpChannel:
    """SMTP over plain TCP to a local test server only."""

    name = "smtp"

    def __init__(self, host, port=25, sender="noreply@example.invalid", base_url="", timeout=10):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ChannelDisabled("SMTP 端口须为 1–65535 的整数")
        if type(timeout) is not int or not 1 <= timeout <= 60:
            raise ChannelDisabled("SMTP 超时须为 1–60 秒")
        if not _loopback(host):
            raise ChannelDisabled("SMTP 只允许连接本机测试服务器；生产启用需另行授权")
        try:
            sender = _mailbox(sender)
        except (ValueError, TypeError, IndexError):
            raise ChannelDisabled("SMTP 发件地址无效") from None
        parsed = urlparse(base_url or "")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ChannelDisabled("SMTP 通知链接须为绝对地址")
        self.host, self.port, self.sender = host, port, sender
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def message(self, payload):
        try:
            recipient = _mailbox(payload.get("recipient_email"))
        except (ValueError, TypeError, IndexError):
            raise DeliveryNotSent("收件人邮箱无效，消息未发送") from None
        period, version = payload.get("period", ""), payload.get("package_version")
        query = {"book": payload.get("book_id", ""), "period": period}
        if payload.get("package_id"):
            query.update(
                package=payload["package_id"],
                package_version=version,
                manifest_sha256=payload.get("manifest_sha256", ""),
            )
        link = f"{self.base_url}/workspace/monthly?{urlencode(query)}"
        # Stable tracing identifier is not a claim that SMTP supports idempotency.
        number = (
            (
                "<"
                + hashlib.sha256(str(payload["notification_id"]).encode()).hexdigest()
                + "@nexus-notice>"
            )
            if payload.get("notification_id")
            else make_msgid(domain="smtp.local")
        )
        text = (
            f"贵司 {period} 月度财税简报第 {version} 版已准备好。\n\n"
            f"请登录后查看：{link}\n\n"
            "为保护财税信息，邮件只发送提醒，不包含金额、报表或文件附件。"
            "登录后仍需通过账号权限查看与签收。\n"
        )
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = (
            self.sender,
            recipient,
            (f"衡账 · {period} 月度财税简报已就绪"),
        )
        message["Message-ID"] = number
        if payload.get("purpose") == "account_invitation":
            link = payload.get("activation_url", "")
            if not link.startswith(self.base_url + "/account-activation#"):
                raise DeliveryNotSent("激活链接与应用地址不一致，未发送")
            message.replace_header("Subject", "衡账 · 请本人激活账号")
            text = (
                "请使用本人邮箱打开以下一次性激活链接；请勿转发。链接一小时内有效，重新生成后旧链接失效。\n\n"
                + link
                + "\n\n邮件被接受不代表账号已激活；账套与复核权限仍须另行授权。"
            )
        message.set_content(text)
        return message, recipient

    def _connect(self):
        return smtplib.SMTP(self.host, self.port, timeout=self.timeout)

    def _prepare(self, client):
        client.ehlo()

    def deliver(self, payload):
        try:
            message, recipient = self.message(payload)
        except (ValueError, TypeError, IndexError):
            # Local construction failed before any connection or SMTP command.
            raise DeliveryNotSent("通知内容格式无效，消息未发送") from None
        try:
            client = self._connect()
        except (OSError, smtplib.SMTPException) as failure:
            # Nothing was written to the wire; a refused connection is provably unsent.
            raise DeliveryNotSent(f"SMTP 连接失败：{type(failure).__name__}") from failure
        try:
            try:
                self._prepare(client)
            except (OSError, smtplib.SMTPException) as failure:
                raise DeliveryNotSent(f"SMTP 握手失败：{type(failure).__name__}") from failure
            try:
                client.send_message(message, to_addrs=[recipient])
            except smtplib.SMTPRecipientsRefused as failure:
                raise DeliveryNotSent("SMTP 服务器拒收全部收件人") from failure
            except smtplib.SMTPDataError as failure:
                code = getattr(failure, "smtp_code", 0)
                if 500 <= int(code) < 600:
                    raise DeliveryNotSent(f"SMTP 明确拒收：{code}") from failure
                # A 4xx answer is not acceptance; wording alone cannot prove it.
                raise TimeoutError("SMTP 服务器未明确接受消息") from failure
        except (TimeoutError, socket.timeout):
            raise
        finally:
            try:
                client.quit()
            except Exception:
                pass
        return "smtp:" + str(message.get("Message-ID", ""))[:400]


class SecureSmtpChannel(SmtpChannel):
    """Explicit opt-in SMTPS, certificate verification mandatory, no plaintext fallback."""

    name = "smtp_tls"

    def __init__(
        self,
        host,
        port=465,
        sender="noreply@example.invalid",
        base_url="",
        timeout=10,
        username="",
        password="",
    ):
        # Reuse bounds/address checks without relaxing the loopback-only adapter.
        super().__init__("127.0.0.1", port, sender, base_url, timeout)
        parsed = urlparse(base_url)
        if (
            not host
            or not username
            or not password
            or parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(char in str(host) + sender + username for char in "\r\n")
        ):
            raise ChannelDisabled("安全邮件通道需要完整配置及 HTTPS 应用地址")
        self.host, self.username, self._password = host, username, password

    def _connect(self):
        return smtplib.SMTP_SSL(
            self.host, self.port, timeout=self.timeout, context=ssl.create_default_context()
        )

    def _prepare(self, client):
        client.login(self.username, self._password)
