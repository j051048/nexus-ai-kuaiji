"""NEXT03 authenticated security probes against the isolated local stack.

The script only talks to loopback services created for this task. It reads test
credentials from the private fixture, never prints them, and writes a sanitized
report that contains status codes and booleans only.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _login(client: httpx.Client, origin: str, account: dict) -> str:
    response = client.post(
        f"{origin}/token",
        params={"grant_type": "password"},
        json={"email": account["email"], "password": account["password"]},
    )
    if response.status_code != 200:
        raise RuntimeError(f"login failed with {response.status_code}")
    return response.json()["access_token"]


def _png(width: int, height: int) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (width, height), (255, 255, 255)).save(stream, format="PNG")
    return stream.getvalue()


def _record(results: list[dict], name: str, response: httpx.Response, *, expect: int | set[int]):
    expected = {expect} if isinstance(expect, int) else expect
    results.append(
        {
            "probe": name,
            "status": response.status_code,
            "expected": sorted(expected),
            "passed": response.status_code in expected,
            "cache_control": response.headers.get("cache-control"),
            "content_type_options": response.headers.get("x-content-type-options"),
            "referrer_policy": response.headers.get("referrer-policy"),
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--api", default="http://127.0.0.1:18100")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    fixture = _load(Path(args.fixture))
    auth_origin = fixture["auth_local_origin"]
    desktop = fixture["cases"]["desktop"]
    mobile = fixture["cases"]["mobile"]
    api = args.api.rstrip("/")
    results: list[dict] = []

    with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
        reviewer = _login(client, auth_origin, desktop["accounts"]["reviewer"])
        maker = _login(client, auth_origin, desktop["accounts"]["maker"])
        client_token = _login(client, auth_origin, desktop["accounts"]["client"])
        outsider = _login(client, auth_origin, desktop["accounts"]["foreign"])

        headers = lambda token: {"Authorization": f"Bearer {token}"}
        _record(results, "api_requires_bearer", client.get(f"{api}/api/workspace/bootstrap"), expect=401)
        _record(
            results,
            "api_rejects_forged_bearer",
            client.get(f"{api}/api/workspace/bootstrap", headers=headers("forged.token.value")),
            expect=401,
        )
        _record(
            results,
            "own_book_readable",
            client.get(
                f"{api}/api/books/{desktop['book_id']}/trial-balance",
                headers=headers(reviewer),
            ),
            expect=200,
        )
        for name, path in [
            ("foreign_trial_balance", f"/api/books/{mobile['book_id']}/trial-balance"),
            ("foreign_reports", f"/api/reports?book_id={mobile['book_id']}"),
            (
                "foreign_monthly_package",
                f"/api/books/{mobile['book_id']}/delivery/monthly-package",
            ),
            (
                "foreign_attachment",
                f"/api/business/attachments/{mobile['attachment_id']}/content",
            ),
        ]:
            _record(
                results,
                name,
                client.get(f"{api}{path}", headers=headers(reviewer)),
                expect=404,
            )
        _record(
            results,
            "outsider_cannot_read_book",
            client.get(
                f"{api}/api/books/{desktop['book_id']}/trial-balance",
                headers=headers(outsider),
            ),
            expect=404,
        )
        _record(
            results,
            "client_cannot_review",
            client.post(
                f"{api}/api/reports/{desktop['report_id']}/review",
                headers=headers(client_token),
                json={"expected_version": desktop["report_version"], "reason": "probe"},
            ),
            expect={403, 404},
        )
        _record(
            results,
            "client_own_original_readable",
            client.get(
                f"{api}/api/business/attachments/{desktop['attachment_id']}/content",
                headers=headers(client_token),
            ),
            expect=200,
        )
        _record(
            results,
            "client_foreign_original_denied",
            client.get(
                f"{api}/api/business/attachments/{mobile['attachment_id']}/content",
                headers=headers(client_token),
            ),
            expect=404,
        )
        _record(
            results,
            "repeated_review_conflicts",
            client.post(
                f"{api}/api/reports/{desktop['report_id']}/review",
                headers=headers(reviewer),
                json={"expected_version": desktop["report_version"], "reason": "probe"},
            ),
            expect=409,
        )

        created = client.post(
            f"{api}/api/business/events",
            headers=headers(client_token),
            json={
                "company_id": desktop["company_id"],
                "template_id": "reimbursement",
                "payload": {
                    "occurred_on": "2026-10-31",
                    "description": "NEXT03 隔离探测",
                    "counterparty": "本地测试",
                    "amount": "1.00",
                    "direction": "expense",
                    "payment_status": "unpaid",
                    "category": "office",
                    "purpose": "安全探测",
                    "project": "NEXT03",
                },
            },
        )
        if created.status_code != 201:
            raise RuntimeError(f"probe event creation failed with {created.status_code}")
        probe_event = created.json()
        event_url = f"{api}/api/business/events/{probe_event['id']}"
        version = probe_event["version"]
        upload_headers = headers(client_token)
        sources = {
            "svg_upload": (
                "probe.svg",
                b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                "image/svg+xml",
            ),
            "png_that_is_svg": (
                "probe.png",
                b'<svg onload="alert(1)"></svg>',
                "image/png",
            ),
            "pdf_that_is_html": (
                "probe.pdf",
                b"<html><script>alert(1)</script></html>",
                "application/pdf",
            ),
            "xml_external_entity": (
                "probe.xml",
                b'<!DOCTYPE invoice [<!ENTITY x SYSTEM "file:///etc/passwd">]><invoice>&x;</invoice>',
                "application/xml",
            ),
        }
        for name, (filename, content, mime) in sources.items():
            response = client.post(
                f"{event_url}/attachments",
                params={"expected_version": version},
                headers=upload_headers,
                files={"file": (filename, content, mime)},
            )
            _record(results, name, response, expect=422)
        oversized = client.post(
            f"{event_url}/attachments",
            params={"expected_version": version},
            headers=upload_headers,
            files={"file": ("probe.png", b"\x89PNG\r\n\x1a\n" + b"0" * (10 * 1024 * 1024), "image/png")},
        )
        _record(results, "oversized_upload", oversized, expect=413)
        huge_dimensions = _png(6000, 6000)
        response = client.post(
            f"{event_url}/attachments",
            params={"expected_version": version},
            headers=upload_headers,
            files={"file": ("huge.png", huge_dimensions, "image/png")},
        )
        _record(results, "decompression_bomb", response, expect={413, 422})

        login_codes = []
        for _ in range(12):
            response = client.post(
                f"{auth_origin}/token",
                params={"grant_type": "password"},
                json={"email": "unknown-next03@example.test", "password": "wrong-password"},
            )
            login_codes.append(response.status_code)
        forged = client.post(
            f"{auth_origin}/token",
            params={"grant_type": "password"},
            headers={"X-Forwarded-For": "203.0.113.77"},
            json={"email": "unknown-next03@example.test", "password": "wrong-password"},
        )
        valid_after = client.post(
            f"{auth_origin}/token",
            params={"grant_type": "password"},
            json={
                "email": desktop["accounts"]["reviewer"]["email"],
                "password": desktop["accounts"]["reviewer"]["password"],
            },
        )
        results.append(
            {
                "probe": "login_failure_codes",
                "codes": login_codes,
                "forged_header_status": forged.status_code,
                "throttled": 429 in login_codes,
                "unrelated_valid_login_after_failures": valid_after.status_code,
            }
        )

        report = {
            "schema": "next03-security-probe-v1",
            "api": urlparse(api).netloc,
            "auth": urlparse(auth_origin).netloc,
            "probes": results,
            "all_passed": all(item.get("passed", True) for item in results),
        }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"passed": report["all_passed"], "count": len(results)}))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
