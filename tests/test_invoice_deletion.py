"""Permanent upload deletion respects source families and the live procurement ledger."""
from decimal import Decimal
import json
from pathlib import Path
import threading
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.pipeline import new_run
from backend.app.invoice_deletion import SourceDeletion
from tests.test_run_documents import invoice, upload_invoice, upload_po


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as api:
        yield api


def preview(client, run_id):
    response = client.get(f'/api/runs/{run_id}/deletion-preview')
    assert response.status_code == 200, response.text
    return response.json()


def delete(client, run_id, fingerprint=None, **kwargs):
    fingerprint = fingerprint or preview(client, run_id)['fingerprint']
    return client.request('DELETE', f'/api/runs/{run_id}', json={'expected_fingerprint': fingerprint}, **kwargs)


def source_file(client, run_id, directory='sources'):
    return client.app.state.settings.data_dir / directory / f'{run_id}.bin'


def seeded_po(client):
    return next(po for po in client.get('/api/procurement').json()['purchase_orders']
                if po['po_number'] == 'PO-2026-001')


def test_delete_releases_amount_and_quantity_preserves_other_uploads_and_allows_reupload(client):
    content = json.dumps(invoice(number='DELETE-APPROVED')).encode()
    approved = upload_invoice(client, content, 'delete-me.json')
    unrelated = upload_invoice(client, json.dumps(invoice(number='KEEP-APPROVED')).encode(), 'keep-me.json')
    po_upload, po_content = upload_po(client)
    assert approved['decision'] == unrelated['decision'] == 'approved'
    before = seeded_po(client)
    assert Decimal(before['committed']) == 200
    assert Decimal(before['line_commitments'][0]['invoiced_quantity']) == 2

    planned = preview(client, approved['id'])
    assert planned['run_ids'] == [approved['id']]
    assert planned['count'] == planned['approved_count'] == 1
    assert planned['filenames'] == ['delete-me.json']
    assert planned['can_delete'] is True
    response = delete(client, approved['id'], planned['fingerprint'])
    assert response.status_code == 200, response.text
    assert response.json()['deleted_run_ids'] == [approved['id']]
    assert response.json()['count'] == 1
    assert not source_file(client, approved['id']).exists()
    assert source_file(client, unrelated['id']).is_file()
    assert source_file(client, po_upload['id'], 'po-sources').read_bytes() == po_content
    assert client.get(f'/api/runs/{approved["id"]}').status_code == 404
    assert client.get(f'/api/runs/{approved["id"]}/source').status_code == 404
    assert [r['id'] for r in client.get('/api/runs').json()['runs']] == [unrelated['id']]
    assert client.get('/api/dashboard').json()['total_runs'] == 1
    assert approved['id'] not in client.get('/api/runs/export?format=json').text
    after = seeded_po(client)
    assert Decimal(after['committed']) == 100
    assert Decimal(after['line_commitments'][0]['invoiced_quantity']) == 1
    assert after['approved_invoice_count'] == 1

    uploaded_again = upload_invoice(client, content, 'delete-me.json')
    assert uploaded_again['decision'] == 'approved'
    assert uploaded_again['result']['duplicate_of'] is None


def test_deleting_batch_child_cascades_parent_sibling_and_correction_sources(client):
    batch = [invoice(number=None), invoice(number='BATCH-KEEP-WITH-FAMILY')]
    parent = upload_invoice(client, json.dumps(batch).encode(), 'batch.json')
    child = next(r for r in client.app.state.store.runs() if r.get('batch_index') == 2)
    assert parent['decision'] == 'needs_review'
    response = client.post(f'/api/runs/{parent["id"]}/correct', json={
        'invoice': invoice(number='CORRECTED-BATCH-ROW'),
        'reviewer': 'Deletion test', 'comment': 'Verified missing invoice number against the source.'})
    assert response.status_code == 202, response.text
    client.app.state.pipeline.queue.join()
    corrected_id = response.json()['runs'][0]['id']
    corrected = client.get(f'/api/runs/{corrected_id}').json()
    assert corrected['decision'] == 'approved'
    expected_ids = {parent['id'], child['id'], corrected_id}
    planned = preview(client, child['id'])
    assert set(planned['run_ids']) == expected_ids
    assert planned['count'] == 3 and planned['approved_count'] == 2
    assert set(preview(client, corrected_id)['run_ids']) == expected_ids
    assert source_file(client, parent['id']).is_file()
    assert source_file(client, corrected_id).is_file()

    response = delete(client, child['id'], planned['fingerprint'])
    assert response.status_code == 200, response.text
    assert set(response.json()['deleted_run_ids']) == expected_ids
    assert response.json()['count'] == 3
    assert client.get('/api/runs').json()['runs'] == []
    assert all(not source_file(client, run_id).exists() for run_id in expected_ids)
    assert Decimal(seeded_po(client)['committed']) == 0
    assert Decimal(seeded_po(client)['line_commitments'][0]['invoiced_quantity']) == 0


def test_retained_duplicate_keeps_historical_decision_and_marks_original_deleted(client):
    content = json.dumps(invoice(number='DUPLICATE-SOURCE')).encode()
    original = upload_invoice(client, content)
    duplicate = upload_invoice(client, content)
    assert duplicate['decision'] == 'rejected'
    assert duplicate['result']['duplicate_of'] == original['id']
    assert delete(client, original['id']).status_code == 200
    retained = client.get(f'/api/runs/{duplicate["id"]}').json()
    assert retained['decision'] == duplicate['decision']
    assert retained['result']['duplicate_of'] == original['id']
    assert retained['result']['duplicate_source_deleted'] is True
    assert retained['result']['checks'] == duplicate['result']['checks']
    assert retained['result']['summary'] == duplicate['result']['summary']
    assert source_file(client, duplicate['id']).read_bytes() == content
    uploaded_again = upload_invoice(client, content)
    assert uploaded_again['decision'] == 'approved'
    assert uploaded_again['result']['duplicate_of'] is None


def test_deletion_preview_must_be_refreshed_after_review(client):
    run = upload_invoice(client, json.dumps(invoice(number='STALE-DELETE')).encode())
    planned = preview(client, run['id'])
    reviewed = client.post(f'/api/runs/{run["id"]}/review', json={
        'action': 'reject', 'reviewer': 'Deletion test', 'comment': 'Cancelled the approved test invoice.'})
    assert reviewed.status_code == 200
    response = delete(client, run['id'], planned['fingerprint'])
    assert response.status_code == 409
    assert source_file(client, run['id']).is_file()
    refreshed = preview(client, run['id'])
    assert refreshed['fingerprint'] != planned['fingerprint']
    assert refreshed['approved_count'] == 0
    assert delete(client, run['id'], refreshed['fingerprint']).status_code == 200


@pytest.mark.parametrize('status', ['queued', 'processing'])
def test_pending_runs_cannot_be_deleted(client, status):
    run = new_run('pending.json', 'test-source-hash')
    run['status'] = status
    client.app.state.store.save_run(run)
    client.app.state.pipeline.save_source(run['id'], b'pending test source')
    planned = preview(client, run['id'])
    assert planned['can_delete'] is False and planned['reason']
    assert delete(client, run['id'], planned['fingerprint']).status_code == 409
    assert client.app.state.store.get_run(run['id']) is not None
    assert source_file(client, run['id']).is_file()


def test_failed_upload_can_be_deleted_and_missing_upload_returns_404(client):
    failed = upload_invoice(client, b'this is not a PDF', 'failed.pdf')
    assert failed['status'] == 'failed'
    planned = preview(client, failed['id'])
    assert planned['can_delete'] is True and planned['approved_count'] == 0
    assert delete(client, failed['id'], planned['fingerprint']).status_code == 200
    missing_id = str(uuid4())
    assert client.get(f'/api/runs/{missing_id}/deletion-preview').status_code == 404
    assert delete(client, missing_id, '0' * 64).status_code == 404
    assert delete(client, failed['id'], planned['fingerprint']).status_code == 404


@pytest.mark.parametrize('public_demo', [False, True])
def test_deletion_retains_hosted_auth_and_browser_origin_boundaries(tmp_path, public_demo):
    origin = 'https://ledgerflow-delete.example'
    password = 'fictional-delete-test-password-123456'
    settings = Settings(data_dir=tmp_path, public_origin=origin, auth_password=password, public_demo=public_demo)
    with TestClient(create_app(settings), base_url=origin) as client:
        client.auth = ('reviewer', password)
        run = upload_invoice(client, json.dumps(invoice(number='HOSTED-DELETE')).encode())
        planned = preview(client, run['id'])
        client.auth = None
        if not public_demo:
            assert client.get(f'/api/runs/{run["id"]}/deletion-preview').status_code == 401
            assert delete(client, run['id'], planned['fingerprint']).status_code == 401
        client.auth = ('reviewer', password)
        assert delete(client, run['id'], planned['fingerprint'], headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.get(f'/api/runs/{run["id"]}').status_code == 200
        assert delete(client, run['id'], planned['fingerprint'], headers={'Origin': origin}).status_code == 200


def test_completed_first_batch_row_stays_protected_until_remaining_rows_are_saved(client, monkeypatch):
    pipeline = client.app.state.pipeline
    finish = pipeline.finish_invoice
    first_finished, release = threading.Event(), threading.Event()

    def pause_after_first_row(run, extraction):
        finish(run, extraction)
        if run.get('batch_index') == 1:
            first_finished.set()
            assert release.wait(10), 'Test did not release the batch worker'

    monkeypatch.setattr(pipeline, 'finish_invoice', pause_after_first_row)
    content = json.dumps([invoice(number='LIVE-BATCH-1'), invoice(number='LIVE-BATCH-2')]).encode()
    try:
        response = client.post('/api/runs/upload', files={'files': ('live-batch.json', content)})
        run_id = response.json()['runs'][0]['id']
        assert first_finished.wait(5)
        runs = client.get('/api/runs').json()['runs']
        assert len(runs) == 1 and runs[0]['status'] == 'completed'
        planned = preview(client, run_id)
        assert planned['count'] == 1 and planned['can_delete'] is False
        assert delete(client, run_id, planned['fingerprint']).status_code == 409
    finally:
        release.set()
        pipeline.queue.join()
    assert preview(client, run_id)['count'] == 2
    assert preview(client, run_id)['can_delete'] is True
    assert delete(client, run_id, planned['fingerprint']).status_code == 409
    assert delete(client, run_id).status_code == 200


def test_failed_deletion_transaction_restores_records_and_staged_source(client, monkeypatch):
    content = json.dumps(invoice(number='ROLLBACK-DELETE')).encode()
    original = upload_invoice(client, content)
    duplicate = upload_invoice(client, content)
    save = client.app.state.store.save_run

    def fail_duplicate_update(run, conn=None):
        if run['id'] == duplicate['id']:
            raise OSError('Test transaction failure')
        return save(run, conn)

    monkeypatch.setattr(client.app.state.store, 'save_run', fail_duplicate_update)
    response = delete(client, original['id'])
    assert response.status_code == 503
    assert client.get(f'/api/runs/{original["id"]}').status_code == 200
    assert source_file(client, original['id']).read_bytes() == content
    assert not client.get(f'/api/runs/{duplicate["id"]}').json()['result'].get('duplicate_source_deleted')
    with client.app.state.store.connection() as conn:
        assert conn.execute('SELECT count(*) FROM invoice_deletions').fetchone()[0] == 0
    assert list((client.app.state.settings.data_dir / 'sources' / '.deletions').iterdir()) == []


def test_committed_file_cleanup_failure_is_reported_and_retried_on_recovery(client, monkeypatch):
    content = json.dumps(invoice(number='CLEANUP-DELETE')).encode()
    run = upload_invoice(client, content)
    unlink = Path.unlink

    def fail_staged_unlink(path, *args, **kwargs):
        if '.deletions' in path.parts:
            raise OSError('Test filesystem failure')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail_staged_unlink)
    response = delete(client, run['id'])
    assert response.status_code == 200 and response.json()['cleanup_pending'] is True
    assert client.get(f'/api/runs/{run["id"]}/source').status_code == 404
    staged = client.app.state.settings.data_dir / 'sources' / '.deletions'
    assert len(list(staged.glob('*/*.bin'))) == 1
    monkeypatch.setattr(Path, 'unlink', unlink)
    SourceDeletion(client.app.state.store, client.app.state.settings).recover()
    assert list(staged.iterdir()) == []
    assert not source_file(client, run['id']).exists()
    with client.app.state.store.connection() as conn:
        audits = [json.loads(row[0]) for row in conn.execute('SELECT data FROM invoice_deletions')]
    assert audits == [{'action': 'delete_invoice_upload', 'run_ids': [run['id']], 'count': 1, 'approved_count': 1}]


def test_startup_recovery_restores_sources_staged_before_uncommitted_deletion(client):
    content = json.dumps(invoice(number='CRASH-BEFORE-COMMIT')).encode()
    run = upload_invoice(client, content)
    sources = SourceDeletion(client.app.state.store, client.app.state.settings)
    folder = sources.stage(str(uuid4()), [run['id']])
    assert not source_file(client, run['id']).exists()
    sources.recover()
    assert source_file(client, run['id']).read_bytes() == content
    assert not folder.exists()
