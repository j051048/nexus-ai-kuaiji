"""Unauthenticated provider callback; authenticity comes from the HMAC."

The route is intentionally outside the user-session router. A provider has no
accounting bearer token, and accepting one would let an arbitrary logged-in
user forge delivery evidence. The signature covers timestamp and raw body.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .receipts import HEADER_EVENT_ID, HEADER_SIGNATURE, HEADER_TIMESTAMP, MAX_BODY


def build_delivery_callback_router():
    router = APIRouter(prefix="/api/delivery", tags=["交付回执回调"])

    @router.post("/provider-callback")
    async def provider_callback(request: Request):
        length = request.headers.get("content-length", "")
        if length:
            try:
                if int(length) > MAX_BODY:
                    return JSONResponse({"detail": "回调内容大小不合法"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "回调长度不合法"}, status_code=422)
        raw = await request.body()
        result = request.app.state.delivery_receipts.receive(
            raw,
            request.headers.get(HEADER_TIMESTAMP, ""),
            request.headers.get(HEADER_SIGNATURE, ""),
            request.headers.get(HEADER_EVENT_ID, ""),
        )
        return JSONResponse(result, status_code=200 if result["duplicate"] else 202)

    return router
