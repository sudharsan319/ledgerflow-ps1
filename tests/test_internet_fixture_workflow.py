import json
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.po_policy import validate_po
from scripts.internet_fixture_workflow import FixtureError, load_fixture, prepare_fixture
from scripts.verify_internet_samples import run_case, temporary_data_environment

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'samples' / 'internet'


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Importing main constructs an unused default app; isolate that database too.
    with temporary_data_environment(tmp_path / 'unused-app'):
        from backend.app.main import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setattr('backend.app.po_extraction._model_extract', lambda *args: pytest.fail('Fixture setup must not use a provider'))
    with TestClient(create_app(Settings(data_dir=tmp_path / 'case', enable_openrouter=False))) as client:
        yield client


def test_reviewable_fixtures_preserve_original_budgets_and_lines():
    legacy = json.loads((CORPUS / 'procurement.json').read_text())
    fixture = load_fixture(CORPUS)
    by_number = {po['po_number']: po for po in fixture['purchase_orders']}
    for original in legacy['purchase_orders']:
        po = by_number[original['po_number']]
        for field in ('vendor_id', 'currency', 'total', 'status', 'description'):
            assert po[field] == original[field]
        if original.get('line_items'):
            assert po['line_items'] == original['line_items']
        assert validate_po(po)['valid'], validate_po(po)
    assert all('approved' not in vendor for vendor in fixture['vendors'])
    assert sum(review['action'] == 'approve' for review in fixture['vendor_reviews']) == 4
    negative = by_number['INTERNET-PO-RECEIPT-HOLD']
    assert negative['approval_status'] == 'pending' and negative['status'] == 'closed'
    assert not validate_po(negative)['eligible']


def test_default_fixture_import_only_creates_a_draft(client):
    before = client.get('/api/procurement').json()
    result = prepare_fixture(client, load_fixture(CORPUS))
    assert result['draft']['status'] == 'ready'
    assert result['po_confirmation'] is None
    assert result['vendor_reviews'] == []
    assert client.get('/api/procurement').json() == before


def test_confirming_fixture_pos_keeps_new_vendors_pending(client):
    result = prepare_fixture(client, load_fixture(CORPUS), confirm_pos=True, po_reviewer='Test PO reviewer')
    assert result['draft']['status'] == 'confirmed'
    vendors = [vendor for vendor in result['procurement']['vendors'] if vendor['id'].startswith('internet-')]
    assert len(vendors) == 5
    assert all(vendor['approved'] is False and vendor['approval_status'] == 'pending' for vendor in vendors)
    assert all(vendor['approval_history'] == [] for vendor in vendors)


def test_explicit_vendor_setup_is_separately_audited_and_preserves_negative_case(client):
    result = prepare_fixture(client, load_fixture(CORPUS), confirm_pos=True, review_vendors=True,
                             po_reviewer='Test PO reviewer', vendor_reviewer='Test supplier reviewer')
    procurement = result['procurement']
    vendors = [vendor for vendor in procurement['vendors'] if vendor['id'].startswith('internet-')]
    assert sum(vendor['approved'] for vendor in vendors) == 4
    assert all(vendor['approval_history'][-1]['reviewer'] == 'Test supplier reviewer' for vendor in vendors)
    pos = {po['po_number']: po for po in procurement['purchase_orders']}
    assert pos['INTERNET-PO-ACME-001']['eligibility_status'] == 'eligible'
    assert pos['INTERNET-PO-RECEIPT-HOLD']['eligibility_status'] == 'ineligible'
    with client.app.state.store.connection() as conn:
        audits = [json.loads(row[0]) for row in conn.execute('SELECT data FROM procurement_audit')]
    assert sum(event['action'] == 'confirm_po' for event in audits) == 5
    assert sum('vendor_id' in event for event in audits) == 5


@pytest.mark.parametrize('options', [
    {'confirm_pos': True},
    {'review_vendors': True},
    {'confirm_pos': True, 'po_reviewer': 'PO tester', 'review_vendors': True},
])
def test_fixture_actions_need_distinct_explicit_options(client, options):
    with pytest.raises(FixtureError):
        prepare_fixture(client, load_fixture(CORPUS), **options)
    assert client.get('/api/procurement/uploads').json()['uploads'] == []


def test_existing_public_invoice_regression_uses_reviewed_fixtures(tmp_path):
    manifest = json.loads((CORPUS / 'manifest.json').read_text())
    case = next(case for case in manifest['cases'] if case['id'] == 'printed-acme-commercial')
    result = run_case(case, CORPUS, load_fixture(CORPUS), Settings(data_dir=tmp_path / 'unused-config'),
                      use_openrouter=False, timeout=30, output=tmp_path / 'results')
    baseline = result['correction'] or result['original']
    assert baseline['decision'] == 'approved', result
    assert result['replay_duplicate_detected']
    assert result['split_overage_detected']
    assert len(result['fixture_setup']['vendor_reviews']) == 5
    assert result['regression_failures'] == []
    assert (tmp_path / 'results' / 'printed-acme-commercial.fixture-setup.json').is_file()


def test_runner_refuses_to_overwrite_previous_results(tmp_path, monkeypatch):
    from scripts import verify_internet_samples
    report = tmp_path / 'summary.json'
    report.write_text('historical result')
    monkeypatch.setattr('sys.argv', ['verify_internet_samples.py', '--local', '--output', str(tmp_path)])
    monkeypatch.setattr(verify_internet_samples.Settings, 'from_env', lambda: Settings(data_dir=tmp_path / 'unused'))
    with pytest.raises(SystemExit) as error:
        verify_internet_samples.main()
    assert error.value.code == 2
    assert report.read_text() == 'historical result'
