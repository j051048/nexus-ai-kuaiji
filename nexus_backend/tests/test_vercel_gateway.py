"""Synthetic HTTP transports only; no customer backend or credentials."""

import asyncio
import importlib.util
import json
from pathlib import Path
import sys

import httpx
import pytest
from fastapi.testclient import TestClient


_spec = importlib.util.spec_from_file_location(
    "isolated_vercel_gateway",
    Path(__file__).resolve().parents[2] / "services/api_gateway/main.py",
)
gateway = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = gateway
_spec.loader.exec_module(gateway)

EXPECTED_TRANSPORT_LIMITS = {
    **gateway.TRANSPORT_LIMITS,
    "direct_upload_max_bytes": gateway.UPLOAD_MAX_BYTES_DEFAULT,
}


class Chunks(httpx.AsyncByteStream):
    def __init__(self, *values):
        self.values = values

    async def __aiter__(self):
        for value in self.values:
            yield value


def upstream(body=b"ok", *, status=200, headers=None):
    return httpx.Response(status, headers=headers or {}, stream=Chunks(body))


def client(handler, *, origin="https://accounting.synthetic.test:8443", **settings):
    return TestClient(
        gateway.create_app(
            gateway.GatewaySettings(backend_origin=origin, **settings),
            transport=httpx.MockTransport(handler),
        )
    )


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_exact_method_prefix_query_body_and_only_safe_headers(method):
    requests = []

    def handle(request):
        requests.append(request)
        return upstream(
            b'{"accepted":true}',
            headers={
                "content-type": "application/json",
                "set-cookie": "private=secret",
                "cache-control": "public",
            },
        )

    with client(handle) as browser:
        response = browser.request(
            method,
            "/api/books/book%20name?offset=0&q=a%2Fb&q=x",
            content=b"original-bytes",
            headers={
                "Authorization": "Bearer synthetic-token",
                "X-Firm-Id": "synthetic-firm",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Origin": "https://frontend.synthetic.test",
                "Cookie": "private=do-not-forward",
                "X-Forwarded-Host": "attacker.synthetic.test",
                "X-Forwarded-For": "1.2.3.4",
                "Host": "attacker.synthetic.test",
            },
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert len(requests) == 1
    sent = requests[0]
    assert sent.method == method
    assert sent.url == httpx.URL(
        "https://accounting.synthetic.test:8443/api/books/book%20name?offset=0&q=a%2Fb&q=x"
    )
    assert sent.content == b"original-bytes"
    assert sent.headers["host"] == "accounting.synthetic.test:8443"
    assert sent.headers["authorization"] == "Bearer synthetic-token"
    assert sent.headers["x-firm-id"] == "synthetic-firm"
    assert sent.headers["origin"] == "https://frontend.synthetic.test"
    assert sent.headers["accept-encoding"] == "identity"
    assert (
        "cookie" not in sent.headers
        and "x-forwarded-host" not in sent.headers
        and "x-forwarded-for" not in sent.headers
    )


def test_raw_multipart_upload_is_preserved_without_parsing():
    body = b'--synthetic-boundary\r\nContent-Disposition: form-data; name="file"; filename="invoice.png"\r\nContent-Type: image/png\r\n\r\n\x89PNG\x00\xff\r\n--synthetic-boundary--\r\n'
    requests = []

    def handle(request):
        requests.append(request)
        return upstream(status=201)

    with client(handle) as browser:
        response = browser.post(
            "/api/business/events/example/attachments",
            content=body,
            headers={"content-type": "multipart/form-data; boundary=synthetic-boundary"},
        )
    assert response.status_code == 201
    assert requests[0].content == body
    assert requests[0].headers["content-type"] == "multipart/form-data; boundary=synthetic-boundary"


@pytest.mark.parametrize(
    "media_type,body",
    [
        ("application/pdf", b"%PDF-synthetic\x00\xff"),
        ("text/csv; charset=utf-8", "摘要,金额\n验证,12.00\n".encode()),
    ],
)
def test_binary_and_csv_downloads_preserve_content_and_safe_headers(media_type, body):
    with client(
        lambda request: upstream(
            body,
            headers={
                "content-type": media_type,
                "content-disposition": "attachment; filename*=UTF-8''SYNTHETIC-export.csv",
                "content-security-policy": "sandbox; default-src 'none'",
            },
        )
    ) as browser:
        response = browser.get("/api/books/example/exports/test/file")
    assert response.status_code == 200 and response.content == body
    assert response.headers["content-type"] == media_type
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["content-security-policy"].startswith("sandbox;")
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("status", [401, 403, 404, 409, 422, 429, 500, 503])
def test_backend_status_and_error_body_are_not_rewritten(status):
    body = '{"detail":"合成错误"}'.encode()
    with client(
        lambda request: upstream(
            body, status=status, headers={"content-type": "application/json", "retry-after": "30"}
        )
    ) as browser:
        response = browser.get("/api/companies/private")
    assert response.status_code == status and response.content == body
    assert response.headers["retry-after"] == "30"


def test_config_adds_platform_limits_without_modifying_backend_auth_fields():
    original = {
        "mode": "supabase",
        "supabase_url": "https://synthetic.supabase.co",
        "anon_key": "public-synthetic",
        "transport_limits": {"max_file_bytes": 999},
    }
    with client(
        lambda request: upstream(
            json.dumps(original).encode(), headers={"content-type": "application/json"}
        )
    ) as browser:
        response = browser.get("/api/auth/config")
    assert response.status_code == 200
    assert response.json() == {**original, "transport_limits": EXPECTED_TRANSPORT_LIMITS}


def test_config_exposes_custom_direct_upload_limit_separately():
    with client(
        lambda request: upstream(b'{"mode":"local"}', headers={"content-type": "application/json"}),
        upload_max_bytes=6 * 1024 * 1024,
    ) as browser:
        response = browser.get("/api/auth/config")
    limits = response.json()["transport_limits"]
    assert limits["max_file_bytes"] == gateway.MAX_FILE_BYTES
    assert limits["direct_upload_max_bytes"] == 6 * 1024 * 1024


@pytest.mark.parametrize("body", [b"null", b"[]", b"not-json", b'{"mode": NaN}', b"\xff"])
def test_invalid_auth_config_fails_safely(body):
    with client(lambda request: upstream(body)) as browser:
        response = browser.get("/api/auth/config")
    assert response.status_code == 502
    assert "登录配置" in response.json()["detail"]


def test_head_config_does_not_require_a_json_body():
    with client(
        lambda request: upstream(b"", headers={"content-type": "application/json"})
    ) as browser:
        response = browser.head("/api/auth/config")
    assert response.status_code == 200 and response.content == b""


@pytest.mark.parametrize(
    "origin",
    [
        "http://accounting.synthetic.test",
        "http://localhost:8100",
        "ftp://accounting.synthetic.test",
        "https://user:password@accounting.synthetic.test",
        "https://accounting.synthetic.test/api",
        "https://accounting.synthetic.test?target=private",
        "https://accounting.synthetic.test#private",
        "https://accounting.synthetic.test?",
        "https://accounting.synthetic.test#",
        "https://accounting.synthetic.test\\attacker",
        "https://accounting.synthetic.test:invalid",
        "https://accounting.synthetic.test:999999",
        "https:///missing",
        "https://accounting.synthetic.test\n",
        "https://exa mple.test",
        "https://%61ccounting.synthetic.test",
    ],
)
def test_invalid_server_targets_are_rejected_without_exposing_them(origin):
    with pytest.raises(ValueError) as error:
        gateway.GatewaySettings(backend_origin=origin)
    assert origin not in str(error.value)


@pytest.mark.parametrize(
    "origin", ["http://localhost:8100", "http://127.0.0.1:8100", "http://[::1]:8100"]
)
def test_http_is_only_allowed_for_explicit_local_development(origin):
    assert (
        gateway.GatewaySettings(backend_origin=origin, allow_local_http=True).backend_origin
        == origin
    )


def test_explicit_local_flag_cannot_enable_remote_http():
    with pytest.raises(ValueError):
        gateway.GatewaySettings(
            backend_origin="http://accounting.synthetic.test", allow_local_http=True
        )


def test_missing_configuration_serves_safe_503_without_an_upstream_call():
    def forbidden(request):
        pytest.fail("No request should occur without a configured origin")

    with client(forbidden, origin="") as browser:
        response = browser.get("/api/auth/config")
    assert response.status_code == 503
    assert "后端尚未配置" in response.json()["detail"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "location",
    [
        "/api/other",
        "https://accounting.synthetic.test/api/other",
        "https://attacker.synthetic.test/steal",
    ],
)
def test_no_redirect_is_followed_or_exposed(location):
    requests = []

    def handle(request):
        requests.append(request)
        return upstream(b"", status=307, headers={"location": location})

    with client(handle) as browser:
        response = browser.post(
            "/api/write", content=b"SYNTHETIC", headers={"authorization": "Bearer synthetic"}
        )
    assert response.status_code == 502
    assert "location" not in response.headers
    assert len(requests) == 1
    assert location not in response.text


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadError, httpx.ReadTimeout])
def test_transport_errors_have_no_automatic_write_retry_or_private_details(error):
    calls = []

    def handle(request):
        calls.append(request)
        raise error("private-origin-and-synthetic-token", request=request)

    with client(handle) as browser:
        response = browser.post("/api/write", content=b"SYNTHETIC")
    assert response.status_code == (504 if error is httpx.ReadTimeout else 502) and len(calls) == 1
    assert "private-origin" not in response.text
    assert "结果尚未确认" in response.json()["detail"]


def test_whole_exchange_has_a_wall_clock_timeout():
    async def handle(request):
        await asyncio.sleep(2)
        return upstream()

    with client(handle, timeout_seconds=1) as browser:
        response = browser.post("/api/write", content=b"SYNTHETIC")
    assert response.status_code == 504 and "超时" in response.json()["detail"]


@pytest.mark.parametrize("declared", [str(gateway.MAX_REQUEST_BYTES + 1), "x", "9" * 5000])
def test_request_length_rejected_before_contacting_upstream(declared):
    def forbidden(request):
        pytest.fail("Oversized or invalid requests must not reach the backend")

    with client(forbidden) as browser:
        response = browser.post(
            "/api/write", content=b"SYNTHETIC", headers={"content-length": declared}
        )
    assert response.status_code in (400, 413)


def test_unknown_length_request_is_bounded_during_asgi_streaming():
    def forbidden(request):
        pytest.fail("Oversized stream must not reach the backend")

    with client(forbidden) as browser:
        response = browser.post(
            "/api/write", content=iter([b"a" * gateway.MAX_REQUEST_BYTES, b"b"])
        )
    assert response.status_code == 413


def test_request_exact_limit_is_forwarded():
    with client(lambda request: upstream(str(len(request.content)).encode())) as browser:
        response = browser.post("/api/write", content=b"a" * gateway.MAX_REQUEST_BYTES)
    assert response.status_code == 200 and response.text == str(gateway.MAX_REQUEST_BYTES)


def test_upload_content_route_streams_beyond_general_request_limit():
    payload = b"x" * (5 * 1024 * 1024)
    seen = {}

    async def handle(request):
        seen["body"] = request.content
        return upstream(b'{"state":"stored"}')

    with client(handle, upload_max_bytes=6 * 1024 * 1024) as browser:
        response = browser.put(f"/api/uploads/{'a' * 32}/content", content=payload)
    assert response.status_code == 200
    assert len(seen["body"]) == len(payload)


def test_upload_route_rejects_above_stream_limit_before_upstream():
    def forbidden(request):
        pytest.fail("Oversized uploads must not reach the backend")

    with client(forbidden, upload_max_bytes=1024 * 1024) as browser:
        response = browser.put(f"/api/uploads/{'a' * 32}/content", content=b"x" * (1024 * 1024 + 1))
    assert response.status_code == 413


def test_only_exact_upload_content_route_uses_streaming_limit():
    def forbidden(request):
        pytest.fail("Only the reviewed upload route may exceed the general limit")

    with client(forbidden, upload_max_bytes=6 * 1024 * 1024) as browser:
        for path in (
            f"/api/uploads/{'a' * 31}/content",
            f"/api/uploads/{'g' * 32}/content",
            f"/api/uploads/{'a' * 32}/content/extra",
        ):
            response = browser.put(path, content=b"x" * (gateway.MAX_REQUEST_BYTES + 1))
            assert response.status_code == 413, path


@pytest.mark.parametrize("declared", [str(gateway.MAX_RESPONSE_BYTES + 1), "invalid", "9" * 5000])
def test_upstream_content_length_is_bounded(declared):
    with client(
        lambda request: upstream(b"small", headers={"content-length": declared})
    ) as browser:
        response = browser.get("/api/export")
    assert response.status_code == 502


def test_unknown_length_upstream_stream_is_bounded():
    with client(
        lambda request: httpx.Response(200, stream=Chunks(b"a" * gateway.MAX_RESPONSE_BYTES, b"b"))
    ) as browser:
        response = browser.get("/api/export")
    assert response.status_code == 502


def test_response_exact_limit_is_forwarded():
    body = b"a" * gateway.MAX_RESPONSE_BYTES
    with client(lambda request: upstream(body)) as browser:
        response = browser.get("/api/export")
    assert response.status_code == 200 and response.content == body


def test_unrequested_upstream_compression_is_rejected():
    with client(
        lambda request: upstream(b"compressed", headers={"content-encoding": "gzip"})
    ) as browser:
        response = browser.get("/api/export")
    assert response.status_code == 502 and "传输格式" in response.json()["detail"]


@pytest.mark.parametrize("path", ["/api/%2e%2e/admin", "/api/a/%2e%2e/admin", "/api/%2e/admin"])
def test_encoded_dot_segments_cannot_remove_public_api_prefix(path):
    def forbidden(request):
        pytest.fail("Invalid API paths must not reach the backend")

    with client(forbidden) as browser:
        response = browser.get(path)
    assert response.status_code == 400


def test_unexposed_paths_and_docs_are_not_proxied():
    def forbidden(request):
        pytest.fail("Only /api routes may reach the backend")

    with client(forbidden) as browser:
        for path in ["/", "/docs", "/openapi.json", "/admin", "/apis"]:
            assert browser.get(path).status_code == 404


def test_settings_reads_only_explicit_server_environment(monkeypatch):
    monkeypatch.setenv("ACCOUNTING_BACKEND_ORIGIN", "http://127.0.0.1:8100/")
    monkeypatch.setenv("ACCOUNTING_GATEWAY_ALLOW_LOCAL_HTTP", "1")
    monkeypatch.setenv("ACCOUNTING_GATEWAY_TIMEOUT_SECONDS", "95")
    settings = gateway.GatewaySettings.from_env()
    assert settings.backend_origin == "http://127.0.0.1:8100"
    assert settings.allow_local_http is True
    assert settings.timeout_seconds == 95
    assert "127.0.0.1" not in repr(settings)


@pytest.mark.parametrize("value", ["invalid", "nan", "111", "0"])
def test_invalid_timeout_environment_is_rejected_safely(monkeypatch, value):
    monkeypatch.setenv("ACCOUNTING_GATEWAY_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError):
        gateway.GatewaySettings.from_env()


@pytest.mark.parametrize(
    "headers",
    [
        [("authorization", "Bearer synthetic-1"), ("authorization", "Bearer synthetic-2")],
        [("x-firm-id", "synthetic-firm-1"), ("x-firm-id", "synthetic-firm-2")],
        [("authorization", "a" * 8193)],
        [("authorization", "Bearer synthetic\x00invalid")],
    ],
)
def test_ambiguous_or_invalid_identity_headers_do_not_reach_backend(headers):
    def forbidden(request):
        pytest.fail("Invalid identity headers must not reach the backend")

    with client(forbidden) as browser:
        response = browser.get("/api/auth/config", headers=headers)
    assert response.status_code == 400


@pytest.mark.parametrize(
    "host", ["[", "attacker.synthetic.test", "https://attacker.synthetic.test"]
)
def test_browser_host_is_never_parsed_to_construct_upstream_target(host):
    with client(lambda request: upstream(str(request.url).encode())) as browser:
        response = browser.get("/api/health", headers={"host": host})
    assert response.status_code == 200
    assert response.text == "https://accounting.synthetic.test:8443/api/health"


@pytest.mark.parametrize("recursion_limit", [1000, 10000])
def test_deeply_nested_login_configuration_fails_safely(recursion_limit):
    body = b'{"nested":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}"
    previous = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(recursion_limit)
        with client(lambda request: upstream(body)) as browser:
            response = browser.get("/api/auth/config")
    finally:
        sys.setrecursionlimit(previous)
    assert response.status_code == 502


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
@pytest.mark.parametrize("container", ["array", "object"])
@pytest.mark.parametrize("extra_depth,status", [(0, 200), (1, 502)])
def test_login_configuration_has_a_fixed_container_depth_limit(
    encoding, container, extra_depth, status
):
    depth = gateway.MAX_AUTH_CONFIG_DEPTH - 1 + extra_depth
    start, end = ("[", "]") if container == "array" else ('{"value":', "}")
    body = ('{"nested":' + start * depth + "0" + end * depth + "}").encode(encoding)
    with client(lambda request: upstream(body)) as browser:
        response = browser.get("/api/auth/config")
    assert response.status_code == status
    if status == 200:
        assert response.json()["transport_limits"] == EXPECTED_TRANSPORT_LIMITS


def test_login_configuration_depth_does_not_count_brackets_inside_strings():
    original = {"mode": "supabase", "nested": {"description": '[{\\"}]' * 1500}}
    with client(lambda request: upstream(json.dumps(original).encode())) as browser:
        response = browser.get("/api/auth/config")
    assert response.status_code == 200
    assert response.json() == {**original, "transport_limits": EXPECTED_TRANSPORT_LIMITS}


def test_request_reading_shares_whole_exchange_deadline_and_is_not_forwarded():
    def forbidden(request):
        pytest.fail("Timed-out body must not reach the backend")

    application = gateway.create_app(
        gateway.GatewaySettings(
            backend_origin="https://accounting.synthetic.test", timeout_seconds=1
        ),
        transport=httpx.MockTransport(forbidden),
    )
    messages = []

    async def run():
        async def receive():
            await asyncio.sleep(2)
            return {"type": "http.request", "body": b"SYNTHETIC", "more_body": False}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/write",
            "raw_path": b"/api/write",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("synthetic.test", 443),
        }
        async with application.router.lifespan_context(application):
            await application(scope, receive, send)

    asyncio.run(run())
    assert messages[0]["status"] == 504
    assert "尚未发送" in json.loads(messages[1]["body"])["detail"]


def test_backend_origin_cannot_be_selected_by_browser_query_or_headers():
    with client(lambda request: upstream(str(request.url).encode())) as browser:
        response = browser.get(
            "/api/health?backend_origin=https%3A%2F%2Fattacker.synthetic.test",
            headers={
                "accounting-backend-origin": "https://attacker.synthetic.test",
            },
        )
    assert response.status_code == 200
    assert response.text.startswith("https://accounting.synthetic.test:8443/api/health?")
