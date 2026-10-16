"""Company setup is derived from live grants; private finance data is not a setup fact."""

import pytest

from app.accounting.admin_models import AdminCompanyStatus
from app.accounting.admin_service import AdminService
from app.accounting.directory import DirectoryService
from app.accounting.domain import AccountingError
from app.business.models import SubmissionPolicyUpdate
from app.main import create_app
from fastapi.testclient import TestClient
from test_business import business  # noqa: F401
from test_business_postgres import business_case, business_postgres  # noqa: F401
from test_stage17_monthly_workbench import month_case  # noqa: F401


def steps(result):
    return {step["id"]: step for step in result["steps"]}


def test_setup_uses_current_month_and_never_claims_an_unrun_trial(month_case):
    c = month_case
    service = AdminService(c.db)
    value = service.onboarding(c.owner, c.company, "2026-10")
    assert value["active"] and not value["ready"]
    assert steps(value)["books"]["complete"]
    assert not steps(value)["trial"]["complete"]
    assert steps(value)["ai"]["complete"] and not steps(value)["ai"]["required"]
    assert all("email" not in step and "payload" not in step for step in value["steps"])
    next_month = service.onboarding(c.owner, c.company, "2026-11")
    assert not steps(next_month)["books"]["complete"]
    assert next_month["period"] == "2026-11"


def test_setup_rechecks_policy_membership_and_denies_employee_cross_company(month_case):
    c = month_case
    service = AdminService(c.db)
    c.business.submission.update(c.owner, c.company, SubmissionPolicyUpdate(
        mode="direct", expected_version=0, reason="小企业由老板出纳直接提交资料"
    ))
    current = service.onboarding(c.owner, c.company, "2026-10")
    assert steps(current)["policy"]["complete"]
    assert "直接提交" in steps(current)["policy"]["detail"]
    with pytest.raises(AccountingError):
        service.onboarding(c.employee, c.company, "2026-10")
    with pytest.raises(AccountingError):
        service.onboarding(c.reviewer, c.other_company, "2026-10")
    # Revocation is evaluated on the next read, not from browser/JWT metadata.
    directory = DirectoryService(c.db)
    directory.revoke_firm_member(c.owner, c.reviewer.user_id)
    with pytest.raises(AccountingError):
        service.onboarding(c.reviewer, c.company, "2026-10")


def test_setup_archived_company_is_metadata_only_and_reads_do_not_write(month_case):
    c = month_case
    service = AdminService(c.db)
    with c.db.transaction(actor=c.owner) as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_events WHERE firm_id=?", (c.owner.firm_id,)).fetchone()[0]
    service.onboarding(c.owner, c.company, "2026-10")
    with c.db.transaction(actor=c.owner) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE firm_id=?", (c.owner.firm_id,)).fetchone()[0] == before
    service.change_status(c.owner, c.company, AdminCompanyStatus(
        status="archived", expected_version=1, reason="虚构企业完成初始化验证后归档"
    ))
    result = service.onboarding(c.owner, c.company, "2026-10")
    assert not result["active"] and not result["ready"] and result["steps"] == []


def test_setup_http_rejects_invalid_month_and_employee_and_keeps_active_schema(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "setup.db", seed=True,
                               queue_worker_enabled=False), base_url="http://127.0.0.1", client=("127.0.0.1", 5678)) as client:
        owner = client.get("/api/session?persona=accountant").json()
        auth = {"Authorization": "Bearer " + owner["token"]}
        company = client.post("/api/companies", headers=auth,
                              json={"name": "虚构开通清单企业", "tax_id": ""}).json()
        endpoint = f"/api/admin/companies/{company['id']}/onboarding"
        assert client.get(endpoint, headers=auth).status_code == 422
        assert client.get(endpoint + "?period=2026-13", headers=auth).status_code == 422
        response = client.get(endpoint + "?period=2026-10", headers=auth)
        assert response.status_code == 200, response.text
        assert response.json()["company_id"] == company["id"]


def test_setup_native_postgres_functions_reject_non_admin_and_cross_company(month_case):
    c = month_case
    if not c.db.is_postgres:
        pytest.skip("原生 PostgreSQL 权限函数")
    import psycopg

    for actor, company_id in ((c.employee, c.company), (c.reviewer, c.other_company)):
        for sql, params in (
            ("SELECT accounting.admin_company_onboarding(?,?)", (company_id, "2026-10")),
            ("SELECT accounting.processing_capacity(?)", (company_id,)),
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with c.db.transaction(actor=actor) as conn:
                    conn.execute(sql, params)
