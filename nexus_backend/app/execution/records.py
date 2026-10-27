"""AK04 read model. It neither settles costs nor decides queue retries.

Callers authorize the current source in their transaction. No client principal,
provider prose, prompts, credentials or inferred stage timestamps enter this view.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.accounting.access import AccessControl


class ExecutionRecordError(ValueError):
    pass


FACT_CODES = {
    "ak04_fact_verified": "verified",
    "ak04_fact_missing": "missing_facts",
    "ak04_fact_conflict": "conflicting_evidence",
    "ak04_fact_invalid": "invalid",
    "ak04_fact_unsupported": "unsupported_case",
    "ak04_fact_accountant": "requires_accountant",
}
CATEGORIES = {
    "authorization_or_scope": {
        "scope_changed",
        "authorization_changed",
        "permission_denied",
        "fact_changed",
        "stale_input",
        "ocr_source_invalid",
    },
    "policy_or_budget": {
        "policy_rejected",
        "quota_exhausted",
        "quota_overrun",
        "pricing_missing",
        "policy_invalid",
        "model_unavailable",
        "unavailable",
        "configuration",
        "policy_changed",
        "company_disabled",
        "redirect",
    },
    "input_or_context": {
        "context_invalid",
        "context_too_large",
        "context_budget_exceeded",
        "document_invalid",
        "document_too_large",
        "unsupported_document",
        "input_too_large",
        "structured_size",
        "structured_invalid",
        "structured_ambiguous",
        "structured_unsupported",
        "structured_red_invoice",
        "ocr_version_changed",
        "ocr_document_too_large",
    },
    "provider_or_transport": {
        "timeout",
        "network",
        "network_error",
        "transport",
        "response_unknown",
        "result_uncertain",
        "busy",
        "http_error",
        "rate_limit",
        "rate_limited",
        "provider_refused",
        "model_refused",
        "refusal",
        "ocr_service_unavailable",
        "ocr_timeout",
    },
    "output_validation": {
        "invalid_json",
        "invalid_response",
        "invalid_output",
        "invalid_proposal",
        "schema_invalid",
        "citation_invalid",
        "empty_response",
        "truncated",
        "output_truncated",
        "response_too_large",
        "ocr_evidence_invalid",
        "ocr_response_invalid",
    },
    "cancelled": {"cancelled", "user_cancelled"},
    "worker_internal": {
        "worker_failure",
        "worker_failed",
        "worker_restarted",
        "lease_expired",
        "queue_interrupted",
        "expired",
        "processing_failed",
    },
}
MESSAGES = {
    "authorization_or_scope": "权限或资料范围已变化，请刷新后核对。",
    "policy_or_budget": "配置、外发政策或额度阻止继续处理，请管理员核对。",
    "input_or_context": "资料格式或处理范围需要调整，请人工核对。",
    "provider_or_transport": "模型服务未完成正常处理，请核对执行与费用状态。",
    "output_validation": "模型结果未通过程序校验，请人工核对原件和业务事项。",
    "cancelled": "处理已取消，已发生的调用仍按实际记录核对。",
    "worker_internal": "后台处理未完成，请管理员核对任务状态。",
    "facts_require_human": "当前资料需要人工核对，尚未完成会计复核。",
}
UNKNOWN_CODES = {"response_unknown", "result_uncertain"}
TERMINAL = {"succeeded", "failed", "stale", "cancelled", "interrupted", "needs_attention"}
KINDS = {"chat", "ocr", "journal", "intake"}


def can_diagnose(conn, actor):
    """Match existing operations permissions using live membership, not a flag."""
    return AccessControl().firm_role(conn, actor) in {"owner", "admin"}


def _dict(value):
    if value is None:
        return {}
    return dict(value)


def _code(value):
    # Only application reason identifiers; arbitrary provider text is not a code.
    if not isinstance(value, str) or not 1 <= len(value) <= 100:
        return None
    if any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-." for c in value
    ):
        return None
    return value


def _validation(run, supplied, findings):
    machine = FACT_CODES.get(run.get("error_code"), "not_checked")
    value = dict(supplied) if isinstance(supplied, Mapping) else {}
    code = value.get("code", machine)
    if code not in {
        "verified",
        "missing_facts",
        "conflicting_evidence",
        "invalid",
        "unsupported_case",
        "requires_accountant",
        "not_checked",
    }:
        code = "not_checked"
    unresolved = [dict(f) for f in findings if not dict(f).get("resolved", False)]
    codes = sorted(
        set(
            c
            for c in [*value.get("finding_codes", []), *(f.get("code") for f in unresolved)]
            if _code(c)
        )
    )[:100]
    blocking = any(f.get("severity") == "blocking" for f in unresolved)
    status = value.get("status")
    if status == "failed":
        status, code = "blocked", "invalid"
    if blocking:
        status = "blocked"
        if code in {"verified", "not_checked"}:
            code = "requires_accountant"
    elif codes and status not in {"blocked", "requires_review"}:
        status, code = "requires_review", "requires_accountant"
    if status not in {"checked", "blocked", "requires_review", "not_checked"}:
        status = (
            "checked"
            if code == "verified"
            else "requires_review"
            if code != "not_checked"
            else "not_checked"
        )
    return {"code": code, "status": status, "finding_codes": codes}


def _bound_rows(kind, run, costs, attempts):
    for cost in costs:
        expected = (
            "chat_turn_id" if kind == "chat" else "intake_run_id" if kind == "intake" else "run_id"
        )
        if (cost.get("firm_id"), cost.get("company_id"), cost.get("id"), cost.get(expected)) != (
            run.get("firm_id"),
            run.get("company_id"),
            run["id"],
            run["id"],
        ):
            raise ExecutionRecordError("cost_scope_mismatch")
    for attempt in attempts:
        if (
            attempt.get("firm_id") != run.get("firm_id")
            or attempt.get("company_id") != run.get("company_id")
            or attempt.get("ai_run_id") != run["id"]
        ):
            raise ExecutionRecordError("attempt_scope_mismatch")


def execution_record(
    kind,
    run,
    *,
    costs=(),
    attempts=(),
    findings=(),
    diagnostics=False,
    validation=None,
    context=None,
):
    """Stable projection; source status is not an approval or posting status."""
    kind = "journal" if kind == "proposal" else kind
    run = _dict(run)
    if kind not in KINDS or not isinstance(run.get("id"), str) or not run["id"]:
        raise ExecutionRecordError("execution_identity_invalid")
    costs, attempts = [dict(c) for c in costs], [dict(a) for a in attempts]
    _bound_rows(kind, run, costs, attempts)
    validation = _validation(run, validation, findings)
    raw_code = run.get("error_code") or ""
    category = next((name for name, values in CATEGORIES.items() if raw_code in values), None)
    if raw_code and raw_code not in FACT_CODES and category is None:
        category = "worker_internal"
    status = run.get("status", "unknown")
    sent = bool(
        run.get("sent_at")
        or any(c.get("sent_at") for c in costs)
        or any(a.get("phase") == "sending" for a in attempts)
    )
    # Legacy rows do not always retain transport proof. Lack of a visible cost
    # row is neither proof of no send nor proof of a free request.
    unknown = (
        status == "interrupted"
        or raw_code in UNKNOWN_CODES
        or raw_code in {"timeout", "network", "network_error", "transport"}
        or (sent and raw_code == "queue_interrupted")
    )
    if (
        status in {"failed", "stale", "cancelled", "interrupted", "needs_attention"}
        and not category
    ):
        category = "cancelled" if status == "cancelled" else "worker_internal"
    human = (
        validation["status"] in {"blocked", "requires_review"}
        or category is not None
        or status in {"failed", "stale", "interrupted", "needs_attention"}
    )
    failure = None
    if category:
        safe_code = raw_code if raw_code in CATEGORIES.get(category, ()) else "worker_failure"
        failure = {
            "category": category,
            "code": safe_code,
            "message": MESSAGES[category],
            "outcome_unknown": unknown,
            "automatic_retry": False if unknown else None,
        }
    reason = failure["code"] if failure else validation["code"]
    action = (
        "inspect_execution"
        if unknown
        else "refresh_scope"
        if category == "authorization_or_scope"
        else "contact_admin"
        if category in {"policy_or_budget", "worker_internal"}
        else "review_draft"
        if kind == "journal"
        else "review_original"
        if kind in {"ocr", "intake"}
        else "review_facts"
    )
    timeline = []
    if run.get("created_at"):
        timeline.append({"stage": "created", "at": run["created_at"], "source": "execution"})
    if run.get("sent_at"):
        timeline.append({"stage": "send_marked", "at": run["sent_at"], "source": "execution"})
    if run.get("finished_at"):
        timeline.append({"stage": "finished", "at": run["finished_at"], "source": "execution"})
    output = {
        "schema": "ak04-execution-v1",
        "execution_id": f"{kind}:{run['id']}",
        "kind": kind,
        "status": status,
        "accounting_approved": False,
        "validation": validation,
        "failure": failure,
        "handoff": {
            "required": human,
            "reason_code": reason,
            "action": action,
            "automatic_retry": False if human else None,
        },
        "timeline": timeline,
    }
    if diagnostics is True:
        basis = _dict(context)
        output["diagnostics"] = {
            "scope": {
                key: basis.get(key, run.get(key))
                for key in ("firm_id", "company_id", "event_id", "book_id", "draft_id", "intake_id")
            },
            "input_fingerprint": run.get("input_fingerprint"),
            "model_fingerprint": run.get("model_fingerprint"),
            "context_pack_hash": None,
            "send_marked": sent,
            "cache_hit": run.get("cache_hit") in (True, 1),
            "deterministic_parse": run.get("provider") == "local-structured-parser",
            "cost_evidence": "recorded" if costs else "not_available",
            "attempt_count": run.get("attempt_count"),
            "attempts": [
                {
                    key: a.get(key)
                    for key in (
                        "id",
                        "job_id",
                        "attempt_no",
                        "phase",
                        "status",
                        "ai_run_id",
                        "created_at",
                        "finished_at",
                    )
                }
                for a in attempts
            ],
            "costs": [
                {
                    key: c.get(key)
                    for key in (
                        "id",
                        "state",
                        "input_tokens",
                        "output_tokens",
                        "reserved_micro",
                        "estimated_micro",
                        "confirmed_micro",
                        "created_at",
                        "sent_at",
                        "finished_at",
                    )
                }
                for c in costs
            ],
        }
    return output
