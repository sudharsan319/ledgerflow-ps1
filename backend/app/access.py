"""Shared reviewer access for the hosted case-study workspace."""
import base64
import binascii
import secrets
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse


def access_error(request, config):
    # Only this minimal endpoint is public; records, sources and API docs are not.
    probe = request.url.path == '/healthz' and request.method in ('GET', 'HEAD')
    if config.auth_password and not probe:
        valid = False
        scheme, _, encoded = request.headers.get('authorization', '').partition(' ')
        if scheme.casefold() == 'basic':
            try:
                username, separator, password = base64.b64decode(encoded, validate=True).decode('utf-8').partition(':')
                user_ok = secrets.compare_digest(username.encode(), config.auth_username.encode())
                password_ok = secrets.compare_digest(password.encode(), config.auth_password.encode())
                valid = bool(separator) and user_ok and password_ok
            except (binascii.Error, UnicodeDecodeError, ValueError):
                pass
        if not valid:
            return JSONResponse({'detail': 'Reviewer sign-in required.'}, 401, headers={
                'WWW-Authenticate': 'Basic realm="LedgerFlow reviewer", charset="UTF-8"', 'Cache-Control': 'no-store'})
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin:
        try:
            supplied = urlsplit(origin)
            valid = (origin.rstrip('/') == config.public_origin.rstrip('/')) if config.public_origin else (
                supplied.scheme in ('http', 'https') and supplied.hostname in {'localhost', '127.0.0.1', '::1', 'testserver'}
                and supplied.username is None and supplied.password is None)
        except ValueError:
            valid = False
        if not valid:
            return JSONResponse({'detail': 'Request origin is not allowed for this workspace.'}, 403)
    return None
