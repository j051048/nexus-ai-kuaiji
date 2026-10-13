from starlette.exceptions import HTTPException


class BridgeBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST' or '/integrations/tplus/' not in scope.get('path', ''):
            return await self.app(scope, receive, send)
        consumed = 0
        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            if message['type'] == 'http.request':
                consumed += len(message.get('body', b''))
                if consumed > 4_000_000:
                    raise HTTPException(413, '接入请求过大，文件内容最多 2 MB')
            return message
        await self.app(scope, bounded_receive, send)
