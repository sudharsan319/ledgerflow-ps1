"""Bounded local worker. Every visible stage records real work, with no simulated delays."""
import hashlib
import logging
import queue
import threading
import uuid

from .extraction import extract_invoices
from .models import normalize_invoice
from .policy import evaluate_invoice
from .store import now

logger = logging.getLogger(__name__)


def new_run(filename, source_hash, parent_id=None):
    timestamp = now()
    return {'id': str(uuid.uuid4()), 'filename': filename, 'status': 'queued', 'decision': None,
            'created_at': timestamp, 'updated_at': timestamp, 'invoice': None, 'result': None,
            'extraction': None, 'error': None, 'reviews': [], 'source_sha256': source_hash,
            'parent_id': parent_id, 'events': [{'stage': 'ingest', 'status': 'completed',
                'message': 'Input accepted and fingerprinted; waiting for the processing worker.', 'at': timestamp}]}


class Pipeline:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.queue = queue.Queue(maxsize=settings.queue_capacity)
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._work, name='ledgerflow-worker', daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopping.set()
        self.thread.join(timeout=3)

    def submit(self, inputs):
        with self.lock:
            if self.queue.qsize() + len(inputs) > self.settings.queue_capacity:
                raise ValueError('The processing queue is full. Please retry after the current runs finish.')
            runs = []
            for filename, content in inputs:
                run = new_run(filename, hashlib.sha256(content).hexdigest())
                self.save_source(run['id'], content)
                self.store.save_run(run)
                self.queue.put_nowait((run['id'], content))
                runs.append(run)
            return runs

    def save_source(self, run_id, content):
        directory = self.settings.data_dir / 'sources'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{run_id}.bin'
        path.write_bytes(content)
        path.chmod(0o600)

    def _event(self, run, stage, status, message):
        # One entry per stage, updated from running to its actual completion status.
        existing = next((e for e in run['events'] if e['stage'] == stage), None)
        if existing:
            existing.update(status=status, message=message, at=now())
        else:
            run['events'].append({'stage': stage, 'status': status, 'message': message, 'at': now()})
        self.store.save_run(run)

    def _work(self):
        while not self.stopping.is_set():
            try:
                run_id, content = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.process(run_id, content)
            except Exception:
                # Document contents and provider responses are intentionally absent from logs.
                logger.error('Unexpected worker failure for run %s', run_id)
                run = self.store.get_run(run_id)
                if run:
                    run.update(status='failed', error='An unexpected processing error occurred. Check the input and retry.')
                    self._event(run, 'output', 'failed', run['error'])
            finally:
                self.queue.task_done()

    def process(self, run_id, content):
        run = self.store.get_run(run_id)
        run['status'] = 'processing'
        self._event(run, 'extract', 'running', 'Reading the input and extracting invoice fields.')
        try:
            extracted = extract_invoices(content, run['filename'], self.settings.extraction_settings())
            if not extracted:
                raise ValueError('No invoices were found in this input.')
        except ValueError as exc:
            run.update(status='failed', error=str(exc)[:500])
            self._event(run, 'extract', 'failed', run['error'])
            return
        for index, item in enumerate(extracted):
            target = run
            if index:
                target = new_run(f"{run['filename']} · row {index + 1}",
                                 hashlib.sha256(content + str(index).encode()).hexdigest(), run_id)
                target['status'] = 'processing'
            if len(extracted) > 1:
                target['batch_size'] = len(extracted)
                target['batch_index'] = index + 1
            self.finish_invoice(target, item)

    def finish_invoice(self, run, extraction):
        raw = dict(extraction['invoice'])
        raw['extraction_method'] = extraction['method']
        raw['field_evidence'] = extraction.get('evidence', {})
        invoice = normalize_invoice(raw)
        run.update(invoice=invoice, extraction=extraction)
        self._event(run, 'extract', 'completed', f"Extracted fields using {extraction['method'].replace('_', ' ')}.")
        self._event(run, 'validate', 'running', 'Checking required fields, arithmetic, vendor identity and duplicates.')
        # The lock covers reading balances, evaluating policy, and recording approval.
        with self.store.connection(write=True) as conn:
            procurement = self.store.procurement(conn)
            prior = [r for r in self.store.runs(conn) if r['id'] != run['id'] and r['status'] == 'completed']
            result = evaluate_invoice(invoice, procurement['vendors'], procurement['purchase_orders'], prior,
                                      procurement['policy'], run['source_sha256'])
            run.update(result=result, decision=result['decision'], status='completed', original_decision=result['decision'],
                       policy_snapshot=procurement['policy'])
            validate_event = next(e for e in run['events'] if e['stage'] == 'validate')
            validate_event.update(status='completed', message='Validation complete; detailed checks are available in the decision.', at=now())
            po = result.get('matched_po')
            run['events'].extend([
                {'stage': 'match', 'status': 'completed', 'message': f"Matched {po['po_number']}." if po else 'No unambiguous eligible PO match; see decision checks.', 'at': now()},
                {'stage': 'decide', 'status': 'completed', 'message': result['summary'], 'at': now()},
                {'stage': 'output', 'status': 'completed', 'message': 'Decision saved. JSON, CSV and printable report are ready.', 'at': now()},
            ])
            self.store.save_run(run, conn)
