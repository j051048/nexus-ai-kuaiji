"""Bound receipt HTTP bodies before JSON parsing; no file is persisted here."""
import re
from starlette.exceptions import HTTPException


class DeliveryBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or not re.fullmatch(r"/api/books/[^/]+/delivery/(?:exports/[^/]+/receipts(?:/import)?|discrepancies/notes)", scope.get("path", "")):
            return await self.app(scope, receive, send)
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > 4_000_000:
                    raise HTTPException(status_code=413, detail="回执请求过大，回执内容最多 2 MB")
            return message

        await self.app(scope, bounded_receive, send)
