import base64

from test_business_api import client  # noqa: F401


def test_manual_evidence_http_fails_closed_without_probe(client):  # noqa: F811
    headers = {"Authorization": "Bearer " + client.get('/api/session?persona=accountant').json()['token']}
    payload = {
        "filename": "回执.pdf",
        "media_type": "application/pdf",
        "content_base64": base64.b64encode(b"%PDF-1.4 synthetic").decode(),
    }
    missing = client.post(
        "/api/books/not-a-book/integrations/tplus/artifacts/a/returns/r/evidence",
        json=payload, headers=headers,
    )
    assert missing.status_code == 404
    bad = client.post(
        "/api/books/not-a-book/integrations/tplus/artifacts/a/returns/r/evidence",
        json={**payload, "content_base64": "not-base64!"}, headers=headers,
    )
    assert bad.status_code == 422
    download = client.get(
        "/api/books/not-a-book/integrations/tplus/artifacts/a/returns/r/evidence",
        headers=headers,
    )
    assert download.status_code == 404
