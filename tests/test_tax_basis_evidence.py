"""Taxable-value anchors resolve semantics without fitting the invoice grand total."""
from copy import deepcopy
from decimal import Decimal
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.calculations import calculate_rules
from backend.app.config import Settings
from backend.app.extraction import FIELDS, _model_extract, _schema
from backend.app.main import create_app
from backend.app.models import normalize_invoice
from tests.test_integration_review import submit


def photo_invoice(copied=False):
    rows = []
    for qty, price, base, part, total in [('1','233.33','233.33','5.83','245.00'), ('5','114.29','571.45','14.29','600.02'), ('5','81.90','409.50','10.24','429.98')]:
        rows.append(dict(description='Photo product', quantity=qty, unit_price=price, amount=total,
            tax=part if copied else None, tax_rate='2.5' if copied else None,
            tax_components=[dict(label=name, rate='2.5', amount=part, taxable_amount=base) for name in ('CGST','SGST')],
            amount_tax_mode='unknown', unit_price_tax_mode='unknown', unit_price_precision=2))
    result = normalize_invoice(dict(invoice_number='NR-995-REGRESSION', vendor_name='Supplier', invoice_date='2026-11-05', currency='INR',
        subtotal='1214.28', tax='60.72', total='1275.00', tax_mode='exclusive', line_items=rows))
    if copied:  # Simulate an older saved extraction, before component precedence.
        for line in result['line_items']:
            line.update(tax=line['tax_components'][0]['amount'], tax_rate='2.5')
    return result


def test_photo_taxable_values_resolve_both_modes_and_preserve_source():
    invoice = photo_invoice()
    before = deepcopy(invoice)
    result = calculate_rules(invoice)
    assert result['status'] == 'reconciled', result['checks']
    assert [row['verification']['calculated_amount'] for row in result['products']] == ['244.99','600.03','429.98']
    assert all(row['verification']['status'] == 'matched' for row in result['products'])
    assert all(row['amount_tax_mode'] == 'inclusive' and row['unit_price_tax_mode'] == 'exclusive' and row['basis_notes'] for row in result['products'])
    assert invoice == before


def test_legacy_copied_totals_are_ignored_without_mutating_saved_source():
    invoice = photo_invoice(copied=True)
    result = calculate_rules(invoice)
    assert result['status'] == 'reconciled'
    assert [row['verification']['calculated_amount'] for row in result['products']] == ['244.99','600.03','429.98']
    assert all(row['verification']['status'] == 'matched' for row in result['products'])
    assert invoice['line_items'][0]['tax'] == '5.83'


def test_invoice_total_is_not_used_to_select_a_line_tax_basis():
    invoice = photo_invoice()
    reference = calculate_rules(invoice)['products']
    invoice['total'] = '999999'
    result = calculate_rules(invoice)
    assert result['products'] == reference
    assert result['status'] == 'mismatch'


@pytest.mark.parametrize('change', ['no_bases', 'different_bases', 'bad_component', 'ambiguous_tax', 'mismatched_amount', 'explicit_mode'])
def test_unsupported_or_explicit_interpretations_are_not_silently_changed(change):
    invoice = photo_invoice()
    line = invoice['line_items'][0]
    if change == 'no_bases':
        for tax in line['tax_components']: tax['taxable_amount'] = None
    elif change == 'different_bases': line['tax_components'][1]['taxable_amount'] = '220'
    elif change == 'bad_component': line['tax_components'][1]['amount'] = '200'
    elif change == 'ambiguous_tax': line['tax_status'] = 'unclear'
    elif change == 'mismatched_amount': line['amount'] = '900'
    elif change == 'explicit_mode': line['amount_tax_mode'] = 'exclusive'
    row = calculate_rules(invoice)['products'][0]
    if change == 'no_bases':
        assert row['amount_tax_mode'] == 'inclusive'
        assert row['verification']['status'] == 'matched'
    else:
        # Independent tax/rate evidence may resolve the basis, but contradictory
        # printed taxable values still fail their own checks.
        assert row['verification']['status'] != 'matched'


def test_review_draft_pins_inferred_semantics_for_later_edits_without_database_changes(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        run = submit(client, photo_invoice(copied=True))
        run['invoice'] = photo_invoice(copied=True)
        client.app.state.store.save_run(run)
        url = '/api/runs/' + run['id']
        with client.app.state.store.connection() as conn: before = '\n'.join(conn.iterdump())
        context = client.get(url + '/review-context').json()
        assert context['invoice'] == run['invoice']
        draft = context['review_invoice']
        assert context['invoice']['line_items'][0]['tax'] == '5.83'
        assert 'tax' not in draft['line_items'][0] and 'tax_rate' not in draft['line_items'][0]
        assert calculate_rules(draft)['status'] == 'reconciled'
        assert draft['line_items'][0]['amount_tax_mode'] == 'inclusive'
        assert context['invoice']['line_items'][0]['amount_tax_mode'] == 'unknown'
        draft['line_items'][0]['quantity'] = '2'
        body = dict(invoice=draft, expected_invoice_fingerprint=context['invoice_fingerprint'])
        data = client.post(url + '/line-preview', json=body).json()
        row = data['rules']['products'][0]
        assert row['verification']['status'] == 'not_matched'
        assert row['verification']['calculated_amount'] is not None
        full = client.post(url + '/correction-preview', json=body).json()
        assert full['result']['calculation'] == data['rules']
        changes = {change['field']: change for change in full['changes']}
        assert changes['line_items.0.tax']['before'] == '5.83' and changes['line_items.0.tax']['after'] is None
        assert changes['line_items.0.tax_rate']['before'] == '2.5' and changes['line_items.0.tax_rate']['after'] is None
        with client.app.state.store.connection() as conn: assert '\n'.join(conn.iterdump()) == before


@pytest.mark.parametrize('with_evidence', [False, True])
def test_ai_uses_components_even_when_redundant_aggregate_evidence_is_supplied(monkeypatch, with_evidence):
    invoice = photo_invoice(copied=True)
    model_invoice = {key: invoice.get(key) for key in FIELDS}
    model_invoice.update(line_items=invoice['line_items'], extraction_confidence=.99)
    for line in model_invoice['line_items']:
        line['tax_evidence'] = 'Total tax ' + line['tax'] if with_evidence else None
        line['tax_rate_evidence'] = 'Combined GST 2.5%' if with_evidence else None
    payload = dict(invoice=model_invoice, evidence={})
    class FakeClient:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs):
            return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps(payload)}}]})
    monkeypatch.setattr(httpx, 'Client', FakeClient)
    result = _model_extract('Taxable Value CGST SGST Net Amount', [], {'openrouter_api_key':'fake', 'openrouter_model':'test/model'})
    line = result['invoice']['line_items'][0]
    assert line['tax'] is None and line['tax_rate'] is None
    normalized = normalize_invoice(result['invoice'])
    assert 'tax' not in normalized['line_items'][0]
    assert 'tax_rate' not in normalized['line_items'][0]
    assert 'tax_evidence' not in normalized['line_items'][0]
    assert _schema()['properties']['invoice']['properties']['line_items']['items']['properties']['tax_evidence']
    assert any('5.83' in warning and 'omitted' in warning for warning in result['warnings'])
    assert calculate_rules(normalized)['status'] == 'reconciled'


@pytest.mark.parametrize('redundant_tax,redundant_rate', [('5.83','2.5'), ('unreadable','150'), ('-50','-5')])
def test_redundant_fields_are_removed_before_numeric_validation(redundant_tax, redundant_rate):
    source = photo_invoice()
    source['line_items'][0].update(tax=redundant_tax, tax_rate=redundant_rate)
    normalized = normalize_invoice(source)
    assert 'tax' not in normalized['line_items'][0] and 'tax_rate' not in normalized['line_items'][0]
    assert not normalized['normalization_warnings']
    assert calculate_rules(normalized)['status'] == 'reconciled'
    assert source['line_items'][0]['tax'] == redundant_tax
