"""月初批量开期：复用受治理的建期与资料生成路径，逐户报告并保持幂等。"""

from datetime import date

import pytest
from pydantic import ValidationError

from app.accounting.domain import AccountingError
from app.accounting.models import CompanyCreate
from app.ledgers.service import LedgersService
from app.monthly.batch import BatchOpen, MonthlyBatchService
from app.service_plans.models import PlanSave, TemplateCreate, TemplateItem
from app.service_plans.service import ServicePlansService
from test_business import business


@pytest.fixture
def monthly_batch(business):
    c = business
    ledgers = LedgersService(c.db)
    plans = ServicePlansService(c.db, c.ledger, c.service)
    service = MonthlyBatchService(c.db, c.ledger, ledgers, plans, c.service)
    c.ledgers, c.plans, c.monthly_batch = ledgers, plans, service
    return c


def test_batch_open_creates_next_period_and_copies_members_idempotently(monthly_batch):
    c = monthly_batch
    result = c.monthly_batch.open_month(
        c.manager, BatchOpen(period="2026-11", scope="awaiting", reason="月初批量开期")
    )
    assert result["period"] == "2026-11"
    assert (result["opened"], result["skipped"], result["blocked"]) == (2, 0, 0)
    rows = {row["company_name"]: row for row in result["results"]}
    row = rows["第一客户企业"]
    assert row["status"] == "created" and row["book_id"]
    assert row["plan"] == {"copied": False, "dropped": [], "reason": "source_without_plan"}
    assert row["materials"] == {"created": 0, "reused": 0}
    assert any("资料模板" in item for item in row["warnings"])
    with c.db.transaction() as conn:
        new = conn.execute("SELECT * FROM books WHERE id=?", (row["book_id"],)).fetchone()
        assert new["period"] == "2026-11" and new["name"] == "第一客户企业 2026-11"
        linked = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (row["book_id"],)
        ).fetchone()
        assert linked
        access = {
            grant["user_id"]: grant["role"]
            for grant in conn.execute(
                "SELECT user_id,role FROM book_access WHERE book_id=?", (row["book_id"],)
            )
        }
        assert access[c.manager.user_id] == "reviewer"
        assert access[c.actors["accountant"].user_id] == "reviewer"
        assert access["finance"] == "editor"
        period = conn.execute(
            "SELECT status FROM periods WHERE book_id=?", (row["book_id"],)
        ).fetchone()
        assert period["status"] == "open"
        audits = [
            dict(item)
            for item in conn.execute("SELECT * FROM audit_events WHERE action='monthly.batch_open'")
        ]
        assert len(audits) == 2 and all(item["company_id"] for item in audits)
    again = c.monthly_batch.open_month(
        c.manager,
        BatchOpen(
            period="2026-11",
            companies=[c.company["id"], c.other["id"]],
            reason="重复执行应当幂等",
        ),
    )
    assert (again["opened"], again["skipped"], again["blocked"]) == (0, 2, 0)
    assert all("已存在" in row["message"] for row in again["results"])
    empty = c.monthly_batch.open_month(
        c.manager, BatchOpen(period="2026-11", scope="awaiting", reason="全部已建账后无待处理")
    )
    assert empty["results"] == []


def test_batch_open_ledger_period_inherits_plan_and_generates_materials(monthly_batch):
    c = monthly_batch
    with c.db.transaction(write=True, actor=c.manager) as conn:
        c.ledgers.ensure_book_in_transaction(conn, c.manager, c.book)
    c.plans.save_plan(
        c.manager,
        c.book["id"],
        PlanSave(
            expected_version=0,
            lead_user_id=c.actors["accountant"].user_id,
            reviewer_user_id=c.manager.user_id,
            contact_user_id="finance",
            due_on=date(2026, 11, 10),
            reason="试点企业分工继承来源",
        ),
    )
    template = c.plans.create_template(
        c.manager,
        c.book["id"],
        TemplateCreate(
            name="月度催资",
            items=[
                TemplateItem(key="bank_statement", title="银行对账单"),
                TemplateItem(key="payroll", title="工资表"),
            ],
            reason="建立月度资料模板",
        ),
    )
    assert template["items"][0]["title"] == "银行对账单"
    result = c.monthly_batch.open_month(
        c.manager,
        BatchOpen(
            period="2026-11",
            companies=[c.company["id"]],
            reason="批量开期并生成催资清单",
        ),
    )
    row = result["results"][0]
    assert row["status"] == "created", row
    assert row["plan"] and row["plan"]["copied"] is True and row["plan"]["dropped"] == []
    assert row["materials"] == {"created": 2, "reused": 0}
    assert not row["warnings"]
    with c.db.transaction() as conn:
        linked = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (row["book_id"],)
        ).fetchone()
        assert linked
        plan = conn.execute(
            "SELECT * FROM service_plan_versions WHERE book_id=? ORDER BY version DESC",
            (row["book_id"],),
        ).fetchone()
        assert plan["lead_user_id"] == c.actors["accountant"].user_id
        assert plan["reviewer_user_id"] == c.manager.user_id
        assert plan["contact_user_id"] == "finance"
        assert plan["due_on"] == "2026-12-10"
        requests = conn.execute(
            "SELECT COUNT(*) AS total FROM material_requests WHERE book_id=? AND period='2026-11'",
            (row["book_id"],),
        ).fetchone()
        assert requests["total"] == 2
        audits = [
            dict(item)
            for item in conn.execute(
                "SELECT * FROM audit_events WHERE action IN ('service_plan.inherit','ledger.period.create')"
            )
        ]
        assert {item["action"] for item in audits} == {
            "service_plan.inherit",
            "ledger.period.create",
        }
    again = c.monthly_batch.open_month(
        c.manager,
        BatchOpen(period="2026-11", companies=[c.company["id"]], reason="重复执行不重复生成"),
    )
    assert again["results"][0]["status"] == "skipped"


def test_batch_open_reports_blocked_rows_and_enforces_manager_scope(monthly_batch):
    c = monthly_batch
    fresh = c.directory.create_company(c.manager, CompanyCreate(name="新接入企业"))
    with pytest.raises(AccountingError) as denied:
        c.monthly_batch.open_month(
            c.actors["accountant"],
            BatchOpen(period="2026-11", companies=[c.company["id"]], reason="非管理层尝试开期"),
        )
    assert denied.value.status == 403
    scoped = c.monthly_batch.open_month(
        c.manager,
        BatchOpen(period="2026-11", scope="awaiting", search="第二", reason="按搜索范围开期"),
    )
    assert [row["company_name"] for row in scoped["results"]] == ["第二客户企业"]
    result = c.monthly_batch.open_month(
        c.manager,
        BatchOpen(
            period="2026-11",
            companies=[c.company["id"], fresh["id"]],
            reason="显式列表包含未接入企业",
        ),
    )
    rows = {row["company_name"]: row for row in result["results"]}
    assert rows["第一客户企业"]["status"] == "created"
    blocked = rows["新接入企业"]
    assert blocked["status"] == "blocked" and "开通清单" in blocked["message"]
    with pytest.raises(ValidationError):
        BatchOpen(period="2026-13", companies=[c.company["id"]], reason="非法月份")
    with pytest.raises(ValidationError):
        BatchOpen(period="2026-11", scope="companies", reason="缺少企业清单")
    with pytest.raises(ValidationError):
        BatchOpen(
            period="2026-11",
            companies=[c.company["id"], c.company["id"]],
            reason="企业清单重复",
        )


class _ReplayingLedgers:
    """The concurrent winner committed the same idempotency key first."""

    def __init__(self, inner, book_id):
        self.inner, self.book_id, self.calls = inner, book_id, []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def create_period(self, actor, book_id, request):
        self.calls.append(request.idempotency_key)
        return {"ledger_id": "ledger", "book_id": self.book_id, "replayed": True}


def test_batch_open_reports_a_concurrent_win_as_skipped(monthly_batch):
    c = monthly_batch
    c.monthly_batch.open_month(
        c.manager, BatchOpen(period="2026-11", scope="awaiting", reason="先建一期")
    )
    with c.db.transaction() as conn:
        target = conn.execute(
            "SELECT id,company_id FROM books WHERE period='2026-11' ORDER BY id LIMIT 1"
        ).fetchone()
    replay = _ReplayingLedgers(c.monthly_batch.ledgers, target["id"])
    c.monthly_batch.ledgers = replay
    before = _audit_count(c)
    result = c.monthly_batch.open_month(
        c.manager,
        BatchOpen(
            period="2026-12", scope="companies", companies=[target["company_id"]], reason="并发撞车"
        ),
    )
    assert replay.calls, "预检通过的企业必须走到建期"
    assert (result["opened"], result["skipped"]) == (0, 1)
    row = result["results"][0]
    assert row["status"] == "skipped" and row["book_id"] == target["id"]
    assert "另一位" in row["message"]
    assert _audit_count(c) == before, "别人的创建不应再写一条创建审计"


def _audit_count(c):
    with c.db.transaction() as conn:
        return conn.execute(
            "SELECT count(*) AS n FROM audit_events WHERE action LIKE 'monthly.batch%'"
        ).fetchone()["n"]
