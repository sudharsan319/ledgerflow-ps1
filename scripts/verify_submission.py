#!/usr/bin/env python3
"""Rehearse the submission through the real API in disposable local databases."""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Optional JSON evidence file')
    args = parser.parse_args()
    results = {}
    with tempfile.TemporaryDirectory(prefix='ledgerflow-submission-') as folder:
        # main constructs a default app at import; isolate that instance too.
        os.environ['LEDGERFLOW_DATA_DIR'] = str(Path(folder) / 'unused-default')
        os.environ['ENABLE_OPENROUTER'] = 'false'
        from backend.app.config import Settings
        from backend.app.main import create_app
        from fastapi.testclient import TestClient

        def client(name):
            return TestClient(create_app(Settings(data_dir=Path(folder) / name, enable_openrouter=False)))

        def completed(api, queued):
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                response = api.get(f'/api/runs/{queued["id"]}')
                response.raise_for_status()
                run = response.json()
                if run['status'] in ('completed', 'failed'):
                    assert run['status'] == 'completed', run.get('error')
                    return run
                time.sleep(.05)
            raise AssertionError('Invoice processing timed out')

        def upload(api, filename):
            response = api.post('/api/runs/upload', files={'files': (filename, (ROOT / 'samples' / filename).read_bytes())})
            assert response.status_code == 202, response.text
            parents = [completed(api, run) for run in response.json()['runs']]
            runs = []
            for parent in parents:
                if parent.get('batch_size', 1) == 1:
                    runs.append(parent)
                    continue
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    batch = [r for r in api.get('/api/runs').json()['runs'] if r['id'] == parent['id'] or r.get('parent_id') == parent['id']]
                    if len(batch) == parent['batch_size'] and all(r['status'] == 'completed' for r in batch):
                        runs.extend(sorted(batch, key=lambda r: r['batch_index']))
                        break
                    time.sleep(.05)
                else:
                    raise AssertionError('Batch processing timed out')
            return runs

        def record(name, runs, expected):
            decisions = [r['decision'] for r in runs]
            assert decisions == expected, {name: [(r['decision'], r.get('result', {}).get('summary')) for r in runs]}
            results[name] = {'decisions': decisions, 'checks': [[c['code'] for c in r['result']['checks'] if c['status'] != 'pass'] for r in runs]}

        with client('pdf') as api:
            happy = upload(api, '01-clean-invoice.pdf')[0]
            record('pdf_happy_path', [happy], ['approved'])
            assert api.get(f'/api/runs/{happy["id"]}/source').content == (ROOT / 'samples/01-clean-invoice.pdf').read_bytes()
            for format in ('json', 'csv', 'html'):
                response = api.get(f'/api/runs/{happy["id"]}/export', params={'format': format})
                assert response.status_code == 200 and response.content
            record('cross_format_duplicate', upload(api, '01-clean-invoice.json'), ['rejected'])
        with client('split') as api:
            record('split_invoices', upload(api, '04-split-first.json') + upload(api, '04-split-second.json'), ['approved', 'needs_review'])
        with client('ambiguous') as api:
            record('ambiguous_po', upload(api, '05-ambiguous-po.json'), ['needs_review'])
        with client('missing') as api:
            record('missing_identity_and_wrong_total', upload(api, '03-missing-and-conflicting-fields.json'), ['needs_review'])
        with client('batch') as api:
            record('csv_batch', upload(api, '06-batch-invoices.csv'), ['approved', 'approved'])
        with client('scan') as api:
            scan = upload(api, '02-scanned-invoice.png')[0]
            record('scanned_input', [scan], ['needs_review'])
            response = api.post(f'/api/runs/{scan["id"]}/correct', json={
                'invoice': json.loads((ROOT / 'samples/02-scanned-verified.json').read_text()),
                'reviewer': 'Fictional submission rehearsal',
                'comment': 'Known generated scan transcription; isolated API rehearsal only.',
            })
            assert response.status_code == 202, response.text
            corrected = completed(api, response.json()['runs'][0])
            record('verified_scan_correction', [corrected], ['approved'])
            assert corrected['correction_of'] == scan['id']
        with client('scenarios') as api:
            expected = {'happy_path': ['approved'], 'duplicate': ['approved', 'rejected'], 'split_overage': ['approved', 'needs_review'], 'ambiguous_po': ['needs_review'], 'missing_fields': ['needs_review']}
            for scenario in api.get('/api/demo').json()['scenarios']:
                response = api.post('/api/demo/' + scenario['id'])
                assert response.status_code == 202, response.text
                runs = [completed(api, run) for run in response.json()['runs']]
                record('scenario_' + scenario['id'], runs, expected[scenario['id']])
    report = {'status': 'passed', 'model_calls': 0, 'isolated_databases': True, 'results': results}
    content = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    print(content)


if __name__ == '__main__':
    main()
