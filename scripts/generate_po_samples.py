#!/usr/bin/env python3
"""Generate fictional PO upload fixtures for the application workflow."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.po_workflow import EXAMPLE_PO, template_bytes


def main():
    destination = ROOT / 'samples' / 'purchase-orders'
    destination.mkdir(parents=True, exist_ok=True)
    for format in ('json', 'csv', 'xlsx'):
        content, _ = template_bytes(format)
        (destination / f'complete-purchase-order.{format}').write_bytes(content)
    lines = [
        'PURCHASE ORDER',
        'FICTIONAL TEST DOCUMENT - not a real purchasing authorization',
        '', 'PO number: PO-UPLOAD-1001', 'Vendor name: Sample Stationery Ltd',
        'PO date: 2026-11-09', 'Currency: USD', 'Subtotal: 1000.00', 'Tax: 100.00', 'Total: 1100.00',
        'Approval status: approved', 'PO status: open', 'Approved by: Sample purchasing manager', '',
        'Description | Quantity | Unit price | Amount', 'Office supplies | 10 | 100.00 | 1000.00', '',
        'Supplier approval is a separate process. This sample vendor starts pending.',
    ]
    (destination / 'complete-purchase-order.txt').write_text('\n'.join(lines) + '\n')
    import pymupdf
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=595, height=842)
        for index, line in enumerate(lines):
            page.insert_text((40, 55 + index * 25), line, fontsize=15 if index == 0 else 10)
        pdf.save(destination / 'complete-purchase-order.pdf')
        page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).save(destination / 'complete-purchase-order.png')
    missing = dict(EXAMPLE_PO)
    missing.pop('tax')
    missing.pop('po_date')
    (destination / 'missing-required-fields.json').write_text(json.dumps(missing, indent=2) + '\n')
    mismatch = {**EXAMPLE_PO, 'total': '999.00'}
    (destination / 'arithmetic-mismatch.json').write_text(json.dumps(mismatch, indent=2) + '\n')
    invoice = {'invoice_number': 'INV-PO-UPLOAD-1001', 'vendor_name': EXAMPLE_PO['vendor_name'],
               'po_number': EXAMPLE_PO['po_number'], 'invoice_date': EXAMPLE_PO['po_date'],
               'currency': 'USD', 'subtotal': '500.00', 'tax': '50.00', 'total': '550.00',
               'line_items': [{'description': 'Office supplies', 'quantity': '5',
                               'unit_price': '100.00', 'amount': '500.00'}]}
    (destination / 'matching-invoice.json').write_text(json.dumps(invoice, indent=2) + '\n')
    print(f'Generated PO fixtures in {destination}')


if __name__ == '__main__':
    main()
