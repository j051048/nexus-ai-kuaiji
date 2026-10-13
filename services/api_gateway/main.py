"""Bounded same-origin proxy for the Vercel frontend.

Accounting, durable files and workers remain on the configured external server.
This service never imports the accounting application or follows redirects.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json
import os
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
import httpx
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, Response


MAX_FILE_BYTES = 3 * 1024 * 1024
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
TRANSPORT_LIMITS = {
    "max_file_bytes": MAX_FILE_BYTES,
    "max_request_bytes": MAX_REQUEST_BYTES,
    "max_response_bytes": MAX_RESPONSE_BYTES,
}
REQUEST_HEADERS = ("authorization", "x-firm-id", "content-type", "accept", "origin")
RESPONSE_HEADERS = (
    "content-type",
    "content-disposition",
    "content-security-policy",
    "retry-after",
)
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")


@dataclass(frozen=True)
class GatewaySettings:
    # Origin is never returned to the browser or included in error messages.
    backend_origin: str = field(default="", repr=False)
    allow_local_http: bool = False
    timeout_seconds: float = 110

    def __post_init__(self) -> None:
        if not 1 <= self.timeout_seconds <= 110:
            raise ValueError("网关超时必须在 1 至 110 秒之间")
        if not self.backend_origin:
            return
        value = self.backend_origin
        if any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value:
            raise ValueError("后端地址格式无效")
        try:
            parsed = urlsplit(value)
            # Accessing port also validates its numeric value and valid range.
            _ = parsed.port
            if (
                not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.path not in ("", "/")
                or "%" in parsed.netloc
                or "?" in value
                or "#" in value
            ):
                raise ValueError("后端地址格式无效")
            local_http = (
                self.allow_local_http
                and parsed.scheme == "http"
                and parsed.hostname.lower() in ("localhost", "127.0.0.1", "::1")
            )
            if parsed.scheme != "https" and not local_http:
                raise ValueError("后端地址必须使用 HTTPS，本地 HTTP 需要显式开启")
            validated = httpx.URL(value)
            if not validated.host or validated.scheme not in ("http", "https"):
                raise ValueError("后端地址格式无效")
        except (httpx.InvalidURL, ValueError) as error:
            raise ValueError("后端地址格式无效，请检查网关服务器配置") from error
        object.__setattr__(self, "backend_origin", str(validated).rstrip("/"))

    @classmethod
    def from_env(cls) -> GatewaySettings:
        try:
            timeout_seconds = float(os.environ.get("ACCOUNTING_GATEWAY_TIMEOUT_SECONDS", "110"))
        except ValueError as error:
            raise ValueError("网关超时格式无效，请检查网关服务器配置") from error
        return cls(
            backend_origin=os.environ.get("ACCOUNTING_BACKEND_ORIGIN", "").strip(),
            allow_local_http=os.environ.get("ACCOUNTING_GATEWAY_ALLOW_LOCAL_HTTP") == "1",
            timeout_seconds=timeout_seconds,
        )


def _error(status: int, detail: str) -> JSONResponse:
    return JSONResponse(
        {"detail": detail},
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


async def _read_request(request: Request) -> bytes | Response:
    length = request.headers.get("content-length")
    if length is not None:
        if len(length) > 20 or not length.isascii() or not length.isdecimal():
            return _error(400, "请求大小信息无效")
        if int(length) > MAX_REQUEST_BYTES:
            return _error(413, "请求超过网关 4 MiB 限制，请减少附件数量或缩小文件后重试")
    # Read ASGI messages directly. Never invoke form()/body() or parse originals.
    content = bytearray()
    try:
        async for chunk in request.stream():
            if len(content) + len(chunk) > MAX_REQUEST_BYTES:
                return _error(413, "请求超过网关 4 MiB 限制，请减少附件数量或缩小文件后重试")
            content.extend(chunk)
    except ClientDisconnect:
        return _error(400, "请求连接已断开，请刷新后查看操作状态")
    return bytes(content)


def _target(settings: GatewaySettings, request: Request) -> httpx.URL:
    # copy_with(raw_path=...) cannot replace the trusted authority with a URL
    # supplied via Host, forwarding headers, path parameters or a query string.
    path = request.scope.get("raw_path", request.url.path.encode("utf-8"))
    if not (path == b"/api" or path.startswith(b"/api/")):
        raise ValueError("无效 API 路径")
    if any(part in (".", "..") for part in request.scope["path"].split("/")):
        raise ValueError("无效 API 路径")
    query = request.scope.get("query_string", b"")
    return httpx.URL(settings.backend_origin).copy_with(
        raw_path=path + (b"?" + query if query else b"")
    )


async def _proxy(app: FastAPI, request: Request) -> Response:
    settings: GatewaySettings = app.state.gateway_settings
    if not settings.backend_origin:
        return _error(503, "会计后端尚未配置，请由管理员设置网关后端地址")
    try:
        target = _target(settings, request)
    except (ValueError, httpx.InvalidURL):
        return _error(400, "无效 API 路径")
    deadline = asyncio.get_running_loop().time() + settings.timeout_seconds
    try:
        async with asyncio.timeout_at(deadline):
            content = await _read_request(request)
    except TimeoutError:
        return _error(504, "请求读取超时，尚未发送到会计后端，请缩小文件后重试")
    if isinstance(content, Response):
        return content
    # Cookies, Host and forwarding headers are intentionally excluded. httpx
    # sets Host from the trusted target, preserving backend TrustedHost checks.
    # Preserve raw header bytes, including valid non-ASCII values, without
    # relying on a lossy decode/encode round trip. Reject ambiguous identities.
    headers: list[tuple[bytes, bytes]] = []
    selected: set[bytes] = set()
    for name, value in request.scope.get("headers", []):
        name = name.lower()
        if name.decode("ascii", errors="ignore") not in REQUEST_HEADERS:
            continue
        if (
            name in selected
            or len(value) > 8192
            or b"\r" in value
            or b"\n" in value
            or b"\x00" in value
        ):
            return _error(400, "请求头格式无效或包含重复身份信息")
        selected.add(name)
        headers.append((name, value))
    headers.append((b"accept-encoding", b"identity"))
    try:
        # Bound the whole exchange, including a slowly streaming upstream.
        async with asyncio.timeout_at(deadline):
            client: httpx.AsyncClient = app.state.gateway_client
            async with client.stream(
                request.method, target, headers=headers, content=content
            ) as upstream:
                if 300 <= upstream.status_code < 400:
                    return _error(502, "会计后端返回了重定向，请由管理员检查后端地址")
                if upstream.headers.get("content-encoding", "identity").lower() != "identity":
                    return _error(502, "会计后端返回了不支持的传输格式")
                length = upstream.headers.get("content-length")
                if length is not None and (
                    len(length) > 20
                    or not length.isascii()
                    or not length.isdecimal()
                    or int(length) > MAX_RESPONSE_BYTES
                ):
                    return _error(502, "会计后端响应超过网关 4 MiB 限制，请缩小查询或导出范围")
                result = bytearray()
                async for chunk in upstream.aiter_raw():
                    if len(result) + len(chunk) > MAX_RESPONSE_BYTES:
                        return _error(502, "会计后端响应超过网关 4 MiB 限制，请缩小查询或导出范围")
                    result.extend(chunk)
                response_headers = {}
                for name in RESPONSE_HEADERS:
                    value = upstream.headers.get(name)
                    if value is not None:
                        if len(value) > 8192 or "\r" in value or "\n" in value or "\x00" in value:
                            return _error(502, "会计后端响应格式无效")
                        response_headers[name] = value
                response_headers["Cache-Control"] = "no-store"
                response_headers["X-Content-Type-Options"] = "nosniff"
                body = bytes(result)
                if (
                    request.method != "HEAD"
                    and request.scope["path"] == "/api/auth/config"
                    and upstream.status_code == 200
                ):
                    try:
                        config = json.loads(body)
                        if not isinstance(config, dict):
                            raise ValueError("invalid auth config")
                        config["transport_limits"] = TRANSPORT_LIMITS
                        body = json.dumps(
                            config, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                        ).encode("utf-8")
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise ValueError("auth config too large")
                    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
                        return _error(502, "会计后端登录配置格式无效")
                    response_headers["content-type"] = "application/json"
                return Response(body, status_code=upstream.status_code, headers=response_headers)
    except (httpx.TimeoutException, TimeoutError):
        return _error(
            504, "会计后端请求超时，操作结果尚未确认，请先刷新任务或操作状态再决定是否重试"
        )
    except httpx.HTTPError:
        return _error(502, "无法连接会计后端，操作结果尚未确认，请稍后刷新状态")


def create_app(
    settings: GatewaySettings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    effective = settings if settings is not None else GatewaySettings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # No implicit environment proxies and no transport retries. A timed-out
        # accounting write may already have committed on the durable backend.
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
        async with httpx.AsyncClient(
            transport=transport
            if transport is not None
            else httpx.AsyncHTTPTransport(retries=0, trust_env=False, limits=limits),
            timeout=httpx.Timeout(effective.timeout_seconds, connect=5, pool=5),
            follow_redirects=False,
            trust_env=False,
            limits=limits,
        ) as client:
            application.state.gateway_client = client
            yield

    application = FastAPI(
        title="会计 API 网关",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.router.redirect_slashes = False
    application.state.gateway_settings = effective

    @application.api_route("/api", methods=list(METHODS))
    @application.api_route("/api/{path:path}", methods=list(METHODS))
    async def forward(request: Request, path: str = "") -> Response:
        return await _proxy(application, request)

    return application


app = create_app()
