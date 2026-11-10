"""Durable PO intake drafts and separately audited vendor decisions."""
import csv
import hashlib
import io
import json
import queue
import threading
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator

from .po_extraction import extract_purchase_orders
from .po_policy import PO_POLICY, normalize_po, validate_po
from .po_registry import po_digest, stamp_po
from .policy import identity, reference
from .store import now

PO_EXTENSIONS = {'.json', '.csv', '.xlsx', '.pdf', '.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.txt', '.md'}
EXAMPLE_PO = {'po_number': 'PO-UPLOAD-1001', 'vendor_name': 'Sample Stationery Ltd', 'vendor_id': None,
              'po_date': '2026-11-09', 'currency': 'USD', 'subtotal': '1000.00', 'tax': '100.00', 'total': '1100.00',
              'approval_status': 'approved', 'status': 'open', 'approved_by': 'Sample purchasing manager',
              'description': 'Fictional example PO; supplier approval is a separate action.',
              'line_items': [{'description': 'Office supplies', 'quantity': '10', 'unit_price': '100.00', 'amount': '1000.00'}]}


class EditPOs(BaseModel):
    revision: int = Field(ge=0)
    purchase_orders: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class OperatorReview(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)
    comment: str = Field(min_length=3, max_length=2000)

    @field_validator('reviewer', 'comment')
    @classmethod
    def meaningful(cls, value, info):
        if len(value.strip()) < (3 if info.field_name == 'comment' else 1):
            raise ValueError('Enter a meaningful nonempty value.')
        return value.strip()


class ConfirmPOs(OperatorReview):
    revision: int = Field(ge=0)
    attest_authorized: bool = False
    allow_updates: bool = False


class VendorReview(OperatorReview):
    action: Literal['approve', 'reject']
    attest_reviewed: bool = False


def template_bytes(format):
    if format == 'json':
        return json.dumps({'purchase_orders': [EXAMPLE_PO]}, indent=2).encode(), 'application/json'
    fields = ['po_number', 'vendor_name', 'vendor_id', 'po_date', 'currency', 'subtotal', 'tax', 'total',
              'approval_status', 'status', 'approved_by', 'line_description', 'quantity', 'unit_price', 'line_amount']
    row = {key: EXAMPLE_PO.get(key, '') for key in fields}
    item = EXAMPLE_PO['line_items'][0]
    row.update(line_description=item['description'], quantity=item['quantity'], unit_price=item['unit_price'], line_amount=item['amount'])
    if format == 'csv':
        output = io.StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerow(row)
        return output.getvalue().encode(), 'text/csv'
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Purchase orders'
    sheet.append(fields)
    sheet.append([row[key] for key in fields])
    for cell in sheet[1]:
        cell.font = Font(color='FFFFFF', bold=True)
        cell.fill = PatternFill('solid', fgColor='173F3B')
        cell.alignment = Alignment(wrap_text=True, vertical='center')
    sheet.row_dimensions[1].height = 30
    for index, field in enumerate(fields, 1):
        sheet.column_dimensions[get_column_letter(index)].width = 27 if field in ('vendor_name', 'line_description', 'approved_by') else 20
        if field in ('subtotal', 'tax', 'total', 'quantity', 'unit_price', 'line_amount'):
            sheet.cell(2, index, float(row[field]))
            sheet.cell(2, index).number_format = '0.00'
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class POWorkflow:
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.queue = queue.Queue(maxsize=20)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._worker, name='po-intake-worker', daemon=True)
        self.directory = config.data_dir / 'po-sources'
        self.directory.mkdir(parents=True, exist_ok=True)

    def start(self):
        with self.store.connection(write=True) as conn:
            for upload in self.list(conn):
                if upload['status'] in ('queued', 'processing'):
                    upload.update(status='failed', error='PO extraction was interrupted by a restart. Upload the source again.')
                    self.save(upload, conn)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=3)

    def list(self, conn=None):
        if conn is None:
            with self.store.connection() as session:
                return self.list(session)
        return [json.loads(row[0]) for row in conn.execute('SELECT data FROM po_uploads ORDER BY created_at DESC')]

    def get(self, upload_id, conn=None):
        if conn is None:
            with self.store.connection() as session:
                return self.get(upload_id, session)
        row = conn.execute('SELECT data FROM po_uploads WHERE id=?', (upload_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'PO upload not found.')
        return json.loads(row[0])

    def save(self, upload, conn=None):
        if conn is None:
            with self.store.connection(write=True) as session:
                return self.save(upload, session)
        upload['updated_at'] = now()
        conn.execute('INSERT INTO po_uploads VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                     (upload['id'], upload['created_at'], json.dumps(upload, ensure_ascii=False)))

    def new(self, filename, content):
        stamp = now()
        upload = {'id': str(uuid.uuid4()), 'filename': filename, 'status': 'queued', 'created_at': stamp,
                  'updated_at': stamp, 'revision': 0, 'items': [], 'events': [], 'error': None,
                  'confirmation': None, 'review_history': [], 'source_sha256': hashlib.sha256(content).hexdigest()}
        path = self.directory / f'{upload["id"]}.bin'
        path.write_bytes(content)
        path.chmod(0o600)
        return upload

    def submit(self, inputs):
        with self.lock:
            if self.queue.qsize() + len(inputs) > self.queue.maxsize:
                raise HTTPException(429, 'PO extraction queue is full. Retry shortly.')
            result = []
            for filename, content in inputs:
                upload = self.new(filename, content)
                upload['events'].append({'stage': 'ingest', 'status': 'completed', 'message': 'PO source saved; awaiting extraction.', 'at': now()})
                self.save(upload)
                self.queue.put_nowait((upload['id'], content))
                result.append(upload)
            return result

    def _worker(self):
        while not self.stop_event.is_set():
            try:
                upload_id, content = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            upload = self.get(upload_id)
            try:
                upload['status'] = 'processing'
                upload['events'].append({'stage': 'extract', 'status': 'running', 'message': 'Extracting purchase-order fields.', 'at': now()})
                self.save(upload)
                extracted = extract_purchase_orders(content, upload['filename'], self.config.extraction_settings())
                if not extracted or len(extracted) > 100:
                    raise ValueError('Provide between one and 100 purchase orders.')
                upload['items'] = [{'purchase_order': normalize_po(item['purchase_order']),
                                    'extraction': {key: item.get(key) for key in ('method', 'evidence', 'warnings', 'raw_text')}} for item in extracted]
                self.refresh(upload, self.store.procurement())
                upload.update(status='ready', revision=1)
                upload['events'][-1].update(status='completed', message=f'Extracted {len(extracted)} PO(s). Review fields and authorization before importing.', at=now())
                upload['events'].append({'stage': 'validate', 'status': 'completed', 'message': 'Required-field and arithmetic checks complete; no vendor approval was changed.', 'at': now()})
                self.save(upload)
            except Exception as exc:
                # Never reflect provider payloads or tracebacks containing credentials.
                safe = str(exc)[:500] if isinstance(exc, ValueError) else 'PO extraction could not complete. Check the source and retry.'
                upload.update(status='failed', error=safe)
                upload['events'].append({'stage': 'extract', 'status': 'failed', 'message': safe, 'at': now()})
                self.save(upload)
            finally:
                self.queue.task_done()

    def refresh(self, upload, procurement):
        seen = set()
        for item in upload['items']:
            po = item['purchase_order']
            validation = validate_po(po)
            item['validation'] = validation
            def problem(code, field, message):
                validation['checks'].append({'code': code, 'field': field, 'label': 'Registry consistency', 'status': 'fail', 'message': message, 'blocking': True})
                validation['valid'] = validation['eligible'] = False
            key = reference(po.get('po_number'))
            if key in seen:
                problem('duplicate_po', 'po_number', 'This upload contains repeated PO numbers. Merge their line items into one record.')
            seen.add(key)
            vendors = procurement['vendors']
            vendor_id, name = reference(po.get('vendor_id')), identity(po.get('vendor_name'))
            matches = [v for v in vendors if reference(v['id']) == vendor_id] if vendor_id else [v for v in vendors if name in {identity(v['name']), *(identity(a) for a in v.get('aliases', []))}]
            if len(matches) > 1:
                problem('ambiguous_vendor', 'vendor_name', 'More than one vendor matches. Supply a unique registered vendor ID.')
            elif matches:
                if name not in {identity(matches[0]['name']), *(identity(a) for a in matches[0].get('aliases', []))}:
                    problem('vendor_identity', 'vendor_name', 'Vendor name conflicts with the registered vendor ID.')
            elif vendor_id and any(name in {identity(v['name']), *(identity(a) for a in v.get('aliases', []))} for v in vendors):
                problem('vendor_identity', 'vendor_id', 'This name already belongs to another registered vendor ID.')
            old = [p for p in procurement['purchase_orders'] if reference(p['po_number']) == key]
            if len(old) > 1:
                problem('ambiguous_po', 'po_number', 'Multiple existing PO records share this reference. Resolve them before importing.')
            item['existing_po'] = old[0]['po_number'] if len(old) == 1 else None
            item['vendor_status'] = ('approved' if matches[0].get('approved') else matches[0].get('approval_status', 'rejected')) if len(matches) == 1 else 'new_pending'

    @staticmethod
    def editable(upload, revision):
        if upload['status'] != 'ready':
            raise HTTPException(409, 'Only a ready, unconfirmed PO draft can be changed.')
        if upload['revision'] != revision:
            raise HTTPException(409, 'This draft changed. Reload it and review the latest version.')

    def router(self):
        router = APIRouter(prefix='/api/procurement', tags=['Purchase order intake'])

        @router.get('/po-policy')
        def policy():
            return PO_POLICY

        @router.get('/template')
        def template(format: Literal['csv', 'xlsx', 'json'] = 'csv'):
            content, media = template_bytes(format)
            return Response(content, media_type=media, headers={'Content-Disposition': f'attachment; filename="purchase-order-template.{format}"'})

        @router.get('/uploads')
        def uploads():
            return {'uploads': self.list()[:50]}

        @router.post('/uploads', status_code=202)
        async def upload(files: list[UploadFile] = File(...)):
            if not 1 <= len(files) <= 5:
                raise HTTPException(400, 'Upload between one and five PO files.')
            inputs = []
            for file in files:
                name = Path((file.filename or '').replace('\\', '/')).name[:180]
                if Path(name).suffix.lower() not in PO_EXTENSIONS:
                    raise HTTPException(415, 'Use CSV, XLSX, JSON, PDF, PNG, JPEG, WEBP, TIFF or text for POs.')
                content = await file.read(self.config.max_file_bytes + 1)
                await file.close()
                if not content or len(content) > self.config.max_file_bytes:
                    raise HTTPException(413, 'Each PO file must be nonempty and at most 10 MB.')
                inputs.append((name, content))
            return {'uploads': self.submit(inputs)}

        @router.get('/uploads/{upload_id}')
        def detail(upload_id: str):
            return self.get(upload_id)

        @router.get('/uploads/{upload_id}/source')
        def source(upload_id: str):
            draft = self.get(upload_id)
            path = self.directory / f'{draft["id"]}.bin'
            if not path.is_file():
                raise HTTPException(404, 'Source is unavailable.')
            return FileResponse(path, media_type='application/octet-stream', filename=draft['filename'])

        @router.post('/uploads/{upload_id}/validate')
        def validate(upload_id: str, body: EditPOs):
            with self.store.connection(write=True) as conn:
                draft = self.get(upload_id, conn)
                self.editable(draft, body.revision)
                previous = draft['items']
                draft['items'] = [{'purchase_order': normalize_po({k: v for k, v in raw.items() if k != 'normalization_warnings'}),
                                   'extraction': previous[index]['extraction'] if index < len(previous) else {'method': 'manual', 'evidence': {}, 'warnings': [], 'raw_text': ''}}
                                  for index, raw in enumerate(body.purchase_orders)]
                self.refresh(draft, self.store.procurement(conn))
                draft['revision'] += 1
                draft['review_history'].append({'action': 'edit_preview', 'at': now(), 'revision': draft['revision']})
                self.save(draft, conn)
                return draft

        @router.post('/uploads/{upload_id}/confirm')
        def confirm(upload_id: str, body: ConfirmPOs):
            if not body.attest_authorized:
                raise HTTPException(422, 'Confirm that you checked the PO fields and its purchasing authorization status against an independent source.')
            with self.store.connection(write=True) as conn:
                draft = self.get(upload_id, conn)
                self.editable(draft, body.revision)
                procurement = self.store.procurement(conn)
                self.refresh(draft, procurement)
                invalid = [item for item in draft['items'] if not item['validation']['valid']]
                if invalid:
                    reasons = [check['message'] for item in invalid for check in item['validation']['checks'] if check.get('blocking') and check['status'] != 'pass']
                    raise HTTPException(422, 'PO import blocked: ' + ' '.join(reasons[:5]))
                vendors = procurement['vendors'][:]
                registered, created_vendors = [], []
                for item in draft['items']:
                    po = dict(item['purchase_order'])
                    vendor_id, name = reference(po.get('vendor_id')), identity(po['vendor_name'])
                    matches = [v for v in vendors if reference(v['id']) == vendor_id] if vendor_id else [v for v in vendors if name in {identity(v['name']), *(identity(a) for a in v.get('aliases', []))}]
                    if matches:
                        vendor = matches[0]
                        if name not in {identity(vendor['name']), *(identity(a) for a in vendor.get('aliases', []))}:
                            raise HTTPException(409, 'The upload assigns conflicting names to the same vendor ID.')
                    else:
                        if any(name in {identity(v['name']), *(identity(a) for a in v.get('aliases', []))} for v in vendors):
                            raise HTTPException(409, 'The upload assigns conflicting IDs to the same vendor name.')
                        vendor = {'id': po.get('vendor_id') or 'vendor-' + uuid.uuid4().hex[:12], 'name': po['vendor_name'], 'aliases': [],
                                  'approved': False, 'approval_status': 'pending', 'created_from_po_upload': upload_id, 'approval_history': []}
                        vendors.append(vendor)
                        created_vendors.append(vendor)
                    po['vendor_id'] = vendor['id']
                    old = next((p for p in procurement['purchase_orders'] if reference(p['po_number']) == reference(po['po_number'])), None)
                    if old:
                        po['po_number'] = old['po_number']
                        if po_digest(old) != po_digest(po) and not body.allow_updates:
                            raise HTTPException(409, f'{po["po_number"]} already exists with different values. Review and explicitly allow the update.')
                        committed = Decimal(old['committed'])
                        if committed > 0 and (old['vendor_id'] != po['vendor_id'] or old['currency'] != po['currency']):
                            raise HTTPException(409, 'A PO with approved invoice commitments cannot change vendor or currency.')
                        if Decimal(po['total']) < committed:
                            raise HTTPException(409, f'PO total cannot fall below its existing {committed} commitment.')
                        from .po_matching import line_usage, po_line_options, number
                        _, used, unresolved, _ = line_usage(old, self.store.runs(conn))
                        proposed = {o['ref']: number(o['quantity']) for o in po_line_options(po)}
                        if unresolved and old.get('line_items') != po.get('line_items'):
                            raise HTTPException(409, 'Resolve prior invoice product allocations before changing PO products.')
                        for ref, quantity in used.items():
                            if quantity > 0 and (proposed.get(ref) is None or proposed[ref] < quantity):
                                raise HTTPException(409, 'A PO product cannot be removed, reordered, renamed, or reduced below its already invoiced quantity.')
                    stamp_po(po, body.reviewer, now(), upload_id, body.comment)
                    registered.append((old, po))
                for vendor in created_vendors:
                    conn.execute('INSERT INTO vendors VALUES (?,?)', (vendor['id'], json.dumps(vendor)))
                for old, po in registered:
                    conn.execute('INSERT INTO purchase_orders VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (po['po_number'], json.dumps(po)))
                    conn.execute('INSERT INTO procurement_audit(at,data) VALUES (?,?)', (now(), json.dumps({'action': 'confirm_po', 'reviewer': body.reviewer, 'comment': body.comment, 'upload_id': upload_id, 'previous': old, 'purchase_order': po})))
                draft['confirmation'] = {'reviewer': body.reviewer, 'comment': body.comment, 'at': now(), 'po_numbers': [p['po_number'] for _, p in registered]}
                draft.update(status='confirmed', revision=draft['revision'] + 1)
                draft['events'].append({'stage': 'confirm', 'status': 'completed', 'message': 'POs registered. Vendor approvals were not changed; new vendors require separate review.', 'at': now()})
                self.save(draft, conn)
                return {'upload': draft, 'procurement': self.store.procurement(conn)}

        @router.post('/purchase-orders/{po_number:path}/review')
        def review_registered(po_number: str):
            with self.store.connection(write=True) as conn:
                procurement = self.store.procurement(conn)
                candidates = [p for p in procurement['purchase_orders'] if p['po_number'] == po_number]
                if not candidates:
                    raise HTTPException(404, 'Purchase order not found.')
                po = normalize_po(candidates[0])
                content = json.dumps({'purchase_orders': [po]}, indent=2).encode()
                draft = self.new('registered-purchase-order.json', content)
                draft.update(status='ready', revision=1, items=[{'purchase_order': po, 'extraction': {'method': 'registered_record', 'evidence': {}, 'warnings': ['This source is the registered record snapshot. Confirm missing details against your original purchasing authorization.'], 'raw_text': content.decode()}}])
                draft['review_of_po'] = po_number
                draft['prior_po_upload_id'] = (candidates[0].get('_validation') or {}).get('upload_id')
                self.refresh(draft, procurement)
                self.save(draft, conn)
                return draft

        @router.post('/vendors/{vendor_id:path}/review')
        def review_vendor(vendor_id: str, body: VendorReview):
            if body.action == 'approve' and not body.attest_reviewed:
                raise HTTPException(422, 'Confirm that you reviewed the supplier independently of its uploaded PO.')
            with self.store.connection(write=True) as conn:
                row = conn.execute('SELECT data FROM vendors WHERE id=?', (vendor_id,)).fetchone()
                if not row:
                    raise HTTPException(404, 'Vendor not found.')
                vendor = json.loads(row[0])
                event = {'action': body.action, 'reviewer': body.reviewer, 'comment': body.comment, 'at': now(), 'previous_approved': vendor.get('approved')}
                vendor.update(approved=body.action == 'approve', approval_status='approved' if body.action == 'approve' else 'rejected')
                vendor.setdefault('approval_history', []).append(event)
                conn.execute('UPDATE vendors SET data=? WHERE id=?', (json.dumps(vendor), vendor_id))
                conn.execute('INSERT INTO procurement_audit(at,data) VALUES (?,?)', (now(), json.dumps({'action': 'vendor_review', 'vendor_id': vendor_id, **event})))
                return {'vendor': vendor, 'procurement': self.store.procurement(conn)}

        return router
