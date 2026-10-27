from __future__ import annotations

import base64
import asyncio
import io
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import httpx
from fastapi.testclient import TestClient
from PIL import Image

from services.paddle_ocr import app as boundary
from services.paddle_ocr.engine import (
    ENGINE_VERSION,
    MODEL,
    InvalidEngineResult,
    normalize_result,
    verify_model_files,
)

TOKEN = "test-only-not-a-real-token-000000000000"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def image_url(size=(320, 100), *, image_format="PNG"):
    output = io.BytesIO()
    Image.new("RGB", size, "white").save(output, format=image_format)
    mime = "png" if image_format == "PNG" else "jpeg"
    return f"data:image/{mime};base64," + base64.b64encode(output.getvalue()).decode()


def payload(**overrides):
    return {"pages": [{"page": 1, "image_data_url": image_url(), **overrides}]}


class FakeEngine:
    engine_version = ENGINE_VERSION
    model = MODEL

    def recognize(self, image, page):
        return normalize_result(
            {
                "rec_texts": ["税额 69.88"],
                "rec_scores": [0.98],
                "rec_polys": [[[2, 3], [90, 3], [90, 20], [2, 20]]],
            },
            page=page,
            width=image.width,
            height=image.height,
        )


@pytest.fixture
def client():
    with TestClient(boundary.create_app(token=TOKEN, engine_factory=FakeEngine)) as instance:
        yield instance


def test_single_initialization_and_contract():
    initialized = []

    def factory():
        initialized.append(True)
        return FakeEngine()

    with TestClient(boundary.create_app(token=TOKEN, engine_factory=factory)) as client:
        for _ in range(2):
            result = client.post("/v1/recognize", json=payload(), headers=HEADERS)
            assert result.status_code == 200
            body = result.json()
            assert body["schema_version"] == "paddle-ocr-v1"
            assert body["model"] == MODEL
            assert body["engine_version"] == ENGINE_VERSION
            assert body["pages"][0]["width"] == 320
            assert body["pages"][0]["blocks"][0]["id"] == "p1-b1"
            assert result.headers["Cache-Control"] == "no-store"
    assert initialized == [True]


def test_health_does_not_disclose_runtime_or_key(client):
    assert client.get("/healthz").json() == {"status": "ready"}
    assert client.get("/openapi.json", headers=HEADERS).status_code == 404


@pytest.mark.parametrize("token", ["", "short", "秘密" * 40, " " * 40])
def test_startup_requires_secret(token):
    with pytest.raises(RuntimeError, match="PADDLEOCR_API_TOKEN"):
        with TestClient(boundary.create_app(token=token, engine_factory=FakeEngine)):
            pass


def test_auth_must_be_header_before_body_parse(client):
    assert client.post("/v1/recognize", content=b"not-json").status_code == 401
    assert client.post(f"/v1/recognize?token={TOKEN}", json=payload()).status_code == 401
    assert client.post("/v1/recognize", content=b"not-json", headers=HEADERS).status_code == 415


@pytest.mark.parametrize("page", [0, 7, True, 1.5, "1"])
def test_invalid_page_and_no_payload_echo(client, page):
    result = client.post("/v1/recognize", json=payload(page=page), headers=HEADERS)
    assert result.status_code == 422
    assert "base64" not in result.text


def test_duplicate_and_too_many_pages(client):
    page = payload()["pages"][0]
    assert (
        client.post("/v1/recognize", json={"pages": [page, page]}, headers=HEADERS).status_code
        == 422
    )
    assert (
        client.post("/v1/recognize", json={"pages": [page] * 7}, headers=HEADERS).status_code == 422
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://private.example/image.png",
        "file:///secrets",
        "data:image/png;base64,invalid!",
        "data:image/svg+xml;base64," + "a" * 24,
    ],
)
def test_only_inline_supported_images(client, value):
    assert (
        client.post(
            "/v1/recognize", json=payload(image_data_url=value), headers=HEADERS
        ).status_code
        == 422
    )


def test_mismatched_mime_and_unknown_fields(client):
    value = image_url().replace("image/png", "image/jpeg")
    assert (
        client.post(
            "/v1/recognize", json=payload(image_data_url=value), headers=HEADERS
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/recognize", json={**payload(), "url": "https://elsewhere"}, headers=HEADERS
        ).status_code
        == 422
    )


def test_image_limits_before_inference(client, monkeypatch):
    monkeypatch.setattr(boundary, "MAX_IMAGE_PIXELS", 100)
    assert client.post("/v1/recognize", json=payload(), headers=HEADERS).status_code == 413


def test_total_pixel_limit(client, monkeypatch):
    monkeypatch.setattr(boundary, "MAX_TOTAL_PIXELS", 40000)
    pages = payload()["pages"] + payload(page=2)["pages"]
    assert client.post("/v1/recognize", json={"pages": pages}, headers=HEADERS).status_code == 413


def test_body_size_before_json_parse(client, monkeypatch):
    monkeypatch.setattr(boundary, "MAX_BODY_BYTES", 20)
    assert client.post("/v1/recognize", json=payload(), headers=HEADERS).status_code == 413


def test_slow_body_timeout_releases_slot(monkeypatch):
    monkeypatch.setattr(boundary, "BODY_READ_SECONDS", 0.01)

    async def slow_body():
        yield b'{"pages":'
        await asyncio.sleep(1)
        yield b"[]}"

    async def run():
        app = boundary.create_app(token=TOKEN, engine_factory=FakeEngine)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                result = await client.post(
                    "/v1/recognize",
                    content=slow_body(),
                    headers={**HEADERS, "Content-Type": "application/json"},
                )
                assert result.status_code == 408
                assert (
                    await client.post("/v1/recognize", json=payload(), headers=HEADERS)
                ).status_code == 200

    asyncio.run(run())


def test_response_limit_and_error_redaction(client, monkeypatch):
    monkeypatch.setattr(boundary, "MAX_RESPONSE_BYTES", 20)
    result = client.post("/v1/recognize", json=payload(), headers=HEADERS)
    assert result.status_code == 502
    assert "69.88" not in result.text


def test_exception_does_not_leak_document(caplog):
    class BrokenEngine(FakeEngine):
        def recognize(self, image, page):
            raise RuntimeError("private invoice 99999")

    with TestClient(boundary.create_app(token=TOKEN, engine_factory=BrokenEngine)) as client:
        result = client.post("/v1/recognize", json=payload(), headers=HEADERS)
        assert result.status_code == 502
        assert "99999" not in result.text
        assert "99999" not in caplog.text


def test_busy_rejects_without_queue_then_recovers():
    entered, release = threading.Event(), threading.Event()

    class SlowEngine(FakeEngine):
        def recognize(self, image, page):
            entered.set()
            assert release.wait(5)
            return super().recognize(image, page)

    with TestClient(boundary.create_app(token=TOKEN, engine_factory=SlowEngine)) as client:
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(client.post, "/v1/recognize", json=payload(), headers=HEADERS)
            try:
                assert entered.wait(3)
                second = client.post("/v1/recognize", json=payload(), headers=HEADERS)
                assert second.status_code == 503
                assert second.headers["Retry-After"] == "2"
            finally:
                release.set()
            assert first.result().status_code == 200
        assert client.post("/v1/recognize", json=payload(), headers=HEADERS).status_code == 200


def test_cancelled_request_does_not_free_native_predictor():
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    class SlowEngine(FakeEngine):
        def recognize(self, image, page):
            entered.set()
            try:
                assert release.wait(5)
                return super().recognize(image, page)
            finally:
                finished.set()

    async def run():
        app = boundary.create_app(token=TOKEN, engine_factory=SlowEngine)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                task = asyncio.create_task(
                    client.post("/v1/recognize", json=payload(), headers=HEADERS)
                )
                try:
                    assert await asyncio.to_thread(entered.wait, 3)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    result = await client.post("/v1/recognize", json=payload(), headers=HEADERS)
                    assert result.status_code == 503
                finally:
                    release.set()
                    assert await asyncio.to_thread(finished.wait, 3)

    asyncio.run(run())


def test_model_manifest_rejects_modified_file(tmp_path):
    (tmp_path / "inference.json").write_text("modified model")
    with pytest.raises(RuntimeError, match="checksum"):
        verify_model_files("PP-OCRv6_small_det", tmp_path)


def test_page_text_limit_includes_join_separators():
    raw = {
        "rec_texts": ["a" * 2000] * 15,
        "rec_scores": [0.99] * 15,
        "rec_polys": [[[0, 0], [100, 0], [100, 100], [0, 100]]] * 15,
    }
    with pytest.raises(InvalidEngineResult, match="oversized"):
        normalize_result(raw, page=1, width=100, height=100)


@pytest.mark.parametrize(
    "alter",
    [
        {"rec_scores": [float("nan")]},
        {"rec_scores": [1.1]},
        {"rec_scores": []},
        {"rec_polys": [[[-1, 0], [1, 0], [1, 1], [0, 1]]]},
        {"rec_polys": [[[0, 0], [101, 0], [101, 1], [0, 1]]]},
        {"rec_polys": [[[0, 0], [1, 0], [1, float("inf")], [0, 1]]]},
        {"rec_texts": ["a" * 2001]},
    ],
)
def test_invalid_engine_geometry_or_values_rejected(alter):
    base = {
        "rec_texts": ["合计"],
        "rec_scores": [0.99],
        "rec_polys": [[[0, 0], [100, 0], [100, 100], [0, 100]]],
    }
    with pytest.raises(InvalidEngineResult):
        normalize_result({**base, **alter}, page=1, width=100, height=100)


@pytest.mark.parametrize(
    "alter",
    [
        {"rec_scores": [True]},
        {"rec_scores": ["0.99"]},
        {"rec_polys": [[[True, 0], [100, 0], [100, 100], [0, 100]]]},
        {"rec_polys": [[["1", 0], [100, 0], [100, 100], [0, 100]]]},
    ],
)
def test_engine_numeric_evidence_does_not_coerce_booleans_or_strings(alter):
    """Malformed scores/coordinates must not become trusted numeric evidence."""
    result = {
        "rec_texts": ["合计"],
        "rec_scores": [0.99],
        "rec_polys": [[[0, 0], [100, 0], [100, 100], [0, 100]]],
        **alter,
    }
    with pytest.raises(InvalidEngineResult):
        normalize_result(result, page=1, width=100, height=100)
