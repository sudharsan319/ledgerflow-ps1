"""Versioned Decimal arithmetic for explicit invoice structures; no model decisions."""
from decimal import Decimal, ROUND_HALF_UP, localcontext
from .models import InvalidNumber, decimal_value, decimal_string
from .product_taxes import resolve_components, product_base, tax_summary, anchored_tax_basis, component_tax_input, tax_amount_basis

RULES_VERSION = '3.4'


def number(value):
    try:
        return decimal_value(value)
    except InvalidNumber:
        return None


def currency_unit(currency):
    if currency in {'JPY', 'KRW', 'VND', 'CLP', 'ISK', 'PYG', 'UGX'}:
        return Decimal('1')
    return Decimal('0.001') if currency in {'BHD', 'KWD', 'OMR', 'JOD', 'TND'} else Decimal('0.01')


def calculate_rules(invoice):
    with localcontext() as context:
        context.prec = 50
        return _calculate(invoice)


def _calculate(invoice):
    checks, steps, products, defaults = [], [], [], []
    unit = currency_unit(invoice.get('currency'))
    total, subtotal, tax = (number(invoice.get(key)) for key in ('total', 'subtotal', 'tax'))
    lines = [component_tax_input(line) for line in invoice.get('line_items') or []]
    # A zero placeholder for an absent header tax is not an aggregate tax total.
    if invoice.get('tax_status') == 'not_stated' and tax == 0:
        tax = None
    document_tax_stated = tax is not None or bool(invoice.get('tax_components')) or invoice.get('tax_status') in ('stated', 'unclear')
    def has_tax(item):
        return bool(item.get('tax_components')) or item.get('tax_status') in ('stated', 'unclear') or any(number(item.get(k)) not in (None, Decimal(0)) for k in ('tax', 'tax_rate'))
    zero_tax_document = not has_tax(invoice) and not any(has_tax(line) for line in lines)
    mode = invoice.get('tax_mode', 'exclusive') or 'unknown'
    if mode == 'unknown' and (zero_tax_document or subtotal is None and lines and all(line.get('amount_tax_mode') in ('exclusive', 'inclusive') for line in lines)):
        mode = 'exclusive'  # Choose a calculation basis, never overwrite source facts.
    scope = invoice.get('subtotal_scope', 'before_adjustments') or 'unknown'
    if scope == 'unknown' and not invoice.get('adjustments'):
        scope = 'before_adjustments'
    def check(code, label, status, message, blocking=True):
        checks.append(dict(code=code, label=label, status=status, message=message, blocking=blocking and status != 'pass'))
    def compare(code, label, calculated, printed, formula, tolerance=unit):
        difference = calculated - printed
        ok = abs(difference) <= tolerance
        steps.append(dict(id=code, label=label, formula=formula, calculated=decimal_string(calculated),
                          printed=decimal_string(printed), difference=decimal_string(difference), tolerance=decimal_string(tolerance), status='pass' if ok else 'fail'))
        check(code, label, 'pass' if ok else 'fail', f'{formula} = {calculated}; printed {printed}; difference {difference}; allowed rounding {tolerance}.')
        return ok
    def derive(code, label, value, formula):
        steps.append(dict(id=code, label=label, formula=formula, calculated=decimal_string(value), printed=None, difference=None, tolerance=str(unit), status='pass'))
    if invoice.get('tax_status') == 'unclear':
        check('tax_status', 'Invoice tax', 'warn', 'The invoice tax is unreadable or ambiguous; verify it against the source.')
    if invoice.get('normalization_warnings'):
        check('calculation_input', 'Calculation input', 'fail', '; '.join(invoice['normalization_warnings']))
    if mode not in ('exclusive', 'inclusive'):
        check('tax_mode', 'Tax inclusion', 'warn', 'Confirm whether subtotal and line amounts include or exclude tax.')
    if scope not in ('before_adjustments', 'after_adjustments'):
        check('subtotal_scope', 'Subtotal meaning', 'warn', 'Confirm whether the subtotal already includes document discounts and charges.')
    if total is None or total <= 0:
        check('calculation_total', 'Printed total', 'fail', 'A positive printed invoice total is required.')
    if any(v is not None and v < 0 for v in (subtotal, tax)):
        check('nonnegative_components', 'Subtotal and tax', 'fail', 'Subtotal and tax must not be negative.')

    components = invoice.get('tax_components') or []
    document_base = None
    # A rate printed below a single product applies to that product's base;
    # never apply mixed summary rates to every product on a multi-item invoice.
    document_product_base = len(lines) == 1 and not any(a.get('kind') != 'rounding' for a in invoice.get('adjustments', []))
    if components and document_product_base:
        item = lines[0]
        item_amount = number(item.get('amount'))
        item_mode = item.get('amount_tax_mode') or mode
        price_mode = item.get('unit_price_tax_mode') or item_mode
        quantity, price = number(item.get('quantity')), number(item.get('unit_price'))
        price_base = quantity * price if quantity is not None and quantity > 0 and price is not None and price >= 0 else None
        discount, percent = number(item.get('discount_amount')), number(item.get('discount_percent'))
        if price_base is not None:
            if discount is not None:
                price_base -= discount
            elif percent is not None and 0 <= percent <= 100:
                price_base -= price_base * percent / 100
        document_base = product_base({**item, 'tax': tax, 'tax_components': components}, item_amount, item_mode, price_mode, price_base)
    resolved_document_components = []
    component_sum = resolve_components(components, document_base, unit, 'tax_rate', check, compare, derive, resolved_document_components,
                                       product=document_product_base) if components else None
    if component_sum is not None:
        if tax is not None:
            compare('tax_components', 'Tax component total', component_sum, tax, ' + '.join(str(c.get('amount') or 'calculated tax') for c in components))
        else:
            derive('tax_components', 'Tax component total (calculated)', component_sum, 'Sum of stated document tax components')
    effective_tax = tax if tax is not None else component_sum

    adjustment_total = Decimal(0)
    adjustments_complete = True
    for index, adjustment in enumerate(invoice.get('adjustments') or []):
        kind, included, amount = adjustment.get('kind'), adjustment.get('included_in_subtotal'), number(adjustment.get('amount'))
        label = adjustment.get('label') or f'Adjustment {index + 1}'
        percent, base_amount = number(adjustment.get('percent')), number(adjustment.get('base_amount'))
        if percent is not None:
            if kind not in ('charge', 'discount') or not 0 <= percent <= 100 or base_amount is None:
                check(f'adjustment_rate_{index}', label, 'warn', 'A percentage adjustment needs a charge/discount role, explicit nonnegative base, and rate between 0 and 100%.')
                adjustments_complete = False
                continue
            derived = base_amount * percent / 100
            if amount is not None:
                compare(f'adjustment_rate_{index}', label + ' percentage', derived, amount, f'{base_amount} × {percent}%')
            else:
                amount = derived
                steps.append(dict(id=f'adjustment_rate_{index}', label=label + ' (derived)', formula=f'{base_amount} × {percent}%', calculated=str(derived), printed=None, difference=None, tolerance=str(unit), status='pass'))
        if kind not in ('charge', 'discount', 'rounding') or amount is None or included is None or (kind != 'rounding' and amount < 0):
            check(f'adjustment_{index}', label, 'warn', 'Confirm the adjustment amount, its charge/discount/rounding role, and whether it is already included in subtotal.')
            adjustments_complete = False
            continue
        signed = -amount if kind == 'discount' else amount
        if included:
            steps.append(dict(id=f'adjustment_{index}', label=label, formula=f'{amount} already included in subtotal; not added again', calculated='0', printed=None, difference=None, tolerance=str(unit), status='pass'))
        else:
            adjustment_total += signed
            steps.append(dict(id=f'adjustment_{index}', label=label, formula=f'{kind}: {signed}', calculated=str(signed), printed=None, difference=None, tolerance=str(unit), status='pass'))

    line_amounts, converted_amounts, line_taxes = [], [], []
    line_issue = False
    for index, item in enumerate(lines):
        code = f'line_{index + 1}'
        label = f'Line {index + 1}'
        quantity, price, amount = (number(item.get(k)) for k in ('quantity', 'unit_price', 'amount'))
        line_mode = item.get('amount_tax_mode') or mode
        price_mode = item.get('unit_price_tax_mode') or line_mode
        if amount is None and price_mode in ('exclusive', 'inclusive'):
            line_mode = price_mode
        if zero_tax_document:
            line_mode = 'exclusive' if line_mode == 'unknown' else line_mode
            price_mode = 'exclusive' if price_mode == 'unknown' else price_mode
        line_tax, rate = number(item.get('tax')), number(item.get('tax_rate'))
        if item.get('tax_status') == 'not_stated' and line_tax == 0:
            line_tax = None
        discount, percent = number(item.get('discount_amount')), number(item.get('discount_percent'))
        price_base = quantity * price if quantity is not None and quantity > 0 and price is not None and price >= 0 else None
        if price_base is not None:
            if discount is not None:
                price_base -= discount
            elif percent is not None and 0 <= percent <= 100:
                price_base -= price_base * percent / 100
        line_mode, price_mode, basis_notes = anchored_tax_basis(item, amount, price_base, line_mode, price_mode, unit)
        if line_mode == 'unknown' or price_mode == 'unknown':
            line_mode, price_mode, extra_notes = tax_amount_basis(item, amount, price_base, line_mode, price_mode, unit)
            basis_notes += extra_notes
        if amount is None and price_base is not None and price_base >= 0 and line_mode == price_mode and line_mode in ('exclusive', 'inclusive'):
            amount = price_base.quantize(unit, rounding=ROUND_HALF_UP)
            derive(code + '_amount', label + ' amount (calculated)', amount, 'Quantity × unit price − stated line discount')
        if item.get('tax_status') == 'unclear':
            check(code + '_tax', label + ' tax', 'warn', 'This product tax is unreadable or ambiguous; verify it instead of entering zero.')
        components_for_line = item.get('tax_components') or []
        resolved_components = []
        base = product_base(item, amount, line_mode, price_mode, price_base)
        if components_for_line:
            component_tax = resolve_components(components_for_line, base, unit, code + '_component', check, compare, derive, resolved_components, product=True)
            if component_tax is not None:
                derive(code + '_tax_components', label + ' tax (calculated)', component_tax, 'Sum of this product’s stated taxes; no missing counterpart added')
                line_tax = component_tax
        tax_absent = not components_for_line and rate is None and (line_tax is None or item.get('tax_status') == 'not_stated' and line_tax == 0) and item.get('tax_status') not in ('stated', 'unclear')
        tax_from_document = False
        if tax_absent and len(lines) == 1 and effective_tax is not None and not any(a.get('kind') != 'rounding' for a in invoice.get('adjustments', [])):
            line_tax = effective_tax
            tax_absent = False
            tax_from_document = True
            base = product_base({**item, 'tax': effective_tax, 'tax_components': components}, amount, line_mode, price_mode, price_base)
            derive(code + '_document_tax', label + ' tax from invoice', line_tax, 'The single product uses the separately stated invoice tax once')
        if tax_absent:
            line_tax = Decimal(0)
            defaults.append(f'Line {index + 1}: no product tax stated; used zero.' + (' Document tax is applied separately.' if document_tax_stated else ''))
        elif line_tax is None and rate is None and not components_for_line and item.get('tax_status') == 'stated':
            check(code + '_tax', label + ' tax', 'warn', 'Tax is stated but its amount or rate is missing.')
        resolved_tax = line_tax
        tax_price, tax_price_mode = (price_base, price_mode) if price_base is not None and price_mode in ('exclusive', 'inclusive') else (amount, line_mode)
        if resolved_tax is None and rate is not None and 0 <= rate <= 100 and tax_price is not None and tax_price_mode in ('exclusive', 'inclusive'):
            resolved_tax = (tax_price * rate / 100 if tax_price_mode == 'exclusive' else tax_price - tax_price / (1 + rate / 100)).quantize(unit, rounding=ROUND_HALF_UP)
            derive(code + '_tax_derived', label + ' tax (calculated)', resolved_tax, f'Tax at {rate}% on the {tax_price_mode} product base {tax_price}')
        calculated_tax = resolved_tax
        if tax_from_document and components:
            values = [number(c.get('calculated_amount')) for c in resolved_document_components]
            calculated_tax = sum(values, Decimal(0)) if len(values) == len(components) and all(v is not None for v in values) else None
        elif components_for_line:
            values = [number(c.get('calculated_amount')) for c in resolved_components]
            calculated_tax = sum(values, Decimal(0)) if len(values) == len(components_for_line) and all(v is not None for v in values) else None
        elif rate is not None and 0 <= rate <= 100 and base is not None:
            calculated_tax = (base * rate / 100).quantize(unit, rounding=ROUND_HALF_UP)
        computed_net = base if price_base is not None and price_mode in ('exclusive', 'inclusive') else None
        computed_gross = computed_net + calculated_tax if computed_net is not None and calculated_tax is not None else None
        product_total = (amount + resolved_tax if line_mode == 'exclusive' else amount) if amount is not None and line_mode in ('exclusive', 'inclusive') and resolved_tax is not None else None
        products.append(dict(index=index, description=item.get('description') or label, quantity=item.get('quantity'), unit_price=item.get('unit_price'),
            tax=None if resolved_tax is None else decimal_string(resolved_tax), total=None if product_total is None else decimal_string(product_total),
            tax_components=components_for_line, resolved_tax_components=resolved_components,
            tax_summary=tax_summary(resolved_document_components if tax_from_document and components else resolved_components, resolved_tax, rate, base),
            tax_from_document=tax_from_document,
            amount_tax_mode=line_mode, unit_price_tax_mode=price_mode,
            basis_notes=basis_notes,
            calculated_net=None if computed_net is None else decimal_string(computed_net.quantize(unit, rounding=ROUND_HALF_UP)),
            calculated_net_unit_price=None if computed_net is None or quantity is None or quantity <= 0 else decimal_string(computed_net / quantity),
            calculated_tax=None if calculated_tax is None else decimal_string(calculated_tax),
            calculated_gross=None if computed_gross is None else decimal_string(computed_gross.quantize(unit, rounding=ROUND_HALF_UP)),
            printed_tax=None if line_tax is None or tax_absent else decimal_string(line_tax),
            tax_defaulted=tax_absent, document_tax_separate=tax_absent and document_tax_stated))
        if amount is not None and line_mode in ('exclusive', 'inclusive'):
            if resolved_tax is not None:
                if not (tax_absent and document_tax_stated):
                    line_taxes.append(resolved_tax)
        if amount is not None:
            line_amounts.append(amount)
            if line_mode == mode:
                converted_amounts.append(amount)
            elif line_mode in ('exclusive', 'inclusive') and mode in ('exclusive', 'inclusive'):
                if line_tax is not None:
                    converted_amounts.append(amount + line_tax if mode == 'inclusive' else amount - line_tax)
                elif rate is not None and 0 <= rate <= 100:
                    converted_amounts.append(amount * (1 + rate / 100) if mode == 'inclusive' else amount / (1 + rate / 100))
                else:
                    check(code + '_basis', label + ' tax basis', 'warn', 'Line and subtotal tax bases differ; explicit tax or rate is required to reconcile.')
        if any(v is not None and v < 0 for v in (quantity, price, amount, line_tax, discount)) or quantity == 0:
            check(code, label, 'fail', 'Line quantity must be positive and amounts cannot be negative.')
            line_issue = True
            continue
        if any(v is not None and not 0 <= v <= 100 for v in (rate, percent)):
            check(code, label, 'fail', 'Discount and tax rates must be between 0 and 100%.')
            line_issue = True
            continue
        if amount is None:
            check(code, label, 'warn', 'Line amount is missing; verify before approval.')
            line_issue = True
            continue
        if price_base is not None and price_base < 0:
            check(code + '_discount', label + ' discount', 'fail', 'The discount exceeds quantity × unit price. Correct the quantity, price or discount.')
            line_issue = True
            continue
        if line_mode == 'unknown' or price_mode == 'unknown':
            check(code, label, 'warn', 'Confirm whether line amounts and rates include tax.')
            line_issue = True
            continue
        if quantity is not None and price is not None:
            expected = quantity * price
            formula = f'{quantity} × {price}'
            if percent is not None:
                calculated_discount = expected * percent / 100
                if discount is not None:
                    compare(code + '_discount', label + ' discount', calculated_discount, discount, f'{expected} × {percent}%')
                else:
                    discount = calculated_discount
            if discount is not None:
                expected -= discount
                formula += f' − {discount} discount'
            if expected < 0:
                check(code, label, 'fail', 'The line discount exceeds its base amount.')
                line_issue = True
                continue
            tax_amount = calculated_tax
            if rate is not None and not components_for_line:
                rate_tax = expected * rate / 100 if price_mode == 'exclusive' else expected - expected / (1 + rate / 100)
                if line_tax is not None:
                    compare(code + '_tax', label + ' tax', rate_tax, line_tax, f'Tax on {expected} after discount at {rate}% ({price_mode})')
                tax_amount = rate_tax.quantize(unit, rounding=ROUND_HALF_UP)
            if line_mode != price_mode:
                if tax_amount is None:
                    check(code, label, 'warn', 'Unit price and line amount use different tax bases; supply the line tax or rate.')
                    line_issue = True
                    continue
                expected += tax_amount if line_mode == 'inclusive' else -tax_amount
                formula += f" {'+' if line_mode == 'inclusive' else '−'} {tax_amount} tax"
            tolerance = unit
            precision = item.get('unit_price_precision')
            if isinstance(precision, int) and not isinstance(precision, bool) and 0 <= precision <= 6:
                if price != price.quantize(Decimal(10) ** -precision):
                    check(code + '_precision', label + ' printed precision', 'fail', 'Declared unit-price precision conflicts with the extracted rate.')
                    line_issue = True
                    continue
                tolerance = max(unit, abs(quantity) * Decimal(10) ** -precision / 2)
                if rate is not None and price_mode == 'exclusive' and line_mode == 'inclusive':
                    tolerance *= 1 + rate / 100
                formula += f' (unit rate printed to {precision} decimals)'
            if not compare(code, label, expected, amount, formula, tolerance):
                line_issue = True
        else:
            check(code, label, 'warn', 'Quantity or unit price is missing; only the stated line amount can be checked.', False)
    has_document_adjustments = any(a.get('kind') != 'rounding' for a in invoice.get('adjustments', []))
    if effective_tax is None and not components and invoice.get('tax_status') not in ('stated', 'unclear'):
        if lines and len(line_taxes) == len(lines):
            if has_document_adjustments and not zero_tax_document:
                check('line_tax_sum', 'Product taxes', 'warn', 'A document discount or charge may affect tax. Supply the final tax summary or verify its explicit taxable base.')
            else:
                effective_tax = sum(line_taxes, Decimal(0))
                derive('line_tax_total', 'Invoice tax from products', effective_tax, 'Sum of product taxes, using zero only where tax is not stated')
        elif not lines and zero_tax_document:
            effective_tax = Decimal(0)
            defaults.append('No tax stated; used zero.')
    if subtotal is None and lines and len(converted_amounts) == len(lines) and mode in ('exclusive', 'inclusive'):
        derived_subtotal = sum(converted_amounts, Decimal(0))
        included = [a for a in invoice.get('adjustments', []) if a.get('included_in_subtotal') is True]
        if scope == 'before_adjustments' and not included or scope == 'after_adjustments' and not included:
            subtotal = derived_subtotal
        elif scope == 'after_adjustments' and adjustments_complete:
            delta = Decimal(0)
            for adjustment in included:
                value = number(adjustment.get('amount'))
                if value is None and number(adjustment.get('percent')) is not None and number(adjustment.get('base_amount')) is not None:
                    value = number(adjustment['percent']) * number(adjustment['base_amount']) / 100
                if value is None:
                    break
                delta += -value if adjustment.get('kind') == 'discount' else value
            else:
                subtotal = derived_subtotal + delta
        if subtotal is not None:
            derive('derived_subtotal', 'Subtotal from products', subtotal, 'Sum of product amounts on the subtotal basis, with any explicitly included adjustments')
    if lines and len(converted_amounts) == len(lines) and subtotal is not None:
        if scope == 'before_adjustments':
            compare('line_sum', 'Line-item subtotal', sum(converted_amounts, Decimal(0)), subtotal, 'Sum of line amounts on the subtotal tax basis')
        elif scope == 'after_adjustments':
            included_adjustments = [a for a in invoice.get('adjustments', []) if a.get('included_in_subtotal') is True]
            def included_value(a):
                amount = number(a.get('amount'))
                if amount is None and number(a.get('base_amount')) is not None and number(a.get('percent')) is not None:
                    amount = number(a['base_amount']) * number(a['percent']) / 100
                return amount
            if adjustments_complete and all(included_value(a) is not None and a.get('kind') in ('charge', 'discount', 'rounding') for a in included_adjustments):
                delta = sum((-1 if a['kind'] == 'discount' else 1) * included_value(a) for a in included_adjustments)
                compare('line_sum', 'Adjusted line subtotal', sum(converted_amounts, Decimal(0)) + delta, subtotal, f'Sum of line amounts + included adjustments ({delta})')
            else:
                check('line_sum', 'Adjusted line subtotal', 'warn', 'Subtotal includes adjustments, but their amounts or treatment are not explicit.')
    elif lines and subtotal is not None and len(converted_amounts) != len(lines):
        check('line_sum', 'Line-item subtotal', 'warn', 'Some line amounts or their tax basis are unavailable.')

    if lines and len(line_taxes) == len(lines) and effective_tax is not None and not any(a.get('kind') != 'rounding' for a in invoice.get('adjustments', [])):
        compare('line_tax_sum', 'Line tax total', sum(line_taxes, Decimal(0)), effective_tax, 'Sum of line taxes (derived rates rounded per line where necessary)')

    calculated = None
    if subtotal is not None and total is not None and adjustments_complete and mode in ('exclusive', 'inclusive'):
        if mode == 'inclusive':
            calculated = subtotal + adjustment_total
            compare('arithmetic', 'Tax-inclusive total + adjustments', calculated, total, f'{subtotal} (tax included) + ({adjustment_total}) adjustments')
        elif effective_tax is not None:
            calculated = subtotal + effective_tax + adjustment_total
            compare('arithmetic', 'Subtotal + tax + adjustments = total', calculated, total, f'{subtotal} + {effective_tax} tax + ({adjustment_total}) adjustments')
        else:
            check('arithmetic', 'Subtotal + tax = total', 'warn', 'Tax cannot be resolved from the available product and document fields. Verify any unreadable or incomplete tax values.')
    elif subtotal is None:
        check('arithmetic', 'Total reconciliation', 'warn', 'Subtotal is missing; review the source before relying on the printed total.', document_tax_stated or bool(lines))
    else:
        check('arithmetic', 'Total reconciliation', 'warn', 'Resolve unknown tax or adjustment treatment before calculating the total.')
    taxable = number(invoice.get('taxable_subtotal'))
    if taxable is not None and mode == 'inclusive' and subtotal is not None and effective_tax is not None:
        compare('taxable_subtotal', 'Taxable amount + tax', taxable + effective_tax, subtotal, f'{taxable} taxable + {effective_tax} tax')
    # Independently rebuild totals from quantities/prices, not printed product
    # amounts or a printed subtotal. Existing reconciliation checks remain.
    computed_totals = None
    if products and all(p['calculated_net'] is not None and p['calculated_tax'] is not None for p in products) and adjustments_complete:
        net_sum = sum(number(p['calculated_net']) for p in products)
        tax_sum = sum(number(p['calculated_tax']) for p in products)
        if all(p['document_tax_separate'] for p in products) and effective_tax is not None:
            tax_sum = effective_tax
        delta = adjustment_total
        for a in invoice.get('adjustments', []):
            if a.get('included_in_subtotal') is True:
                value = number(a.get('amount'))
                if value is None and number(a.get('percent')) is not None and number(a.get('base_amount')) is not None:
                    value = number(a['percent']) * number(a['base_amount']) / 100
                if value is not None:
                    delta += -value if a.get('kind') == 'discount' else value
        recomputed = net_sum + tax_sum + delta
        rounding = max(unit, sum((number(s['tolerance']) for s in steps if s['id'] in {f'line_{i+1}' for i in range(len(products))}), Decimal(0)))
        computed_totals = dict(net=decimal_string(net_sum), tax=decimal_string(tax_sum), adjustments=decimal_string(delta), total=decimal_string(recomputed), tolerance=decimal_string(rounding))
        if subtotal is not None and mode in ('exclusive', 'inclusive') and scope in ('before_adjustments', 'after_adjustments'):
            expected_subtotal = net_sum + (tax_sum if mode == 'inclusive' else Decimal(0))
            if scope == 'after_adjustments':
                expected_subtotal += delta - adjustment_total
            compare('recomputed_subtotal', 'Recomputed product subtotal', expected_subtotal, subtotal,
                    'Calculated products on the stated subtotal tax basis, including stated included adjustments', rounding)
        if total is not None:
            compare('recomputed_total', 'Recomputed products + tax + adjustments', recomputed, total,
                    f'{net_sum} calculated net + {tax_sum} tax + ({delta}) adjustments', rounding)
        if effective_tax is not None and not has_document_adjustments:
            compare('recomputed_tax', 'Recomputed tax total', tax_sum, effective_tax, 'Sum of independently calculated product taxes', rounding)
    # A product is verified only against its own printed amount and all of its
    # component/discount checks. Invoice-level agreement cannot hide a bad row.
    for product in products:
        index = product['index']
        code = f'line_{index + 1}'
        related = [c for c in checks if c['code'] == code or c['code'].startswith(code + '_')]
        if product['tax_from_document']:
            related += [c for c in checks if c['code'].startswith('tax_rate_') or c['code'] in ('tax_components', 'tax_status')]
        invalid = [warning for warning in invoice.get('normalization_warnings', [])
                   if f'line {index + 1} ' in warning or f'line_items.{index}.' in warning]
        equation = next((step for step in steps if step['id'] == code), None)
        printed = number(lines[index].get('amount'))
        failures = [c for c in related if c['status'] != 'pass']
        status = 'not_matched' if invalid or any(c['status'] == 'fail' for c in related) else 'incomplete' if failures or equation is None or printed is None else 'matched'
        product['verification'] = dict(status=status, calculated_amount=equation['calculated'] if equation else None,
            printed_amount=None if printed is None else decimal_string(printed), difference=equation['difference'] if equation and printed is not None else None,
            tolerance=equation['tolerance'] if equation else str(unit), formula=equation['formula'] if equation else None,
            messages=[c['message'] for c in failures] + invalid + (['Printed product amount is missing.'] if printed is None else []))
    status = 'mismatch' if any(c['status'] == 'fail' for c in checks) else 'needs_review' if any(c['status'] == 'warn' for c in checks) else 'reconciled'
    return dict(engine='rules', version=RULES_VERSION, status=status, tax_mode=mode, subtotal_scope=scope,
                printed_total=None if total is None else str(total), calculated_total=None if calculated is None else str(calculated),
                difference=None if total is None or calculated is None else str(calculated - total), currency=invoice.get('currency'),
                steps=steps, checks=checks, products=products, computed_totals=computed_totals, defaults=defaults, effective_subtotal=None if subtotal is None else decimal_string(subtotal), effective_tax=None if effective_tax is None else decimal_string(effective_tax), limitations=['Legacy records without tax_mode use the original tax-exclusive convention.'] if 'tax_mode' not in invoice else [])
