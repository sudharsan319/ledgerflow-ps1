"""Pure purchase-order normalization and validation.

PO validity is distinct from vendor approval. Neither this module nor the source
approval_status authorizes a vendor; the API owns human confirmation and its audit.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
from typing import Any

from .models import InvalidNumber, decimal_string, decimal_value

_REQUIRED = [
    ('po_number', 'PO number', 'A unique purchase-order reference.'),
    ('vendor_name', 'Vendor name', 'The supplier named on the purchase order.'),
    ('po_date', 'PO date', 'A real issue date in YYYY-MM-DD format.'),
    ('currency', 'Currency', 'An explicit three-letter currency code, such as USD or INR.'),
    ('subtotal', 'Subtotal', 'The positive sum of line amounts before tax.'),
    ('tax', 'Tax', 'The stated tax amount; use an explicit 0 for a tax-free PO.'),
    ('total', 'Total', 'The positive gross total including tax.'),
    ('approval_status', 'Approval status', 'approved, pending, or rejected; never inferred from upload.'),
    ('status', 'PO status', 'open or closed; a closed PO is not eligible for invoice matching.'),
    ('line_items', 'Line items', 'At least one described line with quantity, unit price, and amount.'),
]
_LINE_FIELDS = [
    ('description', 'Description', 'The goods or services being purchased.'),
    ('quantity', 'Quantity', 'A positive quantity.'),
    ('unit_price', 'Unit price', 'A nonnegative price in the PO currency; up to six decimal places.'),
    ('amount', 'Line amount', 'The nonnegative quantity × unit price amount, rounded in the PO currency.'),
]
PO_POLICY = {
    'version': '1',
    'required_fields': [{'key': k, 'label': label, 'description': description} for k, label, description in _REQUIRED],
    'line_item_fields': [{'key': k, 'label': label, 'description': description} for k, label, description in _LINE_FIELDS],
    'rules': [
        'All required fields must be present; missing tax, status, or approval status is never filled automatically.',
        'Amounts use finite Decimal arithmetic. Each line must reconcile, line amounts must sum to subtotal, and subtotal plus tax must equal total within one currency rounding unit.',
        'Only a structurally valid, approved, open PO is eligible for invoice matching.',
        'PO validation and vendor approval are separate. A valid PO does not approve or verify a vendor.',
        'A reviewer must confirm source accuracy and purchasing authorization before a document is registered.',
        'Existing invoice commitments are ledger-derived and must not be reset by a PO upload.',
    ],
}

_ALIASES = {
    'po_number': ('po_number', 'po_no', 'po_reference', 'purchase_order', 'purchase_order_number', 'purchase_order_no', 'order_number', 'order_no'),
    'vendor_name': ('vendor_name', 'vendor', 'supplier_name', 'supplier'),
    'vendor_id': ('vendor_id', 'supplier_id'),
    'po_date': ('po_date', 'purchase_order_date', 'order_date', 'issue_date', 'date'),
    'currency': ('currency', 'currency_code'),
    'subtotal': ('subtotal', 'sub_total', 'net_amount'),
    'tax': ('tax', 'tax_amount', 'total_tax', 'vat_amount', 'gst_amount'),
    'total': ('total', 'total_amount', 'grand_total', 'gross_amount'),
    'approval_status': ('approval_status', 'approval_state', 'authorization_status'),
    'status': ('status', 'po_status', 'order_status'),
    'approved_by': ('approved_by', 'approver', 'authorized_by'),
    'description': ('description', 'notes'),
    'line_items': ('line_items', 'items', 'lines'),
}
_LINE_ALIASES = {
    'description': ('description', 'item_description', 'name', 'item'),
    'quantity': ('quantity', 'qty'),
    'unit_price': ('unit_price', 'price', 'unit_cost', 'rate'),
    'amount': ('amount', 'line_total', 'line_amount', 'total'),
    'sku': ('sku', 'item_code', 'product_code'),
}


def _key(value: Any) -> str:
    return re.sub(r'[\s\-]+', '_', str(value).strip().lower())


def _mapped(raw: dict, warnings: list[str], scope: str = '') -> dict:
    mapped = {}
    for key, value in raw.items():
        canonical = _key(key)
        if canonical in mapped and mapped[canonical] != value:
            warnings.append(f'{scope}Conflicting columns named {canonical}.')
        mapped[canonical] = value
    return mapped


def _value(raw: dict, aliases: tuple[str, ...], warnings: list[str], field: str):
    values = [(name, raw[name]) for name in aliases if name in raw and raw[name] is not None and raw[name] != '']
    if len(values) > 1 and any(str(value).strip() != str(values[0][1]).strip() for _, value in values[1:]):
        warnings.append(f'Conflicting aliases for {field}: {", ".join(name for name, _ in values)}.')
    return values[0][1] if values else None


def _text(value: Any, warnings: list[str], field: str, limit: int = 2000) -> str | None:
    if value is None or value == '':
        return None
    if isinstance(value, (dict, list, tuple, bool)):
        warnings.append(f'Invalid {field}: expected text.')
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit or any(ord(char) < 32 and char not in '\n\t' for char in text):
        warnings.append(f'Invalid {field}: value is too long or contains control characters.')
    return text


def _money(value: Any, warnings: list[str], field: str) -> str | None:
    if value is None or value == '':
        return None
    try:
        return decimal_string(decimal_value(value))
    except (InvalidNumber, TypeError, ValueError) as exc:
        warnings.append(f'Invalid {field}: {exc}.')
        return None


def normalize_po(raw: dict) -> dict:
    """Normalize aliases without inventing fields or silently discarding errors."""
    if not isinstance(raw, dict):
        raise ValueError('A purchase order must be a JSON object.')
    warnings = []
    for warning_field in ('normalization_warnings', 'parse_errors'):
        incoming = raw.get(warning_field, [])
        if incoming is None:
            continue
        if not isinstance(incoming, list):
            warnings.append(f'Invalid {warning_field}: expected a list.')
        else:
            warnings.extend(str(item) for item in incoming if item)
    mapped = _mapped(raw, warnings)
    po = {}
    for field, aliases in _ALIASES.items():
        if field == 'line_items':
            continue
        value = _value(mapped, aliases, warnings, field)
        po[field] = _money(value, warnings, field) if field in {'subtotal', 'tax', 'total'} else _text(value, warnings, field, 120 if field in {'po_number', 'vendor_id'} else 2000)
    if po['currency']:
        po['currency'] = po['currency'].upper()
    for field in ('approval_status', 'status'):
        if po[field]:
            po[field] = po[field].lower()
    lines = _value(mapped, _ALIASES['line_items'], warnings, 'line_items')
    if lines is None:
        lines = []
    if not isinstance(lines, list):
        warnings.append('Invalid line_items: expected an array of objects.')
        lines = []
    if len(lines) > 500:
        warnings.append('Too many line items: maximum is 500.')
        lines = lines[:500]
    po['line_items'] = []
    for index, raw_line in enumerate(lines):
        if not isinstance(raw_line, dict):
            warnings.append(f'Invalid line {index + 1}: expected an object.')
            continue
        line_raw = _mapped(raw_line, warnings, f'Line {index + 1}: ')
        line = {}
        for field, aliases in _LINE_ALIASES.items():
            value = _value(line_raw, aliases, warnings, f'line {index + 1} {field}')
            line[field] = _money(value, warnings, f'line {index + 1} {field}') if field in {'quantity', 'unit_price', 'amount'} else _text(value, warnings, f'line {index + 1} {field}')
        po['line_items'].append(line)
    po['normalization_warnings'] = list(dict.fromkeys(warnings))
    return po


def _number(value):
    try:
        return decimal_value(value)
    except (InvalidNumber, ValueError, TypeError):
        return None


def _unit(currency: str) -> Decimal:
    if currency in {'JPY', 'KRW', 'VND', 'CLP', 'ISK', 'PYG', 'UGX'}:
        return Decimal('1')
    if currency in {'BHD', 'KWD', 'OMR', 'JOD', 'TND'}:
        return Decimal('0.001')
    return Decimal('0.01')


def validate_po(po: dict) -> dict:
    """Return structural checks and purchasing eligibility, never vendor approval.

    Accepts normalized or raw canonical data. Validation normalizes defensively so
    malformed nested input cannot escape checks by bypassing normalize_po().
    """
    po = normalize_po(po)
    checks = []
    missing = []

    def check(code, field, label, status, message, blocking=False):
        checks.append({'code': code, 'field': field, 'label': label, 'status': status,
                       'message': message, 'blocking': bool(blocking)})

    for field, label, _ in _REQUIRED:
        value = po.get(field)
        if value is None or value == '' or (field == 'line_items' and not value):
            missing.append(field)
            check('required_' + field, field, label, 'fail', f'{label} is required.', True)
        else:
            check('required_' + field, field, label, 'pass', f'{label} is present.')
    if po['normalization_warnings']:
        check('normalization', 'normalization_warnings', 'Unambiguous input', 'fail', ' '.join(po['normalization_warnings']), True)
    else:
        check('normalization', 'normalization_warnings', 'Unambiguous input', 'pass', 'No malformed or conflicting fields detected.')
    if po['po_date']:
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', po['po_date']):
                raise ValueError('Expected ISO date')
            date.fromisoformat(po['po_date'])
            check('po_date', 'po_date', 'Valid PO date', 'pass', 'PO date is a real ISO calendar date.')
        except ValueError:
            check('po_date', 'po_date', 'Valid PO date', 'fail', 'Use a real date in YYYY-MM-DD format.', True)
    currency = po['currency'] or ''
    if currency and not re.fullmatch(r'[A-Z]{3}', currency):
        check('currency', 'currency', 'Explicit currency', 'fail', 'Use a three-letter currency code; a currency symbol alone is ambiguous.', True)
    unit = _unit(currency)
    amounts = {field: _number(po[field]) for field in ('subtotal', 'tax', 'total')}
    for field, value in amounts.items():
        if po[field] is None:
            continue
        if value is None or value < 0 or (field != 'tax' and value == 0):
            check('amount_' + field, field, field.title(), 'fail', f'{field.title()} must be {"nonnegative" if field == "tax" else "positive"} and finite.', True)
        elif value != value.quantize(unit):
            check('precision_' + field, field, field.title(), 'fail', f'{field.title()} has more precision than supported for {currency or "the currency"}.', True)
        else:
            check('amount_' + field, field, field.title(), 'pass', f'{field.title()} is a valid monetary amount.')
    if all(value is not None for value in amounts.values()):
        difference = abs(amounts['subtotal'] + amounts['tax'] - amounts['total'])
        check('totals', 'total', 'Subtotal + tax = total', 'pass' if difference <= unit else 'fail',
              f'Subtotal {amounts["subtotal"]} + tax {amounts["tax"]} compared with total {amounts["total"]}; difference {difference} {currency}.', difference > unit)
    for field, allowed in (('approval_status', {'approved', 'pending', 'rejected'}), ('status', {'open', 'closed'})):
        value = po[field]
        if value and value not in allowed:
            check(field, field, field.replace('_', ' ').title(), 'fail', f'Use one of: {", ".join(sorted(allowed))}.', True)
    if po['approval_status'] in {'pending', 'rejected'}:
        check('approval_eligibility', 'approval_status', 'Purchasing approval', 'warn', f'This PO is {po["approval_status"]} and is not eligible for invoice matching.')
    elif po['approval_status'] == 'approved':
        check('approval_eligibility', 'approval_status', 'Purchasing approval', 'pass', 'Source states approved; reviewer authorization confirmation is still required.')
    if po['status'] == 'closed':
        check('open_eligibility', 'status', 'PO availability', 'warn', 'This PO is closed and is not eligible for invoice matching.')
    elif po['status'] == 'open':
        check('open_eligibility', 'status', 'PO availability', 'pass', 'PO is open.')
    line_amounts = []
    all_amounts = True
    seen_skus = set()
    for index, item in enumerate(po['line_items']):
        prefix = f'line_items.{index}'
        for field, label, _ in _LINE_FIELDS:
            if item.get(field) is None or item.get(field) == '':
                path = prefix + '.' + field
                missing.append(path)
                check(f'line_{index + 1}_required_{field}', path, f'Line {index + 1} {label.lower()}', 'fail', f'Line {index + 1} requires {label.lower()}.', True)
        quantity, price, amount = (_number(item.get(field)) for field in ('quantity', 'unit_price', 'amount'))
        if amount is None:
            all_amounts = False
        else:
            line_amounts.append(amount)
        for field, value in (('quantity', quantity), ('unit_price', price), ('amount', amount)):
            if value is not None and (value < 0 or (field == 'quantity' and value == 0)):
                check(f'line_{index + 1}_{field}', prefix + '.' + field, f'Line {index + 1} {field.replace("_", " ")}', 'fail', f'Line {index + 1} {field} must be {"positive" if field == "quantity" else "nonnegative"}.', True)
        if amount is not None and amount != amount.quantize(unit):
            check(f'line_{index + 1}_precision', prefix + '.amount', f'Line {index + 1} amount', 'fail', 'Line amount exceeds the currency rounding precision.', True)
        if all(value is not None for value in (quantity, price, amount)):
            difference = abs(quantity * price - amount)
            check(f'line_{index + 1}_arithmetic', prefix, f'Line {index + 1} arithmetic', 'pass' if difference <= unit else 'fail',
                  f'Quantity × unit price = {quantity * price}; stated amount = {amount}.', difference > unit)
        sku = re.sub(r'\W', '', (item.get('sku') or '').casefold())
        if sku:
            if sku in seen_skus:
                check(f'line_{index + 1}_sku', prefix + '.sku', 'Unique line SKU', 'fail', f'Line {index + 1} repeats an existing SKU; combine the line or use distinct item identifiers.', True)
            seen_skus.add(sku)
    if po['line_items'] and all_amounts and amounts['subtotal'] is not None:
        line_sum = sum(line_amounts, Decimal(0))
        difference = abs(line_sum - amounts['subtotal'])
        check('line_sum', 'line_items', 'Line amounts = subtotal', 'pass' if difference <= unit else 'fail',
              f'Line amounts sum to {line_sum}; subtotal is {amounts["subtotal"]}.', difference > unit)
    valid = not any(item['blocking'] and item['status'] != 'pass' for item in checks)
    return {'valid': valid, 'eligible': valid and po['approval_status'] == 'approved' and po['status'] == 'open',
            'checks': checks, 'missing_fields': missing}
