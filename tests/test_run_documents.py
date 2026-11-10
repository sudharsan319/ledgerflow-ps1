"""Document-tab integration: source lineage, historical matches, and read-only access."""
from copy import deepcopy
import hashlib
import json

import fitz
from fastapi.testclient import TestClient
import pytest

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.pipeline import new_run


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as value:
        yield value


def invoice(number='DOC-INVOICE-1', **changes):
    result = {'invoice_number': number, 'vendor_name': 'Acme Cloud Services',
              'po_number': 'PO-2026-001', 'invoice_date': '2026-01-02',
              'currency': 'USD', 'subtotal': '100', 'tax': '0', 'total': '100',
              'line_items': [{'description': 'Cloud services', 'quantity': '1', 'unit_price': '100', 'amount': '100'}]}
    result.update(changes)
    return result


def purchase_order(**changes):
    result = {'po_number': 'PO-DOCUMENT-1', 'vendor_id': 'vendor-acme', 'vendor_name': 'Acme Cloud Services',
              'po_date': '2026-01-01', 'currency': 'USD', 'subtotal': '1000', 'tax': '0', 'total': '1000',
              'approval_status': 'approved', 'status': 'open', 'description': 'Original purchasing agreement',
              'line_items': [{'description': 'Cloud services', 'quantity': '10', 'unit_price': '100', 'amount': '1000'}]}
    result.update(changes)
    return result


def upload_invoice(client, content, filename='supplier-invoice.json'):
    response = client.post('/api/runs/upload', files={'files': (filename, content)})
    assert response.status_code == 202, response.text
    client.app.state.pipeline.queue.join()
    return client.get('/api/runs/' + response.json()['runs'][0]['id']).json()


def submit_invoice(client, **changes):
    return upload_invoice(client, json.dumps(invoice(**changes)).encode())


def upload_po(client, order=None, *, confirm=True, allow_updates=False, filename='supplier-po.json'):
    content = json.dumps(order or purchase_order()).encode()
    response = client.post('/api/procurement/uploads', files={'files': (filename, content)})
    assert response.status_code == 202, response.text
    client.app.state.po_workflow.queue.join()
    upload_id = response.json()['uploads'][0]['id']
    draft = client.get('/api/procurement/uploads/' + upload_id).json()
    assert draft['status'] == 'ready', draft
    if confirm:
        response = client.post(f'/api/procurement/uploads/{upload_id}/confirm', json={
            'revision': draft['revision'], 'reviewer': 'Document reviewer',
            'comment': 'Checked the source and purchasing authorization.',
            'attest_authorized': True, 'allow_updates': allow_updates})
        assert response.status_code == 200, response.text
        draft = response.json()['upload']
    return draft, content


def documents(client, run_id):
    response = client.get(f'/api/runs/{run_id}/documents')
    assert response.status_code == 200, response.text
    return response.json()


def db_snapshot(client):
    with client.app.state.store.connection() as conn:
        return '\n'.join(conn.iterdump())


def test_seed_match_has_original_invoice_and_honest_missing_po_attachment(client):
    run = submit_invoice(client)
    result = documents(client, run['id'])
    assert result['run_id'] == run['id']
    assert result['status'] == run['status']
    assert result['matched_po'] == run['result']['matched_po']
    assert result['matched_vendor'] == run['result']['matched_vendor']
    source = result['invoice_source']
    assert source['source_id'] == run['id']
    assert source['filename'] == 'supplier-invoice.json'
    assert source['source_role']
    assert source['source_sha256'] == run['source_sha256']
    assert source['preview_url'] == f'/api/runs/{run["id"]}/source/preview'
    assert source['download_url'] == f'/api/runs/{run["id"]}/source'
    assert result['processed_input'] is None
    assert result['po_source'] is None
    assert result['po_source_message']
    preview = client.get(source['preview_url'])
    assert preview.status_code == 200, preview.text
    assert preview.json()['kind'] == 'text'
    assert json.loads(preview.json()['text'])['invoice_number'] == 'DOC-INVOICE-1'
    original = client.get(source['download_url'])
    assert original.status_code == 200
    assert original.headers['content-disposition'].startswith('attachment;')


def test_matched_po_source_and_vendor_remain_historical_after_registry_changes(client):
    first_upload, first_content = upload_po(client)
    run = submit_invoice(client, po_number='PO-DOCUMENT-1')
    assert run['decision'] == 'approved', run
    expected_po = deepcopy(run['result']['matched_po'])
    expected_vendor = deepcopy(run['result']['matched_vendor'])
    replacement = purchase_order(description='Replacement agreement', subtotal='2000', total='2000',
        line_items=[{'description': 'Cloud services', 'quantity': '20', 'unit_price': '100', 'amount': '2000'}])
    second_upload, _ = upload_po(client, replacement, allow_updates=True, filename='replacement-po.json')
    assert second_upload['id'] != first_upload['id']
    response = client.post('/api/procurement/vendors/vendor-acme/review', json={
        'action': 'reject', 'reviewer': 'Vendor reviewer', 'comment': 'Supplier no longer authorized.'})
    assert response.status_code == 200

    result = documents(client, run['id'])
    assert result['matched_po'] == expected_po
    assert result['matched_vendor'] == expected_vendor
    source = result['po_source']
    assert source['source_id'] == first_upload['id']
    assert source['filename'] == 'supplier-po.json'
    assert source['source_sha256'] == hashlib.sha256(first_content).hexdigest()
    assert source['preview_url'] == f'/api/procurement/uploads/{first_upload["id"]}/source/preview'
    preview = client.get(source['preview_url'])
    assert preview.status_code == 200, preview.text
    assert preview.json()['kind'] == 'text'
    assert json.loads(preview.json()['text'])['description'] == 'Original purchasing agreement'
    original = client.get(source['download_url'])
    assert original.content == first_content
    assert original.headers['content-disposition'].startswith('attachment;')


def test_correction_chain_keeps_original_document_and_separate_processed_json(client):
    original_content = json.dumps(invoice(number=None)).encode()
    original = upload_invoice(client, original_content, 'original-supplier-invoice.json')
    assert original['decision'] == 'needs_review'
    current = original
    for corrected_invoice in [invoice(number=None, due_date='2026-02-01'), invoice(number='CORRECTED-DOCUMENT')]:
        response = client.post(f'/api/runs/{current["id"]}/correct', json={
            'invoice': corrected_invoice, 'reviewer': 'Invoice reviewer',
            'comment': 'Compared the correction against the original source.'})
        assert response.status_code == 202, response.text
        client.app.state.pipeline.queue.join()
        current = client.get('/api/runs/' + response.json()['runs'][0]['id']).json()
    result = documents(client, current['id'])
    assert result['invoice_source']['source_id'] == original['id']
    assert result['invoice_source']['filename'] == 'original-supplier-invoice.json'
    assert result['invoice_source']['source_sha256'] == hashlib.sha256(original_content).hexdigest()
    assert client.get(result['invoice_source']['download_url']).content == original_content
    processed = result['processed_input']
    assert processed['source_id'] == current['id']
    assert processed['filename'] == 'corrected-invoice.json'
    assert processed['download_url'] != result['invoice_source']['download_url']
    assert json.loads(client.get(processed['download_url']).content)['invoice_number'] == 'CORRECTED-DOCUMENT'


def test_batch_child_resolves_parent_whole_file_and_original_filename(client):
    content = json.dumps([invoice(number='BATCH-DOC-1'), invoice(number='BATCH-DOC-2')]).encode()
    parent = upload_invoice(client, content, 'supplier-batch.json')
    child = next(run for run in client.app.state.store.runs() if run.get('parent_id') == parent['id'])
    assert 'row 2' in child['filename']
    source = documents(client, child['id'])['invoice_source']
    assert source['source_id'] == parent['id']
    assert source['filename'] == 'supplier-batch.json'
    assert source['source_sha256'] == hashlib.sha256(content).hexdigest()
    assert client.get(source['download_url']).content == content
    preview = client.get(source['preview_url'])
    assert preview.status_code == 200
    assert len(json.loads(preview.json()['text'])) == 2


def test_missing_latest_corrected_input_never_substitutes_older_correction(client):
    original = upload_invoice(client, json.dumps(invoice(number=None)).encode(), 'original-invoice.json')
    current = original
    for corrected_invoice in [invoice(number=None, due_date='2026-02-01'), invoice(number='LATEST-CORRECTION')]:
        response = client.post(f'/api/runs/{current["id"]}/correct', json={
            'invoice': corrected_invoice, 'reviewer': 'Invoice reviewer',
            'comment': 'Compared the correction against the original source.'})
        assert response.status_code == 202, response.text
        client.app.state.pipeline.queue.join()
        current = client.get('/api/runs/' + response.json()['runs'][0]['id']).json()
    (client.app.state.settings.data_dir / 'sources' / f'{current["id"]}.bin').unlink()
    result = documents(client, current['id'])
    assert result['invoice_source']['source_id'] == original['id']
    assert result['processed_input'] is None


def test_legacy_po_match_exposes_snapshot_without_inventing_attachment(client):
    response = client.post('/api/procurement/import', json={'purchase_orders': [purchase_order()]})
    assert response.status_code == 200, response.text
    run = submit_invoice(client, po_number='PO-DOCUMENT-1')
    assert run['result']['matched_po'] is not None
    assert run['decision'] == 'needs_review'
    result = documents(client, run['id'])
    assert result['matched_po'] == run['result']['matched_po']
    assert result['po_source'] is None
    assert result['po_source_message']


def test_pdf_context_preview_and_page_reads_do_not_mutate_or_extract(client, monkeypatch):
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), 'Invoice Number: PDF-DOCUMENT\nVendor: Acme Cloud Services\n'
                         'PO Number: PO-2026-001\nInvoice Date: 2026-01-02\nCurrency: USD\n'
                         'Subtotal: 100\nTax: 0\nTotal: 100')
        content = pdf.tobytes()
    run = upload_invoice(client, content, 'supplier-document.pdf')
    before = db_snapshot(client)

    def forbidden(*args, **kwargs):
        raise AssertionError('Document viewing must not invoke extraction or a model provider')

    monkeypatch.setattr('backend.app.extraction._model_extract', forbidden)
    monkeypatch.setattr('backend.app.po_extraction._model_extract', forbidden)
    monkeypatch.setattr('backend.app.pipeline.extract_invoices', forbidden)
    source = documents(client, run['id'])['invoice_source']
    preview = client.get(source['preview_url'])
    assert preview.status_code == 200, preview.text
    metadata = preview.json()
    assert metadata['kind'] == 'pdf'
    assert metadata['filename'] == 'supplier-document.pdf'
    assert metadata['page_count'] == metadata['preview_page_count'] == 1
    response = client.get(f'/api/runs/{run["id"]}/source/preview/page?page=0')
    assert response.status_code == 200, response.text[:200]
    assert response.headers['content-type'].startswith('image/png')
    assert response.content.startswith(b'\x89PNG\r\n\x1a\n')
    assert client.get(source['download_url']).content == content
    assert db_snapshot(client) == before


@pytest.mark.parametrize('status', ['queued', 'processing', 'failed'])
def test_unfinished_or_failed_run_has_source_and_no_fabricated_match(client, status):
    content = b'Input awaiting invoice extraction'
    run = new_run('waiting.txt', hashlib.sha256(content).hexdigest())
    run['status'] = status
    client.app.state.pipeline.save_source(run['id'], content)
    client.app.state.store.save_run(run)
    result = documents(client, run['id'])
    assert result['status'] == status
    assert result['matched_po'] is None
    assert result['matched_vendor'] is None
    assert result['po_source'] is None
    assert result['match_message']
    assert result['invoice_source']['source_id'] == run['id']


def test_unmatched_invoice_has_no_invented_po_document(client):
    run = submit_invoice(client, po_number='UNKNOWN-PO')
    assert run['result']['matched_po'] is None
    result = documents(client, run['id'])
    assert result['matched_po'] is None
    assert result['po_source'] is None
    assert result['match_message']
    assert result['invoice_source'] is not None


def test_missing_source_files_leave_context_available_with_honest_messages(client):
    draft, _ = upload_po(client)
    run = submit_invoice(client, po_number='PO-DOCUMENT-1')
    settings = client.app.state.settings
    (settings.data_dir / 'sources' / f'{run["id"]}.bin').unlink()
    (settings.data_dir / 'po-sources' / f'{draft["id"]}.bin').unlink()
    result = documents(client, run['id'])
    assert result['matched_po'] == run['result']['matched_po']
    assert result['invoice_source'] is None
    assert result['po_source'] is None
    assert result['invoice_source_message']
    assert result['po_source_message']
    assert client.get(f'/api/runs/{run["id"]}/source/preview').status_code == 404
    assert client.get(f'/api/procurement/uploads/{draft["id"]}/source/preview').status_code == 404


@pytest.mark.parametrize('case', ['unconfirmed', 'different_po'])
def test_po_source_requires_confirmed_upload_containing_the_matched_po(client, case):
    legitimate, _ = upload_po(client)
    run = submit_invoice(client, po_number='PO-DOCUMENT-1')
    if case == 'unconfirmed':
        unrelated, _ = upload_po(client, confirm=False)
    else:
        unrelated, _ = upload_po(client, purchase_order(po_number='OTHER-DOCUMENT-PO'))
    assert unrelated['id'] != legitimate['id']
    # Simulate an old/inconsistent stored reference; the viewer must still verify provenance.
    run['result']['matched_po']['_validation']['upload_id'] = unrelated['id']
    client.app.state.store.save_run(run)
    before = db_snapshot(client)
    result = documents(client, run['id'])
    assert result['matched_po'] == run['result']['matched_po']
    assert result['po_source'] is None
    assert result['po_source_message']
    assert db_snapshot(client) == before


@pytest.mark.parametrize('path', [
    '/api/runs/not-present/documents',
    '/api/runs/not-present/source/preview',
    '/api/runs/not-present/source/preview/page?page=0',
    '/api/procurement/uploads/not-present/source/preview',
    '/api/procurement/uploads/not-present/source/preview/page?page=0',
])
def test_document_endpoints_reject_unknown_record_ids(client, path):
    assert client.get(path).status_code == 404
