"""SQLite locally or PostgreSQL on hosted deployments.

Financial commitments derive exclusively from approved runs. Hosted source
bytes share the PostgreSQL database so deploys cannot separate records from
their documents.
"""
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID


def now():
    return datetime.now(timezone.utc).isoformat()


DEFAULT_POLICY = {'tolerance_percent': '2', 'tolerance_absolute': '5', 'min_confidence': 0.75}
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


class _PostgresConnection:
    """Keep the small existing SQL surface shared with sqlite3."""

    def __init__(self, raw):
        self.raw = raw

    @staticmethod
    def query(sql):
        return sql.replace('?', '%s')

    def execute(self, sql, params=()):
        return self.raw.execute(self.query(sql), params)

    def executemany(self, sql, params):
        with self.raw.cursor() as cursor:
            cursor.executemany(self.query(sql), params)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


class Store:
    def __init__(self, path: Path, *, database_url: str = ''):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.database_url = database_url
        self.remote = bool(database_url)
        with self.connection(write=self.remote) as conn:
            if self.remote:
                for statement in (
                    'CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)',
                    'CREATE TABLE IF NOT EXISTS vendors (id TEXT PRIMARY KEY, data TEXT NOT NULL)',
                    'CREATE TABLE IF NOT EXISTS purchase_orders (id TEXT PRIMARY KEY, data TEXT NOT NULL)',
                    'CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY, data TEXT NOT NULL)',
                    'CREATE TABLE IF NOT EXISTS po_uploads (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)',
                    'CREATE TABLE IF NOT EXISTS procurement_audit (id BIGSERIAL PRIMARY KEY, at TEXT NOT NULL, data TEXT NOT NULL)',
                    'CREATE TABLE IF NOT EXISTS invoice_deletions (id TEXT PRIMARY KEY, at TEXT NOT NULL, data TEXT NOT NULL)',
                    'CREATE TABLE IF NOT EXISTS document_sources (kind TEXT NOT NULL, id TEXT NOT NULL, content BYTEA NOT NULL, PRIMARY KEY(kind,id))',
                ):
                    conn.execute(statement)
            else:
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
                    self._seed_insert(conn, 'vendors', item['id'], json.dumps(item))
                for item in SEED_POS:
                    from .po_registry import fixture_po
                    item = fixture_po(item, next(v['name'] for v in SEED_VENDORS if v['id'] == item['vendor_id']))
                    self._seed_insert(conn, 'purchase_orders', item['po_number'], json.dumps(item))
                self._seed_insert(conn, 'settings', 'policy', json.dumps(DEFAULT_POLICY))
                conn.execute('INSERT INTO settings VALUES (?,?)', ('initialized', 'true'))
            # Move an existing database off the previous default once. Keep
            # other chosen thresholds and allow later policy edits to persist.
            if not conn.execute("SELECT 1 FROM settings WHERE id='confidence_threshold_075_migrated'").fetchone():
                row = conn.execute("SELECT data FROM settings WHERE id='policy'").fetchone()
                if row:
                    policy = json.loads(row[0])
                    if policy.get('min_confidence') in (0.85, '0.85'):
                        policy['min_confidence'] = 0.75
                        conn.execute("UPDATE settings SET data=? WHERE id='policy'", (json.dumps(policy),))
                conn.execute('INSERT INTO settings VALUES (?,?)', ('confidence_threshold_075_migrated', 'true'))

    def _seed_insert(self, conn, table, key, data):
        if self.remote:
            conn.execute(f'INSERT INTO {table} VALUES (?,?) ON CONFLICT(id) DO NOTHING', (key, data))
        else:
            conn.execute(f'INSERT OR IGNORE INTO {table} VALUES (?,?)', (key, data))

    @contextmanager
    def connection(self, write=False):
        if self.remote:
            import psycopg
            conn = _PostgresConnection(psycopg.connect(self.database_url, connect_timeout=15))
        else:
            conn = sqlite3.connect(self.path, timeout=30)
        try:
            if write:
                # A single transaction lock serializes PO capacity and vendor
                # decisions across overlapping Render deploy instances.
                conn.execute('SELECT pg_advisory_xact_lock(54685321)' if self.remote else 'BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _source_path(self, kind, source_id):
        if kind not in ('sources', 'po-sources'):
            raise ValueError('Unknown source kind.')
        try:
            identifier = str(UUID(str(source_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError('Invalid source identifier.') from exc
        return self.path.parent / kind / f'{identifier}.bin'

    def save_source(self, kind, source_id, content):
        path = self._source_path(kind, source_id)
        if self.remote:
            with self.connection(write=True) as conn:
                conn.execute('INSERT INTO document_sources (kind,id,content) VALUES (?,?,?) '
                             'ON CONFLICT(kind,id) DO UPDATE SET content=excluded.content', (kind, str(source_id), content))
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o600)

    def has_source(self, kind, source_id):
        try:
            path = self._source_path(kind, source_id)
        except ValueError:
            return False
        if not self.remote:
            return path.is_file()
        with self.connection() as conn:
            return bool(conn.execute('SELECT 1 FROM document_sources WHERE kind=? AND id=?', (kind, str(source_id))).fetchone())

    def source_path(self, kind, source_id):
        try:
            path = self._source_path(kind, source_id)
        except ValueError:
            return None
        if not self.remote:
            return path if path.is_file() else None
        with self.connection() as conn:
            row = conn.execute('SELECT content FROM document_sources WHERE kind=? AND id=?', (kind, str(source_id))).fetchone()
        if not row:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        # Materialize only for existing preview and FileResponse consumers.
        # The durable copy remains in PostgreSQL and survives a new instance.
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f'{source_id}.', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            output.write(bytes(row[0]))
        try:
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def delete_sources(self, kind, source_ids, conn):
        if self.remote:
            conn.executemany('DELETE FROM document_sources WHERE kind=? AND id=?', [(kind, key) for key in source_ids])

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
