#!/usr/bin/env python3
"""Exercise downloaded invoice fixtures through the real local API and worker.

Examples:
    uv run python scripts/verify_internet_samples.py --local
    uv run python scripts/verify_internet_samples.py --openrouter --case handwritten-rillion-invoice

The OpenRouter option sends public fixture content to the configured provider and
may incur usage charges. The default local mode never supplies the provider key.
All cases use disposable, isolated databases; the operator's workspace is untouched.
Synthetic POs are confirmed through the audited intake API, and fictional vendor
reviews are recorded separately. Each invocation writes a new result directory;
historical reports and downloaded originals are never overwritten.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import Settings  # noqa: E402
from scripts.internet_fixture_workflow import FixtureError, load_fixture, prepare_fixture  # noqa: E402


class CaseError(RuntimeError):
    """A safe test-runner error, with no provider headers or credentials."""


@contextmanager
def temporary_data_environment(path: Path):
    """Also isolate the API module's unused default app on its first import."""
    original = os.environ.get('LEDGERFLOW_DATA_DIR')
    os.environ['LEDGERFLOW_DATA_DIR'] = str(path)
    try:
        yield
    finally:
        if original is None:
            os.environ.pop('LEDGERFLOW_DATA_DIR', None)
        else:
            os.environ['LEDGERFLOW_DATA_DIR'] = original


def safe_path(base: Path, relative: str) -> Path:
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise CaseError(f'Missing or unsafe fixture path: {relative}')
    return path


def write_json(path: Path, data: Any, secret: str = '') -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if secret:
        text = text.replace(secret, '[REDACTED]')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text + '\n', encoding='utf-8')
    temporary.replace(path)


def api_json(response, operation: str) -> dict:
    if response.status_code >= 400:
        try:
            detail = response.json().get('detail', 'Request rejected')
        except (ValueError, AttributeError):
            detail = 'Request rejected'
        raise CaseError(f'{operation}: HTTP {response.status_code}: {str(detail)[:800]}')
    return response.json()


def wait_run(client, submitted: dict, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        run = api_json(client.get(f'/api/runs/{submitted["id"]}'), 'Read run')
        if run['status'] in ('completed', 'failed'):
            return run
        if time.monotonic() >= deadline:
            run['verification_timeout'] = True
            run['verification_note'] = f'Runner stopped waiting after {timeout:g} seconds.'
            return run
        time.sleep(0.2)


def outcome(run: dict | None) -> dict | None:
    if run is None:
        return None
    extraction = run.get('extraction') or {}
    invoice = run.get('invoice') or {}
    result = run.get('result') or {}
    return {
        'run_id': run.get('id'),
        'status': run.get('status'),
        'decision': run.get('decision'),
        'method': extraction.get('method'),
        'invoice_number': invoice.get('invoice_number'),
        'vendor_name': invoice.get('vendor_name'),
        'currency': invoice.get('currency'),
        'total': invoice.get('total'),
        'extraction_confidence': invoice.get('extraction_confidence'),
        'matched_po': (result.get('matched_po') or {}).get('po_number'),
        'checks_requiring_attention': [
            {'code': check['code'], 'status': check['status'], 'blocking': check.get('blocking', False), 'message': check['message']}
            for check in result.get('checks', []) if check['status'] != 'pass'
        ],
        'warnings': extraction.get('warnings', []),
        'error': run.get('error'),
        'verification_timeout': run.get('verification_timeout', False),
    }


def run_case(case: dict, corpus: Path, procurement: dict, configured: Settings,
             use_openrouter: bool, timeout: float, output: Path) -> dict:
    case_id = case['id']
    document = safe_path(corpus, case['file'])
    reference = None
    if case.get('normalized_file'):
        reference = json.loads(safe_path(corpus, case['normalized_file']).read_text(encoding='utf-8'))
        if not isinstance(reference, dict):
            raise CaseError('The normalized fixture must contain one invoice object.')
    secret = configured.openrouter_api_key if use_openrouter else ''
    record: dict[str, Any] = {
        'id': case_id, 'file': case['file'], 'kind': case.get('kind'),
        'source_url': case.get('source_url'), 'normalized_file': case.get('normalized_file'),
        'original': None, 'correction': None, 'structured_replay': None, 'split_overage': None,
    }
    with tempfile.TemporaryDirectory(prefix='ledgerflow-internet-') as directory:
        temp = Path(directory)
        with temporary_data_environment(temp / 'unused-default-app'):
            from backend.app.main import create_app
        from fastapi.testclient import TestClient
        settings = Settings(data_dir=temp / 'case', openrouter_api_key=secret,
                            openrouter_model=configured.openrouter_model,
                            enable_openrouter=use_openrouter, max_pages=configured.max_pages)
        with TestClient(create_app(settings)) as client:
            setup = prepare_fixture(
                client, procurement, timeout=min(timeout, 30), confirm_pos=True, review_vendors=True,
                po_reviewer='Fictional PO fixture reviewer', vendor_reviewer='Fictional vendor fixture reviewer',
            )
            record['fixture_setup'] = {key: setup[key] for key in ('upload_id', 'po_confirmation', 'vendor_reviews')}
            write_json(output / f'{case_id}.fixture-setup.json', setup, secret)
            started = time.monotonic()
            submitted = api_json(client.post('/api/runs/upload', files={
                'files': (document.name, document.read_bytes(), mimetypes.guess_type(document.name)[0] or 'application/octet-stream')
            }), 'Upload invoice')
            if len(submitted.get('runs', [])) != 1:
                raise CaseError('Each corpus file must create exactly one submitted document run.')
            original = wait_run(client, submitted['runs'][0], timeout)
            record['original'] = outcome(original)
            # The primary file is always the full original run as first evaluated.
            write_json(output / f'{case_id}.json', original, secret)
            if original.get('verification_timeout') or original['status'] == 'failed':
                record['duration_seconds'] = round(time.monotonic() - started, 2)
                record['workflow_note'] = 'Correction/replay skipped because source processing did not complete.'
                return record
            if reference is not None:
                baseline = original
                if original['decision'] in ('needs_review', 'rejected'):
                    response = api_json(client.post(f'/api/runs/{original["id"]}/correct', json={
                        'invoice': reference, 'reviewer': 'Fixture reviewer',
                        'comment': 'Verified against the published public sample transcription and the explicitly documented test procurement assumptions; this is an isolated fixture evaluation.'
                    }), 'Correct invoice')
                    corrected = wait_run(client, response['runs'][0], timeout)
                    baseline = corrected
                    record['correction'] = outcome(corrected)
                    write_json(output / f'{case_id}.correction.json', corrected, secret)
                    superseded = api_json(client.get(f'/api/runs/{original["id"]}'), 'Read correction lineage')
                    write_json(output / f'{case_id}.superseded-original.json', superseded, secret)
                    if corrected.get('verification_timeout') or corrected['status'] == 'failed':
                        record['duration_seconds'] = round(time.monotonic() - started, 2)
                        record['workflow_note'] = 'Replay skipped because correction did not complete.'
                        return record
                if baseline.get('decision') == 'approved':
                    replay = api_json(client.post('/api/runs/structured', json={'invoice': reference}), 'Replay structured invoice')
                    replayed = wait_run(client, replay['runs'][0], timeout)
                    record['structured_replay'] = outcome(replayed)
                    write_json(output / f'{case_id}.replay.json', replayed, secret)
                    record['replay_duplicate_detected'] = any(
                        check['code'] == 'duplicate' and check['status'] == 'fail'
                        for check in (replayed.get('result') or {}).get('checks', [])
                    )
                    split_invoice = {**reference, 'invoice_number': str(reference['invoice_number']) + '-SPLIT2'}
                    split = api_json(client.post('/api/runs/structured', json={'invoice': split_invoice}), 'Submit split-overage invoice')
                    split_run = wait_run(client, split['runs'][0], timeout)
                    record['split_overage'] = outcome(split_run)
                    write_json(output / f'{case_id}.split-overage.json', split_run, secret)
                    record['split_overage_detected'] = any(
                        check['code'] == 'po_balance' and check['status'] == 'fail'
                        for check in (split_run.get('result') or {}).get('checks', [])
                    )
                else:
                    record['workflow_note'] = 'Duplicate and split-overage probes skipped: no approved original or correction.'
            record['duration_seconds'] = round(time.monotonic() - started, 2)
            record['procurement_after'] = api_json(client.get('/api/procurement'), 'Read final commitments')
            record['regression_failures'] = []
            if reference is not None:
                if baseline.get('decision') != 'approved':
                    record['regression_failures'].append('The verified invoice transcription did not reach its expected approved fixture outcome.')
                else:
                    if not record.get('replay_duplicate_detected'):
                        record['regression_failures'].append('The repeated invoice was not identified as a duplicate.')
                    if not record.get('split_overage_detected'):
                        record['regression_failures'].append('The second full-size invoice was not blocked by the committed PO balance.')
            elif original.get('decision') == 'approved':
                record['regression_failures'].append('The negative receipt fixture must remain unapproved.')
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--local', action='store_true', help='Local extraction only (default; no provider key is supplied).')
    modes.add_argument('--openrouter', action='store_true', help='Use configured OpenRouter model and key; makes paid provider calls.')
    parser.add_argument('--case', action='append', dest='case_ids', help='Run only this manifest case ID; repeat for multiple cases.')
    parser.add_argument('--timeout', type=float, default=180, help='Maximum seconds to await each run (default180; maximum180).')
    parser.add_argument('--corpus', type=Path, default=ROOT / 'samples' / 'internet', help='Directory containing manifest.json and synthetic reviewable-purchase-orders.json.')
    parser.add_argument('--output', type=Path, help='New or empty output directory. Default: a timestamped directory under corpus/results/<mode>/.')
    args = parser.parse_args()
    if not 0 < args.timeout <= 180:
        parser.error('--timeout must be greater than zero and at most180 seconds.')
    configured = Settings.from_env()
    if args.openrouter and not configured.openrouter_api_key:
        parser.error('OPENROUTER_API_KEY is not configured; add it to the local .env or use --local.')
    corpus = args.corpus.resolve()
    try:
        manifest = json.loads((corpus / 'manifest.json').read_text(encoding='utf-8'))
        procurement = load_fixture(corpus)
        cases = manifest['cases']
        if not isinstance(cases, list) or not cases:
            raise ValueError('manifest.cases must be a nonempty array.')
        ids = [case['id'] for case in cases]
        if len(ids) != len(set(ids)) or any(not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', item) for item in ids):
            raise ValueError('Case IDs must be unique filename-safe identifiers.')
        selected = set(args.case_ids or ids)
        unknown = selected - set(ids)
        if unknown:
            raise ValueError('Unknown case IDs: ' + ', '.join(sorted(unknown)))
        cases = [case for case in cases if case['id'] in selected]
    except (OSError, ValueError, KeyError, TypeError, FixtureError) as exc:
        parser.error(str(exc))
    mode = 'openrouter' if args.openrouter else 'local'
    output = args.output.resolve() if args.output else corpus / 'results' / mode / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        parser.error('--output must name a new or empty directory; historical results are not overwritten.')
    secret = configured.openrouter_api_key if args.openrouter else ''
    summary = {
        'generated_at': datetime.now(timezone.utc).isoformat(), 'mode': mode,
        'model': configured.openrouter_model if args.openrouter else None,
        'isolation': 'A fresh temporary database per case; no operator workspace changes.',
        'procurement_fixture': 'reviewable-purchase-orders.json; synthetic PO confirmation and independent vendor review are separately audited.',
        'cases': [],
    }
    failures = 0
    for case in cases:
        try:
            record = run_case(case, corpus, procurement, configured, args.openrouter, args.timeout, output)
            original = record['original'] or {}
            if original.get('status') != 'completed' or original.get('verification_timeout') or record.get('regression_failures'):
                failures += 1
            codes = ','.join(check['code'] for check in original.get('checks_requiring_attention', [])) or 'none'
            line = f"{case['id']}: status={original.get('status')} decision={original.get('decision')} method={original.get('method')} total={original.get('total')} checks={codes}"
            if record.get('correction'):
                line += f" correction={record['correction']['decision']}"
            if record.get('structured_replay'):
                line += f" replay={record['structured_replay']['decision']}"
            if record.get('split_overage'):
                line += f" split={record['split_overage']['decision']}"
            if record.get('regression_failures'):
                line += ' regression_failures=' + '; '.join(record['regression_failures'])
            print(line.replace(secret, '[REDACTED]') if secret else line, flush=True)
        except (CaseError, FixtureError) as exc:
            failures += 1
            message = str(exc).replace(secret, '[REDACTED]') if secret else str(exc)
            record = {'id': case['id'], 'file': case.get('file'), 'runner_error': message}
            print(f"{case['id']}: runner_error={message}", flush=True)
        except Exception as exc:
            # Unknown exceptions may include third-party request details. Only log type.
            failures += 1
            record = {'id': case['id'], 'file': case.get('file'), 'runner_error': f'Unexpected {type(exc).__name__}; inspect the input and application code.'}
            print(f"{case['id']}: runner_error={type(exc).__name__}", flush=True)
        summary['cases'].append(record)
        write_json(output / 'summary.json', summary, secret)
    print(f"Saved {len(summary['cases'])} case result(s) to {output / 'summary.json'}", flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
