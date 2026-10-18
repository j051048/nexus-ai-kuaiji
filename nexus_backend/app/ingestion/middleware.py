"""Bound external ingestion bodies before JSON parsing."""

from starlette.exceptions import HTTPException

MAX_INGESTION_BODY_BYTES = 256 * 1024


class IngestionBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/api/integrations/webhook"
        ):
            return await self.app(scope, receive, send)
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > MAX_INGESTION_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="接入事件超过 256 KiB 上限")
            return message

        await self.app(scope, bounded_receive, send)
