"""Bound request bodies even when a client omits Content-Length."""
from starlette.exceptions import HTTPException


class RequestBodyLimit:
    def __init__(self, app, max_bytes=50 * 1024 * 1024):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        received = 0
        exceeded = False
        replacement_sent = False
        error_body = b'{"detail":"Request exceeds 50 MB. Submit smaller batches."}'

        async def bounded_receive():
            nonlocal received, exceeded
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if received > self.max_bytes:
                    exceeded = True
                    raise HTTPException(413, 'Request exceeds 50 MB. Submit smaller batches.')
            return message

        async def bounded_send(message):
            nonlocal replacement_sent
            if not exceeded:
                await send(message)
            elif message['type'] == 'http.response.start':
                await send({'type': 'http.response.start', 'status': 413, 'headers': [
                    (b'content-type', b'application/json'), (b'content-length', str(len(error_body)).encode())]})
            elif message['type'] == 'http.response.body' and not replacement_sent:
                replacement_sent = True
                await send({'type': 'http.response.body', 'body': error_body})

        await self.app(scope, bounded_receive, bounded_send)
