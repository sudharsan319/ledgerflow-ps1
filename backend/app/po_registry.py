"""Server-owned PO validation provenance, separate from vendor approval."""
import hashlib
import json
from .po_policy import normalize_po, validate_po


def po_digest(po):
    normalized = normalize_po({k: v for k, v in po.items() if not k.startswith('_')})
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def stamp_po(po, reviewer, at, upload_id, comment):
    po['_validation'] = {'version': '1', 'reviewer': reviewer, 'at': at, 'upload_id': upload_id,
                         'comment': comment, 'authorized': True, 'digest': po_digest(po)}
    return po


def fixture_po(po, vendor_name):
    """Complete explicitly fictional starter/demo POs, never migrate real records."""
    from .store import now
    data = {**po, 'vendor_name': vendor_name, 'po_date': '2026-01-01', 'approval_status': 'approved',
            'subtotal': po['total'], 'tax': '0', 'line_items': po.get('line_items') or [
                {'description': po.get('description') or 'Fictional purchasing budget', 'quantity': '1',
                 'unit_price': po['total'], 'amount': po['total']}]}
    data = normalize_po(data)
    return stamp_po(data, 'Fictional demo fixture', now(), 'seed-fixture', 'Preauthorized fictional test procurement; not real purchasing authority.')


def decorate_procurement(vendors, pos):
    by_id = {v['id']: v for v in vendors}
    for vendor in vendors:
        vendor['approval_status'] = 'approved' if vendor.get('approved') is True else ('pending' if vendor.get('approval_status') == 'pending' else 'rejected')
    for po in pos:
        vendor = by_id.get(po.get('vendor_id'))
        # A vendor lookup assists legacy review without inventing missing PO facts.
        po.setdefault('vendor_name', vendor['name'] if vendor else None)
        validation = validate_po(normalize_po(po))
        proof = po.get('_validation') or {}
        trusted = (proof.get('authorized') is True and proof.get('version') == '1'
                   and proof.get('digest') == po_digest(po) and validation['valid'])
        po['validation_status'] = 'validated' if trusted else 'unverified'
        po['validation_issues'] = [c['message'] for c in validation['checks'] if c['status'] != 'pass']
        if not trusted:
            po['validation_issues'].append('Review this PO and confirm its source and purchasing authorization before invoice approval.')
            po['eligibility_status'] = 'unverified'
        elif not validation['eligible']:
            po['eligibility_status'] = 'ineligible'
        elif not vendor or vendor.get('approved') is not True:
            po['eligibility_status'] = 'pending_vendor'
        else:
            po['eligibility_status'] = 'eligible'
        if trusted:
            po['validated_at'], po['reviewer'] = proof['at'], proof['reviewer']
    return vendors, pos
