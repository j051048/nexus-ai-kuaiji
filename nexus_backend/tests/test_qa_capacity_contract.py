import pytest
from contextlib import nullcontext
from types import SimpleNamespace


def test_capacity_report_rejects_empty_partial_and_failed_workloads():
    from tools.capacity.qa_mixed import distribution, require_complete

    assert distribution([1, 2, 100])["p95_ms"] == 100
    for rows, expected in (([], 1), ([{"ok": True}], 2), ([{"ok": False}], 1)):
        with pytest.raises(ValueError):
            require_complete(rows, expected)
    require_complete([{"ok": True}], 1)


def test_capacity_target_must_be_owned_loopback_config():
    from tools.capacity.qa_mixed import load_target

    with pytest.raises(ValueError):
        load_target({"admin": "postgresql://user:secret@production.invalid/accounting"}, 16)


def test_only_verified_admission_rejection_can_count_as_measured_overload():
    from tools.capacity.qa_mixed import require_complete

    require_complete(
        [{"ok": False, "class": "DatabaseCapacityError", "no_write": True}], 1, allow_shed=True
    )
    for row in (
        {"ok": False, "class": "DatabaseCapacityError"},
        {"ok": False, "class": "RuntimeError", "no_write": True},
    ):
        with pytest.raises(ValueError):
            require_complete([row], 1, allow_shed=True)


def test_reused_capacity_seed_releases_its_connection_pool(monkeypatch):
    from tools.capacity import dcp10_capacity as module

    closed = []
    db = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(module, "Database", lambda _: db)
    monkeypatch.setattr(
        module, "connect_admin", lambda _: nullcontext(SimpleNamespace(execute=lambda *a: None))
    )
    monkeypatch.setattr(
        module,
        "DirectoryService",
        lambda _: SimpleNamespace(create_company=lambda *a: {"id": "company"}),
    )
    monkeypatch.setattr(
        module,
        "AccountingService",
        lambda _: SimpleNamespace(create_book=lambda *a: {"id": "book"}),
    )
    module.seed("synthetic", 1)
    assert closed == [True]
