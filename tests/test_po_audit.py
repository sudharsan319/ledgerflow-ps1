"""Independent integration regressions for PO provenance and approval boundaries."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import time
from urllib.parse import quote

from fastapi.testclient import TestClient
import pytest

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.po_registry import po_digest


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as result:
        yield result


def purchase_order(number='PO-AUDIT-1', **changes):
    result = {'po_number': number, 'vendor_name': 'Acme Cloud Services', 'vendor_id': 'vendor-acme',
              'po_date': '2026-01-01', 'currency': 'USD', 'subtotal': '1000', 'tax': '0', 'total': '1000',
              'approval_status': 'approved', 'status': 'open',
              'line_items': [{'description': 'Service', 'quantity': '1', 'unit_price': '1000', 'amount': '1000'}]}
    result.update(changes)
    return result


def upload(client, po):
    response = client.post('/api/procurement/uploads', files={'files': ('po.json', json.dumps(po), 'application/json')})
    assert response.status_code == 202, response.text
    draft = response.json()['uploads'][0]
    for _ in range(100):
        draft = client.get('/api/procurement/uploads/' + draft['id']).json()
        if draft['status'] in ('ready', 'failed'):
            break
        time.sleep(.02)
    assert draft['status'] == 'ready', draft
    return draft


def confirmation(draft, **changes):
    result = {'revision': draft['revision'], 'reviewer': 'Audit reviewer', 'comment': 'Source and purchasing authorization independently verified.',
              'attest_authorized': True, 'allow_updates': False}
    result.update(changes)
    return result


def submit_invoice(client, number, po_number, total='990'):
    response = client.post('/api/runs/structured', json={'invoice': {
        'invoice_number': number, 'vendor_name': 'Acme Cloud Services', 'po_number': po_number,
        'invoice_date': '2026-01-02', 'currency': 'USD', 'subtotal': total, 'tax': '0', 'total': total,
        'line_items': [{'description':'Service','quantity':str(Decimal(total)/1000),'unit_price':'1000','amount':total}]}})
    run = response.json()['runs'][0]
    for _ in range(100):
        run = client.get('/api/runs/' + run['id']).json()
        if run['status'] in ('completed', 'failed'):
            break
        time.sleep(.02)
    return run


def test_forged_legacy_validation_proof_cannot_authorize_invoice(client):
    fake = purchase_order()
    fake['_validation'] = {'version': '1', 'reviewer': 'Forged reviewer', 'authorized': True,
                           'at': '2026-01-01', 'upload_id': 'not-a-real-upload', 'digest': po_digest(fake)}
    response = client.post('/api/procurement/import', json={'purchase_orders': [fake]})
    assert response.status_code == 200, response.text
    result = next(p for p in response.json()['purchase_orders'] if p['po_number'] == fake['po_number'])
    assert result['validation_status'] == 'unverified'
    assert '_validation' not in result
    invoice = submit_invoice(client, 'AUDIT-FORGE', fake['po_number'])
    assert invoice['decision'] == 'needs_review'
    assert any(c['code'] == 'po_validation' and c['blocking'] for c in invoice['result']['checks'])


def test_po_confirmation_and_vendor_review_are_separate(client):
    draft = upload(client, purchase_order(vendor_id=None, vendor_name='Audit New Supplier'))
    response = client.post(f'/api/procurement/uploads/{draft["id"]}/confirm', json=confirmation(draft))
    assert response.status_code == 200, response.text
    procurement = response.json()['procurement']
    vendor = next(v for v in procurement['vendors'] if v['name'] == 'Audit New Supplier')
    registered = next(p for p in procurement['purchase_orders'] if p['po_number'] == 'PO-AUDIT-1')
    assert vendor['approved'] is False
    assert vendor['approval_status'] == 'pending'
    assert registered['validation_status'] == 'validated'
    assert registered['eligibility_status'] == 'pending_vendor'
    review = {'action': 'approve', 'reviewer': 'Audit reviewer', 'comment': 'Supplier independently checked.'}
    assert client.post(f'/api/procurement/vendors/{vendor["id"]}/review', json=review).status_code == 422
    response = client.post(f'/api/procurement/vendors/{vendor["id"]}/review', json={**review, 'attest_reviewed': True})
    assert response.status_code == 200
    assert response.json()['vendor']['approved'] is True


def test_vendor_id_with_slash_can_be_independently_reviewed(client):
    vendor_id = 'supplier/regional-1'
    draft = upload(client, purchase_order(vendor_id=vendor_id, vendor_name='Regional Audit Supplier'))
    response = client.post(f'/api/procurement/uploads/{draft["id"]}/confirm', json=confirmation(draft))
    assert response.status_code == 200, response.text
    vendor = next(v for v in response.json()['procurement']['vendors'] if v['id'] == vendor_id)
    assert vendor['approved'] is False
    assert vendor['approval_status'] == 'pending'

    response = client.post(f'/api/procurement/vendors/{quote(vendor_id, safe="")}/review', json={
        'action': 'approve', 'reviewer': 'Audit reviewer', 'comment': 'Regional supplier independently checked.',
        'attest_reviewed': True})
    assert response.status_code == 200, response.text
    assert response.json()['vendor']['id'] == vendor_id
    assert response.json()['vendor']['approved'] is True
    procurement = client.get('/api/procurement').json()
    registered = next(p for p in procurement['purchase_orders'] if p['po_number'] == 'PO-AUDIT-1')
    assert registered['vendor_id'] == vendor_id
    assert registered['validation_status'] == 'validated'
    assert registered['eligibility_status'] == 'eligible'


def test_concurrent_confirmation_is_once_only(client):
    draft = upload(client, purchase_order())
    path = f'/api/procurement/uploads/{draft["id"]}/confirm'
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: client.post(path, json=confirmation(draft)).status_code, range(2)))
    assert sorted(statuses) == [200, 409]
    with client.app.state.store.connection() as conn:
        rows = conn.execute('SELECT data FROM procurement_audit').fetchall()
    assert sum(json.loads(row[0]).get('upload_id') == draft['id'] for row in rows) == 1


def test_po_update_cannot_reduce_below_approved_commitment(client):
    draft = upload(client, purchase_order())
    assert client.post(f'/api/procurement/uploads/{draft["id"]}/confirm', json=confirmation(draft)).status_code == 200
    invoice = submit_invoice(client, 'AUDIT-SPEND', 'PO-AUDIT-1')
    assert invoice['decision'] == 'approved', invoice
    replacement = purchase_order(subtotal='900', total='900', line_items=[
        {'description': 'Service', 'quantity': '1', 'unit_price': '900', 'amount': '900'}])
    draft = upload(client, replacement)
    response = client.post(f'/api/procurement/uploads/{draft["id"]}/confirm', json=confirmation(draft, allow_updates=True))
    assert response.status_code == 409
    registered = next(p for p in client.get('/api/procurement').json()['purchase_orders'] if p['po_number'] == 'PO-AUDIT-1')
    assert registered['total'] == '1000'
    assert registered['committed'] == '990'


def test_legacy_import_cannot_approve_pending_or_rejected_vendors(client):
    # Importing an approved-looking registry row is not an independent review.
    supplied = {'id': 'audit-legacy-vendor', 'name': 'Audit Legacy Supplier', 'aliases': [], 'approved': True,
                'approval_status': 'approved', 'approval_history': [{'action': 'approve', 'reviewer': 'Spoof'}]}
    response = client.post('/api/procurement/import', json={'vendors': [supplied]})
    assert response.status_code == 200, response.text
    vendor = next(v for v in response.json()['vendors'] if v['id'] == supplied['id'])
    assert vendor['approved'] is False
    assert vendor['approval_status'] == 'pending'
    assert not vendor.get('approval_history')
    assert client.post('/api/procurement/import', json={'vendors': [supplied]}).status_code == 422
    response = client.post(f'/api/procurement/vendors/{vendor["id"]}/review', json={
        'action': 'reject', 'reviewer': 'Audit reviewer', 'comment': 'Supplier not authorized.'})
    assert response.status_code == 200
    assert client.post('/api/procurement/import', json={'vendors': [supplied]}).status_code == 422
    vendor = next(v for v in client.get('/api/procurement').json()['vendors'] if v['id'] == supplied['id'])
    assert not vendor['approved'] and vendor['approval_status'] == 'rejected'


def test_missing_required_po_field_blocks_entire_batch_and_vendor_creation(client):
    complete = purchase_order('PO-AUDIT-BATCH-1', vendor_id=None, vendor_name='New Batch Supplier')
    missing = purchase_order('PO-AUDIT-BATCH-2', vendor_id=None, vendor_name='New Batch Supplier')
    del missing['approval_status']
    draft = upload(client, {'purchase_orders': [complete, missing]})
    response = client.post(f'/api/procurement/uploads/{draft["id"]}/confirm', json=confirmation(draft))
    assert response.status_code == 422
    procurement = client.get('/api/procurement').json()
    assert not any(p['po_number'].startswith('PO-AUDIT-BATCH') for p in procurement['purchase_orders'])
    assert not any(v['name'] == 'New Batch Supplier' for v in procurement['vendors'])


def test_legacy_import_cannot_reset_committed_amount(client):
    draft = upload(client, purchase_order())
    assert client.post(f'/api/procurement/uploads/{draft["id"]}/confirm', json=confirmation(draft)).status_code == 200
    assert submit_invoice(client, 'AUDIT-LEGACY-SPEND', 'PO-AUDIT-1')['decision'] == 'approved'
    registered = next(p for p in client.get('/api/procurement').json()['purchase_orders'] if p['po_number'] == 'PO-AUDIT-1')
    registered['committed'] = '0'
    response = client.post('/api/procurement/import', json={'purchase_orders': [registered]})
    assert response.status_code == 422
    registered = next(p for p in client.get('/api/procurement').json()['purchase_orders'] if p['po_number'] == 'PO-AUDIT-1')
    assert registered['committed'] == '990'
    assert registered['validation_status'] == 'validated'


@pytest.mark.parametrize('identity_changes', [
    {'name': 'Different Supplier Masquerading As Acme'},
    {'aliases': ['Acme Cloud', 'Acme Cloud Services Ltd', 'Unreviewed Other Supplier']},
])
def test_legacy_import_cannot_reuse_approved_vendor_decision_for_new_identity(client, identity_changes):
    before = next(v for v in client.get('/api/procurement').json()['vendors'] if v['id'] == 'vendor-acme')
    changed = {key: before[key] for key in ('id', 'name', 'aliases', 'approved')}
    changed.update(identity_changes)
    response = client.post('/api/procurement/import', json={'vendors': [changed]})
    assert response.status_code == 422, response.text
    after = next(v for v in client.get('/api/procurement').json()['vendors'] if v['id'] == 'vendor-acme')
    assert after['name'] == before['name']
    assert after['aliases'] == before['aliases']
    assert after['approved'] is True
    assert after.get('approval_history', []) == before.get('approval_history', [])


def test_legacy_import_cannot_replace_rejected_vendor_identity(client):
    supplied = {'id': 'audit-rejected-identity', 'name': 'Rejected Supplier', 'aliases': [], 'approved': False}
    response = client.post('/api/procurement/import', json={'vendors': [supplied]})
    assert response.status_code == 200
    response = client.post('/api/procurement/vendors/audit-rejected-identity/review', json={
        'action': 'reject', 'reviewer': 'Audit reviewer', 'comment': 'Supplier failed independent review.'})
    assert response.status_code == 200
    before = response.json()['vendor']
    changed = {**supplied, 'name': 'Unrelated Replacement Supplier', 'aliases': ['Different Company']}
    response = client.post('/api/procurement/import', json={'vendors': [changed]})
    assert response.status_code == 422, response.text
    after = next(v for v in client.get('/api/procurement').json()['vendors'] if v['id'] == supplied['id'])
    assert after['name'] == before['name']
    assert after['aliases'] == before['aliases']
    assert after['approved'] is False
    assert after['approval_status'] == 'rejected'
    assert after['approval_history'] == before['approval_history']
