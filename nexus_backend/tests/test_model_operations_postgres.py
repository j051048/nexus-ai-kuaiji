"""Permanent PostgreSQL tests for supplier costs, budgets and company operations."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.accounting.service import Actor
from test_business_postgres import ADMIN_URL, APP_URL, attach, business_case, business_postgres, insert_event
from test_intelligence_postgres import ocr_run
from test_queue_postgres import WORKER_URL, worker

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL, reason="需要专用 PostgreSQL 管理员、API 及后台测试连接")


@pytest.fixture
def meter_case(business_case):
    case = business_case
    yield case
    # Keep immutable accounting/model evidence. Only mark this random fixture's
    # pending work terminal, so future global dispatcher tests see no leftovers.
    with case.psycopg.connect(ADMIN_URL) as conn:
        stamp = conn.execute("SELECT accounting.ocr_utc_now()").fetchone()[0]
        conn.execute("UPDATE accounting.ocr_attempts SET phase='finished',status='cancelled',finished_at=%s WHERE firm_id=%s AND status='running'", (stamp, case.owner.firm_id))
        conn.execute("UPDATE accounting.ocr_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,finished_at=%s WHERE firm_id=%s AND status IN ('queued','running','retry_wait')", (stamp, case.owner.firm_id))


def policy(case, quota=1, limit=1_000_000, reserve=250_000, **settings):
    fields = {"firm_id": case.owner.firm_id, "company_id": case.company, "quota_enabled": quota,
              "monthly_limit_micro": limit, "ocr_reserve_micro": reserve, "proposal_reserve_micro": reserve,
              "updated_by": case.owner.user_id, "updated_at": "2026-10-13T00:00:00+00:00", **settings}
    with case.db.transaction(write=True, actor=case.owner) as conn:
        conn.execute(f"INSERT INTO model_company_policies({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", tuple(fields.values()))


def run(case):
    event_id = insert_event(case)
    return ocr_run(case, event_id, attach(case, event_id))


def prices(configured=True, **changes):
    return json.dumps({"configured": configured, "input_per_million_micro": 2_000_000 if configured else None,
                       "output_per_million_micro": 4_000_000 if configured else None,
                       "provider": "compatible", "model": "configured-vision", **changes})


def reserve(case, run_id, token=None, pricing=None, amount=250_000, actor=None):
    token = token or uuid4().hex
    with case.db.transaction(write=True, actor=actor or case.employee) as conn:
        result = conn.execute("SELECT accounting.reserve_model_cost(?,?,?,?,?,?)", (run_id, token, "compatible", "configured-vision", pricing or prices(), amount)).fetchone()[0]
    return result, token


def sent(case, run_id, token, actor=None):
    with case.db.transaction(write=True, actor=actor or case.employee) as conn:
        return conn.execute("SELECT accounting.mark_model_cost_sent(?,?)", (run_id, token)).fetchone()[0]


def settle(case, run_id, token, inp=None, out=None, safe=False, actor=None):
    with case.db.transaction(write=True, actor=actor or case.employee) as conn:
        return conn.execute("SELECT accounting.settle_model_cost(?,?,?,?,?)", (run_id, token, inp, out, safe)).fetchone()[0]


def cost(case, run_id):
    with case.db.transaction(actor=case.owner) as conn:
        return dict(conn.execute("SELECT * FROM model_costs WHERE run_id=?", (run_id,)).fetchone())


def metrics(case, actor=None):
    with case.db.transaction(actor=actor or case.owner) as conn:
        month = conn.execute("SELECT to_char(clock_timestamp() AT TIME ZONE 'Asia/Shanghai','YYYY-MM')").fetchone()[0]
        return conn.execute("SELECT accounting.ops_company_metrics(?,?,?)", (case.owner.firm_id, case.company, month)).fetchone()[0]


def refresh(case, actor=None):
    with case.db.transaction(write=True, actor=actor or case.owner) as conn:
        return conn.execute("SELECT accounting.ops_refresh_company_alerts(?,?)", (case.owner.firm_id, case.company)).fetchone()[0]


def reconcile(case, run_id, version, amount, reason="已根据供应商账单核对", actor=None):
    with case.db.transaction(write=True, actor=actor or case.owner) as conn:
        return conn.execute("SELECT accounting.manual_reconcile_model_cost(?,?,?,?)", (run_id, version, amount, reason)).fetchone()[0]


def test_reservation_uses_current_policy_cap_and_is_idempotent(meter_case):
    case = meter_case
    policy(case)
    run_id = run(case)
    status, token = reserve(case, run_id, amount=0)  # Caller cannot under-reserve.
    assert status == "reserved"
    assert cost(case, run_id)["reserved_micro"] == 250_000
    assert reserve(case, run_id, token=token, amount=0)[0] == "reserved"
    assert reserve(case, run_id, token=uuid4().hex)[0] == "token_mismatch"
    assert reserve(case, run_id, token=token, pricing=prices(input_per_million_micro=3_000_000))[0] == "token_mismatch"


def test_concurrent_reservations_never_overbook_month_budget(meter_case):
    case = meter_case
    policy(case, limit=500_000)
    runs = [run(case) for _ in range(5)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        statuses = list(pool.map(lambda run_id: reserve(case, run_id)[0], runs))
    assert statuses.count("reserved") == 2 and statuses.count("quota_exhausted") == 3
    assert metrics(case)["costs"]["exposure_micro"] == 500_000


@pytest.mark.parametrize("change", [
    {"input_per_million_micro": 1.1}, {"input_per_million_micro": "2"}, {"output_per_million_micro": -1},
    {"configured": "true"}, {"provider": "other-provider"}, {"model": "other-model"},
    {"output_per_million_micro": None}, {"extra": "unwanted"},
])
def test_pricing_snapshot_rejects_unknown_fields_float_and_wrong_model(meter_case, change):
    assert reserve(meter_case, run(meter_case), pricing=prices(**change))[0] == "pricing_invalid"


def test_quota_requires_explicit_pricing_but_unlimited_mode_tracks_unknown(meter_case):
    case = meter_case
    policy(case)
    run_id = run(case)
    assert reserve(case, run_id, pricing=prices(False))[0] == "pricing_missing"
    with case.db.transaction(write=True, actor=case.owner) as conn:
        conn.execute("UPDATE model_company_policies SET quota_enabled=0,updated_by=? WHERE company_id=?", (case.owner.user_id, case.company))
    _, token = reserve(case, run_id, pricing=prices(False), amount=0)
    assert sent(case, run_id, token) and settle(case, run_id, token, 100, 50)
    item = cost(case, run_id)
    assert item["state"] == "uncertain" and item["estimated_micro"] is None
    aggregate = metrics(case)["costs"]
    assert aggregate["reported_count"] == 1 and aggregate["input_tokens"] == 100 and aggregate["unknown_count"] == 1


def test_exact_micro_unit_ceiling_and_usage_are_frozen(meter_case):
    case = meter_case
    policy(case)
    run_id = run(case)
    _, token = reserve(case, run_id, pricing=prices(input_per_million_micro=333_333, output_per_million_micro=222_222))
    assert sent(case, run_id, token) and settle(case, run_id, token, 1, 1)
    item = cost(case, run_id)
    assert item["estimated_micro"] == 1 and item["input_tokens"] == 1 and item["output_tokens"] == 1
    assert settle(case, run_id, token, 1_000_000, 1_000_000)
    assert cost(case, run_id)["estimated_micro"] == 1


def test_missing_usage_keeps_reserve_and_unknown_totals_nullable(meter_case):
    case = meter_case
    policy(case)
    run_id = run(case)
    _, token = reserve(case, run_id)
    assert sent(case, run_id, token) and settle(case, run_id, token)
    item = cost(case, run_id)
    assert item["state"] == "uncertain" and item["estimated_micro"] is None and item["reserved_micro"] == 250_000
    aggregate = metrics(case)["costs"]
    assert aggregate["exposure_micro"] == 250_000 and aggregate["reported_count"] == 0
    assert aggregate["input_tokens"] is None and aggregate["estimated_micro"] is None and aggregate["confirmed_micro"] is None


def test_safe_rejection_or_unsent_work_releases_budget(meter_case):
    case = meter_case
    policy(case, limit=250_000)
    run_id = run(case)
    _, token = reserve(case, run_id)
    assert settle(case, run_id, token)  # Never sent; no supplier charge expected.
    assert cost(case, run_id)["state"] == "released"
    another = run(case)
    _, second = reserve(case, another)
    assert sent(case, another, second) and settle(case, another, second, safe=True)
    assert metrics(case)["costs"]["exposure_micro"] == 0


def test_overrun_blocks_future_calls_until_manager_reconciles(meter_case):
    case = meter_case
    policy(case, limit=10_000_000)
    run_id = run(case)
    _, token = reserve(case, run_id)
    assert sent(case, run_id, token) and settle(case, run_id, token, 1_000_000, 0)
    item = cost(case, run_id)
    assert item["estimated_micro"] == 2_000_000 and item["overrun"] == 1
    assert reserve(case, run(case))[0] == "quota_overrun"
    assert not reconcile(case, run_id, item["version"], 1_000_000, actor=case.finance)
    assert not reconcile(case, run_id, item["version"] + 1, 1_000_000)
    assert reconcile(case, run_id, item["version"], 1_000_000)
    final = cost(case, run_id)
    assert final["state"] == "reconciled" and final["overrun"] == 0 and final["estimated_micro"] == 2_000_000 and final["input_tokens"] == 1_000_000
    assert reserve(case, run(case))[0] == "reserved"
    with case.db.transaction(actor=case.owner) as conn:
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='model.cost.reconcile' AND object_id=?", (run_id,)).fetchone()[0] == 1


def test_cannot_manually_release_reserved_sent_or_safe_rejected_cost(meter_case):
    case = meter_case
    policy(case)
    run_id = run(case)
    _, token = reserve(case, run_id)
    assert not reconcile(case, run_id, cost(case, run_id)["version"], 0)
    assert sent(case, run_id, token)
    assert not reconcile(case, run_id, cost(case, run_id)["version"], 0)
    assert settle(case, run_id, token, safe=True)
    assert not reconcile(case, run_id, cost(case, run_id)["version"], 0)


def test_settlement_survives_revocation_without_reading_originals(meter_case):
    case = meter_case
    policy(case)
    run_id = run(case)
    _, token = reserve(case, run_id)
    assert sent(case, run_id, token)
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.company_memberships WHERE company_id=%s AND user_id=%s", (case.company, case.employee.user_id))
    assert settle(case, run_id, token, 100, 100)
    assert cost(case, run_id)["state"] == "estimated"
    assert not settle(case, run_id, token, 100, 100, actor=case.finance)
    assert not settle(case, run_id, uuid4().hex, 100, 100)
    with case.db.transaction(actor=case.employee) as conn:
        assert conn.execute("SELECT count(*) FROM model_costs WHERE run_id=?", (run_id,)).fetchone()[0] == 0


@pytest.mark.parametrize("role", ["employee", "approver", "reviewer", "viewer", "colleague"])
def test_only_company_operations_roles_can_read_full_budget_or_metrics(meter_case, role):
    case = meter_case
    policy(case)
    run_id = run(case)
    reserve(case, run_id)
    actor = getattr(case, role)
    with case.db.transaction(actor=actor) as conn:
        assert conn.execute("SELECT count(*) FROM model_company_policies WHERE company_id=?", (case.company,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM model_costs WHERE run_id=?", (run_id,)).fetchone()[0] == 0
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        metrics(case, actor)
    for allowed in (case.owner, case.company_admin, case.company_owner, case.finance):
        assert metrics(case, allowed)["costs"]["run_count"] == 1
        with case.db.transaction(actor=allowed) as conn:
            # Operations access itself does not expose the employee original.
            if allowed is not case.finance:
                assert conn.execute("SELECT count(*) FROM business_events WHERE id=(SELECT event_id FROM model_costs WHERE run_id=?)", (run_id,)).fetchone()[0] == 0


def test_app_cannot_directly_write_cost_and_worker_cannot_read_cost(meter_case):
    case = meter_case
    reserve(case, run(case))
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("UPDATE model_costs SET confirmed_micro=0 WHERE company_id=?", (case.company,))
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with worker(case) as conn:
            conn.execute("SELECT * FROM accounting.model_costs")
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with worker(case) as conn:
            conn.execute("SELECT * FROM accounting.worker_heartbeats")


def test_china_month_boundary_is_separate_from_utc_timestamps(meter_case):
    case = meter_case
    run_id = run(case)
    reserve(case, run_id)
    item = cost(case, run_id)
    with case.psycopg.connect(ADMIN_URL) as conn:
        assert item["period"] == conn.execute("SELECT to_char(clock_timestamp() AT TIME ZONE 'Asia/Shanghai','YYYY-MM')").fetchone()[0]
        assert conn.execute("SELECT to_char('2026-10-31 16:00:00+00'::timestamptz AT TIME ZONE 'Asia/Shanghai','YYYY-MM')").fetchone()[0] == "2026-11"
    assert item["created_at"].endswith("+00:00")


def test_alert_is_deduplicated_acknowledged_then_reopened_for_new_risk(meter_case):
    case = meter_case
    run_id = run(case)
    _, token = reserve(case, run_id)
    assert sent(case, run_id, token) and settle(case, run_id, token)
    assert refresh(case)["open"] == 1
    with case.db.transaction(write=True, actor=case.finance) as conn:
        alert = conn.execute("SELECT * FROM ops_alerts WHERE company_id=? AND code='usage_uncertain'", (case.company,)).fetchone()
        conn.execute("UPDATE ops_alerts SET state='acknowledged',version=version+1 WHERE id=?", (alert["id"],))
        conn.execute("INSERT INTO ops_alert_actions VALUES(?,?,?,?,?,?,?)", (uuid4().hex, alert["id"], case.finance.firm_id, case.company, case.finance.user_id, "已知需核对账单", "2026-10-13T00:00:00+00:00"))
    refresh(case)
    with case.db.transaction(actor=case.owner) as conn:
        unchanged = conn.execute("SELECT state,version FROM ops_alerts WHERE id=?", (alert["id"],)).fetchone()
        assert unchanged[0] == "acknowledged" and unchanged[1] == alert["version"] + 1
    next_run = run(case)
    _, second = reserve(case, next_run)
    assert sent(case, next_run, second) and settle(case, next_run, second)
    refresh(case)
    with case.db.transaction(actor=case.owner) as conn:
        rows = conn.execute("SELECT state,payload_json FROM ops_alerts WHERE company_id=?", (case.company,)).fetchall()
        assert len(rows) == 1 and rows[0][0] == "open" and json.loads(rows[0][1])["count"] == 2


def test_worker_heartbeat_and_scan_return_no_cross_company_details(meter_case):
    case = meter_case
    worker_id = "stage6-heart-" + uuid4().hex
    try:
        with worker(case) as conn:
            assert conn.execute("SELECT accounting.record_worker_heartbeat(%s,'idle',0)", (worker_id,)).fetchone()["record_worker_heartbeat"]
            assert conn.execute("SELECT accounting.ops_scan_all_alerts() AS n").fetchone()["n"] >= 1
        assert metrics(case)["worker"]["available"] is True
        with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
            with case.db.transaction(write=True, actor=case.owner) as conn:
                conn.execute("SELECT accounting.record_worker_heartbeat('api','idle',0)")
    finally:
        with case.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("DELETE FROM accounting.worker_heartbeats WHERE worker_id=%s", (worker_id,))


def historical_cost(case, state="uncertain", reserve_amount=250_000, estimate=None, previous=True, overrun=0):
    run_id, token = run(case), uuid4().hex
    with case.psycopg.connect(ADMIN_URL) as conn:
        period = conn.execute("SELECT to_char((clock_timestamp() AT TIME ZONE 'Asia/Shanghai')-interval '1 month','YYYY-MM')" if previous else "SELECT to_char(clock_timestamp() AT TIME ZONE 'Asia/Shanghai','YYYY-MM')").fetchone()[0]
        stamp = conn.execute("SELECT to_char((clock_timestamp()-interval '5 minutes') AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')").fetchone()[0]
        event_id = conn.execute("SELECT event_id FROM accounting.ai_runs WHERE id=%s", (run_id,)).fetchone()[0]
        conn.execute("""INSERT INTO accounting.model_costs(id,run_id,firm_id,company_id,event_id,kind,created_by,period,provider,model,charge_token,pricing_json,quota_enabled,reserved_micro,estimated_micro,state,created_at,sent_at,finished_at,overrun)
         VALUES(%s,%s,%s,%s,%s,'ocr',%s,%s,'compatible','configured-vision',%s,%s,1,%s,%s,%s,%s,%s,%s,%s)""",
                     (run_id, run_id, case.employee.firm_id, case.company, event_id, case.employee.user_id, period, token, prices(), reserve_amount, estimate, state, stamp,
                      stamp if state != "reserved" else None, None if state in {"reserved", "sent"} else stamp, overrun))
    return run_id, token


def test_carried_unknown_reservation_limits_new_month_until_reconciled(meter_case):
    case = meter_case
    policy(case, limit=500_000)
    old, _ = historical_cost(case, reserve_amount=500_000)
    summary = metrics(case)["costs"]
    assert summary["carried_reserved_micro"] == 500_000 and summary["exposure_micro"] == 500_000
    assert summary["run_count"] == 0 and summary["input_tokens"] is None and summary["carried_unknown_count"] == 1
    assert reserve(case, run(case))[0] == "quota_exhausted"
    refresh(case)
    with case.db.transaction(actor=case.owner) as conn:
        assert conn.execute("SELECT count(*) FROM ops_alerts WHERE company_id=? AND code='usage_uncertain' AND state='open'", (case.company,)).fetchone()[0] == 1
    assert reconcile(case, old, cost(case, old)["version"], 0)
    assert reserve(case, run(case))[0] == "reserved"
    assert metrics(case)["costs"]["carried_reserved_micro"] == 0


def test_previous_month_known_cost_is_not_charged_again(meter_case):
    case = meter_case
    policy(case, limit=250_000)
    historical_cost(case, state="estimated", estimate=2_000_000)
    assert reserve(case, run(case))[0] == "reserved"
    summary = metrics(case)["costs"]
    assert summary["carried_reserved_micro"] == 0 and summary["exposure_micro"] == 250_000


def test_historical_unreconciled_overrun_still_blocks_new_calls(meter_case):
    case = meter_case
    policy(case, limit=10_000_000)
    historical_cost(case, state="estimated", estimate=2_000_000, overrun=1)
    assert reserve(case, run(case))[0] == "quota_overrun"
    assert metrics(case)["costs"]["historical_overrun_count"] == 1
    refresh(case)
    with case.db.transaction(actor=case.owner) as conn:
        assert conn.execute("SELECT count(*) FROM ops_alerts WHERE company_id=? AND code='quota_overrun' AND state='open'", (case.company,)).fetchone()[0] == 1


def test_maximum_valid_rate_and_tokens_use_numeric_multiplication(meter_case):
    case = meter_case
    run_id = run(case)
    _, token = reserve(case, run_id, pricing=prices(input_per_million_micro=1_000_000_000_000, output_per_million_micro=1_000_000_000_000), amount=0)
    assert sent(case, run_id, token) and settle(case, run_id, token, 10_000_000, 10_000_000)
    assert cost(case, run_id)["estimated_micro"] == 20_000_000_000_000
    assert reserve(case, run(case), pricing=prices(input_per_million_micro=1_000_000_000_001))[0] == "pricing_invalid"


def test_monitor_timeout_preserves_reserve_and_late_token_facts_can_settle(meter_case):
    case = meter_case
    policy(case)
    run_id, token = historical_cost(case, state="sent", previous=False)
    refresh(case)
    pending = cost(case, run_id)
    assert pending["state"] == "uncertain" and pending["reserved_micro"] == 250_000
    assert metrics(case)["costs"]["reserved_micro"] == 250_000
    assert settle(case, run_id, token, 100, 50)
    known = cost(case, run_id)
    assert known["state"] == "estimated" and known["estimated_micro"] == 400
    assert metrics(case)["costs"]["reserved_micro"] == 0
    assert settle(case, run_id, token, 1_000_000, 1_000_000)
    assert cost(case, run_id)["estimated_micro"] == 400


def test_manual_confirmation_cannot_be_overwritten_by_late_response(meter_case):
    case = meter_case
    run_id, token = historical_cost(case, previous=False)
    assert reconcile(case, run_id, cost(case, run_id)["version"], 12_345)
    assert settle(case, run_id, token, 100, 100)
    confirmed = cost(case, run_id)
    assert confirmed["state"] == "reconciled" and confirmed["confirmed_micro"] == 12_345 and confirmed["estimated_micro"] is None


def test_scoped_snapshot_helper_preserves_age_and_avoids_noop_rewrite(meter_case):
    case = meter_case
    event_id = insert_event(case)
    with case.db.transaction(write=True, actor=case.employee) as conn:
        assert conn.execute("SELECT accounting.record_ops_exception_snapshot(?,2,1,NULL,'facts-v1')", (event_id,)).fetchone()[0]
        assert conn.execute("SELECT count(*) FROM ops_exception_snapshots WHERE event_id=?", (event_id,)).fetchone()[0] == 0
    with case.db.transaction(actor=case.owner) as conn:
        old = dict(conn.execute("SELECT * FROM ops_exception_snapshots WHERE event_id=?", (event_id,)).fetchone())
    with case.db.transaction(write=True, actor=case.employee) as conn:
        assert conn.execute("SELECT accounting.record_ops_exception_snapshot(?,2,1,NULL,'facts-v1')", (event_id,)).fetchone()[0]
    with case.db.transaction(actor=case.owner) as conn:
        same = dict(conn.execute("SELECT * FROM ops_exception_snapshots WHERE event_id=?", (event_id,)).fetchone())
        assert same == old
    with case.db.transaction(write=True, actor=case.colleague) as conn:
        assert not conn.execute("SELECT accounting.record_ops_exception_snapshot(?,0,0,NULL,'malicious')", (event_id,)).fetchone()[0]
    with case.db.transaction(write=True, actor=case.employee) as conn:
        assert conn.execute("SELECT accounting.record_ops_exception_snapshot(?,1,0,NULL,'facts-v2')", (event_id,)).fetchone()[0]
    with case.db.transaction(actor=case.owner) as conn:
        updated = dict(conn.execute("SELECT * FROM ops_exception_snapshots WHERE event_id=?", (event_id,)).fetchone())
        assert updated["oldest_opened_at"] == old["oldest_opened_at"] and updated["updated_at"] != old["updated_at"]
    # Explicitly paused processing excludes stored summaries from actionable
    # backlog. New companies now start enabled by default.
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.ai_company_settings SET enabled=0 WHERE firm_id=%s AND company_id=%s", (case.owner.firm_id, case.company))
    assert metrics(case)["exceptions"]["blocking_events"] == 0
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("INSERT INTO accounting.ai_company_settings VALUES(%s,%s,1,%s,%s) ON CONFLICT(firm_id,company_id) DO UPDATE SET enabled=1,updated_by=excluded.updated_by,updated_at=excluded.updated_at", (case.owner.firm_id, case.company, case.owner.user_id, old["updated_at"]))
    assert metrics(case)["exceptions"]["blocking_events"] == 1
    assert metrics(case)["exceptions"]["updated_at"] == updated["updated_at"]


def test_same_count_new_risk_reopens_and_manual_resolution_does_not_rewrite_history(meter_case):
    case = meter_case
    first, _ = historical_cost(case)
    refresh(case)
    with case.db.transaction(write=True, actor=case.owner) as conn:
        alert = conn.execute("SELECT * FROM ops_alerts WHERE company_id=? AND code='usage_uncertain'", (case.company,)).fetchone()
        conn.execute("UPDATE ops_alerts SET state='acknowledged',version=version+1 WHERE id=?", (alert["id"],))
    assert reconcile(case, first, cost(case, first)["version"], 0)
    historical_cost(case)
    refresh(case)
    with case.db.transaction(actor=case.owner) as conn:
        changed = conn.execute("SELECT state,payload_json,version FROM ops_alerts WHERE id=?", (alert["id"],)).fetchone()
        assert changed[0] == "open" and json.loads(changed[1])["count"] == 1
        assert changed[2] == alert["version"] + 2


def test_snapshot_backlog_excludes_already_approved_mapping(meter_case):
    from test_business_postgres import full_event, handoff

    case = meter_case
    event_id = full_event(case)
    _, draft_id = handoff(case, event_id)
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("INSERT INTO accounting.ai_company_settings VALUES(%s,%s,1,%s,%s) ON CONFLICT(firm_id,company_id) DO UPDATE SET enabled=1,updated_by=excluded.updated_by,updated_at=excluded.updated_at", (case.owner.firm_id, case.company, case.owner.user_id, "2026-10-13T00:00:00+00:00"))
    with case.db.transaction(write=True, actor=case.employee) as conn:
        assert conn.execute("SELECT accounting.record_ops_exception_snapshot(?,1,0,NULL,'review-required')", (event_id,)).fetchone()[0]
    assert metrics(case)["exceptions"]["blocking_events"] == 1
    # Ledger domain checks are covered separately. This SQL fixture verifies
    # only that operational summaries omit approved/exported canonical drafts.
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.drafts SET status='approved',approved_by=%s,approved_at=%s WHERE id=%s", (case.finance.user_id, "2026-10-13T00:00:00+00:00", draft_id))
    assert metrics(case)["exceptions"]["blocking_events"] == 0
