"""PLT05-C：返回差异待办与手工回执的独立 HTTP 成功路径。"""

import base64
import time

from app.accounting.seed import LOCAL_ACTOR
from app.auth.supabase import Identity
from app.integrations.service import FinancialBridgeService
from app.main import create_app
from test_accounting import book, service  # noqa: F401
from test_business_api import local_client
from test_plt05_return_import import _artifact
from test_plt05_return_workspace import _mismatch

TENANT = "test-tenant"


def _headers(app, token, firm_id, user_id, name):
    app.state.tokens[token] = time.monotonic() + 120
    app.state.token_users[token] = Identity(user_id, "local@example.invalid", name)
    return {
        "Authorization": "Bearer " + token,
        "X-Firm-Id": firm_id,
        "X-Tenant-Id": TENANT,
    }


def test_return_tasks_http_handler_success_and_idempotent(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    intent, artifact, result = _mismatch(bridge, service, book)
    row = result["rows"][0]
    object_id = f"{result['id']}:{row['voucher_code']}"
    app = create_app(service.db.path, seed=False)
    with local_client(app, 45712) as client:
        headers = _headers(
            app, "plt05c-return-http-token", LOCAL_ACTOR.firm_id,
            LOCAL_ACTOR.user_id, "本地复核会计",
        )
        listed = client.get(
            f"/api/books/{book}/integrations/tplus/returns/tasks", headers=headers
        )
        assert listed.status_code == 200, listed.text
        tasks = listed.json()["tasks"]
        assert [task["object_id"] for task in tasks] == [object_id]
        assert tasks[0]["artifact_id"] == artifact["id"]
        assert tasks[0]["outbox_id"]
        assert tasks[0]["available_action"] == "handle_return"

        url = (
            f"/api/books/{book}/integrations/tplus/artifacts/{artifact['id']}"
            f"/returns/{result['id']}/rows/{row['voucher_code']}/handle"
        )
        body = {
            "action": "resolved",
            "reason": "已与客户确认差额并留痕",
            "evidence_ref": "synthetic://return/http-handle-1",
        }
        handled = client.post(url, headers=headers, json=body)
        assert handled.status_code == 200, handled.text
        assert handled.json()["rows"][0]["handled"] is True
        assert handled.json()["id"] == result["id"]

        again = client.post(url, headers=headers, json=body)
        assert again.status_code == 200, again.text
        assert again.json()["id"] == result["id"]

        empty = client.get(
            f"/api/books/{book}/integrations/tplus/returns/tasks", headers=headers
        )
        assert empty.status_code == 200, empty.text
        assert empty.json()["tasks"] == []


def test_manual_return_http_evidence_roundtrip(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    intent, artifact = _artifact(bridge, service, book)
    app = create_app(service.db.path, seed=False)
    with local_client(app, 45713) as client:
        headers = _headers(
            app, "plt05c-manual-http-token", "local-firm", "local-accountant",
            "本地复核会计",
        )
        base = f"/api/books/{book}/integrations/tplus/artifacts/{artifact['id']}"
        manual = client.post(
            f"{base}/returns/manual",
            headers=headers,
            json={
                "batch_code": intent["external_code"],
                "received": False,
                "posted": False,
                "evidence_ref": "synthetic://return/http-manual-1",
                "note": "客户回传纸质回单",
            },
        )
        assert manual.status_code == 201, manual.text
        import_id = manual.json()["id"]
        assert manual.json()["result_status"] == "accepted"
        assert manual.json()["received"] is False

        pdf = b"%PDF-1.4\n% synthetic manual return evidence\n%%EOF\n"
        evidence = client.post(
            f"{base}/returns/{import_id}/evidence",
            headers=headers,
            json={
                "filename": "customer-receipt.pdf",
                "media_type": "application/pdf",
                "content_base64": base64.b64encode(pdf).decode("ascii"),
            },
        )
        assert evidence.status_code == 201, evidence.text
        assert evidence.json()["size_bytes"] == len(pdf)

        download = client.get(
            f"{base}/returns/{import_id}/evidence", headers=headers
        )
        assert download.status_code == 200, download.text
        assert download.content == pdf
        assert download.headers["content-type"].startswith("application/pdf")
        assert "customer-receipt.pdf" in download.headers["content-disposition"]
        assert download.headers["cache-control"] == "no-store"

        duplicate = client.post(
            f"{base}/returns/{import_id}/evidence",
            headers=headers,
            json={
                "filename": "customer-receipt.pdf",
                "media_type": "application/pdf",
                "content_base64": base64.b64encode(pdf).decode("ascii"),
            },
        )
        assert duplicate.status_code == 409, duplicate.text

        listed = client.get(f"{base}/returns", headers=headers)
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()["returns"]] == [import_id]
