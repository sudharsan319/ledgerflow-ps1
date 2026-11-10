import io
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.store import Store


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as value:
        yield value


def wait(client, run):
    for _ in range(250):
        found = client.get('/api/runs/' + run['id']).json()
        if found['status'] in ('completed', 'failed'):
            return found
        time.sleep(0.01)
    raise AssertionError('Worker did not finish')


def demo(client, scenario):
    response = client.post('/api/demo/' + scenario)
    assert response.status_code == 202, response.text
    return [wait(client, run) for run in response.json()['runs']]


def invoice(number='INV-API-1', **changes):
    result = dict(invoice_number=number, vendor_name='Acme Cloud Services', po_number='PO-2026-001',
                invoice_date='2026-01-01', currency='USD', subtotal='1000', tax='100', total='1100',
                line_items=[dict(description='Cloud services',quantity='10',unit_price='100',amount='1000')])
    result.update(changes)
    return result


def submit(client, data):
    response = client.post('/api/runs/structured', json={'invoice': data})
    assert response.status_code == 202, response.text
    return wait(client, response.json()['runs'][0])


def test_all_demo_decisions_and_commitments(client):
    assert demo(client, 'happy_path')[0]['decision'] == 'approved'
    assert [r['decision'] for r in demo(client, 'duplicate')] == ['approved', 'rejected']
    split = demo(client, 'split_overage')
    assert [r['decision'] for r in split] == ['approved', 'needs_review']
    po_number = split[0]['result']['matched_po']['po_number']
    pos = client.get('/api/procurement').json()['purchase_orders']
    assert next(p['committed'] for p in pos if p['po_number'] == po_number) == '600'
    assert demo(client, 'ambiguous_po')[0]['decision'] == 'needs_review'
    assert demo(client, 'missing_fields')[0]['decision'] == 'needs_review'


def test_real_uploads_batch_bad_format_and_corruption(client):
    data = [invoice('BATCH-1'), invoice('BATCH-2')]
    response = client.post('/api/runs/upload', files=[('files', ('batch.json', json.dumps(data), 'application/json'))])
    parent = wait(client, response.json()['runs'][0])
    for _ in range(100):
        runs = client.get('/api/runs').json()['runs']
        if len(runs) == 2 and all(r['status'] == 'completed' for r in runs):
            break
        time.sleep(0.01)
    assert len(runs) == 2
    assert all(r['decision'] == 'approved' for r in runs)
    assert parent['batch_size'] == 2
    assert client.post('/api/runs/upload', files={'files': ('x.exe', b'no')}).status_code == 415
    response = client.post('/api/runs/upload', files={'files': ('bad.pdf', b'not a pdf')})
    assert wait(client, response.json()['runs'][0])['status'] == 'failed'


def test_review_rechecks_balance_and_rejection_releases(client):
    first, second = demo(client, 'split_overage')
    body = {'action': 'approve', 'reviewer': 'AP reviewer', 'comment': 'Verified the invoice.'}
    assert client.post(f'/api/runs/{second["id"]}/review', json=body).status_code == 409
    body['action'] = 'reject'
    assert client.post(f'/api/runs/{first["id"]}/review', json=body).status_code == 200
    body['action'] = 'approve'
    response = client.post(f'/api/runs/{second["id"]}/review', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['decision'] == 'approved'


def test_ambiguous_po_can_be_resolved_with_review(client):
    run = demo(client, 'ambiguous_po')[0]
    procurement = client.get('/api/procurement').json()
    vendor_id = run['result']['matched_vendor']['id']
    po = next(p for p in procurement['purchase_orders'] if p['vendor_id'] == vendor_id)
    response = client.post(f'/api/runs/{run["id"]}/review', json={
        'action': 'approve', 'reviewer': 'AP reviewer', 'comment': 'Matched to the purchase agreement.', 'po_number': po['po_number']})
    assert response.status_code == 200, response.text
    assert response.json()['decision'] == 'approved'
    assert response.json()['original_invoice']['po_number'] is None


def test_correction_preserves_original_and_reprocesses(client):
    run = demo(client, 'missing_fields')[0]
    data = dict(run['invoice'], invoice_number='CORRECTED-1', total='1100')
    response = client.post(f'/api/runs/{run["id"]}/correct', json={
        'invoice': data, 'reviewer': 'AP reviewer', 'comment': 'Verified identifier and total against original invoice.'})
    assert response.status_code == 202, response.text
    corrected = wait(client, response.json()['runs'][0])
    assert corrected['decision'] == 'approved', corrected
    old = client.get(f'/api/runs/{run["id"]}').json()
    assert old['invoice']['invoice_number'] is None
    assert old['decision'] == 'rejected'
    assert old['superseded_by'] == corrected['id']
    assert corrected['correction_of'] == old['id']
    assert client.post(f'/api/runs/{old["id"]}/review', json={
        'action': 'approve', 'reviewer': 'AP', 'comment': 'Should not work'}).status_code == 409


def test_duplicate_concurrent_submissions(client):
    with ThreadPoolExecutor(max_workers=5) as pool:
        runs = list(pool.map(lambda _: submit(client, invoice('CONCURRENT-1')), range(5)))
    assert sum(r['decision'] == 'approved' for r in runs) == 1
    assert sum(r['decision'] == 'rejected' for r in runs) == 4


def test_exports_escape_injection_and_dashboard(client):
    run = submit(client, invoice('=HYPERLINK("http://evil")'))
    csv = client.get(f'/api/runs/{run["id"]}/export?format=csv')
    assert "'=HYPERLINK" in csv.text
    output = client.get(f'/api/runs/{run["id"]}/export?format=html')
    assert '<html' in output.text and 'Decision checks' in output.text
    all_runs = client.get('/api/runs/export?format=json').json()
    assert len(all_runs) == 1
    assert client.get('/api/dashboard').json()['total_approved_by_currency']['USD'] == '1100'


def test_procurement_import_atomic_and_committed_immutable(client):
    submit(client, invoice())
    procurement = client.get('/api/procurement').json()
    po = next(p for p in procurement['purchase_orders'] if p['po_number'] == 'PO-2026-001')
    po['committed'] = '0'
    response = client.post('/api/procurement/import', json={'vendors': [
        {'id': 'new-vendor', 'name': 'New Vendor', 'approved': True}], 'purchase_orders': [po]})
    assert response.status_code == 422
    assert all(v['id'] != 'new-vendor' for v in client.get('/api/procurement').json()['vendors'])


def test_config_limits_and_origin_boundary(client):
    assert 'openrouter_api_key' not in client.get('/api/health').text
    assert client.put('/api/policy', json={'tolerance_percent': '-1'}).status_code == 422
    assert client.put('/api/policy', json={'min_confidence': .1}).status_code == 422
    assert client.put('/api/policy', json={'min_confidence': .74}).status_code == 422
    assert client.put('/api/policy', json={'min_confidence': .75}).status_code == 200
    assert client.get('/api/procurement').json()['policy']['min_confidence'] == .75
    assert client.post('/api/demo/happy_path', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/api/runs/not-present').status_code == 404


def test_existing_default_confidence_migrates_once(tmp_path):
    path = tmp_path / 'ledgerflow.sqlite3'
    store = Store(path)
    with store.connection(write=True) as conn:
        old_policy = {**store.procurement(conn)['policy'], 'min_confidence': .85}
        conn.execute("UPDATE settings SET data=? WHERE id='policy'", (json.dumps(old_policy),))
        conn.execute("DELETE FROM settings WHERE id='confidence_threshold_075_migrated'")
    migrated = Store(path)
    assert migrated.procurement()['policy']['min_confidence'] == .75
    with migrated.connection(write=True) as conn:
        chosen_policy = {**migrated.procurement(conn)['policy'], 'min_confidence': .85}
        conn.execute("UPDATE settings SET data=? WHERE id='policy'", (json.dumps(chosen_policy),))
    assert Store(path).procurement()['policy']['min_confidence'] == .85


def test_restart_recovery(tmp_path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    from backend.app.pipeline import new_run
    queued = new_run('lost.json', 'abc')
    app.state.store.save_run(queued)
    with TestClient(app) as client:
        run = client.get('/api/runs/' + queued['id']).json()
        assert run['status'] == 'failed'
        assert 'restart' in run['error']


def test_source_download_and_invalid_po_prices(client):
    run = submit(client, invoice('SOURCE-1'))
    source = client.get(f'/api/runs/{run["id"]}/source')
    assert source.status_code == 200
    assert json.loads(source.content)['invoice_number'] == 'SOURCE-1'
    po = {'po_number': 'BAD-LINES', 'vendor_id': 'vendor-acme', 'currency': 'USD', 'total': '1000',
          'status': 'open', 'line_items': [{'sku': 'sku-1', 'unit_price': 'nonsense', 'quantity': '1'}]}
    assert client.post('/api/procurement/import', json={'purchase_orders': [po]}).status_code == 422
    po['line_items'] = [{'sku': 'sku-1', 'unit_price': '10'}, {'sku': 'SKU-1', 'unit_price': '20'}]
    assert client.post('/api/procurement/import', json={'purchase_orders': [po]}).status_code == 422


def test_streaming_body_limit(tmp_path):
    from backend.app.middleware import RequestBodyLimit
    app = create_app(Settings(data_dir=tmp_path))
    app.add_middleware(RequestBodyLimit, max_bytes=100)
    with TestClient(app) as client:
        response = client.post('/api/runs/structured', content=iter([b'{"invoice":', b' ' * 101, b'{}}']),
                               headers={'Content-Type': 'application/json'})
        assert response.status_code == 413
