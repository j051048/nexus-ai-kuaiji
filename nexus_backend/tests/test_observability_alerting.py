"""Batch 08：告警分发（Webhook / 钉钉 / 企业微信）——全部用本地桩，绝不发真实外网请求。"""

import json

import pytest

from app.observability.alerting import (
    Alert,
    AlertDispatcher,
    build_payload,
    dingtalk_payload,
    webhook_payload,
    wecom_payload,
)


class Stub:
    """本地桩：记录调用、可按脚本失败，从不联网。"""

    def __init__(self, failures=0):
        self.calls: list[tuple[str, dict]] = []
        self.failures = failures

    def __call__(self, channel, payload):
        self.calls.append((channel, payload))
        if len(self.calls) <= self.failures:
            raise RuntimeError("通道暂时不可用")


def _alert(**changes):
    base = {
        "name": "accounting.http_5xx_rate",
        "severity": "critical",
        "summary": "5xx 比例超过 5%",
        "labels": {"route": "/api/books/{book_id}", "firm": "f-1"},
        "detail": {"window": "5m", "rate": 0.07},
    }
    return Alert(**{**base, **changes})


def test_fingerprint_is_stable_and_label_order_independent():
    first = _alert(labels={"firm": "f-1", "route": "/api/books/{book_id}"})
    second = _alert(labels={"route": "/api/books/{book_id}", "firm": "f-1"})
    assert first.fingerprint() == second.fingerprint()
    assert len(first.fingerprint()) == 64
    # 摘要变化不改变指纹（同一告警的重复发生）；级别或标签变化才换指纹。
    assert _alert(summary="另一句话").fingerprint() == first.fingerprint()
    assert _alert(severity="warning").fingerprint() != first.fingerprint()
    assert _alert(labels={"firm": "f-2"}).fingerprint() != first.fingerprint()


@pytest.mark.parametrize("bad", ["", "  "])
def test_alert_refuses_an_empty_name(bad):
    with pytest.raises(ValueError):
        _alert(name=bad)


def test_alert_refuses_unknown_severity_and_empty_summary():
    with pytest.raises(ValueError):
        _alert(severity="fatal")
    with pytest.raises(ValueError):
        _alert(summary="")


def test_webhook_payload_is_a_json_ready_envelope():
    payload = webhook_payload(_alert())
    assert payload["schema"] == "accounting.alert.v1"
    assert payload["name"] == "accounting.http_5xx_rate"
    assert payload["severity"] == "critical"
    assert payload["labels"] == {"route": "/api/books/{book_id}", "firm": "f-1"}
    assert payload["occurred_at"].endswith("+00:00")
    assert json.loads(json.dumps(payload))["fingerprint"] == _alert().fingerprint()


def test_dingtalk_and_wecom_payloads_carry_the_message():
    dingtalk = dingtalk_payload(_alert())
    assert dingtalk["msgtype"] == "text"
    assert "accounting.http_5xx_rate" in dingtalk["text"]["content"]
    assert "5xx 比例超过 5%" in dingtalk["text"]["content"]
    assert dingtalk["at"] == {"isAtAll": True}  # critical 才 @所有人
    assert dingtalk_payload(_alert(severity="warning"))["at"] == {"isAtAll": False}

    wecom = wecom_payload(_alert())
    assert wecom["msgtype"] == "markdown"
    assert "**accounting.http_5xx_rate**（critical）" in wecom["markdown"]["content"]
    assert "firm=f-1" in wecom["markdown"]["content"]


def test_build_payload_rejects_unknown_channel():
    with pytest.raises(ValueError):
        build_payload("telegram", _alert())


def test_dispatcher_requires_an_injected_sender_and_bounds_config():
    with pytest.raises(ValueError):
        AlertDispatcher(None)
    with pytest.raises(ValueError):
        AlertDispatcher(Stub(), channel="sms")
    with pytest.raises(ValueError):
        AlertDispatcher(Stub(), silence_seconds=-1)
    with pytest.raises(ValueError):
        AlertDispatcher(Stub(), max_attempts=0)
    with pytest.raises(ValueError):
        AlertDispatcher(Stub(), backoff_seconds=-0.1)


def test_duplicate_alert_inside_the_silence_window_is_not_sent_again():
    now = [1000.0]
    stub = Stub()
    dispatcher = AlertDispatcher(stub, silence_seconds=600, clock=lambda: now[0])
    first = dispatcher.dispatch(_alert())
    assert first["status"] == "sent" and first["attempts"] == 1
    now[0] += 300
    again = dispatcher.dispatch(_alert())
    assert again["status"] == "silenced"
    assert again["fingerprint"] == first["fingerprint"]
    assert again["silence_remaining"] == pytest.approx(300)
    assert len(stub.calls) == 1
    # 窗口过后同一告警会重新发送。
    now[0] += 301
    later = dispatcher.dispatch(_alert())
    assert later["status"] == "sent"
    assert len(stub.calls) == 2


def test_a_different_alert_label_is_not_silenced_by_another():
    stub = Stub()
    dispatcher = AlertDispatcher(stub, silence_seconds=600, clock=lambda: 1000.0)
    assert dispatcher.dispatch(_alert())["status"] == "sent"
    other = dispatcher.dispatch(_alert(labels={"firm": "f-2"}, name="accounting.pool_exhausted"))
    assert other["status"] == "sent"
    assert len(stub.calls) == 2


def test_retry_uses_exponential_backoff_and_reports_attempts():
    stub = Stub(failures=2)
    sleeps: list[float] = []
    dispatcher = AlertDispatcher(
        stub,
        max_attempts=3,
        backoff_seconds=1.0,
        clock=lambda: 1000.0,
        sleep=sleeps.append,
    )
    result = dispatcher.dispatch(_alert())
    assert result["status"] == "sent"
    assert result["attempts"] == 3
    assert sleeps == [1.0, 2.0]
    assert len(stub.calls) == 3


def test_permanent_failure_never_raises_into_the_caller():
    stub = Stub(failures=99)
    sleeps: list[float] = []
    dispatcher = AlertDispatcher(
        stub, max_attempts=3, backoff_seconds=0.5, clock=lambda: 1000.0, sleep=sleeps.append
    )
    result = dispatcher.dispatch(_alert())
    assert result["status"] == "failed"
    assert result["attempts"] == 3
    assert "RuntimeError" in result["error"]
    assert sleeps == [0.5, 1.0]
    # 失败不写静默时间戳：恢复后第一次成功仍应真正发出。
    assert dispatcher.dispatch(_alert())["status"] == "failed"


def test_channel_choice_selects_the_payload_shape():
    stub = Stub()
    dispatcher = AlertDispatcher(stub, channel="wecom", clock=lambda: 1000.0)
    dispatcher.dispatch(_alert())
    channel, payload = stub.calls[0]
    assert channel == "wecom"
    assert payload["msgtype"] == "markdown"
