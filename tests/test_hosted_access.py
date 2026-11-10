import base64
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app

ORIGIN = 'https://ledgerflow-test.onrender.com'
PASSWORD = 'fictional-test-password-0123456789'


@pytest.fixture
def hosted(tmp_path):
    settings = Settings(data_dir=tmp_path, public_origin=ORIGIN, auth_password=PASSWORD)
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        yield client


@pytest.mark.parametrize('path', ['/', '/api/runs', '/api/procurement', '/api/health', '/api/runs/export', '/api/runs/id/source', '/docs', '/openapi.json', '/assets/unknown.js'])
def test_hosted_records_sources_docs_and_assets_require_login(hosted, path):
    response = hosted.get(path)
    assert response.status_code == 401
    assert response.headers['www-authenticate'].startswith('Basic ')
    assert response.headers['cache-control'] == 'no-store'
    assert PASSWORD not in response.text


@pytest.mark.parametrize('header', ['Bearer invalid', 'Basic !!!', 'Basic ////', 'Basic ' + base64.b64encode(b'reviewer:wrong').decode(), 'Basic ' + base64.b64encode(b'wrong:'+PASSWORD.encode()).decode()])
def test_malformed_or_wrong_credentials_fail_closed(hosted, header):
    assert hosted.get('/api/runs', headers={'Authorization': header}).status_code == 401


def test_minimal_health_probe_is_public_but_no_records(hosted):
    assert hosted.get('/healthz').json() == {'status': 'ok'}
    assert hosted.head('/healthz').status_code == 200
    assert hosted.post('/healthz').status_code == 401
    assert hosted.get('/healthz/').status_code == 401


def test_login_allows_pipeline_but_exact_public_origin_required(hosted):
    auth = ('reviewer', PASSWORD)
    assert hosted.get('/api/runs', auth=auth).json() == {'runs': []}
    for origin in ['https://evil.example', 'http://ledgerflow-test.onrender.com', 'https://ledgerflow-test.onrender.com.evil.example', 'http://localhost:5173', ORIGIN + ':444', 'null']:
        assert hosted.post('/api/demo/happy_path', auth=auth, headers={'Origin': origin}).status_code == 403
    response = hosted.post('/api/demo/happy_path', auth=auth, headers={'Origin': ORIGIN})
    assert response.status_code == 202
    assert hosted.get('/api/runs', auth=auth).json()['runs']
    assert hosted.get('/api/runs', auth=auth, headers={'Host': 'attacker.example'}).status_code == 400
    health = hosted.get('/api/health', auth=auth)
    assert health.status_code == 200 and PASSWORD not in health.text


@pytest.mark.parametrize('origin', ['http://demo.example', 'https://demo.example/path', 'https://user:pass@demo.example', 'https://@demo.example', 'https://demo.example?key=secret', 'https://demo.example#part'])
def test_invalid_public_origin_refused(tmp_path, origin):
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, public_origin=origin, auth_password=PASSWORD)


def test_hosted_mode_cannot_start_without_password(tmp_path):
    for password in ['', 'short']:
        with pytest.raises(ValueError, match='LEDGERFLOW_AUTH_PASSWORD'):
            Settings(data_dir=tmp_path, public_origin=ORIGIN, auth_password=password)


def test_render_address_comes_from_server_environment(tmp_path, monkeypatch):
    for key in ('LEDGERFLOW_PUBLIC_ORIGIN', 'RENDER_EXTERNAL_HOSTNAME', 'RENDER_EXTERNAL_URL'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('LEDGERFLOW_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('LEDGERFLOW_AUTH_PASSWORD', PASSWORD)
    with pytest.raises(ValueError, match='public address'):
        Settings.from_env()
    monkeypatch.setenv('RENDER_EXTERNAL_URL', ORIGIN)
    settings = Settings.from_env()
    assert settings.public_origin == ORIGIN
    assert PASSWORD not in repr(settings)
    assert 'hidden-model-key' not in repr(replace(settings, openrouter_api_key='hidden-model-key'))


def test_local_workspace_does_not_require_hosted_credentials(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert client.get('/api/runs').status_code == 200
        assert client.post('/api/demo/happy_path', headers={'Origin': 'http://localhost:8000'}).status_code == 202
        assert client.post('/api/demo/happy_path', headers={'Origin': 'https://evil.example'}).status_code == 403
