"""Private OCR HTTP boundary: no URLs, no database, no durable document copies."""

from __future__ import annotations

import base64
import binascii
import hmac
import io
import json
import logging
import os
import threading
import time
import warnings
from contextlib import asynccontextmanager
from typing import Any, Callable

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from starlette.concurrency import run_in_threadpool

from .engine import InvalidEngineResult, PaddleEngine

MAX_BODY_BYTES = 36 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_TOTAL_PIXELS = 24_000_000
MAX_TOTAL_BYTES = 24 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ENCODED_IMAGE = 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 32
BODY_READ_SECONDS = 15
logger = logging.getLogger(__name__)


class InputPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: StrictInt = Field(ge=1, le=6)
    image_data_url: str = Field(min_length=24, max_length=MAX_ENCODED_IMAGE)


class RecognizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pages: list[InputPage] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def unique_pages(self) -> RecognizeRequest:
        if len({page.page for page in self.pages}) != len(self.pages):
            raise ValueError("duplicate page number")
        return self


def decode_image(value: str) -> tuple[Image.Image, int]:
    """Check dimensions before decompression; never follow an external URL."""
    mime, separator, encoded = value.partition(",")
    if not separator or mime not in {"data:image/png;base64", "data:image/jpeg;base64"}:
        raise HTTPException(422, "Only base64 PNG/JPEG pages are accepted")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "Invalid image encoding") from None
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image byte limit exceeded")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                expected = "PNG" if mime == "data:image/png;base64" else "JPEG"
                if source.format != expected or getattr(source, "n_frames", 1) != 1:
                    raise HTTPException(422, "Image format does not match declared type")
                if source.width < 8 or source.height < 8:
                    raise HTTPException(422, "Image is too small")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise HTTPException(413, "Image pixel limit exceeded")
                source.load()
                # EXIF orientation is intentionally not applied here: our contract
                # uses the input raster coordinate system prepared by the backend.
                return source.convert("RGB"), len(raw)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(413, "Image pixel limit exceeded") from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(422, "Invalid image") from None


class PrivateBoundary:
    """Authenticate and reserve the single predictor before buffering any body."""

    def __init__(self, app: Any, token: str, slot: threading.Lock) -> None:
        self.app, self.token, self.slot = app, token, slot

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"] == "/healthz":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        authorization = headers.get(b"authorization", b"")
        expected = f"Bearer {self.token}".encode()
        if not hmac.compare_digest(authorization, expected):
            await JSONResponse({"detail": "Unauthorized"}, status_code=401)(scope, receive, send)
            return
        if scope["path"] != "/v1/recognize" or scope["method"] != "POST":
            await JSONResponse({"detail": "Not found"}, status_code=404)(scope, receive, send)
            return
        if headers.get(b"content-type", b"").split(b";", 1)[0].strip() != b"application/json":
            await JSONResponse({"detail": "JSON required"}, status_code=415)(scope, receive, send)
            return
        try:
            content_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > MAX_BODY_BYTES:
            await JSONResponse({"detail": "Invalid or oversized content length"}, status_code=413)(
                scope, receive, send
            )
            return
        if not self.slot.acquire(blocking=False):
            await JSONResponse(
                {"detail": "OCR service busy; retry through the accounting queue"},
                status_code=503,
                headers={"Retry-After": "2"},
            )(scope, receive, send)
            return
        try:
            raw = bytearray()
            try:
                with anyio.fail_after(BODY_READ_SECONDS):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        raw.extend(message.get("body", b""))
                        if len(raw) > MAX_BODY_BYTES:
                            await JSONResponse({"detail": "Request too large"}, status_code=413)(
                                scope, receive, send
                            )
                            return
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                await JSONResponse({"detail": "Request body read timeout"}, status_code=408)(
                    scope, receive, send
                )
                return

            async def replay() -> dict[str, Any]:
                return {"type": "http.request", "body": bytes(raw), "more_body": False}

            await self.app(scope, replay, send)
        finally:
            self.slot.release()


def create_app(
    *, token: str | None = None, engine_factory: Callable[[], Any] = PaddleEngine
) -> FastAPI:
    configured_token = token if token is not None else os.environ.get("PADDLEOCR_API_TOKEN", "")
    slot = threading.Lock()
    predictor_slot = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if (
            len(configured_token) < 32
            or not configured_token.isascii()
            or any(character.isspace() for character in configured_token)
        ):
            raise RuntimeError(
                "Set PADDLEOCR_API_TOKEN to a random ASCII secret of at least 32 characters"
            )
        app.state.engine = await run_in_threadpool(engine_factory)
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False

    application = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    application.state.ready = False
    application.add_middleware(PrivateBoundary, token=configured_token, slot=slot)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_request: Any, _exc: Any) -> JSONResponse:
        # Pydantic errors can echo the complete base64 document. Never return it.
        return JSONResponse({"detail": "Invalid OCR request"}, status_code=422)

    @application.get("/healthz")
    def health() -> JSONResponse:
        return JSONResponse(
            {"status": "ready" if application.state.ready else "starting"},
            status_code=200 if application.state.ready else 503,
        )

    def recognize_sync(payload: RecognizeRequest) -> JSONResponse:
        # A cancelled ASGI task must never permit simultaneous access to the
        # underlying native predictor. This lock is owned/released by its thread.
        if not predictor_slot.acquire(blocking=False):
            raise HTTPException(503, "OCR predictor busy", headers={"Retry-After": "2"})
        started = time.monotonic()
        images: list[tuple[int, Image.Image]] = []
        total_bytes = total_pixels = 0
        try:
            for page in payload.pages:
                image, size = decode_image(page.image_data_url)
                images.append((page.page, image))
                total_bytes += size
                total_pixels += image.width * image.height
                if total_bytes > MAX_TOTAL_BYTES or total_pixels > MAX_TOTAL_PIXELS:
                    raise HTTPException(413, "Request image limit exceeded")
            engine = application.state.engine
            result = {
                "schema_version": "paddle-ocr-v1",
                "engine": "paddleocr",
                "engine_version": engine.engine_version,
                "model": engine.model,
                "pages": [engine.recognize(image, number) for number, image in images],
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }
            if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
                raise InvalidEngineResult("response too large")
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except HTTPException:
            raise
        except Exception:
            # Provider exception messages may contain extracted document text.
            logger.error("OCR recognition failed; document and exception details suppressed")
            raise HTTPException(502, "OCR recognition failed") from None
        finally:
            for _number, image in images:
                image.close()
            predictor_slot.release()

    @application.post("/v1/recognize")
    async def recognize(payload: RecognizeRequest) -> JSONResponse:
        # Keep the capacity reservation even if the HTTP caller disconnects or times
        # out. Do not add a wait_for timeout which frees it while Paddle still runs.
        return await run_in_threadpool(recognize_sync, payload)

    return application


app = create_app()
