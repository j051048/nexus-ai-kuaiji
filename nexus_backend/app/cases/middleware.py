"""Multipart 请求体限额，与原业务附件采用相同字节上限。"""

import re
from starlette.exceptions import HTTPException
from app.business.storage import MAX_REQUEST_BYTES


class CaseAttachmentBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        limited = (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and re.fullmatch(r"/api/cases/[^/]+/attachments", scope.get("path", ""))
        )
        if not limited:
            return await self.app(scope, receive, send)
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > MAX_REQUEST_BYTES:
                    raise HTTPException(
                        status_code=413, detail="上传请求过大，单个补件原件最多 10 MB"
                    )
            return message

        await self.app(scope, bounded_receive, send)
