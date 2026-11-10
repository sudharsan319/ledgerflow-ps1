from copy import deepcopy

import pytest

from backend.app.po_policy import PO_POLICY, normalize_po, validate_po


def po(**updates):
    raw = {'po_number': 'PO-101', 'vendor_name': 'Example Supplier', 'po_date': '2026-01-15',
           'currency': 'USD', 'subtotal': '100', 'tax': '5', 'total': '105',
           'approval_status': 'approved', 'status': 'open',
           'line_items': [{'description': 'Paper', 'quantity': '4', 'unit_price': '25', 'amount': '100'}]}
    raw.update(updates)
    return raw


def check(result, code):
    return next(item for item in result['checks'] if item['code'] == code)


def test_complete_po_is_structurally_valid_and_eligible_without_vendor_changes():
    raw = po()
    before = deepcopy(raw)
    result = validate_po(raw)
    assert result['valid'] and result['eligible']
    assert result['missing_fields'] == []
    assert all(item['status'] == 'pass' for item in result['checks'])
    assert raw == before
    assert 'vendor_approved' not in result
    assert 'vendor approval are separate' in ' '.join(PO_POLICY['rules'])


@pytest.mark.parametrize('field', [entry['key'] for entry in PO_POLICY['required_fields']])
def test_each_required_field_is_enforced(field):
    raw = po()
    del raw[field]
    result = validate_po(raw)
    assert not result['valid']
    assert not result['eligible']
    assert field in result['missing_fields']


def test_no_tax_or_status_defaults_are_invented():
    raw = normalize_po({'po_number': 'PO-1', 'vendor_name': 'Acme'})
    assert raw['tax'] is None
    assert raw['approval_status'] is None
    assert raw['status'] is None
    assert raw['line_items'] == []


@pytest.mark.parametrize('approval,status', [('pending', 'open'), ('rejected', 'open'), ('approved', 'closed')])
def test_structurally_valid_noneligible_states(approval, status):
    result = validate_po(po(approval_status=approval, status=status))
    assert result['valid']
    assert not result['eligible']
    assert all(not item['blocking'] for item in result['checks'] if item['status'] == 'warn')


@pytest.mark.parametrize('field,value', [('approval_status', 'yes'), ('status', 'active'), ('approval_status', True)])
def test_unknown_status_is_invalid(field, value):
    result = validate_po(po(**{field: value}))
    assert not result['valid']


@pytest.mark.parametrize('field,value', [('total', 'NaN'), ('subtotal', 'Infinity'), ('tax', True), ('total', '1,234'), ('subtotal', '1e2'), ('total', '12.1234567')])
def test_malformed_and_ambiguous_amounts_are_not_accepted(field, value):
    normalized = normalize_po(po(**{field: value}))
    assert normalized[field] is None
    assert normalized['normalization_warnings']
    assert not validate_po(normalized)['valid']


@pytest.mark.parametrize('field', ['subtotal', 'total'])
def test_subtotal_and_total_must_be_positive(field):
    assert not validate_po(po(**{field: '0'}))['valid']


def test_zero_tax_is_valid_when_explicit():
    assert validate_po(po(tax='0', total='100'))['valid']


def test_negative_tax_is_invalid():
    assert not validate_po(po(tax='-5', total='95'))['valid']


def test_totals_must_reconcile_independently_of_purchase_tolerance():
    result = validate_po(po(total='110'))
    assert not result['valid']
    assert check(result, 'totals')['blocking']


def test_lines_must_reconcile_to_subtotal():
    result = validate_po(po(subtotal='105', total='110'))
    assert check(result, 'line_sum')['status'] == 'fail'
    assert not result['eligible']


def test_line_quantity_times_price_must_equal_amount():
    result = validate_po(po(line_items=[{'description': 'Paper', 'quantity': '5', 'unit_price': '25', 'amount': '100'}]))
    assert check(result, 'line_1_arithmetic')['blocking']


@pytest.mark.parametrize('field', ['description', 'quantity', 'unit_price', 'amount'])
def test_required_line_fields_are_reported(field):
    raw = po()
    del raw['line_items'][0][field]
    result = validate_po(raw)
    assert f'line_items.0.{field}' in result['missing_fields']
    assert not result['valid']


def test_zero_quantity_and_negative_price_are_invalid():
    raw = po()
    raw['line_items'][0]['quantity'] = '0'
    assert not validate_po(raw)['valid']
    raw['line_items'][0].update(quantity='-4', unit_price='-25')
    assert not validate_po(raw)['valid']


def test_high_precision_unit_price_can_be_rounded_to_currency_amount():
    raw = po(subtotal='691.97', tax='0', total='691.97', line_items=[
        {'description': 'Cartons', 'quantity': '1420', 'unit_price': '0.4873', 'amount': '691.97'}])
    assert validate_po(raw)['eligible']


def test_currency_precision_and_zero_decimal_currency():
    assert not validate_po(po(subtotal='100.001', tax='0', total='100.001'))['valid']
    assert validate_po(po(currency='JPY', tax='0', total='100'))['valid']
    assert not validate_po(po(currency='$'))['valid']


@pytest.mark.parametrize('value', ['2026-02-30', '01/02/2026', '2026-1-15'])
def test_invalid_or_ambiguous_dates_are_invalid_and_preserved(value):
    normalized = normalize_po(po(po_date=value))
    assert normalized['po_date'] == value
    assert not validate_po(normalized)['valid']


def test_spreadsheet_aliases_and_case_are_normalized():
    raw = po()
    raw['Purchase Order Number'] = raw.pop('po_number')
    raw['Supplier Name'] = raw.pop('vendor_name')
    raw['Order Date'] = raw.pop('po_date')
    raw['Currency Code'] = 'usd'
    raw.pop('currency')
    raw['approval_status'] = 'APPROVED'
    raw['status'] = 'Open'
    raw['line_items'] = [{'Item Description': 'Paper', 'Qty': 4, 'Unit Price': 25, 'Line Total': 100}]
    normalized = normalize_po(raw)
    assert normalized['currency'] == 'USD'
    assert validate_po(normalized)['eligible']


def test_conflicting_aliases_or_duplicate_columns_block():
    normalized = normalize_po(po(purchase_order_number='OTHER-PO'))
    assert 'Conflicting aliases' in ' '.join(normalized['normalization_warnings'])
    assert not validate_po(normalized)['valid']
    raw = po()
    raw['po number'] = 'OTHER-PO'
    assert not validate_po(raw)['valid']


def test_bad_input_shapes_never_become_empty_valid_data():
    with pytest.raises(ValueError):
        normalize_po([])
    for lines in ('[]', [123], [{}], None):
        assert not validate_po(po(line_items=lines))['valid']


def test_errors_are_preserved_and_trust_metadata_is_not_accepted():
    normalized = normalize_po(po(parse_errors=['Column mismatch'], verified=True, vendor_approved=True))
    assert 'Column mismatch' in normalized['normalization_warnings']
    assert 'verified' not in normalized and 'vendor_approved' not in normalized
    assert not validate_po(normalized)['valid']
    normalized['normalization_warnings'] = []
    assert validate_po(normalized)['valid']


def test_duplicate_skus_are_not_silently_collapsed():
    raw = po(line_items=[
        {'description': 'First', 'sku': 'ABC-1', 'quantity': '2', 'unit_price': '25', 'amount': '50'},
        {'description': 'Second', 'sku': 'abc1', 'quantity': '2', 'unit_price': '25', 'amount': '50'}])
    assert not validate_po(raw)['valid']


def test_normalization_is_idempotent():
    first = normalize_po(po(total='NaN'))
    assert normalize_po(first) == first
