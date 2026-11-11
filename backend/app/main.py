"""LedgerFlow local API and static application server."""
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
import json
import shutil
import uuid

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import ROOT, Settings
from .exports import as_csv, as_html, as_json
from .models import decimal_value, normalize_invoice
from .middleware import RequestBodyLimit
from .pipeline import Pipeline
from .policy import evaluate_invoice
from .store import Store, now
from .po_workflow import POWorkflow
from .run_documents import document_router
from .calculation_api import calculation_router
from .invoice_review import review_router, correction_invoice, field_changes
from .models import invoice_fingerprint
from .access import access_error
from .invoice_deletion import deletion_router

SUPPORTED = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.json', '.csv', '.txt', '.md', '.eml'}
SCENARIOS = [
    {'id': 'happy_path', 'title': 'Clean invoice', 'description': 'An approved vendor, a clear PO and matching totals. Ready for approval.'},
    {'id': 'duplicate', 'title': 'Duplicate invoice', 'description': 'Two submissions share a vendor and invoice number. The second is blocked.'},
    {'id': 'split_overage', 'title': 'Split PO overage', 'description': 'Two individually small invoices exceed their shared PO when combined.'},
    {'id': 'ambiguous_po', 'title': 'Ambiguous PO', 'description': 'A valid vendor has two open POs and the invoice has no reference.'},
    {'id': 'missing_fields', 'title': 'Incomplete invoice', 'description': 'Missing identity and conflicting totals require correction and review.'},
]


class TextInput(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    filename: str = 'invoice.txt'


class StructuredInput(BaseModel):
    invoice: dict[str, Any]


class ReviewInput(BaseModel):
    action: Literal['approve', 'reject']
    reviewer: str = Field(min_length=1, max_length=100)
    comment: str = Field(min_length=3, max_length=2000)
    po_number: str | None = Field(default=None, max_length=120)

    @field_validator('reviewer', 'comment')
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError('Enter a nonempty value')
        return value.strip()


class CorrectionInput(BaseModel):
    invoice: dict[str, Any]
    expected_invoice_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=1, max_length=100)
    comment: str = Field(min_length=3, max_length=2000)

    @field_validator('reviewer', 'comment')
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError('Enter a nonempty value')
        return value.strip()


class PolicyInput(BaseModel):
    tolerance_percent: str = '2'
    tolerance_absolute: str = '5'
    min_confidence: float = Field(default=0.75, ge=0.75, le=1)

    @field_validator('tolerance_percent', 'tolerance_absolute')
    @classmethod
    def amount(cls, value, info):
        number = decimal_value(value, allow_negative=False)
        maximum = Decimal('20') if info.field_name == 'tolerance_percent' else Decimal('1000')
        if number > maximum:
            raise ValueError(f'Must not exceed {maximum}')
        return str(number)


class ProcurementInput(BaseModel):
    vendors: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)
    purchase_orders: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)


def filename_safe(name):
    name = Path(name.replace('\\', '/')).name[:180]
    if not name or Path(name).suffix.lower() not in SUPPORTED:
        raise HTTPException(415, 'Unsupported format. Use PDF, images, JSON, CSV, text, Markdown or EML.')
    return name


def create_app(settings: Settings | None = None):
    config = settings or Settings.from_env()
    store = Store(config.data_dir / 'ledgerflow.sqlite3', database_url=config.database_url)
    pipeline = Pipeline(store, config)
    po_workflow = POWorkflow(store, config)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        pipeline.start()
        po_workflow.start()
        yield
        pipeline.stop()
        po_workflow.stop()

    app = FastAPI(title='LedgerFlow Invoice API', version='1.0.0', lifespan=lifespan)
    app.state.store, app.state.pipeline, app.state.settings = store, pipeline, config
    app.state.po_workflow = po_workflow
    app.include_router(po_workflow.router())
    app.include_router(document_router(store, config))
    app.include_router(calculation_router(store, config))
    app.include_router(review_router(store))
    app.include_router(deletion_router(store, pipeline, config))
    from urllib.parse import urlsplit
    allowed_hosts = ['localhost', '127.0.0.1', '[::1]', 'testserver']
    if config.public_origin:
        allowed_hosts.append(urlsplit(config.public_origin).hostname)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.add_middleware(RequestBodyLimit)

    @app.middleware('http')
    async def workspace_boundary(request: Request, call_next):
        length = request.headers.get('content-length', '0')
        try:
            if int(length) > 50 * 1024 * 1024:
                return JSONResponse({'detail': 'Request exceeds 50 MB. Submit smaller batches.'}, 413)
        except ValueError:
            return JSONResponse({'detail': 'Invalid Content-Length.'}, 400)
        response = access_error(request, config)
        if response is None:
            response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        if config.public_origin or config.auth_password or request.url.path.startswith('/api'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def require_run(run_id, conn=None):
        run = store.get_run(run_id, conn)
        if not run:
            raise HTTPException(404, 'Run not found.')
        return run

    def submit(inputs):
        try:
            return {'runs': pipeline.submit(inputs)}
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'version': '1.0.0', 'openrouter_configured': bool(config.openrouter_api_key),
                'openrouter_enabled': config.enable_openrouter, 'model': config.openrouter_model,
                'ocr_available': bool(shutil.which('tesseract')),
                'storage_mode': 'postgres' if store.remote else 'local', 'hosted': bool(config.public_origin)}

    @app.api_route('/healthz', methods=['GET', 'HEAD'], include_in_schema=False)
    def readiness():
        with store.connection() as conn:
            conn.execute('SELECT 1').fetchone()
        return {'status': 'ok'}

    @app.get('/api/dashboard')
    def dashboard():
        runs = store.runs()
        totals = {}
        for run in runs:
            if run['decision'] == 'approved':
                currency = run['invoice']['currency']
                totals[currency] = totals.get(currency, Decimal('0')) + Decimal(run['invoice']['total'])
        return {'total_runs': len(runs),
                **{d: sum(r['decision'] == d for r in runs) for d in ('approved', 'needs_review', 'rejected')},
                'failed': sum(r['status'] == 'failed' for r in runs),
                'processing': sum(r['status'] in ('queued', 'processing') for r in runs),
                'total_approved_by_currency': {k: str(v) for k, v in totals.items()}, 'recent_runs': runs[:8]}

    @app.get('/api/runs')
    def list_runs(status: str | None = None, search: str = ''):
        runs = store.runs()
        if status:
            runs = [r for r in runs if r['decision'] == status or r['status'] == status]
        if search:
            query = search.casefold()
            runs = [r for r in runs if query in json.dumps({k: r.get(k) for k in ('filename', 'invoice', 'id')}).casefold()]
        return {'runs': runs}

    # Static export path must be registered before the variable run path.
    @app.get('/api/runs/export')
    def export_runs(format: Literal['json', 'csv'] = 'csv'):
        runs = store.runs()
        content = as_csv(runs) if format == 'csv' else as_json(runs)
        media = 'text/csv' if format == 'csv' else 'application/json'
        return Response(content, media_type=media, headers={'Content-Disposition': f'attachment; filename="ledgerflow-runs.{format}"'})

    @app.get('/api/runs/{run_id}')
    def get_run(run_id: str):
        return require_run(run_id)

    @app.post('/api/runs/upload', status_code=202)
    async def upload(files: list[UploadFile] = File(...)):
        if not 1 <= len(files) <= 10:
            raise HTTPException(400, 'Upload between one and ten files.')
        inputs = []
        for file in files:
            name = filename_safe(file.filename or '')
            content = await file.read(config.max_file_bytes + 1)
            await file.close()
            if len(content) > config.max_file_bytes:
                raise HTTPException(413, f'{name} exceeds the 10 MB per-file limit.')
            if not content:
                raise HTTPException(400, f'{name} is empty.')
            inputs.append((name, content))
        return submit(inputs)

    @app.post('/api/runs/text', status_code=202)
    def input_text(body: TextInput):
        return submit([(filename_safe(body.filename), body.text.encode('utf-8'))])

    @app.post('/api/runs/structured', status_code=202)
    def input_structured(body: StructuredInput):
        return submit([('invoice.json', json.dumps(body.invoice).encode())])

    @app.post('/api/runs/{run_id}/review')
    def review(run_id: str, body: ReviewInput):
        with store.connection(write=True) as conn:
            run = require_run(run_id, conn)
            if run['status'] != 'completed' or not run.get('invoice'):
                raise HTTPException(409, 'Only completed invoice runs can be reviewed.')
            if run.get('superseded_by'):
                raise HTTPException(409, 'This run was superseded. Review its corrected run instead.')
            if body.action == 'approve' and run['decision'] == 'approved':
                raise HTTPException(409, 'This invoice is already approved.')
            previous = run['decision']
            if body.action == 'approve':
                invoice = dict(run['invoice'])
                if body.po_number:
                    invoice['po_number'] = body.po_number.strip()
                procurement = store.procurement(conn, exclude_run=run_id)
                prior = [r for r in store.runs(conn) if r['id'] != run_id and r['status'] == 'completed']
                result = evaluate_invoice(invoice, procurement['vendors'], procurement['purchase_orders'], prior,
                                          procurement['policy'], run['source_sha256'])
                blocking = [c for c in result['checks'] if c.get('blocking') and c['status'] != 'pass']
                if blocking:
                    raise HTTPException(409, 'Approval blocked: ' + ' '.join(c['message'] for c in blocking))
                result.update(decision='approved', summary=f'Approved after human review by {body.reviewer}. {body.comment}')
                run.setdefault('original_invoice', run['invoice'])
                run.update(invoice=invoice, result=result, decision='approved', policy_snapshot=procurement['policy'])
            else:
                run['result'] = {**run['result'], 'decision': 'rejected', 'summary': f'Rejected by {body.reviewer}. {body.comment}'}
                run['decision'] = 'rejected'
            run['reviews'].append({**body.model_dump(), 'at': now(), 'previous_decision': previous})
            run['events'].append({'stage': 'review', 'status': 'completed', 'message': run['result']['summary'], 'at': now()})
            store.save_run(run, conn)
        return run

    @app.post('/api/runs/{run_id}/correct', status_code=202)
    def correct(run_id: str, body: CorrectionInput):
        # Create a queued replacement under the same queue lock used by submit.
        from .pipeline import new_run
        import hashlib
        content = json.dumps(body.invoice).encode()
        if len(content) > 100_000:
            raise HTTPException(413, 'Corrected invoice is too large.')
        # Normalize and validate one invoice before superseding the original.
        corrected_snapshot = correction_invoice(body.invoice)
        with pipeline.lock:
            if pipeline.queue.full():
                raise HTTPException(429, 'Processing queue is full; retry shortly.')
            with store.connection(write=True) as conn:
                original = require_run(run_id, conn)
                if original['status'] != 'completed' or original['decision'] == 'approved' or original.get('superseded_by'):
                    raise HTTPException(409, 'Correct an unsuperseded review/rejected run. Reject an approved invoice before replacing it.')
                if body.expected_invoice_fingerprint and body.expected_invoice_fingerprint != invoice_fingerprint(original['invoice']):
                    raise HTTPException(409, 'The invoice changed after this review was opened. Reload before submitting corrections.')
                replacement = new_run('corrected-invoice.json', hashlib.sha256(content).hexdigest(), run_id)
                pipeline.save_source(replacement['id'], content)
                replacement['correction_of'] = run_id
                audit = {'action': 'correct', 'reviewer': body.reviewer, 'comment': body.comment, 'at': now(),
                         'changes': field_changes(original['invoice'], corrected_snapshot)}
                replacement['reviews'] = [audit]
                original.setdefault('original_result', original['result'])
                original['decision'] = 'rejected'
                original['superseded_by'] = replacement['id']
                original['result'] = {**original['result'], 'decision': 'rejected', 'summary': 'Superseded by a human-verified correction; see linked run.'}
                original['reviews'].append(audit)
                store.save_run(original, conn)
                store.save_run(replacement, conn)
            pipeline.queue.put_nowait((replacement['id'], content))
        return {'runs': [replacement]}

    @app.get('/api/runs/{run_id}/source')
    def source(run_id: str):
        run = require_run(run_id)
        path = store.source_path('sources', run['id'])
        while path is None and run.get('parent_id'):
            run = require_run(run['parent_id'])
            path = store.source_path('sources', run['id'])
        if path is None:
            raise HTTPException(404, 'Original source is unavailable for this run.')
        return FileResponse(path, media_type='application/octet-stream', filename=run['filename'])

    @app.get('/api/runs/{run_id}/export')
    def export_run(run_id: str, format: Literal['json', 'csv', 'html'] = 'json'):
        run = require_run(run_id)
        content = as_json(run) if format == 'json' else as_csv([run]) if format == 'csv' else as_html(run)
        media = {'json': 'application/json', 'csv': 'text/csv', 'html': 'text/html'}[format]
        return Response(content, media_type=media, headers={'Content-Disposition': f'attachment; filename="decision-{run_id}.{format}"'})

    @app.get('/api/procurement')
    def procurement():
        return store.procurement()

    @app.post('/api/procurement/import')
    def import_procurement(body: ProcurementInput):
        with store.connection(write=True) as conn:
            existing = store.procurement(conn)
            vendors = {v['id']: v for v in existing['vendors']}
            try:
                for vendor in body.vendors:
                    # Legacy imports cannot impersonate an independent vendor review.
                    old_vendor = vendors.get(vendor.get('id'))
                    vendor_keys = {'id', 'name', 'aliases', 'approved'}
                    vendor = {k: v for k, v in vendor.items() if k in vendor_keys}
                    if old_vendor:
                        vendor['approval_history'] = old_vendor.get('approval_history', [])
                        vendor['approval_status'] = 'approved' if vendor.get('approved') else old_vendor.get('approval_status', 'rejected')
                    if not isinstance(vendor.get('id'), str) or not vendor['id'].strip() or len(vendor['id']) > 120:
                        raise ValueError('Each vendor needs a nonempty id (up to 120 characters).')
                    if not isinstance(vendor.get('name'), str) or not vendor['name'].strip():
                        raise ValueError('Each vendor needs a name.')
                    if not isinstance(vendor.get('approved'), bool):
                        raise ValueError('Each vendor needs an approved boolean.')
                    if old_vendor and vendor['approved'] != old_vendor.get('approved'):
                        raise ValueError('Change vendor approval using the separate vendor review action with a reviewer and reason.')
                    if not isinstance(vendor.get('aliases', []), list) or any(not isinstance(x, str) for x in vendor.get('aliases', [])):
                        raise ValueError('Vendor aliases must be strings.')
                    if old_vendor and old_vendor.get('approval_status') != 'pending':
                        from .policy import identity
                        old_identity = (identity(old_vendor['name']), {identity(alias) for alias in old_vendor.get('aliases', [])})
                        new_identity = (identity(vendor['name']), {identity(alias) for alias in vendor.get('aliases', [])})
                        if old_identity != new_identity:
                            raise ValueError('An approved or rejected vendor cannot change identity through import. Create a new pending vendor record and review it separately.')
                    if not old_vendor:
                        vendor.update(approved=False, approval_status='pending', approval_history=[])
                    vendors[vendor['id']] = vendor
                existing_pos = {po['po_number']: po for po in existing['purchase_orders']}
                pos = []
                for incoming in body.purchase_orders:
                    po = dict(incoming)
                    for key in ('_validation', 'validation_status', 'eligibility_status', 'validated_at', 'reviewer', 'validation_issues', 'validation_history'):
                        po.pop(key, None)
                    if not isinstance(po.get('po_number'), str) or not po['po_number'].strip() or len(po['po_number']) > 120:
                        raise ValueError('Each PO needs a po_number (up to 120 characters).')
                    if po.get('vendor_id') not in vendors:
                        raise ValueError('Each PO must reference an existing vendor_id.')
                    currency = po.get('currency', '')
                    if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha():
                        raise ValueError('PO currency must be a three-letter code.')
                    po['currency'] = currency.upper()
                    if po.get('status') not in ('open', 'closed'):
                        raise ValueError('PO status must be open or closed.')
                    amount = decimal_value(po.get('total'), allow_negative=False)
                    if amount <= 0:
                        raise ValueError('PO total must be positive.')
                    po['total'] = str(amount)
                    old = existing_pos.get(po['po_number'])
                    # Unchanged records retain server-owned provenance; changing a
                    # legacy JSON record requires a new PO review before use.
                    if old:
                        from .po_registry import po_digest
                        if po_digest(old) == po_digest(po) and old.get('_validation'):
                            po['_validation'] = old['_validation']
                    if old and Decimal(old['committed']) > 0:
                        if old['vendor_id'] != po['vendor_id'] or old['currency'] != po['currency']:
                            raise ValueError('A committed PO cannot change vendor or currency.')
                        if amount < Decimal(old['committed']):
                            raise ValueError('A PO total cannot be reduced below its approved commitments.')
                    if 'committed' in po and decimal_value(po['committed']) != Decimal(old['committed'] if old else '0'):
                        raise ValueError('Committed amounts are calculated from approved runs and cannot be imported.')
                    po.pop('committed', None)
                    if not isinstance(po.get('line_items', []), list) or any(not isinstance(item, dict) for item in po.get('line_items', [])):
                        raise ValueError('PO line_items must be an array of objects.')
                    seen_skus = set()
                    for item in po.get('line_items', []):
                        sku = item.get('sku')
                        if sku is not None:
                            if not isinstance(sku, str) or not sku.strip():
                                raise ValueError('PO line SKU must be nonempty text.')
                            from .policy import identity
                            normalized_sku = identity(sku)
                            if normalized_sku in seen_skus:
                                raise ValueError('PO line SKUs must be unique.')
                            seen_skus.add(normalized_sku)
                            if item.get('unit_price') is None:
                                raise ValueError('A PO line with a SKU needs a unit_price for comparison.')
                        for key in ('quantity', 'unit_price', 'amount'):
                            if key in item and item[key] is not None:
                                number = decimal_value(item[key], allow_negative=False)
                                if key == 'quantity' and number <= 0:
                                    raise ValueError('PO line quantity must be positive.')
                                item[key] = str(number)
                    pos.append(po)
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, str(exc)) from exc
            for vendor in body.vendors:
                vendor = vendors[vendor['id']]
                conn.execute('INSERT INTO vendors VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (vendor['id'], json.dumps(vendor)))
            for po in pos:
                conn.execute('INSERT INTO purchase_orders VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (po['po_number'], json.dumps(po)))
            return store.procurement(conn)

    @app.put('/api/policy')
    def update_policy(body: PolicyInput):
        with store.connection(write=True) as conn:
            conn.execute("UPDATE settings SET data=? WHERE id='policy'", (body.model_dump_json(),))
        return body.model_dump()

    @app.get('/api/demo')
    def demo_scenarios():
        return {'scenarios': SCENARIOS}

    @app.post('/api/demo/{scenario}', status_code=202)
    def demo(scenario: str):
        if scenario not in {s['id'] for s in SCENARIOS}:
            raise HTTPException(404, 'Demo scenario not found.')
        suffix = uuid.uuid4().hex[:8].upper()
        vendor_id, po_number = f'demo-{suffix}', f'DEMO-PO-{suffix}'
        vendor = {'id': vendor_id, 'name': f'Demo Cloud Services {suffix}', 'aliases': [], 'approved': True}
        po = {'po_number': po_number, 'vendor_id': vendor_id, 'currency': 'USD', 'total': '1000' if scenario == 'split_overage' else '12000', 'status': 'open', 'description': f'Demo: {scenario}'}
        po['line_items'] = [{'description': 'Managed cloud services', 'quantity': '10' if scenario == 'split_overage' else '12',
                             'unit_price': '100' if scenario == 'split_overage' else '1000', 'amount': po['total']}]
        from .po_registry import fixture_po
        po = fixture_po(po, vendor['name'])
        with store.connection(write=True) as conn:
            conn.execute('INSERT INTO vendors VALUES (?,?)', (vendor_id, json.dumps(vendor)))
            conn.execute('INSERT INTO purchase_orders VALUES (?,?)', (po_number, json.dumps(po)))
            if scenario == 'ambiguous_po':
                alternative = {**po, 'po_number': po_number + '-B'}
                alternative = fixture_po(alternative, vendor['name'])
                conn.execute('INSERT INTO purchase_orders VALUES (?,?)', (alternative['po_number'], json.dumps(alternative)))
        invoice = {'invoice_number': f'DEMO-INV-{suffix}', 'vendor_name': vendor['name'], 'po_number': po_number,
                   'invoice_date': '2026-11-09', 'currency': 'USD', 'subtotal': '1000', 'tax': '100', 'total': '1100',
                   'line_items': [{'description': 'Managed cloud services', 'quantity': '1', 'unit_price': '1000', 'amount': '1000'}]}
        invoices = [invoice]
        if scenario == 'duplicate':
            invoices.append(dict(invoice))
        elif scenario == 'split_overage':
            invoice.update(subtotal='600', tax='0', total='600', line_items=[{'description':'Managed cloud services','quantity':'6','unit_price':'100','amount':'600'}])
            invoices.append({**invoice, 'invoice_number': invoice['invoice_number'] + '-B', 'subtotal': '500', 'total': '500',
                             'line_items':[{'description':'Managed cloud services','quantity':'5','unit_price':'100','amount':'500'}]})
        elif scenario == 'ambiguous_po':
            invoice.pop('po_number')
        elif scenario == 'missing_fields':
            invoice.pop('invoice_number')
            invoice['total'] = '1250'
        return submit([(f'{scenario}-{i+1}.json', json.dumps(inv).encode()) for i, inv in enumerate(invoices)])

    dist = ROOT / 'frontend' / 'dist'
    if dist.is_dir():
        if (dist / 'assets').is_dir():
            app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

        @app.get('/favicon.svg')
        def favicon():
            return FileResponse(dist / 'favicon.svg', media_type='image/svg+xml')

        @app.get('/')
        def index():
            return FileResponse(dist / 'index.html')
    else:
        @app.get('/')
        def index():
            return {'message': 'API is running. Build frontend with npm --prefix frontend run build, or start the Vite development server.', 'docs': '/docs'}
    return app


app = create_app()
