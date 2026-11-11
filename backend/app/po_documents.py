"""Resolve retained PO uploads through independently confirmed record reviews."""
import json
from uuid import UUID


class PODocuments:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings

    def get_upload(self, upload_id, conn):
        if not isinstance(upload_id, str):
            return None
        row = conn.execute('SELECT data FROM po_uploads WHERE id=?', (upload_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def file_available(self, upload):
        try:
            identifier = str(UUID(str(upload['id'])))
        except (ValueError, TypeError, KeyError, AttributeError):
            return False
        return self.store.has_source('po-sources', identifier)

    @staticmethod
    def source_info(upload, role):
        url = f'/api/procurement/uploads/{upload["id"]}/source'
        return {'filename': upload['filename'], 'source_id': upload['id'], 'source_role': role,
                'preview_url': url + '/preview', 'download_url': url,
                'source_sha256': upload.get('source_sha256')}

    @staticmethod
    def is_record_review(upload):
        return bool(upload.get('review_of_po')) or any(
            item.get('extraction', {}).get('method') == 'registered_record'
            for item in upload.get('items', []))

    @staticmethod
    def confirmed_for(upload, po_number):
        return upload.get('status') == 'confirmed' and po_number in (upload.get('confirmation') or {}).get('po_numbers', [])

    def predecessor(self, upload, po_number, conn):
        if 'review_of_po' in upload:
            # Only server-created review drafts carry this immutable lineage.
            if upload['review_of_po'] != po_number:
                return None
            return upload.get('prior_po_upload_id')
        if not self.confirmed_for(upload, po_number):
            return None
        # Older confirmed reviews predate explicit lineage fields. Their audit
        # event contains the exact prior PO; never guess from today's register.
        for row in conn.execute('SELECT data FROM procurement_audit ORDER BY id DESC'):
            event = json.loads(row[0])
            if event.get('action') != 'confirm_po' or event.get('upload_id') != upload['id']:
                continue
            previous, current = event.get('previous') or {}, event.get('purchase_order') or {}
            if current.get('po_number') == po_number and previous.get('po_number') == po_number:
                return (previous.get('_validation') or {}).get('upload_id')
        return None

    def for_po(self, po, conn=None):
        if conn is None:
            with self.store.connection() as session:
                return self.for_po(po, session)
        upload_id = (po.get('_validation') or {}).get('upload_id')
        if upload_id == 'seed-fixture':
            return self.unavailable('This is a fictional seed/demo PO. No original PO document was uploaded.')
        if not upload_id:
            return self.unavailable('No uploaded source is attached to this older or directly imported PO.')
        return self.resolve(upload_id, po.get('po_number'), require_confirmed=True, conn=conn)

    @staticmethod
    def unavailable(message, record_source=None):
        return {'po_source': None, 'po_source_message': message, 'record_source': record_source}

    def resolve(self, upload_id, po_number=None, *, require_confirmed=False, conn=None):
        if conn is None:
            with self.store.connection() as session:
                return self.resolve(upload_id, po_number, require_confirmed=require_confirmed, conn=session)
        upload = self.get_upload(upload_id, conn)
        if not upload:
            return self.unavailable('The source upload linked to this PO is unavailable.')
        if require_confirmed and not self.confirmed_for(upload, po_number):
            return self.unavailable('No confirmed source upload could be verified for this PO.')
        if not self.is_record_review(upload):
            if not self.file_available(upload):
                return self.unavailable('The PO upload is recorded, but its original file is no longer available.')
            count = len((upload.get('confirmation') or {}).get('po_numbers', [])) or len(upload.get('items', []))
            return {'po_source': self.source_info(upload, 'po_upload'), 'record_source': None,
                    'po_source_message': 'This uploaded file contains multiple POs; the preview shows the complete source.' if count > 1 else None}

        record_source = self.source_info(upload, 'registered_record') if self.file_available(upload) else None
        number = po_number or upload.get('review_of_po')
        if not number:
            numbers = {item.get('purchase_order', {}).get('po_number') for item in upload.get('items', [])}
            if len(numbers) == 1:
                number = numbers.pop()
        if not number:
            return self.unavailable('This record review has no unambiguous link to an original uploaded PO.', record_source)
        visited = set()
        for _ in range(100):
            if upload['id'] in visited:
                return self.unavailable('The original PO source could not be resolved from this review history.', record_source)
            visited.add(upload['id'])
            prior_id = self.predecessor(upload, number, conn)
            if not prior_id or prior_id == 'seed-fixture':
                return self.unavailable('No original uploaded PO is linked to this record review. The generated JSON snapshot is separate from an uploaded document.', record_source)
            prior = self.get_upload(prior_id, conn)
            if not prior or not self.confirmed_for(prior, number):
                return self.unavailable('The earlier confirmed PO upload is unavailable or belongs to a different PO.', record_source)
            if self.is_record_review(prior):
                upload = prior
                continue
            if not self.file_available(prior):
                return self.unavailable('The original PO upload is recorded, but its file is no longer available.', record_source)
            return {'po_source': self.source_info(prior, 'po_upload'), 'record_source': record_source,
                    'po_source_message': 'This is the original uploaded PO associated with this record. Review edits may differ from the uploaded document.'}
        return self.unavailable('The PO review history is too long to resolve the original source automatically.', record_source)
