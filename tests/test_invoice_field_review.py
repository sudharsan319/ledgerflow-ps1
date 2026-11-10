"""Field review preserves provenance and runs financial controls without bypasses."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.extraction import extract_invoices, field_comparisons
from backend.app.invoice_review import field_changes, map_field_issues
from backend.app.main import create_app
from tests.test_extraction import TEXT
from tests.test_integration_review import submit


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        yield client


def invoice(**changes):
    data = dict(invoice_number='FIELD-1', vendor_name='Unknown supplier', invoice_date='2026-01-01',
                currency='INR', subtotal='100', tax='18', total='118', tax_mode='exclusive',
                line_items=[dict(description='Goods', quantity='2', unit_price='50', amount='100',
                                 discount_amount='0', tax_rate='18', tax='18', unit_price_precision=2)],
                tax_components=[dict(label='CGST', amount='9', rate='9', taxable_amount='100', evidence='CGST 9.00'),
                                dict(label='SGST', amount='9', rate='9', taxable_amount='100', evidence='SGST 9.00')],
                adjustments=[dict(label='Round off', kind='rounding', amount='0', included_in_subtotal=False)])
    data.update(changes)
    return data


def snapshot(client):
    with client.app.state.store.connection() as conn:
        return '\n'.join(conn.iterdump())


def correction_body(context, **changes):
    data = deepcopy(context['invoice'])
    data.update(changes)
    return dict(invoice=data, expected_invoice_fingerprint=context['invoice_fingerprint'],
                reviewer='Test reviewer', comment='Checked against the uploaded source.')


def test_context_and_check_are_read_only_and_preserve_financial_fields(client):
    run = submit(client, invoice())
    before = snapshot(client)
    url = f'/api/runs/{run["id"]}'
    context = client.get(url + '/review-context').json()
    assert context['editable'] and context['invoice'] == run['invoice']
    body = correction_body(context)
    body['invoice']['line_items'][0]['quantity'] = '3'
    response = client.post(url + '/correction-preview', json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['preview_only'] is True
    assert result['result']['decision'] != 'approved'
    assert result['issues']['fields']['line_items.0.quantity']
    assert result['changes'] == [{'field': 'line_items.0.quantity', 'before': '2', 'after': '3'}]
    for key in ('tax_components', 'adjustments', 'tax_mode'):
        assert result['invoice'][key] == run['invoice'][key]
    assert snapshot(client) == before


def test_source_disagreement_is_saved_and_extraction_choice_unchanged(monkeypatch):
    from backend.app.extraction import _local_extract
    model = _local_extract(TEXT)
    model['method'] = 'openrouter'
    model['invoice']['invoice_date'] = '2026-11-02'
    model['invoice']['tax'] = '111'
    model['evidence']['invoice_date'] = 'Model date quote'
    monkeypatch.setattr('backend.app.extraction._model_extract', lambda *args: deepcopy(model))
    result = extract_invoices(TEXT.encode(), 'source.txt', {'enable_openrouter': True})[0]
    assert result['invoice']['invoice_date'] == '2026-11-02'
    comparison = next(row for row in result['field_comparisons'] if row['field'] == 'invoice_date')
    assert comparison == dict(field='invoice_date', local_value='2026-11-01', ai_value='2026-11-02',
                              local_evidence='Invoice date: 2026-11-01', ai_evidence='Model date quote',
                              disagrees=True, selected_source='ai', origin='saved')
    assert next(row for row in result['field_comparisons'] if row['field'] == 'tax')['disagrees']
    assert result['invoice']['extraction_confidence'] <= .8


def test_saved_candidates_win_over_reconstructed_ones(client):
    run = submit(client, invoice())
    saved = field_comparisons({'invoice': {'invoice_date': '2026-01-02'}}, {'invoice': run['invoice']})
    run['extraction'].update(method='openrouter', raw_text=TEXT, field_comparisons=saved)
    client.app.state.store.save_run(run)
    before = snapshot(client)
    context = client.get(f'/api/runs/{run["id"]}/review-context').json()
    assert context['comparisons'] == saved
    assert snapshot(client) == before


def test_legacy_candidates_are_explicitly_reconstructed_without_ai_or_writes(client, monkeypatch):
    run = submit(client, invoice())
    run['extraction'].update(method='openrouter', raw_text=TEXT)
    client.app.state.store.save_run(run)
    monkeypatch.setattr('backend.app.extraction._model_extract', lambda *args: pytest.fail('Review must not call AI'))
    before = snapshot(client)
    context = client.get(f'/api/runs/{run["id"]}/review-context').json()
    assert context['comparisons']
    assert all(item['origin'] == 'reconstructed' for item in context['comparisons'])
    assert context['invoice'] == run['invoice']
    assert snapshot(client) == before


def test_correction_audits_normalized_changes_and_keeps_original_source(client):
    run = submit(client, invoice())
    original_source = client.get(f'/api/runs/{run["id"]}/source').content
    url = f'/api/runs/{run["id"]}'
    context = client.get(url + '/review-context').json()
    body = correction_body(context, tax='19', total='119')
    body['invoice']['tax_components'][1]['amount'] = '10'
    response = client.post(url + '/correct', json=body)
    assert response.status_code == 202, response.text
    client.app.state.pipeline.queue.join()
    replacement = client.get('/api/runs/' + response.json()['runs'][0]['id']).json()
    original = client.get(url).json()
    assert replacement['status'] == 'completed'
    assert replacement['decision'] != 'approved'  # Unknown supplier and invalid GST still block.
    assert original['invoice'] == run['invoice']
    assert original['superseded_by'] == replacement['id']
    assert replacement['correction_of'] == original['id']
    assert original['reviews'][-1] == replacement['reviews'][0]
    assert original['reviews'][-1]['changes'] == field_changes(run['invoice'], replacement['invoice'])
    assert {item['field'] for item in original['reviews'][-1]['changes']} == {'tax', 'total', 'tax_components.1.amount'}
    documents = client.get(f'/api/runs/{replacement["id"]}/documents').json()
    assert client.get(documents['invoice_source']['download_url']).content == original_source
    assert client.get(url + '/review-context').json()['editable'] is False
    assert client.post(url + '/correct', json=body).status_code == 409
    assert client.post(url + '/correction-preview', json=body).status_code == 409


@pytest.mark.parametrize('endpoint', ['correct', 'correction-preview'])
def test_stale_draft_cannot_overwrite_or_supersede_run(client, endpoint):
    run = submit(client, invoice())
    url = f'/api/runs/{run["id"]}'
    body = correction_body(client.get(url + '/review-context').json(), total='99')
    run['invoice']['po_number'] = 'CHANGED-SINCE-REVIEW'
    client.app.state.store.save_run(run)
    before = snapshot(client)
    response = client.post(url + '/' + endpoint, json=body)
    assert response.status_code == 409
    assert 'changed' in response.json()['detail']
    assert snapshot(client) == before


@pytest.mark.parametrize('endpoint', ['correct', 'correction-preview'])
def test_batch_correction_is_rejected_without_mutation(client, endpoint):
    run = submit(client, invoice())
    before = snapshot(client)
    response = client.post(f'/api/runs/{run["id"]}/{endpoint}', json={
        'invoice': {'invoices': [invoice(), invoice(invoice_number='FIELD-2')]},
        'reviewer': 'Reviewer', 'comment': 'Source checked'})
    assert response.status_code == 422
    assert snapshot(client) == before


def test_approved_and_incomplete_reviews_are_protected(client):
    from tests.test_integration_review import seed, bundled, approve
    seed(client)
    run = submit(client, bundled('APPROVED'))
    assert approve(client, run).status_code == 200
    url = f'/api/runs/{run["id"]}'
    context = client.get(url + '/review-context').json()
    before = snapshot(client)
    assert context['editable'] is False
    for action in ('correct', 'correction-preview'):
        assert client.post(url + '/' + action, json=correction_body(context)).status_code == 409
    assert snapshot(client) == before
    run['status'] = 'failed'
    client.app.state.store.save_run(run)
    assert client.get(url + '/review-context').status_code == 409
    assert client.get('/api/runs/missing/review-context').status_code == 404


def test_invalid_number_is_flagged_at_the_actual_field(client):
    run = submit(client, invoice())
    url = f'/api/runs/{run["id"]}'
    body = correction_body(client.get(url + '/review-context').json())
    body['invoice']['tax_components'][1]['amount'] = 'not a number'
    body['invoice']['line_items'][0]['quantity'] = 'twelve?'
    result = client.post(url + '/correction-preview', json=body).json()
    assert result['issues']['fields']['tax_components.1.amount']
    assert result['issues']['fields']['line_items.0.quantity']
    assert result['result']['decision'] != 'approved'


def test_preview_uses_replacement_source_identity(client):
    # Two rejected/needs-review copies of one source may be given distinct
    # verified invoice numbers. Preview must use the corrected input's hash.
    first = submit(client, invoice())
    second = submit(client, invoice())
    assert second['decision'] == 'rejected'
    url = f'/api/runs/{second["id"]}'
    body = correction_body(client.get(url + '/review-context').json(), invoice_number='FIELD-2')
    preview = client.post(url + '/correction-preview', json=body).json()
    check = next(check for check in preview['result']['checks'] if check['code'] == 'duplicate')
    assert check['status'] == 'pass'
    assert first['source_sha256'] == second['source_sha256']


def test_check_paths_and_audit_paths_are_unambiguous():
    checks = [dict(code=code, status='fail', label=code, message='Requires correction', blocking=True)
              for code in ('line_2_tax', 'tax_rate_0', 'adjustment_rate_1', 'required_fields', 'policy_config')]
    issues = map_field_issues({}, checks)
    assert issues['fields']['line_items.1.tax'][0]['code'] == 'line_2_tax'
    assert issues['fields']['tax_components.0.amount'][0]['code'] == 'tax_rate_0'
    assert issues['fields']['adjustments.1.base_amount'][0]['code'] == 'adjustment_rate_1'
    assert issues['fields']['invoice_number'][0]['code'] == 'required_fields'
    assert [check['code'] for check in issues['general']] == ['policy_config']
    before = invoice(extraction_confidence=.8)
    after = deepcopy(before)
    after['extraction_confidence'] = 1
    after['line_items'].pop()
    changes = field_changes(before, after)
    assert changes == [dict(field='line_items.0', before=before['line_items'][0], after=None)]
