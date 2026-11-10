"""Line verification uses the same Decimal rules as saved policy decisions."""
from copy import deepcopy
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.app.calculations import calculate_rules
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models import normalize_invoice
from tests.test_integration_review import submit


def invoice(line, **fields):
    return normalize_invoice(dict(invoice_number='LIVE-1', vendor_name='Supplier', invoice_date='2026-01-01',
        currency='INR', total='236', line_items=[line], **fields))


def product(**fields):
    return dict(description='Goods', quantity='2', unit_price='100', amount='200', **fields)


def split():
    return [dict(label='CGST', rate='9'), dict(label='SGST', rate='9')]


def first(raw):
    before = deepcopy(raw)
    result = calculate_rules(raw)
    assert raw == before
    return result['products'][0]


@pytest.mark.parametrize('taxes,total_rate,total_tax,cgst,sgst', [
    ({'tax_components': split()}, '18', '36', '18', '18'),
    ({'tax_components': [dict(label='CGST', amount='18'), dict(label='SGST', amount='18')]}, '18', '36', '18', '18'),
    ({'tax_rate': '18'}, '18', '36', '0', '0'),
    ({'tax': '36'}, '18', '36', '0', '0'),
    ({'tax_components': [dict(label='GST', rate='18')]}, '18', '36', '0', '0'),
    ({'tax_components': [dict(label='CGST', rate='9')]}, '9', '18', '18', '0'),
    ({'tax_components': [dict(label='SGST', rate='9')]}, '9', '18', '0', '18'),
    ({}, '0', '0', '0', '0'),
])
def test_combined_tax_and_zero_missing_gst_sides(taxes, total_rate, total_tax, cgst, sgst):
    row = first(invoice(product(**taxes)))
    summary = row['tax_summary']
    assert Decimal(summary['tax_rate']) == Decimal(total_rate)
    assert Decimal(summary['tax']) == Decimal(total_tax)
    assert Decimal(summary['cgst']['amount']) == Decimal(cgst)
    assert Decimal(summary['sgst']['amount']) == Decimal(sgst)
    assert row['verification']['status'] == 'matched'
    assert Decimal(row['verification']['calculated_amount']) == 200


@pytest.mark.parametrize('taxes', [{'tax_components': split()}, {'tax_rate': '18'}])
def test_quantity_edit_recalculates_rate_only_taxes_and_requires_the_product_total_to_match(taxes):
    raw = invoice(product(**taxes))
    assert first(raw)['verification']['status'] == 'matched'
    raw['line_items'][0]['quantity'] = '3'
    row = first(raw)
    assert Decimal(row['tax_summary']['tax']) == 54
    assert row['verification']['status'] == 'not_matched'
    assert Decimal(row['verification']['calculated_amount']) == 300
    assert Decimal(row['verification']['difference']) == 100
    raw['line_items'][0]['amount'] = '300'
    assert first(raw)['verification']['status'] == 'matched'
    # Independent invoice checks still reject its stale header total of 236.
    assert calculate_rules(raw)['status'] == 'mismatch'


@pytest.mark.parametrize('price,line_amount,price_mode,line_mode', [
    ('100', '236', 'exclusive', 'inclusive'),
    ('118', '236', 'inclusive', 'inclusive'),
    ('118', '200', 'inclusive', 'exclusive'),
])
def test_inclusive_and_exclusive_prices_and_line_totals_do_not_double_tax(price, line_amount, price_mode, line_mode):
    line = product(tax_components=split(), unit_price_tax_mode=price_mode, amount_tax_mode=line_mode)
    line.update(unit_price=price, amount=line_amount)
    row = first(invoice(line))
    assert Decimal(row['tax_summary']['tax']) == 36
    assert Decimal(row['verification']['calculated_amount']) == Decimal(line_amount)
    assert row['verification']['status'] == 'matched'


def test_discount_edit_updates_component_tax_and_product_constraint():
    raw = invoice(product(tax_components=split(), discount_percent='10'))
    raw['line_items'][0]['amount'] = '180'
    row = first(raw)
    assert row['verification']['status'] == 'matched'
    assert Decimal(row['tax_summary']['tax']) == Decimal('32.40')
    raw['line_items'][0]['discount_percent'] = '20'
    row = first(raw)
    assert row['verification']['status'] == 'not_matched'
    assert Decimal(row['tax_summary']['tax']) == Decimal('28.80')
    assert Decimal(row['verification']['calculated_amount']) == 160


def test_component_taxes_replace_legacy_line_aggregate_without_changing_history():
    raw = invoice(product(tax_components=split()))
    raw['line_items'][0]['tax'] = '30'
    row = first(raw)
    assert Decimal(row['tax_summary']['tax']) == 36
    assert Decimal(row['tax']) == 36
    assert row['verification']['status'] == 'matched'
    assert raw['line_items'][0]['tax'] == '30'
    assert calculate_rules(raw)['status'] == 'reconciled'
    raw.update(tax='30', tax_status='stated')
    assert calculate_rules(raw)['status'] == 'mismatch'  # Invoice-level totals still reconcile independently.


@pytest.mark.parametrize('changes', [
    {'quantity': None}, {'unit_price': None}, {'amount': None}, {'amount_tax_mode': 'unknown'},
    {'tax_components': [dict(label='CGST', rate=None, amount=None)]},
    {'tax_components': [dict(label='CGST', rate='bad')]},
    {'tax_components': [dict(label='CGST', rate='9', amount='2')]},
])
def test_incomplete_invalid_and_inconsistent_products_never_get_a_matched_badge(changes):
    line = product(tax_rate='18'); line.update(changes)
    assert first(invoice(line))['verification']['status'] != 'matched'


def test_offsetting_product_errors_cannot_be_hidden_by_a_correct_invoice_total():
    raw = invoice(product(), subtotal='400', tax='0')
    raw.update(total='400', line_items=[product(), product()])
    raw['line_items'][0]['amount'], raw['line_items'][1]['amount'] = '210', '190'
    result = calculate_rules(raw)
    assert Decimal(result['calculated_total']) == 400
    assert result['status'] == 'mismatch'
    assert [row['verification']['status'] for row in result['products']] == ['not_matched', 'not_matched']


def test_header_split_taxes_apply_to_single_product_but_not_arbitrarily_to_multiple_products():
    raw = invoice(product(), tax_components=split())
    row = first(raw)
    assert row['tax_from_document']
    assert row['tax_summary']['cgst']['amount'] == row['tax_summary']['sgst']['amount'] == '18'
    raw['line_items'].append(product())
    rows = calculate_rules(raw)['products']
    assert all(row['tax_summary']['cgst']['amount'] == row['tax_summary']['sgst']['amount'] == '0' for row in rows)


def test_live_endpoint_rechecks_each_edit_without_writes_or_ai_calls(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.app.extraction._model_extract', lambda *a: (_ for _ in ()).throw(AssertionError('No AI call')))
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        run = submit(client, invoice(product(tax_components=split())))
        url = f'/api/runs/{run["id"]}'
        context = client.get(url + '/review-context').json()
        body = {'invoice': deepcopy(context['invoice']), 'expected_invoice_fingerprint': context['invoice_fingerprint']}
        with client.app.state.store.connection() as conn:
            before = '\n'.join(conn.iterdump())
        for quantity, status, tax in [('2', 'matched', '36'), ('3', 'not_matched', '54'), ('2', 'matched', '36')]:
            body['invoice']['line_items'][0]['quantity'] = quantity
            response = client.post(url + '/line-preview', json=body)
            assert response.status_code == 200, response.text
            assert response.json()['preview_only'] is True
            rules = response.json()['rules']
            assert rules['products'][0]['verification']['status'] == status
            assert rules['products'][0]['tax_summary']['tax'] == tax
            full = client.post(url + '/correction-preview', json=body).json()
            assert full['result']['calculation'] == rules
        body['expected_invoice_fingerprint'] = '0' * 64
        assert client.post(url + '/line-preview', json=body).status_code == 409
        assert client.post('/api/runs/missing/line-preview', json=body).status_code == 404
        with client.app.state.store.connection() as conn:
            assert '\n'.join(conn.iterdump()) == before
        assert client.get(url).json() == run


def test_printed_component_bases_are_checked_against_the_discounted_product_base():
    line = product(tax_rate='18', tax_components=[dict(label='CGST', rate='9', taxable_amount='100'), dict(label='SGST', rate='9', taxable_amount='100')])
    row = first(invoice(line))
    assert row['tax_summary']['tax_rate'] == '18'
    assert row['calculated_tax'] == '36.00'
    assert row['verification']['status'] == 'not_matched'
    assert any('printed 100' in m for m in row['verification']['messages'])


def test_stale_line_rate_cannot_override_separate_component_rates():
    raw = invoice(product(tax_components=split()))
    raw['line_items'][0]['tax_rate'] = '17'
    row = first(raw)
    assert row['tax_summary']['tax_rate'] == '18'
    assert row['verification']['status'] == 'matched'
    assert raw['line_items'][0]['tax_rate'] == '17'
