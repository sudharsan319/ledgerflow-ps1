import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, LoaderCircle, Trash2, X } from 'lucide-react';
import { deleteInvoiceUpload, loadDeletionPreview } from './invoiceDeletion';
import type { DeletionPreview, DeletionResult } from './invoiceDeletion';

export function DeleteInvoiceConfirmation({ preview, busy, onConfirm }: {
  preview: DeletionPreview; busy: boolean; onConfirm: () => void;
}) {
  return <>
    <p>This permanently deletes the uploaded file and <strong>{preview.count} processing record{preview.count === 1 ? '' : 's'}</strong>, including any batch invoices and corrected versions belonging to this upload.</p>
    <ul className="invoice-delete-files" aria-label="Files included in deletion">{preview.filenames.map((filename, index) => <li key={`${index}-${filename}`}>{filename}</li>)}</ul>
    {preview.approved_count > 0 && <p className="invoice-delete-impact">The PO amounts and quantities reserved by {preview.approved_count} approved invoice{preview.approved_count === 1 ? '' : 's'} will be released.</p>}
    <p className="muted">This cannot be undone. Purchase orders, vendors and other uploads will remain.</p>
    {!preview.can_delete && <div className="alert warning" role="status"><AlertTriangle size={17} />{preview.reason || 'This upload cannot be deleted yet.'}</div>}
    <button className="button danger" disabled={busy || !preview.can_delete} onClick={onConfirm}>{busy ? <LoaderCircle size={16} className="spin" /> : <Trash2 size={16} />}{busy ? 'Deleting…' : 'Delete upload permanently'}</button>
  </>;
}

export default function DeleteInvoiceDialog({ runId, onClose, onDeleted }: {
  runId: string; onClose: () => void; onDeleted: (result: DeletionResult) => void;
}) {
  const [preview, setPreview] = useState<DeletionPreview | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const dialog = useRef<HTMLElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const closeRef = useRef(onClose);
  const busyRef = useRef(false);
  closeRef.current = onClose;
  busyRef.current = busy;

  useEffect(() => {
    const controller = new AbortController();
    setPreview(null); setError(''); setLoading(true);
    void loadDeletionPreview(runId, controller.signal).then(value => {
      if (!controller.signal.aborted) setPreview(value);
    }).catch(cause => {
      if (!controller.signal.aborted) setError((cause as Error).message);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [runId, retry]);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    cancel.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape' && !busyRef.current) closeRef.current();
      if (event.key !== 'Tab') return;
      const buttons = Array.from(dialog.current?.querySelectorAll<HTMLButtonElement>('button:not([disabled])') || []);
      const first = buttons[0], last = buttons.at(-1);
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    document.addEventListener('keydown', onKey);
    return () => { document.body.style.overflow = overflow; document.removeEventListener('keydown', onKey); previous?.focus(); };
  }, []);

  async function confirm() {
    if (!preview || busy) return;
    setBusy(true); setError('');
    try {
      const result = await deleteInvoiceUpload(runId, preview);
      onDeleted(result);
    } catch (cause) {
      // A failed/stale confirmation must never be retried with its old fingerprint.
      setPreview(null);
      setError((cause as Error).message);
    } finally { setBusy(false); }
  }
  function close() { if (!busy) onClose(); }

  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section ref={dialog} className="modal invoice-delete-modal" role="alertdialog" aria-modal="true" aria-labelledby="invoice-delete-title" aria-describedby="invoice-delete-description">
      <div className="modal-head"><div><h2 id="invoice-delete-title">Delete uploaded invoice?</h2><p id="invoice-delete-description">Review everything that will be removed.</p></div><button className="icon-button" disabled={busy} onClick={close} aria-label="Close deletion dialog"><X size={20} /></button></div>
      <div className="invoice-delete-body">
        {loading && <div className="loading-state"><LoaderCircle className="spin" size={20} />Checking related invoices…</div>}
        {error && <div className="alert error" role="alert"><AlertTriangle size={17} />{error}</div>}
        {preview && <DeleteInvoiceConfirmation preview={preview} busy={busy} onConfirm={() => void confirm()} />}
        {!loading && (error || preview && !preview.can_delete) && <button className="button subtle small" disabled={busy} onClick={() => setRetry(value => value + 1)}>Check latest details</button>}
      </div>
      <div className="modal-footer"><button ref={cancel} className="button subtle" disabled={busy} onClick={close}>Cancel</button></div>
    </section>
  </div>;
}
