"""Deterministic local supplier and SQLite capacity checks; no paid model calls."""
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.concurrency.settings import SchedulingSettings
from app.concurrency.provider import provider_capacity, provider_scope, _slot, _release
from app.intelligence.provider import AISettings, ProviderError
from test_intelligence_workflow import ai_case


@pytest.mark.parametrize("value", ["0", "9", "-1", "bad", "1.5"])
def test_processing_configuration_rejects_unbounded_values(value):
    with pytest.raises(ValueError):
        SchedulingSettings.from_env({"ACCOUNTING_PROCESSING_GLOBAL_LIMIT": value})


def test_defaults_stay_conservative_and_raise_only_with_explicit_bounds():
    assert SchedulingSettings.from_env({}).requested_global_limit == 2
    assert SchedulingSettings.from_env({"ACCOUNTING_PROCESSING_GLOBAL_LIMIT": "8"}).requested_global_limit == 8
    assert AISettings(max_concurrency=8).max_concurrency == 8


def test_same_supplier_instances_share_capacity_while_distinct_suppliers_progress():
    entered, release = Event(), Event()
    def held():
        with provider_capacity("https://shared.synthetic.test/v1", 1, 45):
            entered.set()
            assert release.wait(3)
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(held)
        assert entered.wait(3)
        with pytest.raises(ProviderError, match="容量已满"):
            with provider_capacity("https://shared.synthetic.test/other", 2, 45):
                pass
        with provider_capacity("https://different.synthetic.test/v1", 1, 45):
            pass
        release.set()
        running.result()
    with provider_capacity("https://shared.synthetic.test/v1", 1, 45):
        pass


def test_sqlite_transport_slots_are_company_scoped_and_crash_leases_recover(ai_case):
    c = ai_case
    owner = c.manager
    first, second = uuid4().hex, uuid4().hex
    assert _slot(c.db, owner, c.company["id"], "synthetic.test", first, 30)
    assert not _slot(c.db, owner, c.company["id"], "other.test", second, 30)
    with c.db.transaction(write=True) as conn:
        conn.execute("UPDATE provider_request_slots SET lease_until=0 WHERE id=?", (first,))
    assert _slot(c.db, owner, c.company["id"], "synthetic.test", second, 30)
    _release(c.db, owner, first)
    with c.db.transaction() as conn:
        assert conn.execute("SELECT id FROM provider_request_slots").fetchone()[0] == second
    _release(c.db, owner, second)
    with provider_scope(c.db, owner, c.company["id"]):
        with pytest.raises(RuntimeError):
            with provider_capacity("https://synthetic.test/v1", 1, 45):
                raise RuntimeError("Synthetic transport failure")
    with c.db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM provider_request_slots").fetchone()[0] == 0


def test_company_scope_requires_identity_and_explicit_system_firm(ai_case):
    c = ai_case
    for options in [{"actor": c.manager, "company_id": c.company["id"]},
                    {"write": True, "actor": c.manager},
                    {"write": True, "system": True, "company_id": c.company["id"]}]:
        with pytest.raises(ValueError):
            with c.db.transaction(write_scope="company", **options):
                pass
    # 系统身份 + 企业范围是已评审的受控扩展（投影刷新），必须显式给出 firm_id。
    with c.db.transaction(
        write=True,
        system=True,
        write_scope="company",
        company_id=c.company["id"],
        firm_id=c.manager.firm_id,
    ) as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
