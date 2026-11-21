"""Tenant quotas: defaults, manager-only configuration, and the refusal paths."""

import pytest

from app.accounting.domain import AccountingError
from app.security.quotas import DEFAULTS, QuotaService
from test_business import business  # noqa: F401


def _service(business):
    return QuotaService(business.db)


def test_a_firm_without_configuration_still_has_documented_limits(business):
    quota = _service(business).quota(business.manager)

    assert quota.as_dict() == DEFAULTS


def test_only_a_firm_manager_can_change_quotas(business):
    service = _service(business)

    with pytest.raises(AccountingError) as caught:
        service.configure(business.actors["accountant"], import_rows_per_batch=10)

    assert caught.value.status in (403, 404)
    assert (
        service.quota(business.manager).import_rows_per_batch == DEFAULTS["import_rows_per_batch"]
    )


def test_configuration_is_partial_and_persisted(business):
    service = _service(business)

    configured = service.configure(business.manager, async_jobs_per_day=7)

    assert configured.async_jobs_per_day == 7
    assert configured.import_rows_per_batch == DEFAULTS["import_rows_per_batch"]
    assert service.quota(business.manager).async_jobs_per_day == 7


@pytest.mark.parametrize(
    "values",
    [
        {"import_rows_per_batch": 0},
        {"import_rows_per_batch": 200001},
        {"storage_bytes": "big"},
        {"nonsense": 1},
    ],
)
def test_bad_configuration_is_rejected_rather_than_clamped(business, values):
    with pytest.raises(AccountingError):
        _service(business).configure(business.manager, **values)


def test_import_rows_over_the_limit_is_a_413_and_is_logged(business, caplog):
    service = _service(business)
    service.configure(business.manager, import_rows_per_batch=100)

    with pytest.raises(AccountingError) as caught:
        with business.db.transaction(actor=business.manager, write=True) as conn:
            service.ensure_import_rows(conn, business.manager, 101)

    assert caught.value.status == 413
    assert "100" in caught.value.detail
    assert "import_rows_per_batch requested=101 limit=100" in caplog.text


def test_daily_jobs_and_storage_use_the_rate_and_size_statuses(business):
    service = _service(business)
    service.configure(business.manager, async_jobs_per_day=2, storage_bytes=1048576)

    with business.db.transaction(actor=business.manager) as conn:
        with pytest.raises(AccountingError) as jobs:
            service.ensure_daily_jobs(conn, business.manager, 2, adding=1)
        assert jobs.value.status == 429

        with pytest.raises(AccountingError) as storage:
            service.ensure_storage(conn, business.manager, 1000000, 100000)
        assert storage.value.status == 413

        # Exactly at the limit still passes: quotas are ceilings, not reserves.
        service.ensure_daily_jobs(conn, business.manager, 1, adding=1)


def test_quotas_are_scoped_to_the_acting_firm(business):
    service = _service(business)
    service.configure(business.manager, import_rows_per_batch=50)

    with business.db.transaction(actor=business.manager) as conn:
        rows = conn.execute("SELECT firm_id FROM firm_quotas").fetchall()
    assert [row["firm_id"] for row in rows] == [business.manager.firm_id]
