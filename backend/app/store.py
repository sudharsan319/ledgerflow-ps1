"""SQLite persistence. Financial commitments derive exclusively from approved runs."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


DEFAULT_POLICY = {'tolerance_percent': '2', 'tolerance_absolute': '5', 'min_confidence': 0.85}
SEED_VENDORS = [
    {'id': 'vendor-acme', 'name': 'Acme Cloud Services', 'aliases': ['Acme Cloud', 'Acme Cloud Services Ltd'], 'approved': True},
    {'id': 'vendor-northstar', 'name': 'Northstar Office Supply', 'aliases': ['Northstar'], 'approved': True},
]
SEED_POS = [
    {'po_number': 'PO-2026-001', 'vendor_id': 'vendor-acme', 'currency': 'USD', 'total': '12000.00', 'status': 'open', 'description': 'Cloud services annual agreement',
     'line_items': [{'description': 'Cloud services', 'quantity': '120', 'unit_price': '100', 'amount': '12000'}]},
    {'po_number': 'PO-2026-002', 'vendor_id': 'vendor-northstar', 'currency': 'USD', 'total': '5000.00', 'status': 'open', 'description': 'Office equipment'},
    {'po_number': 'PO-2026-003', 'vendor_id': 'vendor-northstar', 'currency': 'USD', 'total': '7500.00', 'status': 'open', 'description': 'Office supplies'},
]


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connection() as conn:
            conn.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS vendors (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS purchase_orders (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS po_uploads (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS procurement_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS invoice_deletions (id TEXT PRIMARY KEY, at TEXT NOT NULL, data TEXT NOT NULL);
            ''')
            if not conn.execute("SELECT 1 FROM settings WHERE id='initialized'").fetchone():
                for item in SEED_VENDORS:
                    conn.execute('INSERT OR IGNORE INTO vendors VALUES (?,?)', (item['id'], json.dumps(item)))
                for item in SEED_POS:
                    from .po_registry import fixture_po
                    item = fixture_po(item, next(v['name'] for v in SEED_VENDORS if v['id'] == item['vendor_id']))
                    conn.execute('INSERT OR IGNORE INTO purchase_orders VALUES (?,?)', (item['po_number'], json.dumps(item)))
                conn.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('policy', json.dumps(DEFAULT_POLICY)))
                conn.execute('INSERT INTO settings VALUES (?,?)', ('initialized', 'true'))

    @contextmanager
    def connection(self, write=False):
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            if write:
                conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_run(self, run, conn=None):
        run['updated_at'] = now()
        if conn is None:
            with self.connection(write=True) as session:
                self.save_run(run, session)
            return
        conn.execute('INSERT INTO runs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                     (run['id'], run['created_at'], json.dumps(run, ensure_ascii=False)))

    def get_run(self, run_id, conn=None):
        if conn is None:
            with self.connection() as session:
                return self.get_run(run_id, session)
        row = conn.execute('SELECT data FROM runs WHERE id=?', (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def runs(self, conn=None):
        if conn is None:
            with self.connection() as session:
                return self.runs(session)
        return [json.loads(row[0]) for row in conn.execute('SELECT data FROM runs ORDER BY created_at DESC, id DESC')]

    def procurement(self, conn=None, exclude_run=None):
        if conn is None:
            with self.connection() as session:
                return self.procurement(session, exclude_run)
        vendors = [json.loads(r[0]) for r in conn.execute('SELECT data FROM vendors ORDER BY id')]
        pos = [json.loads(r[0]) for r in conn.execute('SELECT data FROM purchase_orders ORDER BY id')]
        policy = json.loads(conn.execute("SELECT data FROM settings WHERE id='policy'").fetchone()[0])
        commitments = {}
        runs = [r for r in self.runs(conn) if r['id'] != exclude_run]
        for run in runs:
            if run['id'] == exclude_run or run.get('decision') != 'approved':
                continue
            po = (run.get('result') or {}).get('matched_po')
            if po:
                number = po['po_number']
                commitments[number] = commitments.get(number, Decimal('0')) + Decimal(run['invoice']['total'])
        for po in pos:
            po['committed'] = str(commitments.get(po['po_number'], Decimal('0')))
            from .po_matching import line_usage
            options, usage, unresolved, count = line_usage(po, runs)
            po['line_commitments'] = [{**o, 'invoiced_quantity': str(usage[o['ref']])} for o in options]
            po['quantity_history_unresolved'] = unresolved
            po['approved_invoice_count'] = count
        from .po_registry import decorate_procurement
        vendors, pos = decorate_procurement(vendors, pos)
        return {'vendors': vendors, 'purchase_orders': pos, 'policy': policy}

    def recover(self):
        with self.connection(write=True) as conn:
            for run in self.runs(conn):
                if run['status'] in ('queued', 'processing'):
                    run.update(status='failed', error='Processing was interrupted by a service restart. Resubmit the source to retry.')
                    for event in run['events']:
                        if event['status'] == 'running':
                            event['status'] = 'failed'
                    run['events'].append({'stage': 'output', 'status': 'failed', 'message': run['error'], 'at': now()})
                    self.save_run(run, conn)
