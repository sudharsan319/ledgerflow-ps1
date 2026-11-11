"""Optional end-to-end test against an isolated PostgreSQL test database."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not os.getenv('LEDGERFLOW_TEST_DATABASE_URL'), reason='No PostgreSQL test database configured')
def test_uploaded_records_and_sources_survive_new_instance():
    import psycopg

    database_url = os.environ['LEDGERFLOW_TEST_DATABASE_URL']
    schema = f'ledgerflow_test_{uuid4().hex}'
    parsed = urlsplit(database_url)
    assert parsed.scheme in ('postgres', 'postgresql')
    query = urlencode([*parse_qsl(parsed.query), ('options', f'-csearch_path={schema}')])
    scoped_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))
    invoice = (ROOT / 'samples/01-clean-invoice.pdf').read_bytes()
    po = (ROOT / 'samples/purchase-orders/complete-purchase-order.pdf').read_bytes()
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA {schema}')
        try:
            with TemporaryDirectory() as first:
                with TestClient(create_app(Settings(data_dir=Path(first), database_url=scoped_url))) as client:
                    run_response = client.post('/api/runs/upload', files={'files': ('invoice.pdf', invoice, 'application/pdf')})
                    assert run_response.status_code == 202
                    run_id = run_response.json()['runs'][0]['id']
                    client.app.state.pipeline.queue.join()
                    po_response = client.post('/api/procurement/uploads', files={'files': ('po.pdf', po, 'application/pdf')})
                    assert po_response.status_code == 202
                    upload_id = po_response.json()['uploads'][0]['id']
                    client.app.state.po_workflow.queue.join()
                    assert client.get('/api/runs/' + run_id).json()['status'] == 'completed'
                    assert client.get('/api/procurement/uploads/' + upload_id).json()['status'] == 'ready'
            with TemporaryDirectory() as second:
                with TestClient(create_app(Settings(data_dir=Path(second), database_url=scoped_url))) as client:
                    assert client.get('/api/health').json()['storage_mode'] == 'postgres'
                    assert client.get('/api/runs/' + run_id).status_code == 200
                    assert client.get('/api/procurement/uploads/' + upload_id).status_code == 200
                    assert client.get('/api/runs/' + run_id + '/source').content == invoice
                    assert client.get('/api/procurement/uploads/' + upload_id + '/source').content == po
                    assert client.get('/api/runs/' + run_id + '/source/preview').status_code == 200
                    assert client.get('/api/procurement/uploads/' + upload_id + '/source/preview').status_code == 200
                    draft = client.get('/api/procurement/uploads/' + upload_id).json()
                    validated = client.post('/api/procurement/uploads/' + upload_id + '/validate', json={
                        'revision': draft['revision'],
                        'purchase_orders': [item['purchase_order'] for item in draft['items']],
                    })
                    assert validated.status_code == 200
                    confirmed = client.post('/api/procurement/uploads/' + upload_id + '/confirm', json={
                        'revision': validated.json()['revision'], 'reviewer': 'Fixture reviewer',
                        'comment': 'Verified this fictional purchase order fixture.', 'attest_authorized': True,
                    })
                    assert confirmed.status_code == 200
                    vendor = next(v for v in confirmed.json()['procurement']['vendors'] if v['name'] == 'Sample Stationery Ltd')
                    approved = client.post('/api/procurement/vendors/' + vendor['id'] + '/review', json={
                        'action': 'approve', 'reviewer': 'Fixture reviewer',
                        'comment': 'Reviewed this fictional supplier fixture.', 'attest_reviewed': True,
                    })
                    assert approved.status_code == 200
                    preview = client.get('/api/runs/' + run_id + '/deletion-preview').json()
                    deleted = client.request('DELETE', '/api/runs/' + run_id,
                                             json={'expected_fingerprint': preview['fingerprint']})
                    assert deleted.status_code == 200
                    assert client.get('/api/runs/' + run_id + '/source').status_code == 404
            with TemporaryDirectory() as third:
                with TestClient(create_app(Settings(data_dir=Path(third), database_url=scoped_url))) as client:
                    data = client.get('/api/procurement').json()
                    assert next(v for v in data['vendors'] if v['id'] == vendor['id'])['approved'] is True
                    assert next(p for p in data['purchase_orders'] if p['po_number'] == 'PO-UPLOAD-1001')['eligibility_status'] == 'eligible'
                    assert client.get('/api/procurement/uploads/' + upload_id + '/source').content == po
        finally:
            admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
