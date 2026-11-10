"""Explicit API workflow for fictional internet-corpus procurement fixtures.

Importing this module performs no I/O. Defaults only create a PO draft. Callers
must separately request audited PO confirmation and fictional vendor reviews.
"""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any
from urllib.parse import quote


class FixtureError(RuntimeError):
    """Safe fixture setup error without request headers or provider details."""


def load_fixture(corpus: Path) -> dict[str, Any]:
    fixture = json.loads((corpus / 'reviewable-purchase-orders.json').read_text(encoding='utf-8'))
    if not isinstance(fixture, dict) or fixture.get('fixture_kind') != 'synthetic_internet_procurement_v2':
        raise FixtureError('Expected the explicitly synthetic reviewable-purchase-orders.json fixture.')
    if not isinstance(fixture.get('purchase_orders'), list) or not fixture['purchase_orders']:
        raise FixtureError('The synthetic fixture needs purchase orders.')
    vendors = fixture.get('vendors')
    reviews = fixture.get('vendor_reviews')
    if not isinstance(vendors, list) or not isinstance(reviews, list):
        raise FixtureError('The fixture must declare vendor identities and separate review decisions.')
    ids = [vendor.get('id') for vendor in vendors if isinstance(vendor, dict)]
    if len(ids) != len(vendors) or any(not isinstance(ident, str) or not ident.startswith('internet-') for ident in ids) or len(ids) != len(set(ids)):
        raise FixtureError('Fixture vendors need unique internet- IDs.')
    if len(reviews) != len(ids) or {review.get('vendor_id') for review in reviews if isinstance(review, dict)} != set(ids):
        raise FixtureError('Each fixture vendor must have a separate explicit review decision.')
    if any(not isinstance(review, dict) or review.get('action') not in {'approve', 'reject'} or not isinstance(review.get('comment'), str) or len(review['comment'].strip()) < 3 for review in reviews):
        raise FixtureError('Fixture vendor review actions and reasons must be explicit.')
    return fixture


def response_json(response, operation: str) -> dict:
    if response.status_code >= 400:
        try:
            detail = response.json().get('detail', 'Request rejected')
        except (ValueError, AttributeError):
            detail = 'Request rejected'
        raise FixtureError(f'{operation}: HTTP {response.status_code}: {str(detail)[:600]}')
    return response.json()


def wait_draft(client, upload_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        draft = response_json(client.get('/api/procurement/uploads/' + upload_id), 'Read fixture PO draft')
        if draft['status'] == 'ready':
            return draft
        if draft['status'] == 'failed':
            raise FixtureError('Fixture PO extraction failed: ' + str(draft.get('error') or 'Unknown error')[:600])
        if time.monotonic() >= deadline:
            raise FixtureError('Timed out waiting for the fixture PO draft; no confirmation or vendor review was attempted.')
        time.sleep(0.1)


def prepare_fixture(client, fixture: dict, *, timeout: float = 30, confirm_pos: bool = False,
                    review_vendors: bool = False, po_reviewer: str = '', vendor_reviewer: str = '',
                    allow_updates: bool = False) -> dict:
    """Create a draft, with optional separately requested review actions.

    Benchmark callers use disposable databases. The manual importer exposes
    confirmation and vendor decisions as independent, conspicuous opt-in flags.
    """
    if review_vendors and not confirm_pos:
        raise FixtureError('Vendor fixture review requires explicit PO fixture confirmation first.')
    if confirm_pos and not po_reviewer.strip():
        raise FixtureError('PO confirmation needs a named reviewer.')
    if review_vendors and not vendor_reviewer.strip():
        raise FixtureError('Independent vendor fixture review needs a named reviewer.')
    content = json.dumps(fixture, ensure_ascii=False).encode()
    submitted = response_json(client.post('/api/procurement/uploads', files={
        'files': ('reviewable-purchase-orders.json', content, 'application/json'),
    }), 'Upload fictional PO fixture')
    draft = wait_draft(client, submitted['uploads'][0]['id'], timeout)
    invalid = [item for item in draft['items'] if not item['validation']['valid']]
    if invalid:
        errors = [check['message'] for item in invalid for check in item['validation']['checks'] if check.get('blocking') and check['status'] != 'pass']
        raise FixtureError('Synthetic fixture did not pass PO policy: ' + ' '.join(errors[:5]))
    result = {'upload_id': draft['id'], 'draft': draft, 'po_confirmation': None, 'vendor_reviews': []}
    if not confirm_pos:
        return result
    confirmed = response_json(client.post('/api/procurement/uploads/' + draft['id'] + '/confirm', json={
        'revision': draft['revision'], 'reviewer': po_reviewer,
        'comment': 'TEST FIXTURE ONLY: reviewed the explicitly fictional procurement assumptions and original unchanged benchmark budgets; this records test authorization, not real purchasing authority.',
        'attest_authorized': True, 'allow_updates': allow_updates,
    }), 'Confirm fictional POs')
    result['draft'] = confirmed['upload']
    result['po_confirmation'] = confirmed['upload']['confirmation']
    result['procurement'] = confirmed['procurement']
    if review_vendors:
        # These calls are deliberately separate from PO import and confirmation.
        for review in fixture['vendor_reviews']:
            reviewed = response_json(client.post('/api/procurement/vendors/' + quote(review['vendor_id'], safe='') + '/review', json={
                'action': review['action'], 'reviewer': vendor_reviewer,
                'comment': review['comment'], 'attest_reviewed': True,
            }), 'Record independent fictional vendor review')
            result['vendor_reviews'].append({'vendor_id': review['vendor_id'], 'action': review['action'], 'reviewer': vendor_reviewer})
            result['procurement'] = reviewed['procurement']
    return result
