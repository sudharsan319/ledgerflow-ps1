import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.po_workflow import EXAMPLE_PO


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        yield client


def example(**updates):
    result = deepcopy(EXAMPLE_PO)
    result.update(updates)
    return result


def upload(client, orders=None, filename='po.json', content=None):
    raw = json.dumps({'purchase_orders': orders or [example()]}).encode() if content is None else content
    response = client.post('/api/procurement/uploads', files={'files': (filename, raw)})
    assert response.status_code == 202, response.text
    client.app.state.po_workflow.queue.join()
    return client.get('/api/procurement/uploads/' + response.json()['uploads'][0]['id']).json()


def confirm(client, draft, **updates):
    body = {'revision': draft['revision'], 'reviewer': 'PO reviewer', 'comment': 'Verified the purchasing authorization and source figures.', 'attest_authorized': True, 'allow_updates': False}
    body.update(updates)
    return client.post('/api/procurement/uploads/' + draft['id'] + '/confirm', json=body)


def invoice(client, number='PO-INVOICE', po='PO-UPLOAD-1001', vendor='Sample Stationery Ltd'):
    body = {'invoice_number': number, 'vendor_name': vendor, 'po_number': po, 'invoice_date': '2026-11-09',
            'currency': 'USD', 'subtotal': '1000', 'tax': '100', 'total': '1100',
            'line_items': [{'description': 'Office supplies', 'quantity': '10', 'unit_price': '100', 'amount': '1000'}]}
    response = client.post('/api/runs/structured', json={'invoice': body})
    client.app.state.pipeline.queue.join()
    return client.get('/api/runs/' + response.json()['runs'][0]['id']).json()


def edit(client, draft, orders):
    response = client.post('/api/procurement/uploads/' + draft['id'] + '/validate', json={'revision': draft['revision'], 'purchase_orders': orders})
    assert response.status_code == 200, response.text
    return response.json()


def test_po_and_vendor_approval_are_independent(client):
    draft = upload(client)
    assert draft['status'] == 'ready' and draft['items'][0]['validation']['valid']
    assert all(v['name'] != 'Sample Stationery Ltd' for v in client.get('/api/procurement').json()['vendors'])
    assert confirm(client, draft, attest_authorized=False).status_code == 422
    response = confirm(client, draft)
    assert response.status_code == 200, response.text
    data = response.json()['procurement']
    vendor = next(v for v in data['vendors'] if v['name'] == 'Sample Stationery Ltd')
    po = next(p for p in data['purchase_orders'] if p['po_number'] == 'PO-UPLOAD-1001')
    assert vendor['approved'] is False and vendor['approval_status'] == 'pending'
    assert po['validation_status'] == 'validated' and po['eligibility_status'] == 'pending_vendor'
    run = invoice(client)
    assert run['decision'] == 'needs_review'
    body = {'action': 'approve', 'reviewer': 'Vendor reviewer', 'comment': 'Supplier reviewed independently of the PO.', 'attest_reviewed': False}
    assert client.post('/api/procurement/vendors/' + vendor['id'] + '/review', json=body).status_code == 422
    body['attest_reviewed'] = True
    assert client.post('/api/procurement/vendors/' + vendor['id'] + '/review', json=body).status_code == 200
    # The same invoice is reviewed; no second submission defeats duplicate protection.
    response = client.post('/api/runs/' + run['id'] + '/review', json={'action': 'approve', 'reviewer': 'AP reviewer', 'comment': 'Vendor approval is now recorded.'})
    assert response.status_code == 200 and response.json()['decision'] == 'approved'
    assert confirm(client, draft).status_code == 409


def test_invalid_batch_and_stale_revision_do_not_write_registry(client):
    bad = example(po_number='INVALID-PO', po_date=None)
    draft = upload(client, [example(), bad])
    assert not draft['items'][1]['validation']['valid']
    assert confirm(client, draft).status_code == 422
    assert len(client.get('/api/procurement').json()['purchase_orders']) == 3
    revised = edit(client, draft, [example(), example(po_number='SECOND-PO')])
    assert revised['revision'] > draft['revision']
    assert confirm(client, draft).status_code == 409
    assert confirm(client, revised).status_code == 200


def test_existing_vendor_approval_never_changes_from_po_file(client):
    body = {'vendors': [{'id': 'vendor-acme', 'name': 'Acme Cloud Services', 'approved': False}],
            'purchase_orders': [example(vendor_id='vendor-acme', vendor_name='Acme Cloud Services')]}
    draft = upload(client, content=json.dumps(body).encode())
    result = confirm(client, draft)
    assert result.status_code == 200, result.text
    assert next(v for v in result.json()['procurement']['vendors'] if v['id'] == 'vendor-acme')['approved'] is True


def test_existing_rejected_vendor_stays_rejected(client):
    client.post('/api/procurement/vendors/vendor-acme/review', json={'action': 'reject', 'reviewer': 'Reviewer', 'comment': 'Supplier is blocked.'})
    draft = upload(client, [example(vendor_id='vendor-acme', vendor_name='Acme Cloud Services')])
    result = confirm(client, draft)
    assert result.status_code == 200
    vendor = next(v for v in result.json()['procurement']['vendors'] if v['id'] == 'vendor-acme')
    assert vendor['approved'] is False and vendor['approval_status'] == 'rejected'


def test_replacing_committed_po_preserves_balance_and_blocks_reassignment(client):
    order = example(vendor_id='vendor-acme', vendor_name='Acme Cloud Services')
    draft = upload(client, [order])
    assert confirm(client, draft).status_code == 200
    assert invoice(client, vendor='Acme Cloud Services')['decision'] == 'approved'
    smaller = example(vendor_id='vendor-acme', vendor_name='Acme Cloud Services', subtotal='500', tax='0', total='500',
                      line_items=[{'description': 'Reduced order', 'quantity': '1', 'unit_price': '500', 'amount': '500'}])
    revised = upload(client, [smaller])
    assert confirm(client, revised).status_code == 409
    assert confirm(client, revised, allow_updates=True).status_code == 409
    reassigned = edit(client, revised, [example(vendor_name='Different vendor')])
    assert confirm(client, reassigned, allow_updates=True).status_code == 409
    po = next(p for p in client.get('/api/procurement').json()['purchase_orders'] if p['po_number'] == order['po_number'])
    assert po['committed'] == '1100' and po['total'] == '1100.00'
    assert all(v['name'] != 'Different vendor' for v in client.get('/api/procurement').json()['vendors'])


def test_closed_or_pending_pos_cannot_approve_invoices(client):
    order = example(vendor_id='vendor-acme', vendor_name='Acme Cloud Services', approval_status='pending')
    draft = upload(client, [order])
    assert draft['items'][0]['validation']['valid'] and not draft['items'][0]['validation']['eligible']
    response = confirm(client, draft)
    assert response.status_code == 200
    assert invoice(client, vendor='Acme Cloud Services')['decision'] == 'needs_review'


def test_legacy_metadata_cannot_forge_validation(client):
    seed = client.get('/api/procurement').json()['purchase_orders'][0]
    order = {**example(vendor_id='vendor-acme', vendor_name='Acme Cloud Services'), '_validation': seed['_validation'], 'validation_status': 'validated'}
    assert client.post('/api/procurement/import', json={'purchase_orders': [order]}).status_code == 200
    registered = next(p for p in client.get('/api/procurement').json()['purchase_orders'] if p['po_number'] == order['po_number'])
    assert registered['validation_status'] == 'unverified'
    assert invoice(client, vendor='Acme Cloud Services')['decision'] == 'needs_review'
    draft = client.post('/api/procurement/purchase-orders/' + order['po_number'] + '/review').json()
    assert confirm(client, draft).status_code == 200


def test_csv_xlsx_templates_are_real_ingestable_files(client):
    for format in ('csv', 'xlsx', 'json'):
        response = client.get('/api/procurement/template?format=' + format)
        assert response.status_code == 200
        draft = upload(client, filename='po.' + format, content=response.content)
        assert draft['status'] == 'ready', draft
        assert draft['items'][0]['validation']['valid'], draft
        assert client.get('/api/procurement/uploads/' + draft['id'] + '/source').content == response.content


def test_concurrent_confirmation_has_one_commit(client):
    draft = upload(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: confirm(client, draft), range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert len([p for p in client.get('/api/procurement').json()['purchase_orders'] if p['po_number'] == 'PO-UPLOAD-1001']) == 1


def test_conflicting_batch_vendor_ids_rollback(client):
    draft = upload(client, [example(vendor_id='new-v1'), example(po_number='NEW-2', vendor_id='new-v2')])
    response = confirm(client, draft)
    assert response.status_code == 409
    assert all(v['id'] not in ('new-v1', 'new-v2') for v in client.get('/api/procurement').json()['vendors'])


def test_invalid_files_fail_visibly_and_policy_is_available(client):
    draft = upload(client, filename='bad.pdf', content=b'not pdf')
    assert draft['status'] == 'failed' and draft['error']
    assert client.post('/api/procurement/uploads', files={'files': ('unsafe.exe', b'x')}).status_code == 415
    assert client.get('/api/procurement/po-policy').json()['version'] == '1'
