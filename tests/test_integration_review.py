"""Financial transaction and boundary checks from independent integration review."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.app.config import Settings
from backend.app.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def bundled(number, total='600'):
    return {'invoice_number': number, 'vendor_name': 'Review Vendor', 'po_number': 'REVIEW-PO',
            'invoice_date': '2026-01-01', 'currency': 'USD', 'total': total}


def seed(client):
    result = client.post('/api/procurement/import', json={
        'vendors': [{'id': 'review-v', 'name': 'Review Vendor', 'approved': True}],
        'purchase_orders': [{'po_number': 'REVIEW-PO', 'vendor_id': 'review-v', 'currency': 'USD',
                             'total': '1000', 'status': 'open'}],
    })
    assert result.status_code == 200
    approved = client.post('/api/procurement/vendors/review-v/review', json={
        'action': 'approve', 'reviewer': 'Fixture vendor reviewer', 'comment': 'Fictional supplier independently checked.', 'attest_reviewed': True})
    assert approved.status_code == 200
    draft = client.post('/api/procurement/purchase-orders/REVIEW-PO/review').json()
    po = draft['items'][0]['purchase_order']
    po.update(po_date='2026-01-01', subtotal='1000', tax='0', approval_status='approved',
              line_items=[{'description': 'Authorized test budget', 'quantity': '1', 'unit_price': '1000', 'amount': '1000'}])
    edited = client.post('/api/procurement/uploads/' + draft['id'] + '/validate', json={
        'revision': draft['revision'], 'purchase_orders': [po]}).json()
    confirmed = client.post('/api/procurement/uploads/' + draft['id'] + '/confirm', json={
        'revision': edited['revision'], 'reviewer': 'Fixture PO reviewer', 'comment': 'Fictional purchasing authorization verified.',
        'attest_authorized': True, 'allow_updates': True})
    assert confirmed.status_code == 200, confirmed.text


def submit(client, invoice):
    result = client.post('/api/runs/structured', json={'invoice': invoice})
    assert result.status_code == 202
    client.app.state.pipeline.queue.join()
    return client.get('/api/runs/' + result.json()['runs'][0]['id']).json()


def approve(client, run):
    return client.post('/api/runs/' + run['id'] + '/review', json={
        'action': 'approve', 'reviewer': 'Reviewer', 'comment': 'Verified against vendor source',
    })


def test_concurrent_reviews_cannot_overcommit_po(client):
    seed(client)
    runs = [submit(client, bundled('R-1')), submit(client, bundled('R-2'))]
    assert all(run['decision'] == 'needs_review' for run in runs)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda run: approve(client, run), runs))
    assert sorted(response.status_code for response in responses) == [200, 409]
    po = next(po for po in client.get('/api/procurement').json()['purchase_orders'] if po['po_number'] == 'REVIEW-PO')
    assert Decimal(po['committed']) == Decimal('600')


def test_duplicate_review_cannot_bypass_identity_check(client):
    seed(client)
    first = submit(client, bundled('R-1', '100'))
    second = submit(client, {**bundled('R-1', '100'), 'notes': 'same identity, different bytes'})
    assert first['decision'] == 'needs_review'
    assert second['decision'] == 'rejected'
    assert approve(client, second).status_code == 409
    assert approve(client, first).status_code == 200
    assert approve(client, second).status_code == 409


def test_rejecting_approved_invoice_releases_commitment(client):
    seed(client)
    run = submit(client, bundled('R-1'))
    assert approve(client, run).status_code == 200
    response = client.post('/api/runs/' + run['id'] + '/review', json={
        'action': 'reject', 'reviewer': 'Reviewer', 'comment': 'Vendor canceled invoice',
    })
    assert response.status_code == 200
    po = next(po for po in client.get('/api/procurement').json()['purchase_orders'] if po['po_number'] == 'REVIEW-PO')
    assert Decimal(po['committed']) == 0


def test_corrected_resubmission_cannot_overwrite_approved_run(client):
    seed(client)
    run = submit(client, bundled('R-1'))
    assert approve(client, run).status_code == 200
    response = client.post('/api/runs/' + run['id'] + '/correct', json={
        'invoice': bundled('R-2'), 'reviewer': 'Reviewer', 'comment': 'Changed invoice',
    })
    assert response.status_code == 409
