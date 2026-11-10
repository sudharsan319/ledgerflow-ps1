"""Optional, vendor-independent financial facts shared by both calculation paths."""
from .models import InvalidNumber, decimal_value, decimal_string
from .product_taxes import component_tax_input, LINE_AGGREGATE_FIELDS

EXTRA_FIELDS = ('tax_mode', 'subtotal_scope', 'taxable_subtotal', 'tax_components', 'adjustments', 'tax_status')
LINE_EXTRA_FIELDS = ('discount_amount', 'discount_percent', 'tax', 'tax_rate', 'tax_evidence', 'tax_rate_evidence', 'amount_tax_mode', 'unit_price_tax_mode', 'unit_price_precision', 'tax_components', 'tax_status')


def extraction_properties():
    text = {'type': ['string', 'null']}
    def obj(properties):
        return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
    return {
        'tax_status': {'type': ['string', 'null'], 'enum': ['not_stated', 'stated', 'unclear', None]},
        'tax_mode': {'type': ['string', 'null'], 'enum': ['exclusive', 'inclusive', 'unknown', None]},
        'subtotal_scope': {'type': ['string', 'null'], 'enum': ['before_adjustments', 'after_adjustments', 'unknown', None]},
        'taxable_subtotal': text,
        'tax_components': {'type': 'array', 'items': obj({key: text for key in ('label', 'amount', 'rate', 'taxable_amount', 'evidence')})},
        'adjustments': {'type': 'array', 'items': obj({
            'label': text, 'amount': text, 'percent': text, 'base_amount': text,
            'kind': {'type': ['string', 'null'], 'enum': ['charge', 'discount', 'rounding', 'unknown', None]},
            'included_in_subtotal': {'type': ['boolean', 'null']}, 'evidence': text,
        })},
    }


def normalize_financials(raw, invoice, warnings):
    def number(value, label, signed=False):
        if value in (None, ''):
            return None
        try:
            return decimal_string(decimal_value(value, allow_negative=signed))
        except InvalidNumber as exc:
            warnings.append(f'Invalid {label}: {exc}')
            return None
    def tax_status(source, target, label):
        if source.get('tax_status') is not None:
            value = source['tax_status']
            if value not in ('not_stated', 'stated', 'unclear'):
                warnings.append(f'Invalid {label}tax_status')
                value = 'unclear'
            target['tax_status'] = value
    tax_status(raw, invoice, '')
    def line_components(rows, index):
        path = f'line_items.{index}.tax_components'
        if not isinstance(rows, list) or len(rows) > 20:
            warnings.append(f'Invalid {path}: expected at most 20 entries')
            return []
        result = []
        for tax_index, row in enumerate(rows):
            if not isinstance(row, dict):
                warnings.append(f'Invalid {path}.{tax_index}: expected an object')
                continue
            item = {key: str(row.get(key) or '')[:1000] for key in ('label', 'evidence')}
            for key in ('amount', 'rate', 'taxable_amount'):
                item[key] = number(row.get(key), f'{path}.{tax_index}.{key}')
            result.append(item)
        return result
    for field, choices in [('tax_mode', ('exclusive', 'inclusive', 'unknown')),
                           ('subtotal_scope', ('before_adjustments', 'after_adjustments', 'unknown'))]:
        if field in raw:
            value = raw[field]
            if value is not None and value not in choices:
                warnings.append(f'Invalid {field}')
                value = 'unknown'
            invoice[field] = value or 'unknown'
    if 'taxable_subtotal' in raw:
        invoice['taxable_subtotal'] = number(raw['taxable_subtotal'], 'taxable_subtotal')
    for field in ('tax_components', 'adjustments'):
        if field not in raw:
            continue
        rows = raw[field]
        if not isinstance(rows, list) or len(rows) > 100:
            warnings.append(f'Invalid {field}: expected at most 100 entries')
            invoice[field] = []
            continue
        result = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                warnings.append(f'Invalid {field} entry {index + 1}')
                continue
            item = {key: str(row.get(key) or '')[:1000] for key in ('label', 'evidence')}
            item['amount'] = number(row.get('amount'), f'{field}.{index}.amount', signed=field == 'adjustments' and row.get('kind') == 'rounding')
            if field == 'tax_components':
                for key in ('rate', 'taxable_amount'):
                    item[key] = number(row.get(key), f'{field}.{index}.{key}')
            else:
                item['percent'] = number(row.get('percent'), f'{field}.{index}.percent')
                item['base_amount'] = number(row.get('base_amount'), f'{field}.{index}.base_amount')
                item['kind'] = row.get('kind') or 'unknown'
                if item['kind'] not in ('charge', 'discount', 'rounding', 'unknown'):
                    warnings.append(f'Invalid adjustment kind at {index + 1}')
                    item['kind'] = 'unknown'
                included = row.get('included_in_subtotal')
                if included is not None and not isinstance(included, bool):
                    warnings.append(f'Invalid included_in_subtotal at {index + 1}')
                    included = None
                item['included_in_subtotal'] = included
            result.append(item)
        invoice[field] = result
    source_lines = raw.get('line_items', raw.get('items', []))
    source_lines = source_lines if isinstance(source_lines, list) else []
    source_lines = [line for line in source_lines if isinstance(line, dict)]
    for index, (source, line) in enumerate(zip(source_lines, invoice['line_items'])):
        if not isinstance(source, dict):
            continue
        tax_status(source, line, f'line {index + 1} ')
        if 'tax_components' in source:
            line['tax_components'] = line_components(source['tax_components'], index)
        if line.get('tax_components'):
            source = component_tax_input(source)
            for key in LINE_AGGREGATE_FIELDS:
                line.pop(key, None)
        for key in ('discount_amount', 'discount_percent', 'tax', 'tax_rate'):
            if key in source:
                line[key] = number(source[key], f'line {index + 1} {key}')
        for key in ('tax_evidence', 'tax_rate_evidence'):
            if key in source:
                line[key] = source[key][:500] if isinstance(source[key], str) else None
        # No discount means zero. A rate-only discount still needs its amount
        # calculated; do not invent a contradictory zero counterpart. Invalid
        # supplied values retain their validation warning instead of becoming zero.
        if all(source.get(key) in (None, '') for key in ('discount_amount', 'discount_percent')):
            line['discount_amount'] = '0'
        # A zero in an unused discount column is a placeholder, not a second
        # discount that contradicts the other, nonzero representation.
        amount_discount, percent_discount = line.get('discount_amount'), line.get('discount_percent')
        if amount_discount is not None and percent_discount is not None:
            if decimal_value(amount_discount) > 0 and decimal_value(percent_discount) == 0:
                line['discount_percent'] = None
            elif decimal_value(percent_discount) > 0 and decimal_value(amount_discount) == 0:
                line['discount_amount'] = None
        for key in ('amount_tax_mode', 'unit_price_tax_mode'):
            if source.get(key) is not None:
                value = source[key]
                if value not in ('inclusive', 'exclusive', 'unknown'):
                    warnings.append(f'Invalid line {index + 1} {key}')
                    value = 'unknown'
                line[key] = value
        precision = source.get('unit_price_precision')
        if precision is not None:
            if isinstance(precision, bool) or not isinstance(precision, int) or not 0 <= precision <= 6:
                warnings.append(f'Invalid line {index + 1} unit_price_precision')
            else:
                line['unit_price_precision'] = precision
    # Only absent tax fields default to zero. A malformed or explicitly unclear
    # tax retains its uncertainty so policy checks cannot silently accept it.
    for source, line in zip(source_lines, invoice['line_items']):
        if line.get('tax_status') in ('stated', 'unclear') or line.get('tax_components'):
            continue
        if any(source.get(key) not in (None, '') and line.get(key) is None for key in ('tax', 'tax_rate')):
            line['tax_status'] = 'unclear'
        elif line.get('tax') is None and line.get('tax_rate') is None:
            line['tax'] = '0'
            line['tax_status'] = 'not_stated'
    if invoice.get('tax') is None and not invoice.get('tax_components') and invoice.get('tax_status') not in ('stated', 'unclear'):
        if any(raw.get(key) not in (None, '') for key in ('tax', 'tax_amount', 'total_tax')):
            invoice['tax_status'] = 'unclear'
        else:
            invoice['tax'] = '0'
            invoice['tax_status'] = 'not_stated'
