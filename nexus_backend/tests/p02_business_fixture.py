"""Synthetic nonzero P02 journey through HTTP contracts, with no SQL business seeding.

Opening is bank 10,000 / historical payable 10,000. A current 268.10 expense
and an independently reviewed payment leave bank 9,731.90 / payable 10,000.
Only the current payment is on the bank original; opening never becomes a flow.
"""

import hashlib
import io
import json
from uuid import uuid4
from zipfile import ZipFile

PERIOD = "2026-10"
DAY = "2026-10-08"
REASON = "P02本机虚构业务逐项核验"


def call(client, method, path, headers, payload=None, expected=200, **kwargs):
    if payload is not None:
        kwargs["json"] = payload
    response = client.request(method, path, headers=headers, **kwargs)
    assert response.status_code == expected, (
        f"{method} {path}: {response.status_code} {response.text[:1600]}"
    )
    return response


def _id(value):
    return value["id"] if isinstance(value, dict) else value


def original_pdf():
    """Frozen source bytes, never generated from a provider response or OCR result."""
    lines = (
        "invoice_number: MVP-2026-001",
        "issued_on: 2026-10-08",
        "seller_name: P02 Synthetic Supplier",
        "seller_tax_id: 913101TESTSELLER",
        "buyer_name: P02 Synthetic Buyer",
        "buyer_tax_id: 913101TESTBUYER",
        "gross_amount: 268.10",
        "net_amount: 268.10",
        "tax_amount: 0.00",
        "currency: CNY",
    )
    stream = (
        b"BT /F1 10 Tf 20 460 Td 16 TL\n"
        + b"\n".join(b"(" + line.encode("ascii") + b") Tj T*" for line in lines)
        + b"\nET"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 500] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    result, offsets = bytearray(b"%PDF-1.4\n"), []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    startxref = len(result)
    result.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(startxref).encode() + b"\n%%EOF\n"
    )
    return bytes(result)


def _resolve_originals(client, headers, event_id):
    detail = call(client, "POST", f"/api/business/events/{event_id}/reconcile", headers, {}).json()
    for finding in detail["findings"]:
        if finding["severity"] == "blocking" and not finding["resolved"]:
            # Fixture does not silently accept mismatched amounts or identities.
            assert "original_unverified" in finding["code"], finding
            call(
                client,
                "POST",
                f"/api/intelligence/findings/{finding['id']}/resolve",
                headers,
                {"reason": "合成原件仅用于测试；人工按冻结268.10元样本独立核对"},
            )


def _opening(client, h, ids, state):
    book = state["book_id"]
    ledger = "ledger_" + book
    call(
        client,
        "POST",
        f"/api/books/{book}/ledger/managers",
        h["owner"],
        {"user_id": _id(ids["users"]["reviewer"]), "reason": REASON},
        201,
    )
    policy = call(
        client,
        "POST",
        "/api/opening/policies",
        h["owner"],
        {
            "ledger_id": ledger,
            "standard_kind": "small_enterprise",
            "taxpayer_kind": "small_scale",
            "effective_from": "2026-01",
            "effective_to": "2026-12",
            "reason": REASON,
        },
        201,
    ).json()
    accounts = {}
    for code, name, category, normal in (
        ("1002", "银行存款", "asset", "debit"),
        ("2202", "应付账款", "liability", "credit"),
        ("6602.02", "管理费用—办公费", "expense", "debit"),
    ):
        accounts[code] = call(
            client,
            "POST",
            "/api/opening/accounts",
            h["owner"],
            {
                "ledger_id": ledger,
                "code": code,
                "name": name,
                "category": category,
                "normal_balance": normal,
                "effective_from": "2026-01",
                "effective_to": "2026-12",
                "reason": REASON,
            },
            201,
        ).json()
    package = call(
        client,
        "POST",
        "/api/opening/packages",
        h["owner"],
        {"ledger_id": ledger, "target_period": PERIOD, "policy_id": policy["id"]},
        201,
    ).json()
    lines = []
    for code, account in accounts.items():
        lines.append(
            {
                "account_id": account["id"],
                "account_version": 1,
                "year_opening": "0.00",
                "ytd_debit": "10000.00" if code == "1002" else "0.00",
                "ytd_credit": "10000.00" if code == "2202" else "0.00",
                "opening": "10000.00"
                if code == "1002"
                else "-10000.00"
                if code == "2202"
                else "0.00",
            }
        )
    version = call(
        client,
        "POST",
        f"/api/opening/packages/{package['package']['id']}/versions",
        h["owner"],
        {"idempotency_key": uuid4().hex, "reason": REASON, "lines": lines},
        201,
    ).json()["version"]
    path = f"/api/opening/versions/{version['id']}"
    call(client, "POST", path + "/validate", h["owner"], {})
    call(client, "POST", path + "/submit", h["owner"], {"expected_version": 1, "reason": REASON})
    for action in ("approve", "freeze"):
        call(
            client,
            "POST",
            path + "/" + action,
            h["reviewer"],
            {"expected_version": 1, "reason": REASON},
        )
    for code, account in accounts.items():
        call(
            client,
            "POST",
            "/api/opening/mappings",
            h["owner"],
            {
                "book_id": book,
                "book_account_code": code,
                "account_id": account["id"],
                "account_version": 1,
                "reason": REASON,
            },
            201,
        )
    call(
        client,
        "POST",
        "/api/posting/opening-bindings",
        h["owner"],
        {"book_id": book, "package_version_id": version["id"], "reason": REASON},
        201,
    )
    state.update(accounts=accounts, opening_version_id=version["id"])


def _report(client, h, state, *, review=True):
    report = call(
        client,
        "POST",
        "/api/reports",
        h["accountant"],
        {"book_id": state["book_id"], "period": PERIOD},
        201,
    ).json()["report"]
    call(client, "POST", f"/api/reports/{report['id']}/validate", h["accountant"], {})
    if not review:
        state.update(report_id=report["id"], report_version=report["version"])
        return report
    reviewed = call(
        client,
        "POST",
        f"/api/reports/{report['id']}/review",
        h["reviewer"],
        {"expected_version": report["version"], "reason": REASON},
    ).json()["report"]
    state.update(report_id=reviewed["id"], report_version=reviewed["version"])
    return reviewed


def _preflight(client, h, state):
    return call(
        client,
        "POST",
        "/api/b04-close/preflight",
        h["accountant"],
        {
            "book_id": state["book_id"],
            "period": PERIOD,
            "report_id": state["report_id"],
            "idempotency_key": uuid4().hex,
            "reason": REASON,
        },
        201,
    ).json()


def seed_nonzero_flow(client, headers, ids, *, stop_before_coverage_review=False):
    """Prepare a fully reconciled open period; browser may take over before close."""
    h = headers
    book, company = _id(ids["book"]), _id(ids["company"])
    state = {"book_id": book, "company_id": company, "period": PERIOD}
    fields = {
        "occurred_on": DAY,
        "description": "P02合成办公耗材",
        "counterparty": "P02 Synthetic Supplier",
        "amount": "268.10",
        "direction": "expense",
        "payment_status": "unpaid",
        "category": "office",
        "purpose": "办公耗材",
    }
    intake = call(
        client,
        "POST",
        "/api/intake/drafts",
        h["employee"],
        {
            "company_id": company,
            "department_id": _id(ids["department"]),
            "template_id": "procurement_expense",
            "fields": fields,
            "idempotency_key": uuid4().hex,
        },
        201,
    ).json()
    intake = call(
        client,
        "POST",
        f"/api/intake/drafts/{intake['id']}/attachments?expected_version={intake['version']}",
        h["employee"],
        files={"file": ("synthetic-original.pdf", original_pdf(), "application/pdf")},
    ).json()
    state["attachment_id"] = intake["attachments"][0]["id"]
    event = call(
        client,
        "POST",
        f"/api/intake/drafts/{intake['id']}/complete",
        h["employee"],
        {
            "expected_version": intake["version"],
            "payload": fields,
            "fields_confirmed": True,
            "submit": True,
            "idempotency_key": uuid4().hex,
        },
    ).json()["event"]
    state["event_id"] = event["id"]
    event_path = f"/api/business/events/{event['id']}"
    call(client, "GET", event_path, h["outsider"], expected=404)
    for action, role in (("department-approve", "department"), ("finance-approve", "finance")):
        event = call(
            client,
            "POST",
            event_path + "/" + action,
            h[role],
            {"expected_version": event["version"]},
        ).json()
    call(
        client,
        "PUT",
        f"/api/companies/{company}/intelligence/settings",
        h["owner"],
        {"enabled": True},
    )
    call(
        client,
        "POST",
        event_path + "/ocr",
        h["employee"],
        {
            "attachment_id": state["attachment_id"],
            "expected_version": event["version"],
            "idempotency_key": uuid4().hex,
        },
    )
    _resolve_originals(client, h["finance"], event["id"])
    handoff = call(
        client,
        "POST",
        event_path + "/handoff",
        h["finance"],
        {"expected_version": event["version"], "book_id": book, "idempotency_key": uuid4().hex},
    ).json()
    draft_id = state["draft_id"] = handoff["draft_id"]
    draft_path = f"/api/books/{book}/drafts/{draft_id}"
    draft = call(client, "GET", draft_path, h["accountant"]).json()
    proposal = call(
        client,
        "POST",
        draft_path + "/ai-proposals",
        h["accountant"],
        {"expected_version": draft["version"], "idempotency_key": uuid4().hex},
    ).json()
    assert proposal["status"] == "succeeded", proposal
    draft = call(client, "GET", draft_path, h["accountant"]).json()
    call(
        client,
        "POST",
        f"/api/intelligence/proposals/{proposal['id']}/apply",
        h["accountant"],
        {"expected_version": draft["version"]},
    )
    _resolve_originals(client, h["finance"], event["id"])
    draft = call(client, "GET", draft_path, h["accountant"]).json()
    call(
        client,
        "POST",
        draft_path + "/approve",
        h["accountant"],
        {"expected_version": draft["version"]},
        403,
    )
    call(
        client,
        "POST",
        f"/api/books/{book}/exports",
        h["accountant"],
        {"draft_ids": [draft_id], "idempotency_key": uuid4().hex},
        409,
    )
    approved = call(
        client,
        "POST",
        draft_path + "/approve",
        h["reviewer"],
        {"expected_version": draft["version"]},
    ).json()
    _opening(client, h, ids, state)
    voucher = call(
        client,
        "POST",
        draft_path + "/post",
        h["accountant"],
        {"command_key": uuid4().hex, "expected_version": approved["version"], "voucher_date": DAY},
        201,
    ).json()
    state["voucher_id"] = voucher["voucher"]["id"]

    accounts = state["accounts"]
    request = {
        "command_key": uuid4().hex,
        "book_id": book,
        "voucher_date": DAY,
        "description": "P02实际支付当期采购",
        "reason": REASON,
        "lines": [
            {
                "account_id": accounts[code]["id"],
                "account_version": 1,
                "debit": "268.10" if code == "2202" else "0.00",
                "credit": "268.10" if code == "1002" else "0.00",
            }
            for code in ("2202", "1002")
        ],
    }
    manual = call(client, "POST", "/api/posting/manual-requests", h["owner"], request, 201).json()[
        "request"
    ]
    policy = call(client, "GET", f"/api/books/{book}/review-policy", h["reviewer"]).json()["policy"]
    manual = call(
        client,
        "POST",
        f"/api/posting/manual-requests/{manual['id']}/approve",
        h["reviewer"],
        {
            "expected_version": manual["version"],
            "expected_policy_version": policy["version"],
            "reason": REASON,
        },
    ).json()["request"]
    payment = call(
        client,
        "POST",
        f"/api/posting/manual-requests/{manual['id']}/post",
        h["owner"],
        {"expected_version": manual["version"], "command_key": request["command_key"]},
        201,
    ).json()
    state["payment_voucher_id"] = payment["voucher"]["id"]
    _report(client, h, state)
    blocked = _preflight(client, h, state)
    assert blocked["close"]["status"] == "preflight_failed", blocked
    state["initial_blockers"] = [c["code"] for c in blocked["checks"] if c["status"] == "blocked"]
    assert set(state["initial_blockers"]) >= {"bank_reconciliation", "subledger_control"}
    for check in blocked["checks"]:
        if check["status"] != "blocked":
            continue
        assert isinstance(check["owner_role"], str) and check["owner_role"].strip()
        assert isinstance(check["next_action"], str) and check["next_action"].strip()
        target = check["action_target"]
        assert target["book_id"] == book and target["period"] == PERIOD
        assert isinstance(target["kind"], str) and target["kind"]
    subledger_blocker = next(
        check for check in blocked["checks"] if check["code"] == "subledger_control"
    )
    assert isinstance(subledger_blocker["detail"], dict), "empty rows must not erase the reason"
    assert subledger_blocker["detail"]["passed"] is False
    assert "配置" in subledger_blocker["detail"]["reason"]
    assert subledger_blocker["action_target"]["kind"] == "subledger"
    state["initial_close_id"] = blocked["close"]["id"]

    counterparty = call(
        client,
        "POST",
        f"/api/books/{book}/ledger/entities",
        h["owner"],
        {"kind": "counterparty", "code": "P02-EMPLOYEE", "name": "P02虚构供应商采购往来"},
        201,
    ).json()
    state["counterparty_id"] = counterparty["id"]
    call(
        client,
        "POST",
        "/api/subledger/control-accounts",
        h["owner"],
        {
            "book_id": book,
            "kind": "payable",
            "control_account_id": accounts["2202"]["id"],
            "control_account_version": 1,
            "effective_from": "2026-01",
            "reason": REASON,
        },
        201,
    )
    items = []
    for source_kind, source_id, amount, original_date in (
        ("opening", state["opening_version_id"], "10000.00", "2026-09-30"),
        ("voucher", state["voucher_id"], "268.10", DAY),
    ):
        items.append(
            call(
                client,
                "POST",
                "/api/subledger/items",
                h["owner"],
                {
                    "book_id": book,
                    "kind": "payable",
                    "counterparty_id": counterparty["id"],
                    "control_account_id": accounts["2202"]["id"],
                    "control_account_version": 1,
                    "original_date": original_date,
                    "amount": amount,
                    "source_kind": source_kind,
                    "source_id": source_id,
                    "source_version": 1,
                    "reason": REASON,
                },
                201,
            ).json()
        )
    call(
        client,
        "POST",
        "/api/subledger/allocations",
        h["owner"],
        {
            "book_id": book,
            "voucher_id": state["payment_voucher_id"],
            "parts": [{"item_id": items[1]["id"], "amount": "268.10"}],
            "idempotency_key": uuid4().hex,
            "reason": REASON,
        },
        201,
    )
    state["subledger_items"] = [item["id"] for item in items]

    bank = call(
        client,
        "POST",
        "/api/subledger/bank-accounts",
        h["owner"],
        {
            "book_id": book,
            "name": "P02虚构银行账户",
            "ledger_account_id": accounts["1002"]["id"],
            "ledger_account_version": 1,
            "reason": REASON,
        },
        201,
    ).json()
    state["bank_account_id"] = bank["id"]
    csv = (
        "流水号,日期,摘要,交易对方,金额,方向,原件\n"
        f"P02-PAYMENT,{DAY},P02实际支付当期采购,虚构供应商,268.10,expense,P02-payment-receipt\n"
    ).encode()
    original = call(
        client,
        "POST",
        f"/api/books/{book}/import-wizard/files",
        h["owner"],
        files={"file": ("synthetic-bank.csv", csv, "text/csv")},
        expected=201,
    ).json()
    preview = call(
        client,
        "POST",
        f"/api/books/{book}/import-wizard/previews",
        h["owner"],
        {
            "file_id": original["file_id"],
            "namespace": "p02-" + uuid4().hex,
            "bank_account": bank["id"],
            "mapping": {
                "columns": {
                    "external_id": 0,
                    "date": 1,
                    "description": 2,
                    "counterparty": 3,
                    "amount": 4,
                    "direction": 5,
                    "evidence_ref": 6,
                }
            },
        },
        201,
    ).json()
    original_read = call(
        client, "GET", f"/api/books/{book}/import-wizard/files/{original['file_id']}", h["reviewer"]
    )
    assert hashlib.sha256(original_read.content).hexdigest() == original["sha256"]
    call(
        client,
        "GET",
        f"/api/books/{book}/import-wizard/files/{original['file_id']}",
        h["outsider"],
        expected=404,
    )
    call(
        client,
        "POST",
        f"/api/books/{book}/import-wizard/previews/{preview['id']}/confirm",
        h["owner"],
        {"remember_mapping": False},
    )
    line = call(
        client,
        "POST",
        "/api/subledger/bank-lines",
        h["owner"],
        {
            "book_id": book,
            "bank_account_id": bank["id"],
            "line_date": DAY,
            "description": "P02实际支付当期采购",
            "amount": "-268.10",
        },
        201,
    ).json()
    bank_voucher_line = next(line for line in payment["lines"] if line["code_snapshot"] == "1002")
    call(
        client,
        "POST",
        f"/api/subledger/bank-lines/{line['id']}/match",
        h["owner"],
        {"voucher_line_id": bank_voucher_line["id"]},
    )
    coverage_payload = {
        "book_id": book,
        "account_id": bank["id"],
        "period": PERIOD,
        "opening": "10000.00",
        "closing": "9731.90",
        "source_file_id": original["file_id"],
        "source_preview_id": preview["id"],
        "source_sha256": original["sha256"],
        "reason": REASON,
    }
    # The rejected requests must not leave a pending declaration behind.
    for changed, status in (
        ({"period": "2026-09"}, 422),
        ({"book_id": _id(ids["other_book"])}, 404),
        ({"source_sha256": "f" * 64}, 409),
        ({"source_file_id": "nonexistent-original"}, 404),
        ({"closing": "10000.00"}, 409),
    ):
        call(
            client,
            "POST",
            "/api/subledger/bank-coverages",
            h["owner"],
            {**coverage_payload, **changed},
            status,
        )
    coverage = call(
        client, "POST", "/api/subledger/bank-coverages", h["owner"], coverage_payload, 201
    ).json()
    state.update(
        coverage_id=coverage["id"],
        coverage_payload=coverage_payload,
        bank_original_sha256=hashlib.sha256(csv).hexdigest(),
    )
    review_payload = {
        "expected_version": coverage["version"],
        "decision": "verified",
        "reason": REASON,
    }
    call(
        client,
        "POST",
        f"/api/subledger/bank-coverages/{coverage['id']}/review",
        h["reviewer"],
        {**review_payload, "expected_version": coverage["version"] + 1},
        409,
    )
    if stop_before_coverage_review:
        state["coverage_version"] = coverage["version"]
        return state
    call(
        client,
        "POST",
        f"/api/subledger/bank-coverages/{coverage['id']}/review",
        h["owner"],
        review_payload,
        403,
    )
    call(
        client,
        "POST",
        f"/api/subledger/bank-coverages/{coverage['id']}/review",
        h["reviewer"],
        review_payload,
    )
    bank_result = call(
        client, "GET", f"/api/subledger/bank-reconciliation?book_id={book}", h["reviewer"]
    ).json()
    controls = call(
        client, "GET", f"/api/subledger/control-reconciliation?book_id={book}", h["reviewer"]
    ).json()
    assert bank_result["passed"] and controls["passed"], {"bank": bank_result, "controls": controls}
    trial = call(client, "GET", f"/api/books/{book}/trial-balance", h["reviewer"]).json()
    balances = {row["code"]: row["closing_cents"] for row in trial["rows"]}
    assert balances == {"1002": 973190, "2202": -1000000, "6602.02": 26810}, balances
    state.update(bank_closing_cents=balances["1002"], payable_remaining_cents=-balances["2202"])
    return state


def complete_close_delivery(client, headers, ids, state, *, stop_after_report_validation=False):
    """Review/export, internally lock, publish, then exact customer signature."""
    h, book = headers, state["book_id"]
    draft_path = f"/api/books/{book}/drafts/{state['draft_id']}"
    draft = call(client, "GET", draft_path, h["owner"]).json()
    export = call(
        client,
        "POST",
        f"/api/books/{book}/exports",
        h["owner"],
        {"draft_ids": [draft["id"]], "idempotency_key": uuid4().hex},
        200,
    ).json()
    state["export_id"] = export["id"]
    receipt_lines = [
        {
            "ordinal": i,
            "account_code": line["account_code"],
            "debit": line["debit"],
            "credit": line["credit"],
            "auxiliary_values": {},
        }
        for i, line in enumerate(draft["lines"])
    ]
    call(
        client,
        "POST",
        f"/api/books/{book}/delivery/exports/{export['id']}/receipts",
        h["reviewer"],
        {
            "book_id": book,
            "company_id": state["company_id"],
            "period": PERIOD,
            "currency": "CNY",
            "items": [
                {
                    "draft_id": draft["id"],
                    "external_voucher_id": "P02-SYNTHETIC-001",
                    "status": "accepted",
                    "date": draft["source_date"],
                    "description": draft["description"],
                    "amount": "268.10",
                    "lines": receipt_lines,
                }
            ],
            "idempotency_key": uuid4().hex,
            "reason": "合成外部软件回执，与内部过账及客户签收分别核验",
        },
        201,
    )
    # Rebuild the reviewed report after all underlying reconciliation inputs changed.
    _report(client, h, state, review=not stop_after_report_validation)
    if stop_after_report_validation:
        return state
    run = _preflight(client, h, state)
    assert run["close"]["status"] == "preflight_passed", run
    close = run["close"]
    command = {"expected_version": close["version"], "reason": REASON}
    call(client, "POST", f"/api/b04-close/{close['id']}/review", h["reviewer"], command)
    locked = call(
        client, "POST", f"/api/b04-close/{close['id']}/lock", h["reviewer"], command
    ).json()
    state.update(close_id=close["id"], close_status=locked["close"]["status"])
    call(
        client,
        "POST",
        "/api/subledger/bank-coverages",
        h["owner"],
        {**state["coverage_payload"], "reason": "锁期后不得变更已审覆盖依据"},
        409,
    )
    report = call(
        client,
        "POST",
        f"/api/reports/{state['report_id']}/publish",
        h["reviewer"],
        {"expected_version": state["report_version"], "reason": REASON},
    ).json()["report"]
    state["report_status"] = report["status"]
    call(
        client,
        "PUT",
        f"/api/books/{book}/service-plan",
        h["owner"],
        {
            "expected_version": 0,
            "contact_user_id": _id(ids["users"]["customer"]),
            "due_on": "2026-11-10",
            "reason": REASON,
        },
    )
    # Ordinary reviewer is deliberately the notification administrator here.
    call(
        client,
        "POST",
        f"/api/books/{book}/delivery/monthly-followup/recipient",
        h["reviewer"],
        {"period": PERIOD, "recipient_user_id": _id(ids["users"]["customer"]), "reason": REASON},
        201,
    )
    package_path = f"/api/books/{book}/delivery/monthly-package"
    package = call(
        client,
        "POST",
        package_path,
        h["reviewer"],
        {
            "report_id": report["id"],
            "expected_report_version": report["version"],
            "idempotency_key": uuid4().hex,
            "reason": REASON,
        },
        201,
    ).json()
    state.update(
        package_id=package["id"],
        package_version=package["version"],
        manifest_sha256=package["manifest_sha256"],
    )
    path = package_path + "/" + package["id"]
    call(client, "GET", path, h["outsider"], expected=404)
    opened = call(client, "POST", path + "/open", h["customer"], {}).json()
    assert opened
    downloaded = call(client, "GET", path + "/download", h["customer"])
    with ZipFile(io.BytesIO(downloaded.content)) as archive:
        assert "manifest.json" in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert (
            hashlib.sha256(archive.read("manifest.json")).hexdigest() == package["manifest_sha256"]
        )
        for key, expected in (
            ("package_id", package["id"]),
            ("book_id", book),
            ("company_id", state["company_id"]),
            ("period", PERIOD),
            ("version", package["version"]),
        ):
            assert manifest[key] == expected
        for entry in manifest["files"]:
            payload = archive.read(entry["path"])
            assert len(payload) == entry["size_bytes"]
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
    call(
        client,
        "POST",
        path + "/acknowledge",
        h["customer"],
        {"manifest_sha256": "f" * 64, "reason": REASON},
        409,
    )
    acknowledged = call(
        client,
        "POST",
        path + "/acknowledge",
        h["customer"],
        {
            "manifest_sha256": package["manifest_sha256"],
            "reason": "客户已收到并核对本准确版本合成包",
        },
    ).json()
    assert acknowledged["customer_receipt_status"] == "acknowledged", acknowledged
    assert acknowledged["external_import_status"] == "separate_workflow"
    assert acknowledged["acknowledgement"]["acknowledged_by"] == _id(ids["users"]["customer"])
    state["acknowledgement"] = acknowledged
    state["customer_acknowledged"] = True
    return state
