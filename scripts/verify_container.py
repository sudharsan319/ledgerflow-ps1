#!/usr/bin/env python3
"""Smoke-test the built image through loopback HTTP using disposable hosted settings."""
import argparse
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://ledgerflow-ci.example'


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def expect(response, status):
    if response.status_code != status:
        raise AssertionError(f'{response.request.method} {response.request.url.path}: expected {status}, got {response.status_code}')
    return response


def verify(api):
    deadline = time.monotonic() + 90
    while True:
        try:
            response = api.get('/healthz')
            if response.status_code == 200:
                assert response.json() == {'status': 'ok'}
                break
        except httpx.TransportError:
            pass
        if time.monotonic() >= deadline:
            raise AssertionError('Container did not become healthy within 90 seconds')
        time.sleep(.5)
    expect(api.get('/api/runs'), 401)
    expect(api.get('/'), 401)


def verify_authenticated(api):
    home = expect(api.get('/'), 200)
    assert '<!doctype html>' in home.text.lower(), 'Built frontend was not served'
    assert expect(api.get('/api/runs'), 200).json() == {'runs': []}
    health = expect(api.get('/api/health'), 200).json()
    assert health['openrouter_enabled'] is False and health['openrouter_configured'] is False

    def upload(filename):
        content = (ROOT / 'samples' / filename).read_bytes()
        response = expect(api.post('/api/runs/upload', files={'files': (filename, content)}), 202)
        queued = response.json()['runs']
        assert len(queued) == 1
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            run = expect(api.get(f'/api/runs/{queued[0]["id"]}'), 200).json()
            if run['status'] in ('completed', 'failed'):
                assert run['status'] == 'completed', 'Container invoice processing failed'
                return run, content
            time.sleep(.2)
        raise AssertionError('Container invoice processing exceeded 40 seconds')

    happy, pdf = upload('01-clean-invoice.pdf')
    assert happy['decision'] == 'approved', 'Clean PDF was not approved'
    source = expect(api.get(f'/api/runs/{happy["id"]}/source'), 200)
    assert source.content == pdf, 'Downloaded invoice differs from uploaded PDF'
    duplicate, _ = upload('01-clean-invoice.json')
    assert duplicate['decision'] == 'rejected', 'Equivalent JSON invoice was not rejected'
    assert duplicate['result']['duplicate_of'] == happy['id'], 'Duplicate did not reference the PDF run'
    assert any(check['code'] == 'duplicate' and check['status'] == 'fail'
               for check in duplicate['result']['checks']), 'Duplicate policy check did not fail'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='ledgerflow-ci')
    args = parser.parse_args()
    name = 'ledgerflow-smoke-' + secrets.token_hex(6)
    password = secrets.token_urlsafe(32)
    if os.getenv('GITHUB_ACTIONS') == 'true':
        print(f'::add-mask::{password}', flush=True)
    environment = {**os.environ, 'LEDGERFLOW_AUTH_PASSWORD': password}

    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)

    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        docker('run', '--detach', '--name', name, '--publish', '127.0.0.1::8000',
               '--env', 'RENDER=true', '--env', f'RENDER_EXTERNAL_URL={ORIGIN}',
               '--env', 'LEDGERFLOW_AUTH_USERNAME=reviewer', '--env', 'LEDGERFLOW_AUTH_PASSWORD',
               '--env', 'ENABLE_OPENROUTER=false', '--env', 'OPENROUTER_API_KEY=',
               args.image, env=environment, timeout=30)
        address = docker('port', name, '8000/tcp', timeout=10)
        host, port = address.rsplit(':', 1)
        assert host == '127.0.0.1' and port.isdigit(), 'Container port is not restricted to loopback'
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=10, trust_env=False,
                          headers={'Host': 'ledgerflow-ci.example', 'Origin': ORIGIN}) as api:
            verify(api)
            api.auth = ('reviewer', password)
            verify_authenticated(api)
        print('Container smoke passed: hosted auth, frontend, PDF approval, JSON duplicate, source download; AI disabled.')
    finally:
        # Runs on failed assertions, startup errors, and normal CI cancellation signals.
        try:
            subprocess.run(['docker', 'rm', '--force', name], check=False, capture_output=True, timeout=30)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


if __name__ == '__main__':
    main()
