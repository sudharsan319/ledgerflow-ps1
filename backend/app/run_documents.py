"""Read-only access to an invoice's original source and matched-PO evidence."""
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from .document_preview import describe_document, render_document_page
from .po_documents import PODocuments


def document_router(store, settings):
    router = APIRouter(tags=['Run documents'])
    po_documents = PODocuments(store, settings)

    def require_run(run_id):
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, 'Run not found.')
        return run

    def source_path(directory: str, source_id: str):
        # Stored provenance selects a record, never an arbitrary filesystem path.
        try:
            identifier = str(UUID(str(source_id)))
        except (ValueError, TypeError, AttributeError):
            return None
        return store.source_path(directory, identifier)

    def run_source(run, *, original=False):
        visited = set()
        for _ in range(100):
            if not run or run['id'] in visited:
                return None
            visited.add(run['id'])
            if original and run.get('correction_of'):
                # Corrected JSON is available separately; it is not the uploaded invoice.
                run = store.get_run(run['correction_of'])
                continue
            path = source_path('sources', run['id'])
            if path:
                return run, path
            run = store.get_run(run['parent_id']) if run.get('parent_id') else None
        return None

    def require_run_source(run_id):
        result = run_source(require_run(run_id))
        if result is None:
            raise HTTPException(404, 'The retained source file is unavailable for this run.')
        return result

    def po_upload(upload_id):
        with store.connection() as conn:
            row = conn.execute('SELECT data FROM po_uploads WHERE id=?', (upload_id,)).fetchone()
        if not row:
            return None
        import json
        return json.loads(row[0])

    def require_po_source(upload_id):
        upload = po_upload(upload_id)
        if not upload:
            raise HTTPException(404, 'PO upload not found.')
        path = source_path('po-sources', upload['id'])
        if not path:
            raise HTTPException(404, 'The retained PO source file is unavailable.')
        return upload, path

    def source_info(record, *, po=False, role='original_upload'):
        prefix = '/api/procurement/uploads' if po else '/api/runs'
        source_url = f'{prefix}/{record["id"]}/source'
        return {'filename': record['filename'], 'source_id': record['id'], 'source_role': role,
                'preview_url': source_url + '/preview', 'download_url': source_url,
                'source_sha256': record.get('source_sha256')}

    def metadata(record, path: Path, *, po=False):
        info = source_info(record, po=po)
        return {**describe_document(path, record['filename']),
                'page_url': info['preview_url'] + '/page', 'download_url': info['download_url']}

    def image_page(record, path, page):
        content = render_document_page(path, record['filename'], page)
        return Response(content, media_type='image/png', headers={
            'Content-Disposition': 'inline; filename="document-page.png"',
            'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'no-store',
        })

    @router.get('/api/runs/{run_id}/source/preview')
    def invoice_preview(run_id: str):
        record, path = require_run_source(run_id)
        return metadata(record, path)

    @router.get('/api/runs/{run_id}/source/preview/page')
    def invoice_page(run_id: str, page: int = Query(default=0, ge=0)):
        record, path = require_run_source(run_id)
        return image_page(record, path, page)

    @router.get('/api/procurement/uploads/{upload_id}/source/preview')
    def po_preview(upload_id: str):
        record, path = require_po_source(upload_id)
        return metadata(record, path, po=True)

    @router.get('/api/procurement/uploads/{upload_id}/source/preview/page')
    def po_page(upload_id: str, page: int = Query(default=0, ge=0)):
        record, path = require_po_source(upload_id)
        return image_page(record, path, page)

    @router.get('/api/procurement/purchase-orders/{po_number:path}/documents')
    def registered_po_documents(po_number: str):
        with store.connection() as conn:
            po = next((item for item in store.procurement(conn)['purchase_orders'] if item['po_number'] == po_number), None)
            if po is None:
                raise HTTPException(404, 'Purchase order not found.')
            return {'po_number': po_number, **po_documents.for_po(po, conn)}

    @router.get('/api/procurement/uploads/{upload_id}/documents')
    def upload_documents(upload_id: str):
        with store.connection() as conn:
            if po_documents.get_upload(upload_id, conn) is None:
                raise HTTPException(404, 'PO upload not found.')
            return {'upload_id': upload_id, **po_documents.resolve(upload_id, conn=conn)}

    @router.get('/api/runs/{run_id}/documents')
    def documents(run_id: str):
        run = require_run(run_id)
        original = run_source(run, original=True)
        processed = run_source(run)
        invoice_source = None
        processed_input = None
        if original:
            record, _ = original
            batch = bool(record.get('batch_size', 0) > 1 or run.get('batch_size', 0) > 1)
            invoice_source = source_info(record, role='batch_container' if batch else 'original_upload')
            invoice_message = None
            if run.get('correction_of'):
                invoice_message = 'This run uses corrected fields. Shown here is the original uploaded source; the corrected input can be downloaded separately.'
            if batch:
                invoice_message = (invoice_message + ' ' if invoice_message else '') + 'This source contains a batch; the preview shows the full original file.'
            if Path(record['filename']).suffix.lower() == '.eml':
                invoice_message = (invoice_message + ' ' if invoice_message else '') + 'This is the original email source; its attachments remain in the downloaded email.'
        else:
            invoice_message = 'The original uploaded file is no longer available in local storage.'
        if run.get('correction_of') and processed and processed[0]['id'] == run['id'] and (not original or processed[0]['id'] != original[0]['id']):
            processed_input = source_info(processed[0], role='corrected_input')

        result = run.get('result') or {}
        matched_po = result.get('matched_po')
        matched_vendor = result.get('matched_vendor')
        po_source_info = None
        po_message = None
        if matched_po:
            match_message = 'These are the matched PO details saved with this run. Matching alone does not mean the invoice was approved.'
            sources = po_documents.for_po(matched_po)
            po_source_info = sources['po_source'] or sources['record_source']
            po_message = sources['po_source_message']
        elif run.get('status') in {'queued', 'processing'}:
            match_message = 'Processing is still in progress. A matched purchase order will appear here when matching completes.'
        else:
            match_message = 'No purchase order was matched to this run. See Decision & checks for missing or ambiguous PO references.'
        return {'run_id': run_id, 'status': run['status'], 'matched_po': matched_po,
                'matched_vendor': matched_vendor, 'match_message': match_message,
                'invoice_source': invoice_source, 'invoice_source_message': invoice_message,
                'processed_input': processed_input, 'po_source': po_source_info,
                'po_source_message': po_message}

    return router
