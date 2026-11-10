#!/usr/bin/env python3
"""Prepare fictional internet-sample PO drafts in an already running local app.

The default creates a reviewable draft only. It does not register POs or change
vendor approval. For a dedicated local TEST workspace, explicit flags can record
fictional PO confirmation and a separate fictional vendor decision:

    python scripts/import_internet_procurement.py
    python scripts/import_internet_procurement.py --confirm-test-pos --reviewer "PO tester"
    python scripts/import_internet_procurement.py --confirm-test-pos --reviewer "PO tester" \\
        --review-test-vendors --vendor-reviewer "Supplier tester" --allow-test-po-updates

These are synthetic procurement assumptions, never real supplier endorsements.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import Settings
from scripts.internet_fixture_workflow import FixtureError, load_fixture, prepare_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--confirm-test-pos', action='store_true', help='Explicitly register the fictional fixture POs with an audited test authorization. Does not approve vendors.')
    parser.add_argument('--reviewer', default='', help='Name recorded for explicit PO confirmation.')
    parser.add_argument('--review-test-vendors', action='store_true', help='Separately apply four fictional vendor approvals and one rejection; TEST workspace only.')
    parser.add_argument('--vendor-reviewer', default='', help='Name recorded for the independent fictional vendor review.')
    parser.add_argument('--allow-test-po-updates', action='store_true', help='Explicitly allow fixture POs to replace existing records, subject to commitment protection.')
    args = parser.parse_args()
    parsed = urlparse(args.url)
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        parser.error('Use a loopback HTTP URL for the local application.')
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        parser.error('Use a loopback base URL without credentials, path, query or fragment.')
    if args.confirm_test_pos and not args.reviewer.strip():
        parser.error('--confirm-test-pos requires --reviewer.')
    if args.review_test_vendors and (not args.confirm_test_pos or not args.vendor_reviewer.strip()):
        parser.error('--review-test-vendors requires --confirm-test-pos and --vendor-reviewer.')
    if args.allow_test_po_updates and not args.confirm_test_pos:
        parser.error('--allow-test-po-updates requires --confirm-test-pos.')
    try:
        incoming = load_fixture(ROOT / 'samples/internet')
        with httpx.Client(base_url=args.url, timeout=20) as client:
            response = client.get('/api/procurement')
            response.raise_for_status()
            existing = response.json()
            # Vendor identity is never replaced as a side effect of PO intake.
            known_vendors = {record['id']: record for record in existing['vendors']}
            for vendor in incoming['vendors']:
                old = known_vendors.get(vendor['id'])
                if old and (old.get('name') != vendor['name'] or old.get('aliases', []) != vendor.get('aliases', [])):
                    raise ValueError(f'Refusing to act on a different existing vendor identity: {vendor["id"]}')
            backup = Settings.from_env().data_dir / 'backups'
            backup.mkdir(parents=True, exist_ok=True)
            path = backup / ('procurement-before-internet-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
            path.write_text(json.dumps(existing, indent=2) + '\n')
            path.chmod(0o600)
            result = prepare_fixture(client, incoming, confirm_pos=args.confirm_test_pos,
                                     review_vendors=args.review_test_vendors, po_reviewer=args.reviewer,
                                     vendor_reviewer=args.vendor_reviewer, allow_updates=args.allow_test_po_updates)
        print(f'Prepared {len(incoming["purchase_orders"])} fictional test POs. Draft: {result["upload_id"]}')
        if args.confirm_test_pos:
            print('PO test authorization was recorded. Vendor approvals were changed only if --review-test-vendors was explicitly supplied.')
        else:
            print('Draft only: open Procurement → Upload POs to review and confirm. No PO was registered and no vendor approval changed.')
        if args.review_test_vendors:
            print('Recorded separate fictional vendor reviews: four approvals and one rejection. These are test assumptions, not real supplier endorsements.')
        print('Existing invoice runs were not changed.')
        print(f'Procurement snapshot: {path}')
    except httpx.HTTPError as exc:
        # No headers, credentials, or response body are printed.
        print(f'Local import failed ({type(exc).__name__}). Start the app and inspect its procurement import validation.', file=sys.stderr)
        return 1
    except (ValueError, OSError, FixtureError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
