"""PLT05-A: formal book ownership and three independent external states."""

import pytest
from pydantic import ValidationError

from app.accounting.seed import LOCAL_ACTOR
from app.integrations import tplus54
from app.integrations.models import BridgeReceiptImport
from app.opening.models import PolicyCreate
from app.reports.service import ReportService
from test_accounting import service, book  # noqa: F401
from test_business import business  # noqa: F401
from test_plt02_report_modes import _prepared
from test_tax08_tplus54 import configured, frozen_intent, receipt


def test_external_states_are_not_inferred_from_export(service, book):
    bridge = configured(service, book)
    intent = frozen_intent(service, book, bridge)

    pending = bridge.get_outbox(LOCAL_ACTOR, book, intent["id"])
    assert pending["result_summary"] == "not_exported"
    assert pending["statuses"]["exported"]["at"] is None
    assert pending["statuses"]["received"]["at"] is None
    assert pending["statuses"]["posted"]["at"] is None
    assert pending["result_unknown"] is False

    artifact = bridge.create_artifact(LOCAL_ACTOR, book, intent["id"])
    exported = bridge.get_outbox(LOCAL_ACTOR, book, intent["id"])
    assert exported["result_summary"] == "result_unknown"
    assert exported["statuses"]["exported"]["at"]
    assert exported["statuses"]["received"]["at"] is None
    assert exported["statuses"]["posted"]["at"] is None
    assert exported["result_unknown"] is True
    listed = next(row for row in bridge.overview(LOCAL_ACTOR, book)["outbox"] if row["id"] == intent["id"])
    assert listed["statuses"] == exported["statuses"]
    assert listed["result_summary"] == "result_unknown"

    codes = tplus54.build_rows(intent["package"])[1]
    bridge.record_receipt(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        BridgeReceiptImport(receipt_code="PLT05-A-1", content=receipt(codes), reason="客户回传结果"),
    )
    received = bridge.get_outbox(LOCAL_ACTOR, book, intent["id"])
    assert received["result_summary"] == "received"
    assert received["statuses"]["received"]["at"]
    assert received["statuses"]["posted"]["at"] is None
    assert received["result_unknown"] is False

    # Manual posting registration is PLT05-B; export and receipt must never
    # imply that the external system posted it.
    assert received["statuses"]["posted"]["at"] is None


def test_overview_reports_unassessed_without_policy(service, book):
    bridge = configured(service, book)
    view = bridge.overview(LOCAL_ACTOR, book)
    assert view["formal_book"] == {
        "system": "unassessed",
        "name": "",
        "policy_version": None,
        "approved": False,
    }


def test_external_policy_is_frozen_into_formal_report(business):
    case = business
    _, policy = _prepared(case)
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "UPDATE accounting_policies SET formal_book_system='external',formal_book_system_name=? WHERE id=?",
            ("畅捷通 T+", policy["id"]),
        )
    report = ReportService(case.db).create(case.manager, case.book["id"], "2026-10")["report"]
    assert report["mode"] == "formal"
    assert report["formal_book_system"] == "external"
    assert report["formal_book_system_name"] == "畅捷通 T+"


def test_unassessed_formal_book_forces_trial(business):
    case = business
    _, policy = _prepared(case)
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "UPDATE accounting_policies SET formal_book_system='unassessed',formal_book_system_name='' WHERE id=?",
            (policy["id"],),
        )
    report = ReportService(case.db).create(case.manager, case.book["id"], "2026-10")
    assert report["report"]["mode"] == "trial"
    assert "正式账所在系统未核定" in "".join(report["report"]["trial_reasons"])


def test_formal_book_policy_validation():
    with pytest.raises(ValidationError, match="外部正式账必须写明系统名称"):
        PolicyCreate(
            ledger_id="ledger-1",
            standard_kind="small_enterprise",
            taxpayer_kind="small_scale",
            effective_from="2026-01",
            reason="PLT05 校验",
            formal_book_system="external",
        )
    with pytest.raises(ValidationError, match="仅外部正式账可以填写系统名称"):
        PolicyCreate(
            ledger_id="ledger-1",
            standard_kind="small_enterprise",
            taxpayer_kind="small_scale",
            effective_from="2026-01",
            reason="PLT05 校验",
            formal_book_system="local",
            formal_book_system_name="T+",
        )
