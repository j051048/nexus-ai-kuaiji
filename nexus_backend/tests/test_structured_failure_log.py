"""Structured failure log: enough to locate, never enough to leak."""

import json
import logging
import sqlite3

import pytest

from test_business_api import auth, client  # noqa: F401

from app.observability.failures import failure_record, firm_of


class DatatypeMismatch(Exception):
    """Stand-in for the driver error that used to log as a bare class name."""

    sqlstate = "42804"


def _records(caplog):
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.getMessage().startswith("{")
    ]


def test_failure_record_carries_the_fields_an_operator_needs():
    try:
        raise DatatypeMismatch("column amount_cents is integer but expression is text")
    except DatatypeMismatch as error:
        record = failure_record(
            trace_id="trace-1",
            method="POST",
            route="/api/vouchers",
            error=error,
            firm_id="firm-1",
        )
    assert record["event"] == "request.failed"
    assert record["request_id"] == record["trace_id"] == "trace-1"
    assert record["firm_id"] == "firm-1"
    assert record["error_class"] == "DatatypeMismatch"
    assert record["sqlstate"] == "42804"
    assert record["route"] == "/api/vouchers"
    assert record["safe_location"].startswith("test_structured_failure_log.py:")
    assert "amount_cents" not in json.dumps(record, ensure_ascii=False)


def test_driver_text_never_reaches_the_record():
    error = sqlite3.OperationalError("password=hunter2 card 6222020000000000000")
    record = failure_record(trace_id="t", method="GET", route="/api/x", error=error)
    blob = json.dumps(record, ensure_ascii=False)
    assert "hunter2" not in blob
    assert "6222020000000000000" not in blob


def test_identity_firm_is_taken_from_the_request_state_when_present():
    class State:
        identity = type("Identity", (), {"firm_id": "firm-9"})()

    class Request:
        state = State()

    assert firm_of(Request()) == "firm-9"
    assert firm_of(type("R", (), {"state": object()})()) == ""


def test_unhandled_error_logs_one_json_record_with_the_correlation_id(client, caplog):
    @client.app.api_route("/api/qa-structured-fault", methods=["POST"])
    def fail():
        raise DatatypeMismatch("column amount_cents is integer but expression is text")

    caplog.set_level(logging.ERROR)
    response = client.post(
        "/api/qa-structured-fault",
        headers={**auth(client, "accountant"), "X-Request-Id": "req-structured-1"},
    )
    assert response.status_code == 500
    assert response.headers["x-request-id"] == "req-structured-1"

    records = [r for r in _records(caplog) if r.get("route") == "/api/qa-structured-fault"]
    assert len(records) == 1
    record = records[0]
    assert record["request_id"] == "req-structured-1"
    assert record["error_class"] == "DatatypeMismatch"
    assert record["sqlstate"] == "42804"
    assert record["method"] == "POST"
    assert "amount_cents" not in json.dumps(record, ensure_ascii=False)


@pytest.mark.parametrize("header", ["bad id\u0000", "x" * 200])
def test_hostile_request_ids_are_replaced_not_logged(client, caplog, header):
    @client.app.api_route("/api/qa-structured-fault-2", methods=["GET"])
    def fail():
        raise DatatypeMismatch("boom")

    caplog.set_level(logging.ERROR)
    client.get(
        "/api/qa-structured-fault-2", headers={**auth(client, "accountant"), "X-Request-Id": header}
    )
    records = [r for r in _records(caplog) if r.get("route") == "/api/qa-structured-fault-2"]
    assert len(records) == 1
    assert len(records[0]["trace_id"]) == 32
