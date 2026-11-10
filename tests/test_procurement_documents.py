"""Registered PO viewers retain real source lineage without creating new reviews."""
import json
from urllib.parse import quote

import fitz
from fastapi.testclient import TestClient
import pytest

from backend.app.config import Settings
from backend.app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as value:
        yield value


def order(number='PO-SOURCE-1', **changes):
    value = {'po_number': number, 'vendor_id': 'document-supplier', 'vendor_name': 'Document Supplier',
             'po_date': '2026-01-01', 'currency': 'USD', 'subtotal': '1000', 'tax': '0', 'total': '1000',
             'approval_status': 'approved', 'status': 'open', 'description': 'Original supplier order',
             'line_items': [{'description': 'Consulting services', 'quantity': '10', 'unit_price': '100', 'amount': '1000'}]}
    value.update(changes)
    return value


def pdf_order(po):
    labels = {'po_number': 'PO Number', 'vendor_id': 'Vendor ID', 'vendor_name': 'Vendor', 'po_date': 'PO Date',
              'currency': 'Currency', 'subtotal': 'Subtotal', 'tax': 'Tax', 'total': 'Total',
              'approval_status': 'Approval Status', 'status': 'Order Status', 'description': 'Description'}
    text = 'PURCHASE ORDER\n' + '\n'.join(f'{label}: {po[key]}' for key, label in labels.items())
    for line in po['line_items']:
        text += '\n' + ' | '.join(line[key] for key in ('description', 'quantity', 'unit_price', 'amount'))
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((60, 60), text, fontsize=11)
        return document.tobytes()


def upload(client, po=None, *, pdf=False, filename=None):
    po = po or order()
    content = pdf_order(po) if pdf else json.dumps(po).encode()
    filename = filename or ('supplier-order.pdf' if pdf else 'supplier-order.json')
    response = client.post('/api/procurement/uploads', files={'files': (filename, content)})
    assert response.status_code == 202, response.text
    client.app.state.po_workflow.queue.join()
    draft = client.get('/api/procurement/uploads/' + response.json()['uploads'][0]['id']).json()
    assert draft['status'] == 'ready', draft
    assert draft['items'][0]['validation']['valid'], draft
    return draft, content


def confirm(client, draft, **changes):
    body = {'revision': draft['revision'], 'reviewer': 'PO source reviewer',
            'comment': 'Compared the original document and purchasing authorization.',
            'attest_authorized': True, 'allow_updates': False}
    body.update(changes)
    response = client.post(f'/api/procurement/uploads/{draft["id"]}/confirm', json=body)
    assert response.status_code == 200, response.text
    return response.json()['upload']


def review(client, number='PO-SOURCE-1'):
    response = client.post(f'/api/procurement/purchase-orders/{quote(number, safe="")}/review')
    assert response.status_code == 200, response.text
    return response.json()


def context(client, *, number=None, upload_id=None):
    path = (f'/api/procurement/purchase-orders/{quote(number, safe="")}/documents' if number is not None
            else f'/api/procurement/uploads/{upload_id}/documents')
    response = client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


def snapshot(client):
    with client.app.state.store.connection() as conn:
        return '\n'.join(conn.iterdump())


@pytest.mark.parametrize('number', ['PO-SOURCE-1', 'PO/regional-1'])
def test_pdf_is_retained_through_registered_view_and_repeated_reviews(client, number):
    original, content = upload(client, order(number), pdf=True)
    initial = context(client, upload_id=original['id'])
    assert initial['upload_id'] == original['id']
    assert initial['po_source']['source_id'] == original['id']
    assert initial['record_source'] is None
    original = confirm(client, original)
    vendor_before = client.get('/api/procurement').json()['vendors']
    assert next(v for v in vendor_before if v['id'] == 'document-supplier')['approved'] is False

    before = snapshot(client)
    opened = context(client, number=number)
    assert opened['po_number'] == number
    source = opened['po_source']
    assert source['source_id'] == original['id']
    assert source['filename'] == 'supplier-order.pdf'
    assert opened['record_source'] is None
    assert client.get(source['download_url']).content == content
    metadata = client.get(source['preview_url'])
    assert metadata.status_code == 200
    assert metadata.json()['kind'] == 'pdf'
    page = client.get(metadata.json()['page_url'] + '?page=0')
    assert page.status_code == 200
    assert page.content.startswith(b'\x89PNG')
    assert snapshot(client) == before

    first_review = review(client, number)
    assert first_review['review_of_po'] == number
    assert first_review['prior_po_upload_id'] == original['id']
    first_context = context(client, upload_id=first_review['id'])
    assert first_context['po_source']['source_id'] == original['id']
    assert first_context['record_source']['source_id'] == first_review['id']
    assert first_context['record_source']['source_role'] == 'registered_record'
    assert first_context['record_source']['download_url'] != source['download_url']
    confirm(client, first_review)
    registered = context(client, number=number)
    assert registered['po_source']['source_id'] == original['id']
    assert registered['record_source']['source_id'] == first_review['id']

    second_review = review(client, number)
    assert second_review['prior_po_upload_id'] == first_review['id']
    second_context = context(client, upload_id=second_review['id'])
    assert second_context['po_source']['source_id'] == original['id']
    assert second_context['record_source']['source_id'] == second_review['id']
    confirm(client, second_review)
    assert context(client, number=number)['po_source']['source_id'] == original['id']
    assert client.get('/api/procurement').json()['vendors'] == vendor_before


def test_replacement_raw_upload_becomes_current_original_without_rewriting_invoice_history(client):
    original, content = upload(client, pdf=True)
    confirm(client, original)
    response = client.post('/api/runs/structured', json={'invoice': {
        'invoice_number': 'SOURCE-HISTORY', 'vendor_name': 'Document Supplier', 'po_number': 'PO-SOURCE-1',
        'invoice_date': '2026-01-02', 'currency': 'USD', 'subtotal': '100', 'tax': '0', 'total': '100'}})
    assert response.status_code == 202
    client.app.state.pipeline.queue.join()
    run_id = response.json()['runs'][0]['id']
    confirm(client, review(client))
    replacement, _ = upload(client, order(description='Replacement original'), pdf=True, filename='replacement-order.pdf')
    confirm(client, replacement, allow_updates=True)
    assert context(client, number='PO-SOURCE-1')['po_source']['source_id'] == replacement['id']
    new_review = review(client)
    assert context(client, upload_id=new_review['id'])['po_source']['source_id'] == replacement['id']
    historical = client.get(f'/api/runs/{run_id}/documents')
    assert historical.status_code == 200
    assert historical.json()['po_source']['source_id'] == original['id']
    assert client.get(historical.json()['po_source']['download_url']).content == content


def test_missing_original_still_exposes_separate_registered_record_snapshot(client):
    original, _ = upload(client, pdf=True)
    confirm(client, original)
    registered_review = confirm(client, review(client))
    (client.app.state.settings.data_dir / 'po-sources' / f'{original["id"]}.bin').unlink()
    before = snapshot(client)
    for result in [context(client, number='PO-SOURCE-1'), context(client, upload_id=registered_review['id'])]:
        assert result['po_source'] is None
        assert result['po_source_message']
        assert result['record_source']['source_id'] == registered_review['id']
        response = client.get(result['record_source']['preview_url'])
        assert response.status_code == 200
        assert response.json()['kind'] == 'text'
    assert snapshot(client) == before


@pytest.mark.parametrize('origin', ['seed', 'legacy'])
def test_po_without_original_never_presents_generated_review_as_uploaded_document(client, origin):
    number = 'PO-2026-001' if origin == 'seed' else 'PO-SOURCE-1'
    if origin == 'legacy':
        response = client.post('/api/procurement/import', json={'purchase_orders': [order(
            vendor_id='vendor-acme', vendor_name='Acme Cloud Services')]})
        assert response.status_code == 200, response.text
    before = snapshot(client)
    opened = context(client, number=number)
    assert opened['po_source'] is None
    assert opened['po_source_message']
    assert snapshot(client) == before
    draft = review(client, number)
    result = context(client, upload_id=draft['id'])
    assert result['po_source'] is None
    assert result['po_source_message']
    assert result['record_source']['source_id'] == draft['id']
    confirm(client, draft)
    assert context(client, number=number)['po_source'] is None


def test_old_confirmed_review_recovers_original_only_from_its_own_confirm_audit(client):
    original, _ = upload(client, pdf=True)
    confirm(client, original)
    old_review = confirm(client, review(client))
    old_review.pop('review_of_po', None)
    old_review.pop('prior_po_upload_id', None)
    client.app.state.po_workflow.save(old_review)
    before = snapshot(client)
    result = context(client, upload_id=old_review['id'])
    assert result['po_source']['source_id'] == original['id']
    assert result['record_source']['source_id'] == old_review['id']
    assert context(client, number='PO-SOURCE-1')['po_source']['source_id'] == original['id']
    assert snapshot(client) == before


def test_old_unconfirmed_review_without_lineage_does_not_guess_from_current_register(client):
    original, _ = upload(client, pdf=True)
    confirm(client, original)
    old_review = review(client)
    old_review.pop('review_of_po', None)
    old_review.pop('prior_po_upload_id', None)
    client.app.state.po_workflow.save(old_review)
    result = context(client, upload_id=old_review['id'])
    assert result['po_source'] is None
    assert result['record_source']['source_id'] == old_review['id']
    assert result['po_source_message']


@pytest.mark.parametrize('invalid_predecessor', ['wrong_po', 'unconfirmed', 'cycle'])
def test_invalid_review_lineage_never_supplies_an_unrelated_original(client, invalid_predecessor):
    original, _ = upload(client, pdf=True)
    confirm(client, original)
    current = review(client)
    if invalid_predecessor == 'wrong_po':
        other, _ = upload(client, order('OTHER-PO'), pdf=True)
        predecessor = confirm(client, other)
    elif invalid_predecessor == 'unconfirmed':
        predecessor, _ = upload(client, pdf=True)
    else:
        current = confirm(client, current)
        predecessor = current
    current['prior_po_upload_id'] = predecessor['id']
    client.app.state.po_workflow.save(current)
    before = snapshot(client)
    result = context(client, upload_id=current['id'])
    assert result['po_source'] is None
    assert result['po_source_message']
    assert result['record_source']['source_id'] == current['id']
    assert snapshot(client) == before


def test_raw_file_cannot_inject_server_owned_lineage(client):
    original, _ = upload(client, pdf=True)
    confirm(client, original)
    supplied = order('NEW-RAW-PO', review_of_po='PO-SOURCE-1', prior_po_upload_id=original['id'])
    raw, _ = upload(client, supplied)
    assert not raw.get('review_of_po')
    assert not raw.get('prior_po_upload_id')
    result = context(client, upload_id=raw['id'])
    assert result['po_source']['source_id'] == raw['id']
    assert result['record_source'] is None


@pytest.mark.parametrize('path', [
    '/api/procurement/purchase-orders/unknown/documents',
    '/api/procurement/uploads/unknown/documents',
])
def test_unknown_procurement_documents_return_404(client, path):
    assert client.get(path).status_code == 404
