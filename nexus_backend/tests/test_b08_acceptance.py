"""B08 independent comparator and non-authorizing switch package tests."""

from copy import deepcopy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import b08_acceptance as acceptance
import b08_switch_package as switch


def dataset():
    return {
        "company_id": "PILOT-A",
        "ledger_id": "LEDGER-A",
        "data_version": "v1",
        "periods": ["2026-10", "2026-11"],
        "documents": [
            {
                "match_key": "D1",
                "period": "2026-10",
                "date": "2026-10-05",
                "account_code": "1002",
                "aux": {},
                "debit_cents": 10000,
                "credit_cents": 0,
            },
            {
                "match_key": "D2",
                "period": "2026-10",
                "date": "2026-10-05",
                "account_code": "6602",
                "aux": {"department": "ops"},
                "debit_cents": 10000,
                "credit_cents": 0,
            },
        ],
        "balances": [
            {
                "period": "2026-10",
                "account_code": "1002",
                "opening_cents": 0,
                "debit_cents": 10000,
                "credit_cents": 0,
                "closing_cents": 10000,
            },
            {
                "period": "2026-10",
                "account_code": "6602",
                "aux": {"department": "ops"},
                "opening_cents": 0,
                "debit_cents": 10000,
                "credit_cents": 0,
                "closing_cents": 10000,
            },
            {
                "period": "2026-11",
                "account_code": "1002",
                "opening_cents": 10000,
                "debit_cents": 0,
                "credit_cents": 0,
                "closing_cents": 10000,
            },
            {
                "period": "2026-11",
                "account_code": "6602",
                "aux": {"department": "ops"},
                "opening_cents": 10000,
                "debit_cents": 0,
                "credit_cents": 0,
                "closing_cents": 10000,
            },
        ],
        "reports": [
            {
                "period": "2026-10",
                "report_id": "bs",
                "line": "cash",
                "amount_cents": 10000,
                "source_version": "v1",
                "status": "published",
            }
        ],
        "receipts": [
            {
                "period": "2026-10",
                "receipt_id": "R1",
                "status": "accepted",
                "source": "vendor_api",
                "external_id": "E1",
            }
        ],
    }


def compare(a, b):
    return acceptance.compare(a, b)


def test_identical_datasets_pass():
    assert compare(dataset(), deepcopy(dataset()))["status"] == "pass"


def test_equal_closing_but_wrong_debit_credit_is_blocked():
    candidate = deepcopy(dataset())
    candidate["balances"][0]["debit_cents"] = 9000
    candidate["balances"][0]["credit_cents"] = -1000  # integer; mismatch regardless
    result = compare(dataset(), candidate)
    assert result["status"] == "blocked"
    assert any(item["code"] == "value_mismatch" for item in result["issues"])


def test_missing_and_extra_net_offset_is_blocked():
    candidate = deepcopy(dataset())
    candidate["documents"] = [candidate["documents"][0]]
    candidate["documents"].append({**dataset()["documents"][1], "match_key": "D-EXTRA"})
    result = compare(dataset(), candidate)
    assert result["status"] == "blocked"
    assert {item["code"] for item in result["issues"]} >= {"missing", "extra"}


def test_auxiliary_difference_is_blocked():
    candidate = deepcopy(dataset())
    candidate["documents"][1]["aux"] = {"department": "sales"}
    assert compare(dataset(), candidate)["status"] == "blocked"


def test_date_difference_is_blocked():
    candidate = deepcopy(dataset())
    candidate["documents"][0]["date"] = "2026-10-06"
    assert compare(dataset(), candidate)["status"] == "blocked"


def test_period_outside_scope_is_blocked():
    candidate = deepcopy(dataset())
    candidate["documents"][0]["period"] = "2026-12"
    assert any(
        item["code"] == "period_out_of_scope" for item in compare(dataset(), candidate)["issues"]
    )


def test_opening_duplicated_as_voucher_is_blocked():
    candidate = deepcopy(dataset())
    candidate["documents"].append(
        {
            "match_key": "OPENING-DUP",
            "period": "2026-10",
            "date": "2026-10-01",
            "account_code": "1002",
            "aux": {},
            "debit_cents": 50000,
            "credit_cents": 0,
            "kind": "opening",
        }
    )
    assert any(
        item["code"] == "opening_duplicate" for item in compare(dataset(), candidate)["issues"]
    )


def test_period_transition_is_blocked():
    candidate = deepcopy(dataset())
    candidate["balances"][2]["opening_cents"] = 9000
    result = compare(dataset(), candidate)
    assert any(item["code"] == "period_transition" for item in result["issues"])


def test_stale_published_report_is_blocked():
    candidate = deepcopy(dataset())
    candidate["reports"][0]["source_version"] = "v0"
    assert any(item["code"] == "stale_report" for item in compare(dataset(), candidate)["issues"])


def test_manual_receipt_cannot_be_vendor_confirmed():
    candidate = deepcopy(dataset())
    candidate["receipts"][0].update({"status": "vendor_confirmed", "source": "manual_upload"})
    assert any(
        item["code"] == "manual_receipt_as_vendor"
        for item in compare(dataset(), candidate)["issues"]
    )


def test_cross_tenant_record_is_blocked():
    candidate = deepcopy(dataset())
    candidate["documents"][0]["company_id"] = "PILOT-B"
    assert any(item["code"] == "cross_scope" for item in compare(dataset(), candidate)["issues"])


def test_version_change_invalidates_comparison():
    candidate = deepcopy(dataset())
    candidate["data_version"] = "v2"
    assert any(
        item["code"] == "version_mismatch" for item in compare(dataset(), candidate)["issues"]
    )


def _manifest(gates=None):
    gate_names = list(switch.REQUIRED_GATES)
    values = {name: "passed" for name in gate_names}
    if gates:
        values.update(gates)
    return {
        "scope": {
            "company_id": "PILOT-A",
            "ledger_id": "LEDGER-A",
            "start_period": "2026-10",
            "mode": "internal_ledger",
        },
        "gates": values,
        "evidence": [
            {"gate": name, "ref": f"evidence/{name}.json", "sha256": "a" * 64}
            for name in gate_names
        ],
        "freeze": {
            "data_boundary": "after 2026-09 close",
            "freeze_at": "2026-10-01T00:00:00+08:00",
            "program_version": "b08-local",
            "migration_version": "pg107/sqlite082",
        },
        "not_applicable": {},
    }


def test_switch_missing_gate_is_blocked_and_never_approved():
    package = switch.evaluate(_manifest({"b03_posting": "missing"}))
    assert package["status"] == "blocked"
    assert package["status"] != "approved"


def test_switch_all_gates_without_signature_is_only_ready_for_authorization():
    package = switch.evaluate(_manifest())
    assert package["status"] == "ready_for_authorization"
    assert "approved" not in package["status"]
    assert package["package_fingerprint"]


def test_switch_not_applicable_requires_scope_reason():
    manifest = _manifest({"b05_ai_scope": "not_applicable"})
    package = switch.evaluate(manifest)
    assert package["status"] == "blocked"
    manifest["not_applicable"]["b05_ai_scope"] = "本试点不启用任何 AI 场景"
    assert switch.evaluate(manifest)["status"] == "ready_for_authorization"
