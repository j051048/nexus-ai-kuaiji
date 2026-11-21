"""Pluggable electronic-tax-bureau connector, sandbox-first.

The interface is deliberately narrow: build a reproducible export package, ask
the connector to validate it, submit it, and poll for a receipt. A real
connector (province portal / service provider) implements the same four
methods; until one is authorised, ``SandboxConnector`` is the only
implementation and it never leaves the process.

State machine (enforced here, not by the caller)::

    prepared -> submitted -> accepted | rejected | unknown
    unknown  -> accepted | rejected        (only via resolve_unknown)
    accepted / rejected are terminal

An ``unknown`` receipt is *not* a failure: the declaration may have reached the
bureau. Resubmitting while unknown would risk a duplicate filing, so it is
refused until a human resolves the receipt.
"""

import hashlib
import json
import re
import uuid
from typing import Protocol

PERIOD_RE = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")
KINDS = ("vat", "surtax", "cit_prepayment", "stamp_duty")

STATES = ("prepared", "submitted", "accepted", "rejected", "unknown")
TRANSITIONS = {
    "prepared": {"submitted"},
    "submitted": {"accepted", "rejected", "unknown"},
    "unknown": {"accepted", "rejected"},
    "accepted": set(),
    "rejected": set(),
}


class EtaxError(Exception):
    """A refusal that must be shown to the operator, never swallowed."""


class TransitionError(EtaxError):
    pass


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_package(declaration):
    """Return ``{"filename", "content", "sha256", "manifest"}``.

    The package is deterministic: the same declaration always produces the
    same bytes and digest, so an operator can prove what was submitted.
    """
    body = {
        "schema": "accounting.etax.declaration/v1",
        "declaration": declaration,
    }
    content = _canonical(body).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    kind = declaration.get("kind") if isinstance(declaration, dict) else None
    period = declaration.get("period") if isinstance(declaration, dict) else None
    manifest = {
        "schema": body["schema"],
        "kind": kind,
        "period": period,
        "sha256": digest,
        "bytes": len(content),
        "note": "未经电子税务局核定，仅作复核底稿",
    }
    filename = f"etax-{kind or 'unknown'}-{period or 'unknown'}-{digest[:12]}.json"
    return {"filename": filename, "content": content, "sha256": digest, "manifest": manifest}


def validate_declaration(declaration):
    """Reject anything the connector could not transmit faithfully."""
    if not isinstance(declaration, dict):
        raise EtaxError("申报数据必须是对象")
    kind = declaration.get("kind")
    if kind not in KINDS:
        raise EtaxError(f"未知税种：{kind!r}，可选 {', '.join(KINDS)}")
    period = declaration.get("period")
    if not isinstance(period, str) or not PERIOD_RE.match(period):
        raise EtaxError("申报期间格式必须是 YYYY-MM")
    lines = declaration.get("lines")
    if not isinstance(lines, list) or not lines:
        raise EtaxError("申报数据缺少明细行")
    for index, line in enumerate(lines):
        cents = line.get("cents") if isinstance(line, dict) else None
        if type(cents) is not int:
            raise EtaxError(f"第 {index + 1} 行金额必须是整数分（不接受浮点）")
    return True


class SandboxConnector:
    """Deterministic in-process connector used by tests and local demos."""

    name = "sandbox"

    def __init__(self):
        self._receipts = {}

    def submit(self, declaration, *, state="prepared"):
        validate_declaration(declaration)
        if state != "prepared":
            raise TransitionError(f"当前状态 {state} 不能提交申报")
        receipt_id = uuid.uuid5(uuid.NAMESPACE_URL, _canonical(declaration) + self.name).hex[:32]
        outcome = declaration.get("sandbox_outcome", "accepted")
        if outcome not in {"accepted", "rejected", "unknown"}:
            raise EtaxError("沙箱结果只能是 accepted / rejected / unknown")
        receipt = {
            "receipt_id": receipt_id,
            "state": "submitted",
            "outcome": outcome,
            "message": {
                "accepted": "沙箱受理",
                "rejected": "沙箱拒绝：需退回修改",
                "unknown": "沙箱结果未知：先查回回执，禁止重报",
            }[outcome],
        }
        self._receipts[receipt_id] = receipt
        return receipt

    def poll(self, receipt_id):
        receipt = self._receipts.get(receipt_id)
        if receipt is None:
            raise EtaxError("回执不存在；请先提交或核对回执号")
        if receipt["state"] == "submitted":
            receipt = {**receipt, "state": receipt["outcome"]}
            self._receipts[receipt_id] = receipt
        return receipt

    def resolve_unknown(self, receipt_id, *, outcome):
        receipt = self._receipts.get(receipt_id)
        if receipt is None:
            raise EtaxError("回执不存在")
        if receipt["state"] != "unknown":
            raise TransitionError(f"回执当前状态 {receipt['state']} 不是 unknown")
        if outcome not in {"accepted", "rejected"}:
            raise EtaxError("unknown 只能被人工核定为 accepted 或 rejected")
        resolved = {**receipt, "state": outcome}
        self._receipts[receipt_id] = resolved
        return resolved


def assert_can_submit(current_state):
    """Guard used by the service layer before calling any connector."""
    if current_state == "unknown":
        raise EtaxError("上次回执结果未知，必须先查回回执终态，禁止自动重报")
    allowed = TRANSITIONS.get(current_state)
    if allowed is None:
        raise TransitionError(f"未知申报状态 {current_state}")
    if "submitted" not in allowed:
        raise TransitionError(f"当前状态 {current_state} 不能再次提交")
    return True


class Connector(Protocol):
    name: str

    def submit(self, declaration, *, state): ...

    def poll(self, receipt_id): ...

    def resolve_unknown(self, receipt_id, *, outcome): ...
