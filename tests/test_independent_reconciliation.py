"""Printed values are targets, not inputs used to force equations to match."""
from copy import deepcopy
from decimal import Decimal
import pytest
from backend.app.models import normalize_invoice
from backend.app.calculations import calculate_rules


def calculate(row, **headers):
    return calculate_rules(normalize_invoice(dict(invoice_number='MATH', invoice_date='2026-01-01',
        vendor_name='Supplier', currency='INR', tax_mode='exclusive', subtotal='350', tax='63', total='413',
        line_items=[row], **headers)))


def product(**updates):
    return dict(description='Product', quantity='2', unit_price='200', discount_amount='50',
                amount='413', tax='63', tax_rate='18', unit_price_tax_mode='exclusive', amount_tax_mode='inclusive', **updates)


def test_wrong_printed_tax_cannot_drive_a_matching_gross_total():
    row=product(); row.update(tax='99',amount='449')
    result=calculate_rules(normalize_invoice(dict(invoice_number='MATH',vendor_name='Supplier',invoice_date='2026-01-01',
        currency='INR',subtotal='350',tax='99',total='449',line_items=[row])))
    calc=result['products'][0]
    assert calc['verification']['calculated_amount']=='413.00'
    assert calc['calculated_tax']=='63.00'
    assert calc['verification']['status']=='not_matched'
    assert result['computed_totals']['total']=='413.00'
    assert any(c['code']=='recomputed_total' and c['status']=='fail' for c in result['checks'])


def test_price_edit_uses_discounted_base_not_a_stale_extracted_base():
    row=dict(description='Flat Booking - Unit A-302',quantity='1200',unit_price='350',discount_amount='20000',
        discount_percent=None,amount='472000',unit_price_tax_mode='unknown',amount_tax_mode='unknown',
        tax_components=[dict(label='GST',rate='18',amount='72000',taxable_amount='720000')])
    original=deepcopy(row)
    result=calculate_rules(normalize_invoice(dict(currency='INR',subtotal='400000',tax='72000',total='472000',line_items=[row])))
    p=result['products'][0]
    assert p['calculated_net']=='400000.00' and p['calculated_tax']=='72000.00'
    assert p['verification']['calculated_amount']=='472000.00'
    assert p['verification']['status']=='not_matched'  # Bad printed-base candidate stays visible.
    assert any(c['code']=='line_1_component_0_base' and c['status']=='fail' for c in result['checks'])
    assert row==original
    row['tax_components'][0]['taxable_amount']=None
    corrected=calculate_rules(normalize_invoice(dict(currency='INR',subtotal='400000',tax='72000',total='472000',line_items=[row])))
    assert corrected['status']=='reconciled'
    row['unit_price']='400'
    changed=calculate_rules(normalize_invoice(dict(currency='INR',subtotal='400000',tax='72000',total='472000',line_items=[row])))
    assert changed['products'][0]['verification']['status']!='matched'


def test_negative_discounted_base_explains_the_quantity_or_price_error():
    row=product(); row.update(quantity='1',unit_price='350',discount_amount='20000',unit_price_tax_mode='unknown',amount_tax_mode='unknown')
    result=calculate(row)
    assert any('discount exceeds quantity' in c['message'] for c in result['checks'])
    assert result['products'][0]['verification']['status']=='not_matched'


@pytest.mark.parametrize('taxes', [
    {'tax_rate':'18','tax':'90'},
    {'tax_components':[dict(label='CGST',rate='9',amount='45'),dict(label='SGST',rate='9',amount='45')]},
])
def test_tax_is_calculated_after_percentage_discount(taxes):
    row=dict(description='Goods',quantity='10',unit_price='100',discount_percent='50',amount='590',
             unit_price_tax_mode='exclusive',amount_tax_mode='inclusive',**taxes)
    result=calculate_rules(normalize_invoice(dict(currency='INR',subtotal='500',tax='90',total='590',line_items=[row])))
    assert result['status']=='reconciled'
    assert result['products'][0]['calculated_tax']=='90.00'
    row['discount_percent']='25'
    changed=calculate_rules(normalize_invoice(dict(currency='INR',subtotal='500',tax='90',total='590',line_items=[row])))
    assert changed['products'][0]['calculated_tax']=='135.00'
    assert changed['products'][0]['verification']['calculated_amount']=='885.00'


def test_inclusive_price_calculation_does_not_backsolve_using_wrong_printed_tax():
    row=dict(description='Goods',quantity='2',unit_price='118',amount='236',tax='99',tax_rate='18',
             unit_price_tax_mode='inclusive',amount_tax_mode='inclusive')
    result=calculate_rules(normalize_invoice(dict(currency='INR',subtotal='236',tax='99',total='236',tax_mode='inclusive',line_items=[row])))
    p=result['products'][0]
    assert p['calculated_net']=='200.00' and p['calculated_tax']=='36.00'
    assert p['verification']['status']=='not_matched'


def test_live_correction_keeps_amount_and_percentage_discount_conflict_visible():
    row=product(); row.update(discount_percent='18')
    result=calculate(row)
    assert result['products'][0]['calculated_tax']=='63.00'
    assert any(c['code']=='line_1_discount' and c['status']=='fail' for c in result['checks'])


def test_document_tax_on_multiple_products_is_not_added_twice():
    rows=[dict(description=name,quantity='1',unit_price='100',amount='100') for name in ('A','B')]
    result=calculate_rules(normalize_invoice(dict(currency='INR',subtotal='200',tax='36',total='236',line_items=rows)))
    assert result['computed_totals']['total']=='236.00'
    assert result['status']=='reconciled'


def test_single_product_document_tax_is_independent_of_wrong_printed_amounts():
    row=dict(description='Goods',quantity='2',unit_price='100',amount='240',unit_price_tax_mode='exclusive',amount_tax_mode='inclusive')
    raw=dict(currency='INR',subtotal='200',tax='40',total='240',line_items=[row],
             tax_components=[dict(label=name,rate='9',amount='20',taxable_amount='500') for name in ('CGST','SGST')])
    result=calculate_rules(normalize_invoice(raw)); p=result['products'][0]
    assert p['calculated_tax']=='36.00'
    assert p['verification']['calculated_amount']=='236.00'
    assert p['verification']['status']=='not_matched'
    assert result['computed_totals']['total']=='236.00'


@pytest.mark.parametrize('discount', [{'discount_amount':'0','discount_percent':'50'}, {'discount_amount':'50','discount_percent':'0'}])
def test_unused_zero_discount_column_does_not_conflict_with_the_stated_discount(discount):
    row=dict(description='Goods',quantity='1',unit_price='100',amount='59',tax_rate='18',tax='9',
             unit_price_tax_mode='exclusive',amount_tax_mode='inclusive',**discount)
    result=calculate_rules(normalize_invoice(dict(currency='INR',subtotal='50',tax='9',total='59',line_items=[row])))
    assert result['status']=='reconciled'
