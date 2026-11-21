"""Batch 04: the connector must be reproducible, and must never double-file."""

import json

import pytest

from app.tax import etax_connector


def declaration(**kw):
    base = {
        "kind": "vat",
        "period": "2026-10",
        "lines": [{"account": "2221.01", "cents": 12_345}],
    }
    return {**base, **kw}


def test_same_declaration_produces_same_bytes_and_digest():
    first = etax_connector.build_package(declaration())
    second = etax_connector.build_package(declaration())
    assert first["content"] == second["content"]
    assert first["sha256"] == second["sha256"]
    assert first["filename"].endswith(".json")
    body = json.loads(first["content"].decode("utf-8"))
    assert body["schema"] == "accounting.etax.declaration/v1"


def test_manifest_states_it_is_not_a_final_declaration():
    manifest = etax_connector.build_package(declaration())["manifest"]
    assert manifest["note"] == "未经电子税务局核定，仅作复核底稿"
    assert manifest["kind"] == "vat" and manifest["period"] == "2026-10"


def test_float_amount_is_refused():
    bad = declaration(lines=[{"cents": 12.5}])
    with pytest.raises(etax_connector.EtaxError):
        etax_connector.validate_declaration(bad)


@pytest.mark.parametrize(
    "bad",
    [
        declaration(kind="property_tax"),
        declaration(period="2026-9"),
        declaration(lines=[]),
        "not-an-object",
    ],
)
def test_invalid_declarations_are_refused(bad):
    with pytest.raises(etax_connector.EtaxError):
        etax_connector.validate_declaration(bad)


def test_submit_then_poll_reaches_terminal_state():
    connector = etax_connector.SandboxConnector()
    receipt = connector.submit(declaration())
    assert receipt["state"] == "submitted"
    final = connector.poll(receipt["receipt_id"])
    assert final["state"] == "accepted"
    assert connector.poll(receipt["receipt_id"])["state"] == "accepted"


def test_unknown_receipt_blocks_resubmission_until_resolved():
    connector = etax_connector.SandboxConnector()
    receipt = connector.submit(declaration(sandbox_outcome="unknown"))
    assert connector.poll(receipt["receipt_id"])["state"] == "unknown"
    with pytest.raises(etax_connector.EtaxError, match="禁止自动重报"):
        etax_connector.assert_can_submit("unknown")
    resolved = connector.resolve_unknown(receipt["receipt_id"], outcome="accepted")
    assert resolved["state"] == "accepted"
    with pytest.raises(etax_connector.TransitionError):
        connector.resolve_unknown(receipt["receipt_id"], outcome="rejected")


def test_submitting_a_non_prepared_state_is_refused():
    with pytest.raises(etax_connector.TransitionError):
        etax_connector.assert_can_submit("accepted")
    connector = etax_connector.SandboxConnector()
    with pytest.raises(etax_connector.TransitionError):
        connector.submit(declaration(), state="submitted")


def test_unknown_cannot_be_resolved_to_unknown():
    connector = etax_connector.SandboxConnector()
    receipt = connector.submit(declaration(sandbox_outcome="unknown"))
    connector.poll(receipt["receipt_id"])
    with pytest.raises(etax_connector.EtaxError):
        connector.resolve_unknown(receipt["receipt_id"], outcome="unknown")


def test_receipt_id_is_stable_for_the_same_declaration():
    a = etax_connector.SandboxConnector().submit(declaration())
    b = etax_connector.SandboxConnector().submit(declaration())
    assert a["receipt_id"] == b["receipt_id"]
    other = etax_connector.SandboxConnector().submit(declaration(period="2026-11"))
    assert other["receipt_id"] != a["receipt_id"]
