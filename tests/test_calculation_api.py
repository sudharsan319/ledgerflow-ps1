from fastapi.testclient import TestClient
from backend.app.config import Settings
from backend.app.main import create_app
from tests.test_integration_review import submit
from tests.test_calculations import inv


def test_calculations_are_local_and_read_only_and_ai_route_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.app.extraction._model_extract', lambda *args: (_ for _ in ()).throw(AssertionError('No AI calculation call')))
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        run = submit(client, inv(tax=None, subtotal=None, total='1180', line_items=[dict(quantity='10', unit_price='100', amount='1000', tax_rate='18')]))
        before = client.get('/api/procurement').json()
        response = client.get(f'/api/runs/{run["id"]}/calculations')
        assert response.status_code == 200
        data = response.json()
        assert data['rules']['calculated_total'] == '1180.00'
        assert data['rules_basis'] == 'decision_snapshot'
        assert 'ai' not in data and 'ai_available' not in data
        assert client.post(f'/api/runs/{run["id"]}/calculations/ai').status_code in (404, 405)
        assert client.get('/api/runs/' + run['id']).json() == run
        assert client.get('/api/procurement').json() == before
        assert client.get('/api/runs/missing/calculations').status_code == 404


def test_older_calculation_is_labeled_current_preview_and_history_is_untouched(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        run = submit(client, inv())
        run['result']['calculation']['version'] = '2.0'
        run['ai_calculation'] = {'status': 'historical'}
        app.state.store.save_run(run)
        stored = app.state.store.get_run(run['id'])
        data = client.get(f'/api/runs/{run["id"]}/calculations').json()
        assert data['rules_basis'] == 'current_rules_preview'
        assert data['rules']['version'] == '3.4'
        assert 'ai' not in data
        assert app.state.store.get_run(run['id']) == stored


def test_exports_show_only_deterministic_calculation():
    from backend.app.exports import as_html, as_csv
    run = dict(id='example', filename='invoice.json', created_at='2026-01-01', status='completed', decision='needs_review',
               invoice=inv(), result={'calculation': {'status': 'reconciled', 'calculated_total': '1180', 'steps': []}}, reviews=[], source_sha256='hash',
               ai_calculation={'status': 'old', 'calculated_total': '1180', 'warnings': ['<script>alert(1)</script>']})
    assert 'AI calculation' not in as_html(run)
    assert 'ai_calculated_total' not in as_csv([run])
    assert 'Deterministic calculations' in as_html(run)
