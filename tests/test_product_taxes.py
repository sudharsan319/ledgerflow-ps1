from copy import deepcopy
from decimal import Decimal
import json
import pytest

from backend.app.models import normalize_invoice
from backend.app.calculations import calculate_rules
from backend.app.extraction import extract_invoices, _schema


def calc(total, lines, **fields):
    raw = dict(invoice_number='PRODUCT-1', vendor_name='Supplier', invoice_date='2026-01-01', currency='INR', total=total, line_items=lines, **fields)
    normalized = normalize_invoice(raw)
    before = deepcopy(normalized)
    result = calculate_rules(normalized)
    assert normalized == before
    return result


def product(price='100', **fields):
    return dict(description='Product', quantity='1', unit_price=price, amount=price, **fields)


def assert_total(result, total):
    assert result['status'] == 'reconciled', result['checks']
    assert Decimal(result['calculated_total']) == Decimal(total)


@pytest.mark.parametrize('lines,total,tax', [
    ([product()], '100', '0'),
    ([product(), product('200')], '300', '0'),
    ([product(tax_rate='5'), product('200', tax_rate='18')], '341', '41'),
    ([product(tax='5'), product('200', tax='36')], '341', '41'),
    ([product(tax='5'), product('200')], '305', '5'),
    ([product(tax_components=[dict(label='CGST', rate='9'), dict(label='SGST', rate='9')])], '118', '18'),
    ([product(tax_components=[dict(label='CGST', amount='9'), dict(label='SGST', amount='9')])], '118', '18'),
    ([product(tax_components=[dict(label='CGST', rate='9')])], '109', '9'),
    ([product(tax_components=[dict(label='SGST', amount='9')])], '109', '9'),
    ([product(tax_components=[dict(label='CGST', rate='2.5'), dict(label='SGST', rate='2.5')]), product('200', tax_components=[dict(label='IGST', rate='18')])], '341', '41'),
])
def test_products_with_optional_taxes_and_no_header_totals(lines, total, tax):
    result = calc(total, lines)
    assert_total(result, total)
    assert Decimal(result['effective_tax']) == Decimal(tax)
    assert sum(Decimal(p['total']) for p in result['products']) == Decimal(total)


def test_quantity_price_can_supply_missing_product_amount_and_subtotal():
    result = calc('236', [dict(description='Service', quantity='2', unit_price='100', tax_rate='18')])
    assert_total(result, '236')
    assert any(step['id'] == 'line_1_amount' and step['printed'] is None for step in result['steps'])


def test_zero_placeholder_on_absent_header_is_not_a_stated_total_tax():
    result = calc('118', [product(tax_components=[dict(label='CGST', rate='9'), dict(label='SGST', rate='9')], amount_tax_mode='exclusive', unit_price_tax_mode='exclusive')], tax='0', tax_status='not_stated', tax_mode='unknown', subtotal_scope='unknown')
    assert_total(result, '118')
    assert calc('118', [product(tax_rate='18')], tax='0')['status'] == 'mismatch'


@pytest.mark.parametrize('components', [
    [dict(label='CGST', rate='9'), dict(label='SGST', rate='9')],
    [dict(label='CGST', amount='9'), dict(label='SGST', amount='9')],
])
def test_tax_inclusive_product_is_counted_once(components):
    result = calc('118', [product('118', tax_components=components)], tax_mode='inclusive')
    assert_total(result, '118')
    assert Decimal(result['products'][0]['tax']) == 18


def test_net_unit_price_with_gross_product_total():
    line = product(tax_components=[dict(label='CGST', rate='9'), dict(label='SGST', rate='9')], amount_tax_mode='inclusive', unit_price_tax_mode='exclusive')
    line['amount'] = '118'
    assert_total(calc('118', [line]), '118')


@pytest.mark.parametrize('label', ['CGST', 'SGST'])
def test_single_tax_is_never_doubled_to_make_the_invoice_match(label):
    result = calc('118', [product(tax_components=[dict(label=label, amount='9')])])
    assert result['status'] == 'mismatch'
    assert Decimal(result['calculated_total']) == 109


def test_header_and_product_tax_are_reconciled_not_added_twice():
    lines = [product(tax_components=[dict(label='CGST', rate='9'), dict(label='SGST', rate='9')])]
    assert_total(calc('118', lines, subtotal='100', tax='18', tax_components=[dict(label='CGST', amount='9'), dict(label='SGST', amount='9')]), '118')
    assert calc('119', lines, subtotal='100', tax='19')['status'] == 'mismatch'


def test_header_tax_for_single_and_multiple_products():
    one = calc('118', [product()], tax='18')
    assert_total(one, '118')
    assert one['products'][0]['total'] == '118'
    many = calc('354', [product(), product('200')], tax='54')
    assert_total(many, '354')
    assert all(p['document_tax_separate'] for p in many['products'])


@pytest.mark.parametrize('line', [
    product(tax_status='unclear'),
    product(tax_status='stated'),
    product(tax='unreadable'),
    product(tax_rate='bad'),
    product(tax_components=[dict(label='CGST', amount=None, rate=None)]),
    product(tax_components=[dict(label='CGST', amount='bad')]),
    product(tax_components=[dict(label='CGST', rate='150')]),
    product(tax_components=[dict(label='CGST', rate='9', amount='99')]),
])
def test_unreadable_or_invalid_taxes_are_never_silently_zero(line):
    assert calc('100', [line])['status'] != 'reconciled'


def test_unknown_tax_basis_still_requires_review_when_tax_is_present():
    assert calc('118', [product(tax_rate='18', amount_tax_mode='unknown')], tax_mode='unknown')['status'] != 'reconciled'
    assert_total(calc('100', [product(amount_tax_mode='unknown', unit_price_tax_mode='unknown')], tax_mode='unknown'), '100')


def test_missing_quantity_is_not_defaulted_to_zero():
    line = product(); line.pop('quantity')
    assert calc('100', [line])['status'] == 'needs_review'


def test_tax_only_defaults_and_nested_fields_survive_structured_roundtrip():
    raw = dict(invoice_number='A', currency='INR', total='218', line_items=[product(tax_components=[dict(label='CGST', rate='9'), dict(label='SGST', rate='9')]), product()])
    normalized = normalize_invoice(extract_invoices(json.dumps(raw).encode(), 'invoice.json')[0]['invoice'])
    assert normalized['tax'] == '0' and normalized['tax_status'] == 'not_stated'
    assert normalized['line_items'][1]['tax'] == '0'
    assert normalized['line_items'][0]['tax_components'][0]['rate'] == '9'
    assert normalized['subtotal'] is None
    assert _schema()['properties']['invoice']['properties']['line_items']['items']['properties']['tax_components']['type'] == 'array'
    assert_total(calculate_rules(normalized), '218')


def test_rounding_and_zero_tax_charge_still_work():
    assert_total(calc('110', [product()], adjustments=[dict(kind='charge', amount='10', included_in_subtotal=False)]), '110')
    assert_total(calc('118.34', [product(tax_rate='18')], adjustments=[dict(kind='rounding', amount='0.34', included_in_subtotal=False)]), '118.34')


def test_document_discount_with_only_line_rates_requires_final_tax_basis():
    result = calc('106.20', [product(tax_rate='18')], adjustments=[dict(kind='discount', amount='10', included_in_subtotal=False)])
    assert result['status'] != 'reconciled'


@pytest.mark.parametrize('inclusive', [False, True])
def test_single_product_with_tax_rates_printed_below_the_product(inclusive):
    result = calc('118', [product('118' if inclusive else '100')], tax_mode='inclusive' if inclusive else 'exclusive',
                  tax_components=[dict(label='CGST', rate='9'), dict(label='SGST', rate='9')])
    assert_total(result, '118')


def test_missing_product_total_uses_explicit_unit_price_basis():
    result = calc('118', [dict(quantity='1', unit_price='100', amount_tax_mode='unknown', unit_price_tax_mode='exclusive', tax_rate='18')])
    assert_total(result, '118')


def test_summary_rates_without_bases_cannot_be_applied_to_all_products():
    result = calc('354', [product(), product('200')], tax_components=[dict(label='Tax group A', rate='5'), dict(label='Tax group B', rate='18')])
    assert result['status'] != 'reconciled'


@pytest.mark.parametrize('fields', [{}, {'discount_amount': None, 'discount_percent': None}, {'discount_amount': '', 'discount_percent': ''}])
def test_absent_discounts_default_to_zero_without_inventing_required_values(fields):
    invoice = normalize_invoice(dict(currency='INR', total='100', line_items=[dict(description='Service', **fields)]))
    line = invoice['line_items'][0]
    assert line['discount_amount'] == '0'
    assert line.get('discount_percent') is None
    assert line['quantity'] is None and line['unit_price'] is None and line['amount'] is None
    assert normalize_invoice(invoice) == invoice


@pytest.mark.parametrize('discount', [{'discount_amount': '10'}, {'discount_percent': '10'}, {'discount_amount': '10', 'discount_percent': '10'}])
def test_stated_discount_with_product_tax_keeps_its_own_representation(discount):
    line = product(tax_rate='18', **discount)
    line['amount'] = '90'
    invoice = normalize_invoice(dict(currency='INR', total='106.20', line_items=[line]))
    normalized = invoice['line_items'][0]
    for missing in {'discount_amount', 'discount_percent'} - discount.keys():
        assert normalized.get(missing) is None
    assert_total(calculate_rules(invoice), '106.20')


@pytest.mark.parametrize('discount', [{'discount_amount': 'unreadable'}, {'discount_percent': 'bad'}, {'discount_amount': '0', 'discount_percent': '10'}])
def test_invalid_or_conflicting_discounts_do_not_turn_into_no_discount(discount):
    line = product(**discount)
    result = calc('100', [line])
    assert result['status'] != 'reconciled'
