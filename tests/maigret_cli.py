"""Child process bootstrap for the offline run_maigret fixture."""

import json
import runpy
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


SITES = {
    'Example Profiles': 'https://profiles.example.test',
    'Example Forum': 'https://forum.example.test',
}


def run(responses):
    from aiohttp import ClientSession
    from curl_cffi.requests import AsyncSession

    from maigret import settings

    blocked = []

    def block(description):
        message = f'Unexpected network access in run_maigret: {description}'
        blocked.append(message)
        raise RuntimeError(message)

    def block_socket_access(event, args):
        if event in {
            'socket.getaddrinfo', 'socket.gethostbyname',
            'socket.gethostbyaddr', 'socket.getnameinfo',
        }:
            block(f'{event} {args[0]!r}')
        if event == 'socket.sendto':
            block(f'{event} {args[-1]!r}')
        if event == 'socket.connect' and args[0].family in (socket.AF_INET, socket.AF_INET6):
            block(f'{event} {args[1]!r}')

    async def mock_request(session, method, url, **kwargs):
        url = str(url)
        if url in responses:
            status, body = responses[url]
        elif any(url.startswith(f'{base}/users/') for base in SITES.values()):
            status, body = 200, '<html><body>Example profile</body></html>'
        else:
            block(f'{method} {url}')
        response = MagicMock(
            status=status,
            headers={},
            charset='utf-8',
            content=SimpleNamespace(read=AsyncMock(return_value=body.encode('utf-8'))),
        )
        response.__aenter__.return_value = response
        return response

    sys.addaudithook(block_socket_access)
    # The DNS and curl libraries use native sockets outside Python's audit hook.
    with (
        patch.object(ClientSession, '_request', mock_request),
        patch.object(AsyncSession, 'request', side_effect=lambda *a, **kw: block('curl request')),
        patch('pycares.Channel', side_effect=lambda *a, **kw: block('c-ares DNS lookup')),
        patch.object(settings, 'SETTINGS_FILES_PATHS', settings.SETTINGS_FILES_PATHS[:1]),
    ):
        try:
            runpy.run_module('maigret', run_name='__main__', alter_sys=True)
        finally:
            # Network errors can be caught by individual checkers or the updater.
            if blocked:
                raise SystemExit('\n'.join(blocked))


if __name__ == '__main__':
    responses_path = Path(sys.argv.pop(1))
    run(json.loads(responses_path.read_text(encoding='utf-8')))
