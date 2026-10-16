"""Scoped admin AI controls use synthetic providers and preserve budget semantics."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.accounting.admin_models import (
    AdminAIControlUpdate,
    AdminAIPolicyUpdate,
    AdminCompanyStatus,
    AdminCompanyUpdate,
)
from app.accounting.admin_router import build_admin_router
from app.accounting.admin_service import AdminService
from app.accounting.directory import DirectoryService
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import CompanyMemberGrant
from app.intelligence.provider import AISettings
from test_intelligence_workflow import ai_case, create_event, extract  # noqa: F401
from test_model_cost_workflow import cost_case  # noqa: F401


def admin(c):
    return AdminService(c.db)


def read(c, actor=None):
    return admin(c).ai_control(
        actor or c.manager,
        c.company["id"],
        c.intelligence,
        c.operations,
        SimpleNamespace(available=True),
    )


def change(c, value, actor=None):
    current = read(c, actor)
    return AdminAIControlUpdate(
        enabled=value,
        reason="值班管理员根据企业实际情况调整",
        expected_enabled=current["ai"]["enabled"],
        expected_updated_at=current["ai"]["updated_at"],
        expected_company_version=current["company"]["version"],
    )


def policy_request(**changes):
    return AdminAIPolicyUpdate(
        **{
            "expected_version": 0,
            "quota_enabled": True,
            "monthly_limit": "10.00",
            "ocr_reserve": "0.10",
            "proposal_reserve": "0.20",
            "chat_reserve": "0.30",
            "reason": "试点企业本月明确费用上限",
            **changes,
        }
    )


def test_admin_aggregate_reports_known_and_unknown_tokens_without_provider_calls(cost_case):
    c = cost_case
    with c.db.transaction(actor=c.manager) as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    result = read(c)
    assert result["active"] and result["ai"]["can_manage"] and result["ai"]["enabled"]
    assert result["usage"]["total_tokens"] is None
    assert (
        result["ai"]["ocr_ready"] and result["ai"]["journal_ready"] and result["ai"]["chat_ready"]
    )
    assert result["ai"]["provider_label"] == "supplier.example.invalid"
    assert "test-only-not-a-secret" not in json.dumps(result) and "api_key" not in json.dumps(
        result
    )
    assert c.provider.extract_calls == c.provider.proposal_calls == 0
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == before
    extract(c, create_event(c))
    usage = read(c)["usage"]
    assert usage["input_tokens"] == 3 and usage["output_tokens"] == 4
    assert usage["total_tokens"] == 7 and usage["requests"] == 1


def test_pause_resume_uses_current_settings_and_company_versions_with_reason_audit(cost_case):
    c = cost_case
    original_policy = c.operations.policy(c.manager, c.company["id"])
    old = change(c, False)
    paused = admin(c).update_ai_control(c.manager, c.company["id"], old, c.intelligence)
    assert not paused["ai"]["enabled"]
    assert c.intelligence.company_settings(c.manager, c.company["id"])["enabled"] is False
    with pytest.raises(AccountingError) as stale:
        admin(c).update_ai_control(c.manager, c.company["id"], old, c.intelligence)
    assert stale.value.status == 409
    resume = change(c, True)
    admin(c).update_company(
        c.manager,
        c.company["id"],
        AdminCompanyUpdate(
            name="企业已更名",
            reason="独立修改企业资料",
            expected_version=1,
        ),
    )
    with pytest.raises(AccountingError) as changed:
        admin(c).update_ai_control(c.manager, c.company["id"], resume, c.intelligence)
    assert changed.value.status == 409
    admin(c).update_ai_control(c.manager, c.company["id"], change(c, True), c.intelligence)
    assert read(c)["ai"]["enabled"]
    assert c.operations.policy(c.manager, c.company["id"]) == original_policy
    # An ABA enabled->paused->enabled transition never revives an old command.
    with pytest.raises(AccountingError):
        admin(c).update_ai_control(c.manager, c.company["id"], old, c.intelligence)
    events = admin(c).audit(c.manager, company_id=c.company["id"], action="admin.ai.configure")[
        "events"
    ]
    assert len(events) == 2
    assert all(e["details"]["reason"] == old.reason for e in events)


def test_two_admins_using_the_same_snapshot_cannot_overwrite_each_other(cost_case):
    c = cost_case
    command = change(c, False)

    def pause(_):
        try:
            admin(c).update_ai_control(c.manager, c.company["id"], command, c.intelligence)
            return "saved"
        except AccountingError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(pause, range(2)))
    assert outcomes.count("saved") == 1 and outcomes.count(409) == 1
    assert not read(c)["ai"]["enabled"]
    events = admin(c).audit(c.manager, company_id=c.company["id"], action="admin.ai.configure")[
        "events"
    ]
    assert len(events) == 1


@pytest.mark.parametrize("role", ["employee", "approver", "finance", "accountant", "outsider"])
def test_non_admin_cannot_read_ai_admin_or_change_settings_or_budget(cost_case, role):
    c, actor = cost_case, cost_case.actors[role]
    command = change(c, False)
    for call in (
        lambda: read(c, actor),
        lambda: admin(c).update_ai_control(actor, c.company["id"], command, c.intelligence),
        lambda: admin(c).update_ai_policy(actor, c.company["id"], policy_request(), c.operations),
    ):
        with pytest.raises(AccountingError) as denied:
            call()
        assert denied.value.status in (403, 404)
    assert read(c)["ai"]["enabled"]
    assert c.operations.policy(c.manager, c.company["id"])["version"] == 0


def test_company_admin_only_controls_own_company_and_warm_access_dies_on_revoke(cost_case):
    c = cost_case
    identity = SimpleNamespace(
        user_id="synthetic-ai-admin", email="ai-admin@example.test", display_name="企业管理员"
    )
    c.directory.register_identity(identity)
    c.directory.grant_company_member(
        c.manager, c.company["id"], CompanyMemberGrant(email=identity.email, role="admin")
    )
    actor = Actor(identity.user_id, c.manager.firm_id)
    assert read(c, actor)["ai"]["can_manage"]
    # 代账包干制：客户企业管理员只得到隐私授权元数据，用量与策略不再下发，
    # 费用限额也只能由事务所管理层维护。
    company_view = read(c, actor)
    assert company_view["usage"] is None and company_view["policy"] is None
    assert company_view["costs"] == [] and company_view["can_manage_budget"] is False
    with pytest.raises(AccountingError) as policy_denied:
        admin(c).update_ai_policy(actor, c.company["id"], policy_request(), c.operations)
    assert policy_denied.value.status == 403
    with pytest.raises(AccountingError) as cross:
        admin(c).ai_control(actor, c.other_company["id"], c.intelligence, c.operations)
    assert cross.value.status in (403, 404)
    command = change(c, False, actor)
    c.directory.revoke_company_member(c.manager, c.company["id"], actor.user_id)
    for call in (
        lambda: read(c, actor),
        lambda: admin(c).update_ai_control(actor, c.company["id"], command, c.intelligence),
    ):
        with pytest.raises(AccountingError):
            call()
    assert read(c)["ai"]["enabled"]


def test_inactive_company_is_metadata_only_and_does_not_reopen_operations(cost_case, monkeypatch):
    c = cost_case
    command = change(c, False)
    admin(c).change_status(
        c.manager,
        c.company["id"],
        AdminCompanyStatus(
            status="archived",
            expected_version=1,
            reason="试点结束后归档",
        ),
    )
    monkeypatch.setattr(
        c.operations,
        "overview",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("private overview was opened")),
    )
    result = read(c)
    assert not result["active"] and not result["ai"]["can_manage"]
    assert result["usage"] is None and result["policy"] is None and result["costs"] == []
    for call in (
        lambda: admin(c).update_ai_control(c.manager, c.company["id"], command, c.intelligence),
        lambda: admin(c).update_ai_policy(
            c.manager, c.company["id"], policy_request(), c.operations
        ),
    ):
        with pytest.raises(AccountingError):
            call()


def test_failed_switch_audit_rolls_back_settings_and_pause_survives_reactivation(
    cost_case, monkeypatch
):
    c = cost_case
    before = read(c)["ai"]
    with monkeypatch.context() as local:
        local.setattr(
            DirectoryService,
            "_audit",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic audit unavailable")),
        )
        with pytest.raises(RuntimeError):
            admin(c).update_ai_control(c.manager, c.company["id"], change(c, False), c.intelligence)
    assert read(c)["ai"] == before
    admin(c).update_ai_control(c.manager, c.company["id"], change(c, False), c.intelligence)
    admin(c).change_status(
        c.manager,
        c.company["id"],
        AdminCompanyStatus(status="suspended", expected_version=1, reason="暂停企业"),
    )
    admin(c).change_status(
        c.manager,
        c.company["id"],
        AdminCompanyStatus(status="active", expected_version=2, reason="恢复企业"),
    )
    assert not read(c)["ai"]["enabled"]


def test_admin_policy_reuses_exact_money_and_version_and_audits_reason_atomically(
    cost_case, monkeypatch
):
    c = cost_case
    result = admin(c).update_ai_policy(c.manager, c.company["id"], policy_request(), c.operations)
    assert result["version"] == 1 and result["monthly_limit"] == "10.00"
    assert read(c)["ai"]["enabled"]
    events = admin(c).audit(c.manager, company_id=c.company["id"], action="admin.ai.policy")[
        "events"
    ]
    assert len(events) == 1 and events[0]["details"]["reason"] == "试点企业本月明确费用上限"
    with pytest.raises(AccountingError) as stale:
        admin(c).update_ai_policy(c.manager, c.company["id"], policy_request(), c.operations)
    assert stale.value.status == 409
    monkeypatch.setattr(
        DirectoryService,
        "_audit",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic audit unavailable")),
    )
    with pytest.raises(RuntimeError):
        admin(c).update_ai_policy(
            c.manager,
            c.company["id"],
            policy_request(expected_version=1, monthly_limit="20.00"),
            c.operations,
        )
    assert c.operations.policy(c.manager, c.company["id"])["version"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": "false"},
        {"expected_enabled": 1},
        {"expected_updated_at": ""},
        {"expected_company_version": True},
        {"reason": " "},
        {"api_key": "forged"},
    ],
)
def test_control_commands_reject_coercion_missing_scope_and_secrets(cost_case, changes):
    payload = change(cost_case, False).model_dump()
    with pytest.raises(ValidationError):
        AdminAIControlUpdate(**{**payload, **changes})


def test_admin_ai_http_contract_and_month_bounds(cost_case):
    c = cost_case
    app = FastAPI()
    app.state.admin, app.state.intelligence, app.state.operations = (
        admin(c),
        c.intelligence,
        c.operations,
    )
    app.state.assistant_provider = SimpleNamespace(available=True)
    app.include_router(build_admin_router(lambda: c.manager))

    @app.exception_handler(AccountingError)
    async def error(_request, failure):
        return JSONResponse({"detail": failure.detail}, status_code=failure.status)

    with TestClient(app) as client:
        path = f"/api/admin/companies/{c.company['id']}/ai-control"
        current = client.get(path)
        assert current.status_code == 200 and current.json()["ai"]["chat_ready"]
        assert client.get(path + "?period=2026-13").status_code == 422
        response = client.patch(path, json=change(c, False).model_dump())
        assert response.status_code == 200 and response.json()["ai"]["enabled"] is False
        assert client.put(path + "/policy", json=policy_request().model_dump()).status_code == 200
        assert (
            client.put(
                path + "/policy", json={**policy_request().model_dump(), "monthly_limit": 1.0}
            ).status_code
            == 422
        )
