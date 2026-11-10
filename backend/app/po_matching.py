"""Deterministic PO line allocation and cumulative approved-invoice quantities."""
import hashlib
import json
import re
from decimal import Decimal
from .models import InvalidNumber, decimal_value, decimal_string


def identity(value):
    return re.sub(r'[^\w]', '', str(value or '').casefold())


def number(value):
    try:
        return decimal_value(value)
    except (InvalidNumber, TypeError, ValueError):
        return None


def po_line_options(po):
    result = []
    for index, item in enumerate(po.get('line_items') or []):
        # Reordering or changing product identity invalidates an old selection;
        # changing quantities/prices keeps the identity and rechecks its limits.
        key = [po.get('po_number'), index, identity(item.get('sku')), identity(item.get('description'))]
        ref = hashlib.sha256(json.dumps(key).encode()).hexdigest()
        result.append(dict(ref=ref, index=index, description=item.get('description') or f'PO product {index+1}',
                           sku=item.get('sku'), quantity=item.get('quantity'), unit_price=item.get('unit_price')))
    return result


def match_line(item, options):
    explicit = item.get('po_line_ref')
    if explicit:
        matches = [o for o in options if o['ref'] == explicit]
        if len(matches) != 1:
            return None, 'The selected PO product changed or belongs to another PO. Select it again.'
        if item.get('sku') and matches[0].get('sku') and identity(item['sku']) != identity(matches[0]['sku']):
            return None, 'Invoice SKU conflicts with the selected PO product. Correct the SKU or selection.'
        return matches[0], 'selected'
    sku = identity(item.get('sku'))
    if sku and any(o.get('sku') for o in options):
        matches = [o for o in options if identity(o.get('sku')) == sku]
        return (matches[0], 'sku') if len(matches) == 1 else (None, 'SKU is missing from the PO or matches multiple products. Select the PO product.')
    description = identity(item.get('description'))
    matches = [o for o in options if description and identity(o['description']) == description]
    return (matches[0], 'description') if len(matches) == 1 else (None, 'No unique exact product match. Select the corresponding PO product; fuzzy matches are not accepted automatically.')


def line_usage(po, prior_runs):
    options = po_line_options(po)
    usage = {o['ref']: Decimal(0) for o in options}
    unresolved = []
    invoice_count = 0
    for run in prior_runs:
        if run.get('decision') != 'approved' or run.get('superseded_by'):
            continue
        prior_po = (run.get('result') or {}).get('matched_po') or {}
        if str(prior_po.get('po_number', '')).casefold() != str(po.get('po_number', '')).casefold():
            continue
        invoice_count += 1
        rows = (run.get('invoice') or {}).get('line_items') or []
        if not rows:
            unresolved.append(run.get('id'))
        prior_options = po_line_options(prior_po) or options
        for row in rows:
            matched, _ = match_line(row, prior_options)
            qty = number(row.get('quantity'))
            if matched is None or matched['ref'] not in usage or qty is None or qty <= 0:
                unresolved.append(run.get('id'))
            else:
                usage[matched['ref']] += qty
    return options, usage, list(dict.fromkeys(unresolved)), invoice_count


def reconcile_po(invoice, po, prior_runs, products, check, unit):
    options, used, unresolved, invoice_count = line_usage(po, prior_runs)
    result = dict(po_number=po['po_number'], currency=po.get('currency'), options=options, lines=[],
                  ordered_total=po.get('total'), previously_invoiced=po.get('committed', '0'),
                  current_total=invoice.get('total'), prior_invoice_count=invoice_count,
                  receipt_status='not_recorded', receipt_message='These are invoiced quantities, not proof of delivery. No goods-receipt record is available.',
                  history_unresolved=unresolved)
    budget, committed, amount = number(po.get('total')), number(po.get('committed', '0')), number(invoice.get('total'))
    cumulative = committed + amount if committed is not None and amount is not None else None
    remaining = budget - cumulative if budget is not None and cumulative is not None else None
    result.update(cumulative_total=None if cumulative is None else decimal_string(cumulative),
                  remaining_amount=None if remaining is None else decimal_string(remaining),
                  amount_status='unavailable' if remaining is None else 'fully_invoiced' if abs(remaining) <= unit else 'partially_invoiced' if remaining > 0 else 'over_order_value')
    rows = invoice.get('line_items') or []
    if not rows or not options:
        result.update(quantity_status='unavailable', message='Product quantities cannot be checked without both invoice and PO product rows.')
        if options:
            check('po_quantity_unavailable', 'PO product quantities', 'warn', result['message'], False)
        return result
    if unresolved:
        check('po_quantity_history', 'Prior PO quantities', 'warn',
              'Earlier approved invoices have unallocated products or no product quantities. Resolve their allocation before relying on the remaining PO quantity.', True)
    allocations = []
    current = {o['ref']: Decimal(0) for o in options}
    invalid_refs = set()
    for index, row in enumerate(rows):
        matched, reason = match_line(row, options)
        qty = number(row.get('quantity'))
        detail = dict(index=index, description=row.get('description'), po_line_ref=matched['ref'] if matched else None,
                      po_description=matched['description'] if matched else None, match_method=reason if matched else None,
                      current_quantity=row.get('quantity'), status='unmatched', messages=[])
        if matched is None:
            detail['messages'].append(reason)
            check(f'po_line_{index+1}', 'PO product match', 'warn', f'Product {index+1}: {reason}', True)
        elif qty is None or qty <= 0:
            detail['messages'].append('A positive invoiced quantity is required.')
            invalid_refs.add(matched['ref'])
            check(f'po_line_{index+1}_quantity', 'PO product quantity', 'fail', detail['messages'][-1], True)
        else:
            current[matched['ref']] += qty
            detail['status'] = 'matched'
            calc = next((p for p in products if p['index'] == index), {})
            expected_price = number(matched['unit_price'])
            # Calculator output may have more than six decimals; do not parse
            # it as a source field or round a whole line before dividing by qty.
            raw_price = calc.get('calculated_net_unit_price')
            price = Decimal(raw_price) if raw_price is not None else None
            detail.update(po_unit_price=matched['unit_price'], invoice_net_unit_price=None if price is None else decimal_string(price))
            if price is None or expected_price is None:
                detail['status'] = 'incomplete'
                detail['messages'].append('Cannot compare net unit prices until product price/tax treatment is resolved.')
                check(f'po_line_{index+1}_price', 'PO product price', 'warn', detail['messages'][-1], True)
            elif abs(price-expected_price) > unit:
                detail['status'] = 'not_matched'
                detail['messages'].append(f'Net unit price after discount {price} differs from PO net unit price {expected_price}.')
                check(f'po_line_{index+1}_price', 'PO product price', 'fail', detail['messages'][-1], True)
            else:
                check(f'po_line_{index+1}_price', 'PO product price', 'pass', f'Product {index+1}: net unit price after discount agrees with the PO.')
        allocations.append(detail)
    balances = []
    for option in options:
        ref = option['ref']; ordered = number(option['quantity'])
        cumulative_qty = used[ref] + current[ref]
        remaining_qty = ordered-cumulative_qty if ordered is not None else None
        status = 'incomplete' if ordered is None or ref in invalid_refs or unresolved else 'over_invoiced' if remaining_qty < 0 else 'fully_invoiced' if remaining_qty == 0 else 'partially_invoiced'
        balance = dict(**option, previously_invoiced=decimal_string(used[ref]), current_quantity=decimal_string(current[ref]),
                       cumulative_quantity=decimal_string(cumulative_qty), remaining_quantity=None if remaining_qty is None else decimal_string(remaining_qty), status=status)
        balances.append(balance)
        if current[ref] > 0 or status == 'over_invoiced':
            message = f'{option["description"]}: previously invoiced {used[ref]} + this invoice {current[ref]} = {cumulative_qty}; ordered {ordered}; remaining {remaining_qty}.'
            check(f'po_quantity_{option["index"]+1}', 'Cumulative PO product quantity', 'fail' if status == 'over_invoiced' else 'warn' if status == 'incomplete' else 'pass', message, status in ('over_invoiced','incomplete'))
        for allocation in allocations:
            if allocation['po_line_ref'] == ref:
                allocation.update(ordered_quantity=option['quantity'], previously_invoiced=balance['previously_invoiced'],
                                  cumulative_quantity=balance['cumulative_quantity'], remaining_quantity=balance['remaining_quantity'])
                if status in ('over_invoiced', 'incomplete'):
                    allocation['status'] = 'not_matched' if status == 'over_invoiced' else 'incomplete'
                    allocation['messages'].append('Ordered quantity exceeded.' if status == 'over_invoiced' else 'Prior quantity allocation is incomplete.')
    result.update(lines=allocations, balances=balances,
                  quantity_status='matched' if all(a['status']=='matched' for a in allocations) and all(b['status'] not in ('over_invoiced','incomplete') for b in balances) else 'needs_review')
    return result
