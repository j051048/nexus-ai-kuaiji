"""可插拔告警分发（Batch 08）：Webhook / 钉钉 / 企业微信 + 指纹去重 + 静默窗口 + 退避重试。

设计边界（与仓库其余集成一致）：

* 本模块**只组装负载与调度**。真正的网络调用由调用方注入的 ``sender`` 完成；
  默认不提供可用的网络 sender，测试一律使用本地桩，绝不发起真实外网请求。
* 去重按「告警指纹 + 静默窗口」在进程内完成——重复告警（同一指标同一标签）在窗口
  内只发一次；窗口过后再次发生才会再发。
* 重试是显式退避，休眠函数可注入，测试不需要真的等待。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import time
from typing import Callable, Protocol

CHANNELS = ("webhook", "dingtalk", "wecom")
SEVERITIES = ("info", "warning", "critical")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Alert:
    name: str
    severity: str
    summary: str
    labels: dict = field(default_factory=dict)
    detail: dict = field(default_factory=dict)

    def __post_init__(self):
        if not str(self.name or "").strip():
            raise ValueError("告警必须有名称")
        if self.severity not in SEVERITIES:
            raise ValueError(f"告警级别必须是 {SEVERITIES} 之一")
        if not str(self.summary or "").strip():
            raise ValueError("告警必须有摘要")

    def fingerprint(self) -> str:
        """同一告警（名称 + 级别 + 标签）得到同一指纹；摘要变化不改变指纹。"""
        payload = {
            "name": self.name,
            "severity": self.severity,
            "labels": {str(k): str(v) for k, v in sorted(self.labels.items())},
        }
        return sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class Sender(Protocol):  # pragma: no cover - 协议，不参与覆盖率
    def __call__(self, channel: str, payload: dict) -> None: ...


def webhook_payload(alert: Alert) -> dict:
    return {
        "schema": "accounting.alert.v1",
        "fingerprint": alert.fingerprint(),
        "name": alert.name,
        "severity": alert.severity,
        "summary": alert.summary,
        "labels": dict(alert.labels),
        "detail": dict(alert.detail),
        "occurred_at": _now_iso(),
    }


def dingtalk_payload(alert: Alert) -> dict:
    labels = " ".join(f"{key}={value}" for key, value in sorted(alert.labels.items()))
    content = f"[{alert.severity}] {alert.name}\n{alert.summary}"
    if labels:
        content += f"\n{labels}"
    return {
        "msgtype": "text",
        "text": {"content": content},
        "at": {"isAtAll": alert.severity == "critical"},
        "fingerprint": alert.fingerprint(),
    }


def wecom_payload(alert: Alert) -> dict:
    labels = " ".join(f"{key}={value}" for key, value in sorted(alert.labels.items()))
    lines = [f"**{alert.name}**（{alert.severity}）", alert.summary]
    if labels:
        lines.append(labels)
    return {
        "msgtype": "markdown",
        "markdown": {"content": "\n".join(lines)},
        "fingerprint": alert.fingerprint(),
    }


BUILDERS: dict[str, Callable[[Alert], dict]] = {
    "webhook": webhook_payload,
    "dingtalk": dingtalk_payload,
    "wecom": wecom_payload,
}


def build_payload(channel: str, alert: Alert) -> dict:
    if channel not in BUILDERS:
        raise ValueError(f"未知的告警通道：{channel}")
    return BUILDERS[channel](alert)


class AlertDispatcher:
    """按通道组装负载并调度发送；网络由注入的 sender 完成。"""

    def __init__(
        self,
        sender: Sender,
        *,
        channel: str = "webhook",
        silence_seconds: float = 600,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if sender is None:
            raise ValueError("必须注入 sender：本模块不内置真实网络发送")
        if channel not in CHANNELS:
            raise ValueError(f"未知的告警通道：{channel}")
        if silence_seconds < 0:
            raise ValueError("静默窗口不能为负")
        if max_attempts < 1:
            raise ValueError("最大尝试次数至少为 1")
        if backoff_seconds < 0:
            raise ValueError("退避基数不能为负")
        self.sender = sender
        self.channel = channel
        self.silence_seconds = silence_seconds
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self._clock = clock
        self._sleep = sleep
        self._last_sent: dict[str, float] = {}

    def should_silence(self, alert: Alert) -> bool:
        last = self._last_sent.get(alert.fingerprint())
        return last is not None and self._clock() - last < self.silence_seconds

    def dispatch(self, alert: Alert) -> dict:
        fingerprint = alert.fingerprint()
        if self.should_silence(alert):
            return {
                "status": "silenced",
                "fingerprint": fingerprint,
                "attempts": 0,
                "silence_remaining": round(
                    self.silence_seconds - (self._clock() - self._last_sent[fingerprint]), 3
                ),
            }
        payload = build_payload(self.channel, alert)
        attempts = 0
        last_error = ""
        while attempts < self.max_attempts:
            attempts += 1
            try:
                self.sender(self.channel, payload)
            except Exception as error:  # noqa: BLE001 - 任何发送失败都要退避重试
                last_error = f"{type(error).__name__}: {error}"
                if attempts < self.max_attempts:
                    self._sleep(self.backoff_seconds * (2 ** (attempts - 1)))
                continue
            self._last_sent[fingerprint] = self._clock()
            return {"status": "sent", "fingerprint": fingerprint, "attempts": attempts}
        # 发送失败不抛给调用方：告警通道坏掉不能反过来打断业务请求。
        return {
            "status": "failed",
            "fingerprint": fingerprint,
            "attempts": attempts,
            "error": last_error,
        }

    def reset(self, fingerprint: str | None = None) -> None:
        if fingerprint is None:
            self._last_sent.clear()
        else:
            self._last_sent.pop(fingerprint, None)
