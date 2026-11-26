"""Shared HTTP transport. Local mode is loopback-only; protected mode requires HTTPS.

The server must independently validate tokens and enforce resource permissions.
This client does not configure BaSyx security or refresh access tokens.
"""
import os
from pathlib import Path
from urllib.parse import urlsplit
import requests


def configuration():
    base = os.getenv('AAS_BASE_URL', 'http://localhost:8082').rstrip('/')
    mode = os.getenv('AAS_SECURITY_MODE', 'local').lower()
    url = urlsplit(base)
    if mode not in ('local', 'protected'):
        raise ValueError('AAS_SECURITY_MODE must be local or protected')
    if url.scheme not in ('http', 'https') or not url.hostname:
        raise ValueError('AAS_BASE_URL must be an HTTP(S) URL')
    if url.username or url.password or url.query or url.fragment:
        raise ValueError('Credentials, query and fragment are not allowed in AAS_BASE_URL')
    if mode == 'local' and url.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Local mode permits only loopback hosts')
    token_file = os.getenv('AAS_TOKEN_FILE')
    token = Path(token_file).read_text(encoding='utf-8-sig').strip() if token_file else ''
    if mode == 'protected' and (url.scheme != 'https' or not token):
        raise ValueError('Protected mode requires HTTPS and a nonempty AAS_TOKEN_FILE')
    if token and url.scheme != 'https':
        raise ValueError('Refusing to send a bearer token over HTTP')
    if '\n' in token or '\r' in token:
        raise ValueError('Token must be a single line')
    ca = os.getenv('AAS_CA_BUNDLE')
    if ca and not Path(ca).is_file():
        raise ValueError('AAS_CA_BUNDLE must name an existing PEM file')
    return base, token, ca or True


def request(method, path, **kwargs):
    base, token, verify = configuration()
    if not path.startswith('/') or path.startswith('//'):
        raise ValueError('Expected relative API path beginning with one slash')
    # A fresh session avoids sharing mutable state between motor workers.
    with requests.Session() as session:
        session.trust_env = False  # No implicit proxy credentials or .netrc.
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        response = session.request(method, base + path, headers=headers,
                                   verify=verify, allow_redirects=False, **kwargs)
    if 300 <= response.status_code < 400:
        raise RuntimeError('Redirect refused; configure the final API URL directly')
    return response
