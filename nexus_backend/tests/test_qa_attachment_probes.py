"""Application-level dynamic security probes through authenticated HTTP routes."""

import pytest

from test_business_api import auth, client, create, attachment_bytes  # noqa: F401


@pytest.mark.parametrize(
    "name,mime,data",
    [
        (
            "invoice.svg",
            "image/svg+xml",
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        ),
        ("invoice.png", "image/png", b'<svg onload="alert(1)"></svg>'),
        ("invoice.pdf", "application/pdf", b"<html><script>alert(1)</script></html>"),
        (
            "invoice.xml",
            "application/xml",
            b'<!DOCTYPE invoice [<!ENTITY x SYSTEM "file:///secret">]><invoice>&x;</invoice>',
        ),
    ],
)
def test_malicious_upload_never_changes_parent_or_stores_attachment(client, name, mime, data):
    employee = auth(client, "employee")
    event = create(client, employee)
    url = f"/api/business/events/{event['id']}"
    response = client.post(
        url + "/attachments?expected_version=1",
        headers=employee,
        files={"file": (name, data, mime)},
    )
    assert response.status_code == 422
    current = client.get(url, headers=employee).json()
    assert current["version"] == 1 and current["attachments"] == []


def test_safe_original_download_has_no_inline_execution_or_path_disclosure(client):
    employee = auth(client, "employee")
    event = create(client, employee)
    response = client.post(
        f"/api/business/events/{event['id']}/attachments?expected_version=1",
        headers=employee,
        files={"file": ("../../private.png", attachment_bytes(), "image/png")},
    )
    assert response.status_code == 200
    (metadata,) = response.json()["attachments"]
    assert metadata["filename"] == "private.png"
    assert "storage_key" not in metadata
    url = f"/api/business/attachments/{metadata['id']}/content"
    downloaded = client.get(url, headers=employee)
    assert downloaded.status_code == 200
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in downloaded.headers["content-security-policy"]
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert client.get(url, headers=auth(client, "accountant")).status_code == 404
    assert client.get(url).status_code == 401
