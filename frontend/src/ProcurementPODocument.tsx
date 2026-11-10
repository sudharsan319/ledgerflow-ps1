import { useEffect, useState } from 'react';
import { AlertTriangle, ArrowDownToLine, FileText, LoaderCircle, RefreshCw } from 'lucide-react';
import DocumentPreview from './DocumentPreview';
import type { SourceInfo } from './DocumentPreview';

type PODocuments = {
  po_source: SourceInfo | null;
  po_source_message: string | null;
  record_source: SourceInfo | null;
};

export default function ProcurementPODocument({ endpoint, refreshKey = '' }: { endpoint: string; refreshKey?: string }) {
  const [state, setState] = useState<{ key: string; data: PODocuments | null; error: string }>({ key: '', data: null, error: '' });
  const [retry, setRetry] = useState(0);
  const key = `${endpoint}:${refreshKey}:${retry}`;

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(endpoint, { signal: controller.signal });
        if (!response.ok) {
          let message = `The uploaded PO could not be loaded (${response.status}).`;
          try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail; } catch { /* retain fallback */ }
          throw new Error(message);
        }
        const data = await response.json() as PODocuments;
        if (!controller.signal.aborted) setState({ key, data, error: '' });
      } catch (error) {
        if (!controller.signal.aborted) setState({ key, data: null, error: (error as Error).message });
      }
    }
    void load();
    return () => controller.abort();
  }, [endpoint, key]);

  const data = state.key === key ? state.data : null;
  const error = state.key === key ? state.error : '';
  if (error) return <div className="procurement-po-document"><div className="alert error" role="alert"><AlertTriangle size={18} /><span>{error}</span></div><button className="button subtle small" onClick={() => setRetry(value => value + 1)}><RefreshCw size={14} />Retry uploaded PO</button></div>;
  if (!data) return <div className="loading-state" role="status"><LoaderCircle className="spin" size={21} />Loading uploaded PO…</div>;

  return <div className="procurement-po-document">
    {data.po_source && data.po_source_message && <p className="document-source-note">{data.po_source_message}</p>}
    {data.po_source ? <DocumentPreview source={data.po_source} title="Uploaded PO document" /> : <div className="empty document-unavailable"><FileText size={28} /><h3>No uploaded PO document available</h3><p>{data.po_source_message || 'The original uploaded file is not attached to this purchase order.'}</p></div>}
    {data.record_source && <div className="document-corrected-input"><div><h3>Registered PO record snapshot</h3><p>{data.record_source.filename}</p><p>This saved review record is separate from the original uploaded document.</p></div><a className="button subtle small" href={data.record_source.download_url} download><ArrowDownToLine size={15} />Download record snapshot</a></div>}
  </div>;
}
