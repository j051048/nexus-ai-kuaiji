"""Bounded, offline investigation contract. No provider, database or automatic resume.

V2 separates call usage from evidence and requires a trusted binding on restoration.
V1 snapshots are deliberately not upgraded implicitly.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable

INVESTIGATION_VERSION = "investigation-v2"
MAX_TOKENS = 100_000_000
MAX_CALLS = 10_000
MAX_EVIDENCE_ITEMS = 256
MAX_VALUE_DEPTH = 32
MAX_VALUE_NODES = 10_000
MAX_VALUE_BYTES = 262_144
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_DEPTH = MAX_VALUE_DEPTH + 8
MAX_SNAPSHOT_NODES = 1_000_000


class InvestigationError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _integer(value: Any, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _text(value: Any) -> bool:
    return type(value) is str and bool(value.strip()) and len(value) <= 512


def _finite(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


@dataclass(frozen=True)
class Budget:
    steps: int = 3
    calls: int = 2
    seconds: float = 10
    tokens: int = 1000

    def __post_init__(self) -> None:
        if (not _integer(self.steps, MAX_CALLS) or not _integer(self.calls, MAX_CALLS)
                or not _integer(self.tokens, MAX_TOKENS)
                or not _finite(self.seconds) or self.seconds > 86_400):
            raise InvestigationError("investigation_budget_invalid", "预算类型或范围无效")


@dataclass(frozen=True)
class Binding:
    """Trusted caller identity plus frozen input/config fingerprints, never model output."""
    run_id: str
    company_id: str
    book_id: str
    fact_version: int
    input_sha256: str
    config_sha256: str
    prompt_sha256: str
    policy_sha256: str

    def __post_init__(self) -> None:
        if (not all(_text(v) for v in (self.run_id, self.company_id, self.book_id))
                or not _integer(self.fact_version, 2**63 - 1)):
            raise InvestigationError("investigation_scope_invalid", "可信运行范围无效")
        for value in (self.input_sha256, self.config_sha256, self.prompt_sha256, self.policy_sha256):
            if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise InvestigationError("investigation_binding_invalid", "输入、配置、提示词及政策必须有 SHA-256 指纹")


@dataclass(frozen=True)
class Result:
    kind: str
    value: Any = None
    evidence_id: str = ""
    company_id: str = ""
    book_id: str = ""
    version: int = 0
    instruction: str = ""


@dataclass(frozen=True)
class CallUsage:
    """Once per call. Unknown/invalid may retain a validated partial known total."""
    known_tokens: int = 0
    state: str = "unknown"  # known / unknown / invalid


@dataclass(frozen=True)
class CallOutcome:
    results: tuple[Result, ...] = ()
    usage: CallUsage = field(default_factory=CallUsage)
    error: str = ""


@dataclass(frozen=True)
class RunView:
    binding: Binding
    budget: Budget
    calls: int
    tokens_used: int
    steps: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]

    @property
    def run_id(self) -> str:
        return self.binding.run_id

    @property
    def company_id(self) -> str:
        return self.binding.company_id

    @property
    def book_id(self) -> str:
        return self.binding.book_id

    @property
    def fact_version(self) -> int:
        return self.binding.fact_version


@dataclass
class Investigation:
    binding: Binding
    budget: Budget
    status: str = "running"
    stop_reason: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0
    handoff: dict[str, Any] | None = None
    tokens_used: int = 0
    elapsed_seconds: float = 0
    stop_facts: list[str] = field(default_factory=list)
    resume_requested: bool = False
    version: str = INVESTIGATION_VERSION

    @property
    def run_id(self) -> str:
        return self.binding.run_id

    @property
    def company_id(self) -> str:
        return self.binding.company_id

    @property
    def book_id(self) -> str:
        return self.binding.book_id

    @property
    def fact_version(self) -> int:
        return self.binding.fact_version


def _authorize(check: Callable[[], bool]) -> None:
    try:
        allowed = check() is True
    except Exception:
        allowed = False
    if not allowed:
        raise InvestigationError("investigation_revoked", "当前身份不能访问调查证据")


def _bound(actual: Binding, expected: Binding) -> None:
    if not isinstance(expected, Binding):
        raise InvestigationError("investigation_binding_invalid", "必须提供可信的恢复范围")
    if actual.run_id != expected.run_id:
        raise InvestigationError("investigation_run_mismatch", "调查记录不属于当前运行")
    if (actual.company_id, actual.book_id) != (expected.company_id, expected.book_id):
        raise InvestigationError("investigation_scope_mismatch", "调查记录不属于当前企业或账套")
    if actual.fact_version != expected.fact_version:
        raise InvestigationError("investigation_version_stale", "调查事实版本已经变化")
    if actual != expected:
        raise InvestigationError("investigation_binding_mismatch", "输入或运行配置已经变化")


def _json_copy(value: Any) -> Any:
    pending = [(value, 0)]
    nodes, size = 0, 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        size += 8  # Bound structure overhead as well as string bytes.
        if depth > MAX_VALUE_DEPTH or nodes > MAX_VALUE_NODES:
            raise InvestigationError("investigation_evidence_limit", "证据深度或节点数超过离线运行上限")
        if type(item) is str:
            if len(item) > MAX_VALUE_BYTES:
                raise InvestigationError("investigation_evidence_limit", "证据文本超过离线运行上限")
            size += len(item.encode("utf-8"))
        elif item is None or type(item) is bool:
            pass
        elif type(item) is int:
            if abs(item) > 2**63 - 1:
                raise InvestigationError("investigation_evidence_limit", "证据整数超出支持范围")
            size += 20
        elif type(item) is float and math.isfinite(item):
            size += 24
        elif type(item) is list:
            if len(item) > MAX_VALUE_NODES:
                raise InvestigationError("investigation_evidence_limit", "证据列表过大")
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is dict and all(type(key) is str for key in item):
            if len(item) > MAX_VALUE_NODES:
                raise InvestigationError("investigation_evidence_limit", "证据对象过大")
            pending.extend((child, depth + 1) for child in item.values())
            pending.extend((key, depth + 1) for key in item)
        else:
            raise InvestigationError("investigation_evidence_invalid", "证据必须是有限 JSON 数据")
        if size > MAX_VALUE_BYTES:
            raise InvestigationError("investigation_evidence_limit", "证据大小超过离线运行上限")
    try:
        return deepcopy(value)
    except (RecursionError, ValueError, TypeError):
        raise InvestigationError("investigation_evidence_invalid", "证据无法安全复制") from None


def _handoff(run: Investigation, **extra: Any) -> dict[str, Any]:
    return {
        "kind": "offline_accountant_handoff", "conflict": None,
        "suggested_role": "accountant", "stop_reason": run.stop_reason,
        "resolved": False, "evidence": [item["evidence_id"] for item in run.evidence],
        **extra,
    }


def _stop(run: Investigation, reason: str, facts: list[str] | None = None) -> None:
    run.status = "stopped"
    run.stop_reason = reason
    run.stop_facts = list(dict.fromkeys([reason, *(facts or [])]))
    if reason == "revoked":
        # No old evidence is returned after in-run revocation.
        run.evidence = []
        run.handoff = None
    else:
        run.handoff = _handoff(run)


def _usage(outcome: CallOutcome) -> tuple[int, str]:
    usage = outcome.usage
    if not isinstance(usage, CallUsage):
        return 0, "invalid"
    tokens = usage.known_tokens if _integer(usage.known_tokens, MAX_TOKENS) else 0
    if (not _integer(usage.known_tokens, MAX_TOKENS)
            or type(usage.state) is not str or usage.state not in {"known", "unknown", "invalid"}):
        return tokens, "invalid"
    return tokens, usage.state


def investigate(
    *, binding: Binding, budget: Budget, resolver: Callable[[RunView], CallOutcome],
    revoked: bool = False, conflict: tuple[str, str] | None = None,
    clock: Callable[[], float] = time.monotonic,
    authorization_check: Callable[[], bool] | None = None,
    fact_version_check: Callable[[], int] | None = None,
) -> Investigation:
    if not isinstance(binding, Binding) or not isinstance(budget, Budget):
        raise InvestigationError("investigation_binding_invalid", "必须提供已验证的运行契约")
    run = Investigation(binding, budget)
    try:
        started = clock()
    except Exception:
        raise InvestigationError("investigation_clock_invalid", "无法读取单调时钟") from None
    if not _finite(started):
        raise InvestigationError("investigation_clock_invalid", "时钟必须返回有限非负数")

    def refresh() -> bool:
        try:
            now = clock()
        except Exception:
            return False
        if not _finite(now) or now - started < run.elapsed_seconds:
            return False
        run.elapsed_seconds = now - started
        return True

    def auth_reason() -> str:
        try:
            if authorization_check is not None and authorization_check() is not True:
                return "revoked"
            if fact_version_check is not None:
                version = fact_version_check()
                if type(version) is not int or version != binding.fact_version:
                    return "fact_version_changed"
        except Exception:
            return "revoked"
        return ""

    if revoked:
        _stop(run, "revoked")
        return run
    if min(budget.steps, budget.calls, budget.seconds, budget.tokens) == 0:
        _stop(run, "budget_exhausted")
        return run
    while True:
        reason = auth_reason()
        if reason:
            _stop(run, reason)
            break
        if not refresh():
            _stop(run, "clock_invalid")
            break
        checks = ((run.elapsed_seconds >= budget.seconds, "time_budget"),
                  (run.tokens_used >= budget.tokens, "token_budget"),
                  (len(run.steps) >= budget.steps, "step_budget"),
                  (run.calls >= budget.calls, "call_budget"))
        reason = next((reason for exhausted, reason in checks if exhausted), "")
        if reason:
            _stop(run, reason)
            break
        run.calls += 1
        step = {"step": run.calls, "attempt_id": f"{binding.run_id}:{run.calls}",
                "tool": "resolver", "known_tokens": 0, "usage_state": "unknown",
                "elapsed_seconds": run.elapsed_seconds, "error": "", "evidence_ids": []}
        run.steps.append(step)
        try:
            # Deep isolation also protects nested evidence and prior call records.
            outcome = resolver(RunView(binding, budget, run.calls, run.tokens_used,
                                       tuple(deepcopy(run.steps)), tuple(deepcopy(run.evidence))))
        except InvestigationError as error:
            outcome = CallOutcome(error=error.code if _text(error.code) else "resolver_failed")
        except Exception:
            outcome = CallOutcome(error="resolver_failed")
        if not isinstance(outcome, CallOutcome):
            # Legacy bare result lists have no trustworthy once-per-call usage.
            outcome = CallOutcome()
        known, state = _usage(outcome)
        run.tokens_used += known
        step.update(known_tokens=known, usage_state=state)
        clock_valid = refresh()
        step["elapsed_seconds"] = run.elapsed_seconds
        facts = []
        auth = auth_reason()
        if auth:
            facts.append(auth)
        if type(outcome.error) is not str or (outcome.error and not _text(outcome.error)):
            facts.append("resolver_contract_invalid")
        elif outcome.error:
            facts.append(outcome.error)
        if state != "known":
            facts.append("usage_invalid" if state == "invalid" else "usage_unknown")
        if not clock_valid:
            facts.append("clock_invalid")
        if run.elapsed_seconds >= budget.seconds:
            facts.append("time_budget_late")
        if run.tokens_used > budget.tokens:
            facts.append("token_budget_overrun")
        if facts:
            step["error"] = facts[0]
            _stop(run, facts[0], facts)
            break
        rejected = ""
        new = []
        try:
            if not isinstance(outcome.results, (list, tuple)):
                raise InvestigationError("investigation_evidence_invalid", "结果必须是证据序列")
            if len(outcome.results) > MAX_EVIDENCE_ITEMS:
                raise InvestigationError("investigation_evidence_limit", "单次证据数量超过上限")
            accepted: dict[str, dict[str, Any]] = {}
            existing = {item["evidence_id"]: item for item in run.evidence}
            for result in outcome.results:
                if not isinstance(result, Result) or type(result.instruction) is not str:
                    raise InvestigationError("investigation_evidence_invalid", "证据结构无效")
                if result.instruction:
                    continue  # Untrusted instructions are data, never executable.
                if result.company_id != run.company_id or result.book_id != run.book_id:
                    raise InvestigationError("investigation_scope_mismatch", "工具返回其他企业或账套资料")
                if type(result.version) is not int or result.version != run.fact_version:
                    raise InvestigationError("investigation_version_stale", "工具返回旧事实版本")
                if not _text(result.kind) or not _text(result.evidence_id):
                    raise InvestigationError("investigation_evidence_invalid", "证据缺少身份或类型")
                item = {"evidence_id": result.evidence_id, "kind": result.kind,
                        "value": _json_copy(result.value), "version": result.version}
                prior = accepted.get(result.evidence_id, existing.get(result.evidence_id))
                if prior is not None and prior != item:
                    raise InvestigationError("investigation_evidence_conflict", "同 ID 证据内容不一致")
                accepted[result.evidence_id] = item
            new = [item for identity, item in accepted.items() if identity not in existing]
            if len(new) + len(existing) > MAX_EVIDENCE_ITEMS:
                raise InvestigationError("investigation_evidence_limit", "运行证据总量超过上限")
        except InvestigationError as error:
            rejected = error.code
        # Parsing, validation and defensive copying consume the same deadline.
        # Check current authority again before committing any prepared evidence.
        clock_valid = refresh()
        step["elapsed_seconds"] = run.elapsed_seconds
        processing_facts = []
        auth = auth_reason()
        if auth:
            processing_facts.append(auth)
        if rejected:
            processing_facts.append(rejected)
        if not clock_valid:
            processing_facts.append("clock_invalid")
        if run.elapsed_seconds >= budget.seconds:
            processing_facts.append("time_budget_late")
        if processing_facts:
            step["error"] = processing_facts[0]
            _stop(run, processing_facts[0], processing_facts)
            break
        run.evidence.extend(new)
        step["evidence_ids"] = [item["evidence_id"] for item in new]
        if conflict is not None and len(run.evidence) >= 2:
            try:
                if not isinstance(conflict, tuple) or len(conflict) != 2:
                    raise ValueError
                left, right = (Decimal(str(value)) for value in conflict)
                if not left.is_finite() or not right.is_finite():
                    raise InvalidOperation
                difference = abs(left - right)
                details = {"left": format(left, "f"), "right": format(right, "f"), "difference": format(difference, "f")}
                run.stop_reason = "conflicting_evidence"
            except (InvalidOperation, ValueError):
                details = None
                run.stop_reason = "conflict_invalid"
            run.status = "handoff"
            run.stop_facts = [run.stop_reason]
            run.handoff = _handoff(run, conflict=details)
            if details is None:
                run.handoff["message"] = "冲突金额不是有效有限十进制数，请人工核对"
            break
        if not new:
            _stop(run, "no_new_evidence")
            break
        if any(item["kind"] == "resolved" for item in new):
            run.status = "resolved"
            run.stop_reason = "evidence_resolved"
            run.stop_facts = [run.stop_reason]
            break
    return run


def _snapshot_chunks(payload: dict[str, Any]):
    """Bound both canonical hashing and persisted JSON before recursive encoding."""
    pending, nodes = [(payload, 0)], 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > MAX_SNAPSHOT_DEPTH or nodes > MAX_SNAPSHOT_NODES:
            raise InvestigationError("investigation_snapshot_invalid", "快照结构深度或节点数超出上限")
        if type(value) is dict:
            pending.extend((child, depth + 1) for child in value.values())
        elif type(value) in (list, tuple):
            pending.extend((child, depth + 1) for child in value)
    size = 0
    try:
        encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        for text in encoder.iterencode(payload):
            chunk = text.encode("utf-8")
            size += len(chunk)
            if size > MAX_SNAPSHOT_BYTES:
                raise InvestigationError("investigation_snapshot_too_large", "快照超出读写共用大小上限")
            yield chunk
    except InvestigationError:
        raise
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise InvestigationError("investigation_snapshot_invalid", "快照无法安全序列化") from None


def _digest(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "snapshot_hash"}
    digest = sha256()
    for chunk in _snapshot_chunks(body):
        digest.update(chunk)
    return digest.hexdigest()


def _validate(run: Investigation) -> None:
    if run.version != INVESTIGATION_VERSION:
        raise InvestigationError("investigation_version_unknown", "未知调查快照版本")
    if run.status not in {"running", "stopped", "handoff", "resolved"}:
        raise InvestigationError("investigation_status_invalid", "调查状态无效")
    if (not _integer(run.calls, run.budget.calls) or run.calls > run.budget.steps
            or not _integer(run.tokens_used, MAX_TOKENS * MAX_CALLS)
            or not _finite(run.elapsed_seconds) or type(run.resume_requested) is not bool
            or type(run.steps) is not list or len(run.steps) != run.calls):
        raise InvestigationError("investigation_counter_invalid", "调用账本与计数不一致")
    if type(run.stop_facts) is not list or any(not _text(item) for item in run.stop_facts):
        raise InvestigationError("investigation_status_invalid", "停止事实无效")
    if len(run.stop_facts) != len(set(run.stop_facts)):
        raise InvestigationError("investigation_status_invalid", "停止事实重复")
    usage_total, elapsed, accepted_ids = 0, 0, []
    for index, step in enumerate(run.steps, 1):
        # Reconstruct admission at each call, not just the final totals. A
        # legitimate last call can exceed a token estimate and then stop, but
        # no later call may start after an exhausted token/time budget.
        if (min(run.budget.steps, run.budget.calls, run.budget.seconds, run.budget.tokens) == 0
                or usage_total >= run.budget.tokens or elapsed >= run.budget.seconds):
            raise InvestigationError("investigation_budget_invalid", "调用账本包含预算耗尽后的调用")
        if (type(step) is not dict or set(step) != {"step", "attempt_id", "tool", "known_tokens", "usage_state", "elapsed_seconds", "error", "evidence_ids"}
                or type(step["step"]) is not int or step["step"] != index
                or step["attempt_id"] != f"{run.run_id}:{index}" or step["tool"] != "resolver"
                or not _integer(step["known_tokens"], MAX_TOKENS)
                or step["usage_state"] not in {"known", "unknown", "invalid"}
                or not _finite(step["elapsed_seconds"]) or not elapsed <= step["elapsed_seconds"] <= run.elapsed_seconds
                or type(step["error"]) is not str
                or type(step["evidence_ids"]) is not list or any(not _text(identity) for identity in step["evidence_ids"])):
            raise InvestigationError("investigation_counter_invalid", "调用记录格式或顺序无效")
        usage_total += step["known_tokens"]
        elapsed = step["elapsed_seconds"]
        if (step["usage_state"] != "known" or step["error"]):
            if index != run.calls or step["evidence_ids"] or not step["error"]:
                raise InvestigationError("investigation_counter_invalid", "失败或未知调用之后禁止追加调用和证据")
            usage_fact = {"unknown": "usage_unknown", "invalid": "usage_invalid"}.get(step["usage_state"])
            if (step["error"] != run.stop_reason or step["error"] not in run.stop_facts
                    or (usage_fact is not None and usage_fact not in run.stop_facts)):
                raise InvestigationError("investigation_status_invalid", "原始调用失败或未知用量事实不得丢失")
        accepted_ids.extend(step["evidence_ids"])
    if usage_total != run.tokens_used or len(accepted_ids) != len(set(accepted_ids)):
        raise InvestigationError("investigation_counter_invalid", "用量累计或证据接纳记录不一致")
    if type(run.evidence) is not list or len(run.evidence) > MAX_EVIDENCE_ITEMS:
        raise InvestigationError("investigation_evidence_invalid", "证据列表无效")
    identities = []
    for item in run.evidence:
        if (type(item) is not dict or set(item) != {"evidence_id", "kind", "value", "version"}
                or not _text(item["evidence_id"]) or not _text(item["kind"])
                or type(item["version"]) is not int or item["version"] != run.fact_version):
            raise InvestigationError("investigation_evidence_invalid", "证据身份或版本无效")
        _json_copy(item["value"])
        identities.append(item["evidence_id"])
    if len(identities) != len(set(identities)):
        raise InvestigationError("investigation_evidence_conflict", "证据身份重复")
    if run.stop_reason == "revoked":
        if identities:
            raise InvestigationError("investigation_evidence_invalid", "撤权后不得投影原始证据")
    elif identities != accepted_ids:
        raise InvestigationError("investigation_evidence_invalid", "证据与调用接纳记录不一致")
    if run.status == "running":
        if run.stop_reason or run.handoff is not None or run.stop_facts or run.calls or run.resume_requested:
            raise InvestigationError("investigation_status_invalid", "在途快照不支持自动恢复")
    else:
        if not _text(run.stop_reason) or run.stop_reason not in run.stop_facts:
            raise InvestigationError("investigation_status_invalid", "终态缺少原始停止原因")
    if run.status == "resolved":
        if (not run.calls or not any(item["kind"] == "resolved" for item in run.evidence)
                or run.handoff is not None or run.stop_reason != "evidence_resolved"
                or any(step["error"] or step["usage_state"] != "known" for step in run.steps)):
            raise InvestigationError("investigation_status_invalid", "resolved 缺少有效调用及解决证据")
    if run.handoff is not None:
        if (type(run.handoff) is not dict or run.handoff.get("kind") != "offline_accountant_handoff"
                or run.handoff.get("stop_reason") != run.stop_reason
                or run.handoff.get("resolved") is not False or run.handoff.get("suggested_role") != "accountant"
                or run.handoff.get("evidence") != identities):
            raise InvestigationError("investigation_handoff_invalid", "人工接手信息不一致")
        _json_copy(run.handoff)
    if run.status in {"stopped", "handoff"} and run.handoff is None and run.stop_reason != "revoked":
        raise InvestigationError("investigation_handoff_invalid", "缺少人工接手信息")
    if run.tokens_used > run.budget.tokens:
        if run.status in {"resolved", "running"} or "token_budget_overrun" not in run.stop_facts:
            raise InvestigationError("investigation_budget_invalid", "超额用量缺少停止事实")
    if run.elapsed_seconds >= run.budget.seconds and run.status == "resolved":
        raise InvestigationError("investigation_budget_invalid", "超时结果不能解决问题")


def save(investigation: Investigation, path: str | Path) -> None:
    _validate(investigation)
    payload = asdict(investigation)
    payload["snapshot_hash"] = _digest(payload)
    encoded = b"".join(_snapshot_chunks(payload))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load(path: str | Path, *, expected: Binding, expected_budget: Budget,
         authorization_check: Callable[[], bool]) -> Investigation:
    _authorize(authorization_check)
    if not isinstance(expected, Binding):
        raise InvestigationError("investigation_binding_invalid", "必须提供可信恢复范围")
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise InvestigationError("investigation_snapshot_too_large", "快照超出读写共用大小上限")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    except InvestigationError:
        raise
    except (OSError, ValueError, TypeError, RecursionError, UnicodeError):
        raise InvestigationError("investigation_snapshot_invalid", "调查快照不可读或已损坏") from None
    if not isinstance(payload, dict) or payload.get("version") != INVESTIGATION_VERSION:
        raise InvestigationError("investigation_version_unknown", "旧版或未知调查快照不能隐式升级")
    if payload.pop("snapshot_hash", None) != _digest(payload):
        raise InvestigationError("investigation_snapshot_integrity", "调查快照完整性校验失败")
    try:
        binding = Binding(**payload.pop("binding"))
        _bound(binding, expected)
        budget = Budget(**payload.pop("budget"))
        if not isinstance(expected_budget, Budget) or budget != expected_budget:
            raise InvestigationError("investigation_budget_mismatch", "快照预算与可信调用预算不一致")
        run = Investigation(binding=binding, budget=budget, **payload)
        _validate(run)
    except InvestigationError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError, UnicodeError):
        raise InvestigationError("investigation_snapshot_structure", "调查快照结构无效") from None
    _authorize(authorization_check)
    return run


def resume_as_handoff(investigation: Investigation, *, expected: Binding, expected_budget: Budget,
                      authorization_check: Callable[[], bool]) -> Investigation:
    """An authorized projection, never a resolver call or an automatic continuation."""
    _authorize(authorization_check)
    _bound(investigation.binding, expected)
    if not isinstance(expected_budget, Budget) or investigation.budget != expected_budget:
        raise InvestigationError("investigation_budget_mismatch", "不能在恢复时改变原运行预算")
    _validate(investigation)
    run = deepcopy(investigation)
    if run.status != "resolved":
        run.status = "handoff"
        run.resume_requested = True
        if not run.stop_reason:
            run.stop_reason = "manual_resume_required"
            run.stop_facts = [run.stop_reason]
        run.handoff = _handoff(run, resume_requires_review=True)
    _validate(run)
    _authorize(authorization_check)
    return run
