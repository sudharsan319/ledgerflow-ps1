from copy import deepcopy
import json
import pytest

from backend.app.models import normalize_invoice
from backend.app.extraction import extract_invoices, _schema
from backend.app.calculations import calculate_rules
from backend.app.policy import evaluate_invoice


def inv(**changes):
    data = dict(invoice_number='CALC-1', vendor_name='Supplier', invoice_date='2026-01-01', currency='INR', subtotal='1000', tax='180', total='1180',
                line_items=[dict(description='Goods', quantity='10', unit_price='100', amount='1000')], extraction_method='structured')
    data.update(changes)
    return normalize_invoice(data)


def adjustment(kind, amount, included=False, label='Adjustment'):
    return dict(label=label, kind=kind, amount=amount, included_in_subtotal=included, evidence=f'{label}: {amount}')


@pytest.mark.parametrize('changes,expected', [
    ({}, '1180'),
    ({'tax_mode': 'inclusive', 'subtotal': '1180', 'line_items': [{'quantity': '10', 'unit_price': '118', 'amount': '1180'}]}, '1180'),
    ({'tax': '162', 'total': '1062', 'adjustments': [adjustment('discount', '100')]}, '1062'),
    ({'total': '1080', 'adjustments': [adjustment('discount', '100')]}, '1080'),
    ({'total': '1230', 'adjustments': [adjustment('charge', '50')]}, '1230'),
    ({'total': '1180.34', 'adjustments': [adjustment('rounding', '0.34')]}, '1180.34'),
    ({'total': '1179.66', 'adjustments': [adjustment('rounding', '-0.34')]}, '1179.66'),
    ({'tax': '0', 'total': '1000'}, '1000'),
])
def test_rules_supported_totals(changes, expected):
    result = calculate_rules(inv(**changes))
    assert result['status'] == 'reconciled', result
    assert result['calculated_total'] == expected
    assert result['difference'] == '0' or float(result['difference']) == 0


def test_mixed_tax_components_and_missing_aggregate_are_derived_not_written():
    invoice = inv(subtotal='200', tax=None, total='223', line_items=[], tax_components=[
        dict(label='GST 5%', amount='5', rate='5', taxable_amount='100'),
        dict(label='GST 18%', amount='18', rate='18', taxable_amount='100')])
    result = calculate_rules(invoice)
    assert result['status'] == 'reconciled'
    assert result['calculated_total'] == '223'
    assert invoice['tax'] is None


def test_tax_aggregate_does_not_double_count_components():
    result = calculate_rules(inv(tax_components=[dict(label='CGST', amount='90'), dict(label='SGST', amount='90')]))
    assert result['calculated_total'] == '1180'
    assert result['status'] == 'reconciled'
    broken = calculate_rules(inv(tax='90', tax_components=[dict(amount='90'), dict(amount='90')]))
    assert any(c['code'] == 'tax_components' and c['status'] == 'fail' for c in broken['checks'])


def test_discount_in_subtotal_is_not_subtracted_twice():
    result = calculate_rules(inv(subtotal='900', tax='162', total='1062', subtotal_scope='after_adjustments', adjustments=[adjustment('discount', '100', True)]))
    assert result['status'] == 'reconciled'
    assert result['calculated_total'] == '1062'


def test_line_discount_and_tax_basis_conversion():
    invoice = inv(subtotal='900', tax='162', total='1062', line_items=[dict(description='Goods', quantity='10', unit_price='100', discount_percent='10', discount_amount='100', tax_rate='18', tax='162', amount='1062', amount_tax_mode='inclusive', unit_price_tax_mode='exclusive')])
    assert calculate_rules(invoice)['status'] == 'reconciled'
    invoice['line_items'][0]['tax'] = '180'
    assert calculate_rules(invoice)['status'] == 'mismatch'


@pytest.mark.parametrize('changes', [
    {'tax_mode': 'unknown'}, {'adjustments': [adjustment('unknown', '5')]},
    {'adjustments': [adjustment('charge', '5', None)]}, {'tax': None, 'tax_status': 'unclear'},
    {'adjustments': [adjustment('charge', '-5')]}, {'adjustments': 'not an array'}, {'tax_components': [False]},
    {'line_items': [{'quantity': '1', 'unit_price': '1000', 'amount': '1000', 'tax_rate': '120'}]},
    {'line_items': [{'quantity': '10', 'unit_price': '100', 'amount': '1000', 'discount_percent': 'Infinity'}]},
])
def test_uncertainty_or_malformed_values_cannot_reconcile(changes):
    result = calculate_rules(inv(**changes))
    assert result['status'] != 'reconciled'


def test_actual_photo_rounding_case_does_not_hide_large_rate_errors():
    invoice = inv(subtotal='363.30', tax=None, total='363.30', tax_mode='inclusive', line_items=[dict(quantity='100', unit_price='3.63', amount='363.30', unit_price_precision=2)])
    result = calculate_rules(invoice)
    assert result['status'] == 'reconciled'
    assert result['steps'][0]['tolerance'] == '0.50'
    invoice['line_items'][0]['amount'] = '364'
    assert calculate_rules(invoice)['status'] == 'mismatch'
    invoice['line_items'][0]['unit_price_precision'] = 0
    assert any(c['code'] == 'line_1_precision' for c in calculate_rules(invoice)['checks'])


def test_tax_summary_one_paise_difference_is_explicit():
    result = calculate_rules(inv(tax_mode='inclusive', subtotal='6673.66', tax='670.98', taxable_subtotal='6002.69', total='6674', line_items=[], adjustments=[adjustment('rounding', '0.34')]))
    assert result['status'] == 'reconciled'
    summary = next(s for s in result['steps'] if s['id'] == 'taxable_subtotal')
    assert summary['difference'] == '0.01'


def test_currency_units_and_negative_rounding():
    result = calculate_rules(inv(currency='KWD', subtotal='1', tax='0', total='1.002', line_items=[]))
    assert result['status'] == 'mismatch'
    assert calculate_rules(inv(currency='JPY', subtotal='100', tax='0', total='101', line_items=[]))['status'] == 'reconciled'


def test_extended_fields_survive_json_csv_and_normalization():
    raw = inv(tax_mode='inclusive', tax_components=[dict(label='GST', amount='180')], adjustments=[adjustment('rounding', '0.34')])
    for filename, content in [('x.json', json.dumps(raw).encode()), ('x.csv', b'invoice_number,total,tax_mode,adjustments\nTEST,1180,inclusive,"[{""kind"":""rounding"",""amount"":""0.34"",""included_in_subtotal"":false}]"')]:
        extracted = extract_invoices(content, filename)[0]['invoice']
        normalized = normalize_invoice(extracted)
        assert normalized['tax_mode'] == 'inclusive'
        assert normalized['adjustments'][0]['amount'] == '0.34'
    assert 'tax_components' in _schema()['properties']['invoice']['properties']


def test_percentage_document_discount_uses_explicit_base_and_is_not_invented():
    change = adjustment('discount', None)
    change.update(percent='10', base_amount='1000')
    invoice = inv(tax='162', total='1062', adjustments=[change])
    assert calculate_rules(invoice)['status'] == 'reconciled'
    assert invoice['adjustments'][0]['amount'] is None
    change.pop('base_amount')
    assert calculate_rules(inv(tax='162', total='1062', adjustments=[change]))['status'] == 'needs_review'


def test_declared_percentage_discount_amount_must_agree():
    change = adjustment('discount', '200')
    change.update(percent='10', base_amount='1000')
    result = calculate_rules(inv(tax='180', total='980', adjustments=[change]))
    assert result['status'] == 'mismatch'
    assert result['difference'] == '0'


def test_included_percentage_discount_reconciles_lines_once():
    change = adjustment('discount', None, True)
    change.update(percent='10', base_amount='1000')
    result = calculate_rules(inv(subtotal='900', tax='162', total='1062', subtotal_scope='after_adjustments', adjustments=[change]))
    assert result['status'] == 'reconciled'


def test_mixed_line_taxes_must_reconcile_even_without_tax_summary():
    invoice = inv(subtotal='200', tax='24', total='224', line_items=[dict(quantity='1', unit_price='100', amount='100', tax_rate='5'), dict(quantity='1', unit_price='100', amount='100', tax_rate='18')])
    result = calculate_rules(invoice)
    assert result['difference'] == '0'
    assert result['status'] == 'mismatch'
    assert any(c['code'] == 'line_tax_sum' and c['status'] == 'fail' for c in result['checks'])


@pytest.mark.parametrize('name,expected', [
    ('01-tax-exclusive', 'reconciled'), ('02-tax-inclusive', 'reconciled'), ('03-mixed-rates', 'reconciled'),
    ('04-discount-and-shipping', 'reconciled'), ('05-percentage-discount', 'reconciled'), ('06-explicit-rounding', 'reconciled'),
    ('07-real-mismatch', 'mismatch'), ('08-unknown-adjustment', 'needs_review'),
])
def test_manual_calculation_samples(name, expected):
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'samples' / 'calculations' / (name + '.json')
    extracted = extract_invoices(path.read_bytes(), path.name)[0]['invoice']
    assert calculate_rules(normalize_invoice(extracted))['status'] == expected

