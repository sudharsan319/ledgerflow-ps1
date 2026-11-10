"""Resolve only stated product taxes; never invent a CGST/SGST counterpart."""
from decimal import Decimal, ROUND_HALF_UP
import re

from .models import decimal_value, InvalidNumber

LINE_AGGREGATE_FIELDS = ('tax', 'tax_rate', 'tax_evidence', 'tax_rate_evidence')


def component_tax_input(item):
    """Component rows are the product tax input; combined values are outputs.

    Return a copy only when needed so historical extracted data stays intact.
    Incomplete components stay authoritative and must fail their own checks;
    they must never fall back to a redundant combined value.
    """
    if item.get('tax_components'):
        return {key: value for key, value in item.items() if key not in LINE_AGGREGATE_FIELDS}
    return item


def number(value):
    try:
        return decimal_value(value)
    except InvalidNumber:
        return None


def resolve_components(components, base, unit, prefix, check, compare, derive, resolved=None, product=False):
    amounts = []
    for index, component in enumerate(components):
        code = f'{prefix}_{index}'
        label = component.get('label') or f'Tax {index + 1}'
        amount, rate = number(component.get('amount')), number(component.get('rate'))
        printed_base = number(component.get('taxable_amount'))
        taxable = base if product else printed_base if printed_base is not None else base
        detail = dict(index=index, label=label, amount=None, calculated_amount=None, rate=None,
                      printed_taxable_amount=None if printed_base is None else format(printed_base, 'f'),
                      taxable_amount=None if taxable is None else format(taxable, 'f'))
        if resolved is not None:
            resolved.append(detail)
        if rate is not None and not 0 <= rate <= 100:
            check(code, label, 'fail', 'Tax rate must be between 0 and 100%.')
            continue
        if amount is not None and amount < 0:
            check(code, label, 'fail', 'Tax amount must not be negative.')
            continue
        if product and base is not None and printed_base is not None:
            compare(code + '_base', label + ' taxable base', base, printed_base,
                    'Quantity × unit price after discount, excluding tax')
        if rate is not None and taxable is not None and taxable >= 0:
            expected = (taxable * rate / 100).quantize(unit, rounding=ROUND_HALF_UP)
            detail['calculated_amount'] = format(expected, 'f')
            formula = f'{taxable} × {rate}%'
            if amount is None:
                amount = expected
                derive(code, label + ' (calculated)', amount, formula)
            else:
                compare(code, label, expected, amount, formula)
        if amount is None:
            check(code, label, 'warn', 'Enter the stated tax amount, or its rate and a known taxable base. Unreadable tax is not zero.')
        else:
            amounts.append(amount)
            detail['amount'] = format(amount, 'f')
            if detail['calculated_amount'] is None and rate is None:
                detail['calculated_amount'] = format(amount, 'f')
        if rate is None and amount is not None and taxable is not None and taxable > 0:
            rate = (amount * 100 / taxable).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
        detail['rate'] = None if rate is None else format(rate.normalize(), 'f')
    return sum(amounts, Decimal(0)) if len(amounts) == len(components) else None


def tax_summary(resolved, tax, rate, base=None):
    """Display combined taxes without inserting synthetic components into the source."""
    def summed(rows, key):
        values = [number(row.get(key)) for row in rows]
        return None if any(value is None for value in values) else sum(values, Decimal(0))
    def formatted(value):
        return None if value is None else format(value.normalize(), 'f')
    if resolved:
        tax, rate = summed(resolved, 'amount'), summed(resolved, 'rate')
    elif rate is None and tax is not None and base is not None and base > 0:
        rate = (tax * 100 / base).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
    if tax == 0 and rate is None:
        rate = Decimal(0)
    result = dict(tax=formatted(tax), tax_rate=formatted(rate))
    for name, aliases in [('cgst', ('cgst', 'centraltax', 'centralgst')), ('sgst', ('sgst', 'statetax', 'stategst'))]:
        rows = [row for row in resolved if any(re.sub(r'[^a-z]', '', row['label'].lower()).startswith(alias) for alias in aliases)]
        result[name] = dict(rate=formatted(summed(rows, 'rate')), amount=formatted(summed(rows, 'amount')))
    return result


def product_base(item, amount, line_mode, price_mode, price_base):
    """Find a taxable base without using the invoice total as a target."""
    if price_mode == 'exclusive' and price_base is not None:
        return price_base
    if price_mode == 'inclusive' and price_base is not None:
        amount, line_mode = price_base, 'inclusive'
    if line_mode == 'exclusive' and amount is not None:
        return amount
    if line_mode != 'inclusive' or amount is None:
        return None
    # Rates calculate the base independently of potentially incorrect printed
    # tax amounts. Printed tax/base values remain comparison targets.
    components = item.get('tax_components') or []
    rate = number(item.get('tax_rate'))
    if components:
        rates = [number(c.get('rate')) for c in components]
        if all(value is not None and 0 <= value <= 100 for value in rates):
            rate = sum(rates, Decimal(0))
    if rate is not None and 0 <= rate <= 100:
        return amount / (1 + rate / 100)
    tax = number(item.get('tax'))
    if item.get('tax_status') == 'not_stated' and tax == 0:
        tax = None
    if tax is not None:
        return amount - tax
    amounts = [number(c.get('amount')) for c in components]
    if components and all(value is not None for value in amounts):
        return amount - sum(amounts, Decimal(0))
    rate = number(item.get('tax_rate'))
    if rate is None and components and all(c.get('taxable_amount') in (None, '') for c in components):
        rates = [number(c.get('rate')) for c in components]
        if all(value is not None and 0 <= value <= 100 for value in rates):
            rate = sum(rates, Decimal(0))
    if rate is not None and 0 <= rate <= 100:
        return amount / (1 + rate / 100)
    return None


def tax_amount_basis(item, amount, price_base, line_mode, price_mode, unit):
    """Resolve only a unique interpretation anchored by printed tax AND rate.

    This never uses the invoice grand total or an extracted taxable-base guess.
    The review draft retains a resolved basis before subsequent edits.
    """
    if item.get('tax_status') == 'unclear' or price_base is None or price_base <= 0:
        return line_mode, price_mode, []
    components = item.get('tax_components') or []
    rows = components or [{'rate': item.get('tax_rate'), 'amount': item.get('tax')}]
    rates, amounts = [number(r.get('rate')) for r in rows], [number(r.get('amount')) for r in rows]
    if any(r is None or r <= 0 or r > 100 for r in rates) or any(a is None or a <= 0 for a in amounts):
        return line_mode, price_mode, []
    rate, tax = sum(rates, Decimal(0)), sum(amounts, Decimal(0))
    if rate > 100:
        return line_mode, price_mode, []
    candidates = []
    for price_kind in ('exclusive', 'inclusive'):
        if price_mode not in ('unknown', price_kind):
            continue
        net = price_base if price_kind == 'exclusive' else price_base / (1 + rate / 100)
        if any(abs((net*r/100).quantize(unit, rounding=ROUND_HALF_UP)-a) > unit for r,a in zip(rates,amounts)):
            continue
        for amount_kind in ('exclusive', 'inclusive'):
            if line_mode not in ('unknown', amount_kind) or amount is None:
                continue
            expected = net if amount_kind == 'exclusive' else net + tax
            if abs(expected-amount) <= unit:
                candidates.append((amount_kind,price_kind))
    if len(candidates) != 1:
        return line_mode, price_mode, []
    return *candidates[0], ['Tax basis resolved from quantity, price, discount, and independently printed tax rates/amounts and product total.']


def anchored_tax_basis(item, amount, price_base, line_mode, price_mode, unit):
    """Resolve unknown bases from an independently stated per-line taxable value.

    The grand total is never an input. Differing bases, missing taxes, ambiguous
    zero-tax comparisons and inconsistent component rates cannot resolve a mode.
    Existing explicit interpretations are never overwritten.
    """
    components = item.get('tax_components') or []
    if not components or item.get('tax_status') == 'unclear':
        return line_mode, price_mode, []
    bases = [number(component.get('taxable_amount')) for component in components]
    if any(base is None or base < 0 for base in bases) or len(set(bases)) != 1:
        return line_mode, price_mode, []
    base = bases[0]
    taxes = []
    for component in components:
        tax, rate = number(component.get('amount')), number(component.get('rate'))
        if rate is not None and not 0 <= rate <= 100 or tax is not None and tax < 0:
            return line_mode, price_mode, []
        if rate is not None:
            expected = (base * rate / 100).quantize(unit, rounding=ROUND_HALF_UP)
            if tax is not None and abs(tax - expected) > unit:
                return line_mode, price_mode, []
            tax = expected if tax is None else tax
        if tax is None:
            return line_mode, price_mode, []
        taxes.append(tax)
    gross = base + sum(taxes, Decimal(0))
    notes = []
    def resolve(value, mode, label):
        if mode != 'unknown' or value is None:
            return mode
        net_match, gross_match = abs(value - base) <= unit, abs(value - gross) <= unit
        if net_match == gross_match:
            return mode
        resolved = 'exclusive' if net_match else 'inclusive'
        notes.append(f'{label}: tax {"excluded" if net_match else "included"}, resolved from stated taxable value {base} and component tax {gross - base}.')
        return resolved
    return resolve(amount, line_mode, 'Line amount'), resolve(price_base, price_mode, 'Quantity × unit price after discount'), notes
