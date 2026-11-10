"""Field-level review context and side-effect-free correction checks."""
import hashlib
import json
import re
from copy import deepcopy

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .models import invoice_fingerprint
from .extraction import FIELDS, _local_extract, extract_invoices, field_comparisons
from .models import normalize_invoice
from .policy import evaluate_invoice
from .calculations import calculate_rules
from .product_taxes import component_tax_input


def field_changes(before, after):
    """Record changed business fields, excluding extraction/transport metadata."""
    ignored = {'extraction_method', 'extraction_source', 'extraction_confidence', 'field_evidence', 'evidence', 'warnings', 'normalization_warnings', 'notes'}
    changes = []
    def walk(left, right, path):
        if left == right:
            return
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                if not path and key in ignored:
                    continue
                walk(left.get(key), right.get(key), f'{path}.{key}' if path else key)
        elif isinstance(left, list) and isinstance(right, list):
            for index in range(max(len(left), len(right))):
                walk(left[index] if index < len(left) else None, right[index] if index < len(right) else None, f'{path}.{index}')
        else:
            changes.append({'field': path, 'before': left, 'after': right})
    walk(before, after, '')
    return changes


def correction_invoice(raw):
    content = json.dumps(raw).encode()
    if len(content) > 100_000:
        raise HTTPException(413, 'Corrected invoice is too large.')
    try:
        extracted = extract_invoices(content, 'correction.json', {})
        if len(extracted) != 1:
            raise ValueError('Correct one invoice at a time; batches are not supported here.')
        parsed = extracted[0]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    invoice = normalize_invoice({**parsed['invoice'], 'extraction_method': 'structured', 'field_evidence': parsed['evidence']})
    return invoice


def map_field_issues(invoice, checks):
    fields, general = {}, []
    def add(paths, check):
        for path in paths:
            fields.setdefault(path, []).append(check)
    for check in checks:
        if check['status'] == 'pass':
            continue
        code = check['code']
        paths = []
        direct = {
            'invoice_date': ['invoice_date'], 'future_date': ['invoice_date'], 'due_date': ['due_date'],
            'currency_format': ['currency'], 'currency_precision': ['currency', 'total'], 'positive_total': ['total'],
            'tax_status': ['tax_status', 'tax'], 'calculation_total': ['total'], 'tax_mode': ['tax_mode'], 'subtotal_scope': ['subtotal_scope'],
            'nonnegative_components': ['subtotal', 'tax'], 'arithmetic': ['subtotal', 'tax', 'total', 'tax_mode', 'adjustments'],
            'line_sum': ['subtotal', 'subtotal_scope', 'line_items'], 'line_tax_sum': ['tax', 'line_items'],
            'tax_components': ['tax', 'tax_components'], 'taxable_subtotal': ['taxable_subtotal', 'subtotal', 'tax'],
            'vendor_match': ['vendor_name', 'vendor_id'], 'vendor_identity': ['vendor_name', 'vendor_id'],
            'vendor_approved': ['vendor_name', 'vendor_id'], 'duplicate': ['invoice_number'],
        }
        if code in direct:
            paths = direct[code]
        elif code == 'required_fields':
            paths = [key for key in ('invoice_number', 'invoice_date', 'currency', 'total') if invoice.get(key) in (None, '')]
            if not invoice.get('vendor_name') and not invoice.get('vendor_id'):
                paths += ['vendor_name', 'vendor_id']
        elif code in ('normalization', 'calculation_input'):
            for warning in invoice.get('normalization_warnings', []):
                if match := re.search(r'line_items\.\d+\.tax_components(?:\.\d+(?:\.[a-z_]+)?)?', warning):
                    paths.append(match[0])
                elif match := re.search(r'line (\d+) ([a-z_]+)', warning):
                    paths.append(f'line_items.{int(match[1]) - 1}.{match[2]}')
                elif match := re.search(r'(tax_components|adjustments)\.\d+\.[a-z_]+', warning):
                    paths.append(match[0])
                elif match := re.search(r'Invalid (adjustment kind|included_in_subtotal) at (\d+)', warning):
                    paths.append(f'adjustments.{int(match[2]) - 1}.{"kind" if match[1] == "adjustment kind" else "included_in_subtotal"}')
                elif match := re.search(r'(?:Invalid |Conflicting aliases for )([a-z_]+)', warning):
                    if match[1] in (*FIELDS, 'tax_mode', 'subtotal_scope', 'taxable_subtotal', 'tax_components', 'adjustments', 'line_items'):
                        paths.append(match[1])
                    elif check not in general:
                        general.append(check)
                elif check not in general:
                    general.append(check)
            paths = list(dict.fromkeys(paths))
        elif code == 'extraction_evidence':
            evidence = invoice.get('field_evidence') or {}
            if isinstance(evidence, dict):
                paths = [key for key in ('invoice_number', 'invoice_date', 'currency', 'total', 'vendor_name' if invoice.get('vendor_name') else 'vendor_id') if not evidence.get(key)]
        elif code.startswith('po_'):
            paths = ['po_number'] + (['total'] if code in ('po_balance', 'po_total') else [])
        elif match := re.fullmatch(r'line_(\d+)_component_(\d+)', code):
            paths = [f'line_items.{int(match[1])-1}.tax_components.{match[2]}.{key}' for key in ('amount', 'rate', 'taxable_amount')]
        elif match := re.fullmatch(r'line_(\d+)_tax_components', code):
            paths = [f'line_items.{int(match[1])-1}.tax', f'line_items.{int(match[1])-1}.tax_components']
        elif match := re.fullmatch(r'line_(\d+)(?:_(discount|tax|basis|precision))?', code):
            index, suffix = int(match[1]) - 1, match[2]
            keys = {'discount': ['discount_amount', 'discount_percent'], 'tax': ['tax', 'tax_rate', 'tax_status'],
                    'basis': ['amount_tax_mode', 'unit_price_tax_mode', 'tax'], 'precision': ['unit_price', 'unit_price_precision']}.get(suffix,
                    ['quantity', 'unit_price', 'amount', 'discount_amount', 'discount_percent', 'tax', 'tax_rate', 'amount_tax_mode', 'unit_price_tax_mode'])
            paths = [f'line_items.{index}.{key}' for key in keys]
        elif match := re.fullmatch(r'tax_rate_(\d+)', code):
            paths = [f'tax_components.{match[1]}.{key}' for key in ('amount', 'rate', 'taxable_amount')]
        elif match := re.fullmatch(r'adjustment_(rate_)?(\d+)', code):
            paths = [f'adjustments.{match[2]}.{key}' for key in ('amount', 'percent', 'base_amount')] if match[1] else [f'adjustments.{match[2]}.{key}' for key in ('amount', 'kind', 'included_in_subtotal')]
        if paths:
            add(paths, check)
        elif check not in general:
            general.append(check)
    return {'fields': fields, 'general': general}


class CorrectionPreview(BaseModel):
    invoice: dict
    expected_invoice_fingerprint: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


def review_router(store):
    router = APIRouter(tags=['Invoice field review'])
    def require_run(run_id, conn=None):
        run = store.get_run(run_id, conn)
        if not run:
            raise HTTPException(404, 'Run not found.')
        if run['status'] != 'completed' or not run.get('invoice'):
            raise HTTPException(409, 'Field review is available after invoice extraction completes.')
        return run
    @router.get('/api/runs/{run_id}/review-context')
    def context(run_id: str):
        run = require_run(run_id)
        extraction = run.get('extraction') or {}
        comparisons = extraction.get('field_comparisons', [])
        if not comparisons and extraction.get('method') == 'openrouter' and extraction.get('raw_text'):
            # Older runs did not retain the local candidates. Label the current
            # parser's reconstruction honestly; never replace saved extraction.
            local = _local_extract(extraction['raw_text'])
            comparisons = field_comparisons(local, {'invoice': extraction.get('invoice') or run['invoice'], 'evidence': extraction.get('evidence') or {}}, origin='reconstructed')
        # Supply a draft with supported semantic defaults, while retaining the
        # exact saved invoice/fingerprint. Edits then retain the source basis
        # rather than losing it when a changed amount no longer reconciles.
        review_invoice = deepcopy(run['invoice'])
        review_invoice['line_items'] = [component_tax_input(line) for line in review_invoice.get('line_items', [])]
        for product in calculate_rules(run['invoice'])['products']:
            if product.get('basis_notes'):
                line = review_invoice['line_items'][product['index']]
                for key in ('amount_tax_mode', 'unit_price_tax_mode'):
                    if line.get(key) == 'unknown' and product[key] in ('inclusive', 'exclusive'):
                        line[key] = product[key]
        return {'run_id': run_id, 'updated_at': run['updated_at'], 'invoice': run['invoice'], 'review_invoice': review_invoice, 'invoice_fingerprint': invoice_fingerprint(run['invoice']),
                'comparisons': comparisons, 'evidence': extraction.get('evidence') or run['invoice'].get('field_evidence') or {},
                'raw_text': extraction.get('raw_text') or '', 'warnings': extraction.get('warnings') or [],
                'issues': map_field_issues(run['invoice'], (run.get('result') or {}).get('checks', [])),
                'editable': run['decision'] != 'approved' and not run.get('superseded_by'),
                'read_only_reason': 'This run was superseded. Review its corrected run.' if run.get('superseded_by') else 'Approved invoices must be rejected before they can be corrected.' if run['decision'] == 'approved' else None}
    @router.post('/api/runs/{run_id}/correction-preview')
    def preview(run_id: str, body: CorrectionPreview):
        invoice = correction_invoice(body.invoice)
        with store.connection() as conn:
            run = require_run(run_id, conn)
            if run['decision'] == 'approved' or run.get('superseded_by'):
                raise HTTPException(409, 'Only an unsuperseded review/rejected run can be corrected.')
            if body.expected_invoice_fingerprint and body.expected_invoice_fingerprint != invoice_fingerprint(run['invoice']):
                raise HTTPException(409, 'The invoice changed after this review was opened. Reload before checking corrections.')
            procurement = store.procurement(conn, exclude_run=run_id)
            prior = [r for r in store.runs(conn) if r['id'] != run_id and r['status'] == 'completed']
            result = evaluate_invoice(invoice, procurement['vendors'], procurement['purchase_orders'], prior, procurement['policy'], hashlib.sha256(json.dumps(body.invoice).encode()).hexdigest())
        return {'run_id': run_id, 'invoice': invoice, 'result': result, 'issues': map_field_issues(invoice, result['checks']),
                'changes': field_changes(run['invoice'], invoice), 'preview_only': True,
                'message': 'Preview assumes the submitted fields are source-verified. Nothing has been saved or approved; all checks run again on reprocessing.'}
    @router.post('/api/runs/{run_id}/line-preview')
    def line_preview(run_id: str, body: CorrectionPreview):
        invoice = correction_invoice(body.invoice)
        # Read one current ledger snapshot for arithmetic and PO previews. No
        # model call, approval, reservation or other mutation happens here.
        with store.connection() as conn:
            run = require_run(run_id, conn)
            if body.expected_invoice_fingerprint and body.expected_invoice_fingerprint != invoice_fingerprint(run['invoice']):
                raise HTTPException(409, 'The invoice changed after this review was opened. Reload before checking corrections.')
            procurement = store.procurement(conn, exclude_run=run_id)
            prior = [r for r in store.runs(conn) if r['id'] != run_id and r['status'] == 'completed']
            result = evaluate_invoice(invoice, procurement['vendors'], procurement['purchase_orders'], prior, procurement['policy'])
        return {'run_id': run_id, 'rules': result['calculation'], 'po_reconciliation': result['po_reconciliation'],
                'po_checks': [c for c in result['checks'] if c['code'].startswith('po_')], 'preview_only': True}
    return router
