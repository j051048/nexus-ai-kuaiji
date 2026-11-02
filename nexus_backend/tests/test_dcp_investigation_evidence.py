import pytest
from app.assistant import investigation
from test_business import business  # noqa: F401


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv(investigation.ENV_FLAG, "1")


def test_actual_reader_evidence_is_bound_to_scope_and_contents(business):
    result = investigation.investigate(
        business.service, business.manager, business.book["id"], "2026-10"
    )
    assert result["evidence"]["company_id"] == business.book["company_id"]
    assert result["evidence"]["fingerprint"] == investigation.evidence_fingerprint(result)
    assert result["model_calls"] == 0
    result["findings"]["voucher_status"]["draft_rows"] += 1
    assert result["evidence"]["fingerprint"] != investigation.evidence_fingerprint(result)


def test_final_reader_failure_returns_no_stale_evidence(business, monkeypatch):
    original = investigation.READERS["voucher_status"]
    calls = 0

    def reader(conn, book, period):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("private driver message")
        return original(conn, book, period)

    monkeypatch.setitem(investigation.READERS, "voucher_status", reader)
    result = investigation.investigate(
        business.service, business.manager, business.book["id"], "2026-10"
    )
    assert result["status"] == "blocked"
    assert result["findings"] == {}
    assert result["evidence"] is None
    assert "private" not in str(result)
