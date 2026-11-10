import { useEffect, useState } from 'react';
import { AlertTriangle, ArrowDownToLine, ChevronLeft, ChevronRight, FileText, LoaderCircle, RefreshCw, ZoomIn } from 'lucide-react';

export type SourceInfo = {
  filename: string;
  source_id: string;
  source_role: 'original_upload' | 'batch_container' | 'corrected_input' | 'po_upload' | 'registered_record';
  preview_url: string;
  download_url: string;
  source_sha256: string | null;
};

type PreviewMetadata = {
  filename: string;
  kind: 'pdf' | 'image' | 'text' | 'unsupported';
  page_count: number;
  preview_page_count: number;
  text: string | null;
  truncated: boolean;
  message: string | null;
  page_url: string;
  download_url: string;
};

async function failureMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === 'string') return body.detail;
  } catch { /* retain a useful fallback */ }
  return `Document preview is unavailable (${response.status}). You can still download the original.`;
}

export default function DocumentPreview({ source, title = 'Original document' }: { source: SourceInfo; title?: string }) {
  const [state, setState] = useState<{ key: string; metadata: PreviewMetadata | null; error: string }>({ key: '', metadata: null, error: '' });
  const [retry, setRetry] = useState(0);
  const [page, setPage] = useState(0);
  const [pageRetry, setPageRetry] = useState(0);
  const [zoom, setZoom] = useState<'fit' | 'actual'>('fit');
  const [loadedImage, setLoadedImage] = useState('');
  const [failedImage, setFailedImage] = useState('');
  const sourceKey = `${source.preview_url}:${source.source_sha256 || ''}`;

  useEffect(() => {
    const controller = new AbortController();
    setState({ key: sourceKey, metadata: null, error: '' });
    setPage(0);
    setPageRetry(0);
    setZoom('fit');
    setLoadedImage('');
    setFailedImage('');
    async function load() {
      try {
        const response = await fetch(source.preview_url, { signal: controller.signal });
        if (!response.ok) throw new Error(await failureMessage(response));
        const metadata = await response.json() as PreviewMetadata;
        if (!controller.signal.aborted) setState({ key: sourceKey, metadata, error: '' });
      } catch (error) {
        if (!controller.signal.aborted) setState({ key: sourceKey, metadata: null, error: (error as Error).message });
      }
    }
    void load();
    return () => controller.abort();
  }, [source.preview_url, sourceKey, retry]);

  const metadata = state.key === sourceKey ? state.metadata : null;
  const error = state.key === sourceKey ? state.error : '';
  const loading = !metadata && !error;
  const visual = metadata && ['pdf', 'image'].includes(metadata.kind) && metadata.preview_page_count > 0;
  const currentPage = metadata ? Math.min(page, Math.max(0, metadata.preview_page_count - 1)) : 0;
  const imageUrl = visual ? `${metadata.page_url}${metadata.page_url.includes('?') ? '&' : '?'}page=${currentPage}&attempt=${pageRetry}` : '';
  const imageFailed = !!imageUrl && failedImage === imageUrl;
  const imageLoading = !!imageUrl && loadedImage !== imageUrl && !imageFailed;

  return <section className="document-preview" aria-label={title}>
    <div className="document-preview-header">
      <div className="document-preview-title"><FileText size={20} /><div><h3>{title}</h3><p>{source.filename}</p></div></div>
      <div className="document-preview-actions"><a className="button subtle small" href={source.download_url} download><ArrowDownToLine size={15} />Download original</a></div>
    </div>
    {loading && <div className="loading-state" role="status"><LoaderCircle className="spin" size={21} />Loading document preview…</div>}
    {error && <div className="document-unavailable"><div className="alert error" role="alert"><AlertTriangle size={18} /><span>{error}</span></div><button className="button subtle small" onClick={() => setRetry(value => value + 1)}><RefreshCw size={14} />Retry preview</button></div>}
    {metadata?.message && <p className="document-source-note">{metadata.message}</p>}
    {visual && <>
      <div className="document-preview-toolbar">
        <div className="document-page-controls">
          <button className="button subtle small" aria-label="Previous document page" disabled={currentPage === 0} onClick={() => setPage(value => Math.max(0, value - 1))}><ChevronLeft size={15} />Previous</button>
          <span role="status" aria-live="polite">Page {currentPage + 1} of {metadata.page_count}</span>
          <button className="button subtle small" aria-label="Next document page" disabled={currentPage >= metadata.preview_page_count - 1} onClick={() => setPage(value => Math.min(metadata.preview_page_count - 1, value + 1))}>Next<ChevronRight size={15} /></button>
        </div>
        <button className="button subtle small" onClick={() => setZoom(value => value === 'fit' ? 'actual' : 'fit')}><ZoomIn size={14} />{zoom === 'fit' ? '100% size' : 'Fit width'}</button>
      </div>
      <div className={`document-page-viewport ${zoom === 'fit' ? 'fit' : 'actual'}`} aria-busy={imageLoading}>
        {imageLoading && <div className="document-page-status" role="status"><LoaderCircle className="spin" size={19} />Loading page {currentPage + 1}…</div>}
        {imageFailed && <div className="document-page-status"><div className="alert error" role="alert"><AlertTriangle size={18} /><span>This page could not be displayed. Retry the page or download the original document.</span></div><button className="button subtle small" onClick={() => setPageRetry(value => value + 1)}><RefreshCw size={14} />Retry page</button></div>}
        <img key={imageUrl} className="document-page-image" src={imageUrl} alt={`${source.filename}, page ${currentPage + 1}`} onLoad={() => setLoadedImage(imageUrl)} onError={() => setFailedImage(imageUrl)} hidden={imageFailed} />
      </div>
      {metadata.truncated && <p className="document-source-note">The preview includes the first {metadata.preview_page_count} of {metadata.page_count} pages. Download the original to view all pages.</p>}
    </>}
    {metadata?.kind === 'text' && <>
      <pre className="document-text-preview" tabIndex={0} aria-label={`Original contents of ${source.filename}`}>{metadata.text || 'This document contains no displayable text.'}</pre>
      {metadata.truncated && <p className="document-source-note">The text preview is shortened. Download the original for its complete contents.</p>}
    </>}
    {metadata && (metadata.kind === 'unsupported' || (['pdf', 'image'].includes(metadata.kind) && !visual)) && <div className="document-unavailable"><FileText size={25} /><p>A preview is not available for this file. Use Download original to open it in its usual application.</p></div>}
    {source.source_sha256 && <details className="document-fingerprint raw-text"><summary>Original source fingerprint</summary><code className="hash">{source.source_sha256}</code></details>}
  </section>;
}
