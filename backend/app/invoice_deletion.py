"""Delete complete upload families without leaving shared sources or commitments."""
import hashlib
import json
import logging
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .store import now

logger = logging.getLogger(__name__)


class DeleteInvoice(BaseModel):
    expected_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')


def canonical_id(value):
    try:
        return str(UUID(str(value))) == value
    except (ValueError, TypeError, AttributeError):
        return False


def family_preview(runs, run_id, active):
    by_id = {run['id']: run for run in runs}
    if run_id not in by_id:
        raise HTTPException(404, 'Run not found.')
    links = {key: set() for key in by_id}
    for run in runs:
        for key in ('parent_id', 'correction_of'):
            parent = run.get(key)
            if parent in by_id:
                links[run['id']].add(parent)
                links[parent].add(run['id'])
    found, pending = set(), [run_id]
    while pending:
        current = pending.pop()
        if current not in found:
            found.add(current)
            pending.extend(links[current] - found)
    family = [by_id[key] for key in sorted(found)]
    busy = bool(found & active) or any(run['status'] in ('queued', 'processing') for run in family)
    # Include every stored field, not only invoice fields: a review, correction,
    # or later batch row must invalidate a confirmation opened beforehand.
    digest = hashlib.sha256(json.dumps(family, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return {'run_ids': sorted(found), 'count': len(family),
            'filenames': sorted({run['filename'] for run in family}),
            'approved_count': sum(run.get('decision') == 'approved' for run in family),
            'can_delete': not busy,
            'reason': 'Wait for this upload and all of its invoice rows to finish processing.' if busy else None,
            'fingerprint': digest}


class SourceDeletion:
    """Stage files by rename; restore on SQL rollback and recover after a crash."""
    def __init__(self, store, settings):
        self.store = store
        self.sources = settings.data_dir / 'sources'
        self.staging = self.sources / '.deletions'

    def checked_directory(self):
        # Never follow a substituted directory outside the workspace.
        if self.sources.is_symlink() or self.staging.is_symlink():
            raise OSError('Source directory cannot be a symbolic link.')

    def recover(self):
        self.checked_directory()
        if not self.staging.exists():
            return
        for folder in self.staging.iterdir():
            if not canonical_id(folder.name) or folder.is_symlink() or not folder.is_dir():
                continue
            with self.store.connection() as conn:
                committed = conn.execute('SELECT 1 FROM invoice_deletions WHERE id=?', (folder.name,)).fetchone()
            try:
                self.finish(folder, committed=bool(committed))
            except OSError:
                logger.error('Source cleanup recovery remains pending for deletion %s', folder.name)

    def stage(self, deletion_id, run_ids):
        self.checked_directory()
        folder = self.staging / deletion_id
        folder.mkdir(parents=True)
        try:
            for run_id in run_ids:
                if not canonical_id(run_id):
                    continue  # Legacy IDs never become filesystem paths.
                source = self.sources / f'{run_id}.bin'
                if source.exists() or source.is_symlink():
                    source.replace(folder / source.name)
        except OSError:
            self.finish(folder, committed=False)
            raise
        return folder

    def finish(self, folder: Path, *, committed):
        for source in folder.iterdir():
            if source.suffix != '.bin' or not canonical_id(source.stem) or (source.is_dir() and not source.is_symlink()):
                raise OSError('Unexpected staged source entry.')
            if committed:
                source.unlink()
            else:
                destination = self.sources / source.name
                if destination.exists() or destination.is_symlink():
                    raise OSError('Cannot overwrite an existing source during recovery.')
                source.replace(destination)
        folder.rmdir()


def deletion_router(store, pipeline, settings):
    router = APIRouter(tags=['Invoice deletion'])
    sources = SourceDeletion(store, settings)
    sources.recover()

    @router.get('/api/runs/{run_id}/deletion-preview')
    def preview(run_id: str):
        with pipeline.lock:
            return family_preview(store.runs(), run_id, pipeline.active_run_ids)

    @router.delete('/api/runs/{run_id}')
    def delete(run_id: str, body: DeleteInvoice):
        folder = None
        committed = False
        cleanup_pending = False
        with pipeline.lock:
            try:
                with store.connection(write=True) as conn:
                    runs = store.runs(conn)
                    snapshot = family_preview(runs, run_id, pipeline.active_run_ids)
                    if not snapshot['can_delete']:
                        raise HTTPException(409, snapshot['reason'])
                    if snapshot['fingerprint'] != body.expected_fingerprint:
                        raise HTTPException(409, 'This upload changed after the deletion preview. Reload the preview before deleting.')
                    deletion_id = str(uuid4())
                    deleted = set(snapshot['run_ids'])
                    folder = sources.stage(deletion_id, snapshot['run_ids'])
                    conn.executemany('DELETE FROM runs WHERE id=?', [(key,) for key in snapshot['run_ids']])
                    for run in runs:
                        if run['id'] in deleted:
                            continue
                        changed = False
                        for key in ('result', 'original_result'):
                            result = run.get(key)
                            if result and result.get('duplicate_of') in deleted:
                                result['duplicate_source_deleted'] = True
                                changed = True
                        if changed:
                            store.save_run(run, conn)
                    audit = {'action': 'delete_invoice_upload', 'run_ids': snapshot['run_ids'],
                             'count': snapshot['count'], 'approved_count': snapshot['approved_count']}
                    conn.execute('INSERT INTO invoice_deletions VALUES (?,?,?)', (deletion_id, now(), json.dumps(audit)))
                committed = True
            except OSError as exc:
                raise HTTPException(503, 'Unable to remove the source files. No invoice records were deleted; retry later.') from exc
            finally:
                if folder is not None:
                    try:
                        sources.finish(folder, committed=committed)
                    except OSError:
                        # The staged files are unavailable via the API after commit.
                        # Startup uses the transaction audit to purge or restore them.
                        logger.error('Source cleanup remains pending for deletion %s', folder.name)
                        cleanup_pending = committed
            return {'deleted_run_ids': snapshot['run_ids'], 'count': snapshot['count'], 'cleanup_pending': cleanup_pending}

    return router
