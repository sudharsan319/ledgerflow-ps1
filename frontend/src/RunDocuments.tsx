import { useEffect, useState } from 'react';
import { AlertTriangle, ArrowDownToLine, CheckCircle2, FileText, FolderOpen, LoaderCircle, RefreshCw, ShieldCheck, XCircle } from 'lucide-react';
import DocumentPreview from './DocumentPreview';
import type { SourceInfo } from './DocumentPreview';
import { ListSearch } from './ListSearch';
import { indexedMatches } from './search';

type Line = { description?: string; sku?: string; quantity?: string; unit_price?: string; amount?: string };
type POSnapshot = {
  po_number?: string; vendor_id?: string; vendor_name?: string; po_date?: string; currency?: string;
  subtotal?: string; tax?: string; total?: string; committed?: string; status?: string; approval_status?: string;
  approved_by?: string; description?: string; validation_status?: string; validated_at?: string; reviewer?: string;
  line_items?: Line[]; _validation?: { reviewer?: string; at?: string; comment?: string };
};
type VendorSnapshot = { id?: string; name?: string; approved?: boolean; approval_status?: string };
type Check = { code: string; label: string; status: string; message: string; blocking: boolean };
type Context = {
  run_id: string; status: string; matched_po: POSnapshot | null; matched_vendor: VendorSnapshot | null;
  match_message: string; invoice_source: SourceInfo | null; invoice_source_message: string | null;
  processed_input: SourceInfo | null; po_source: SourceInfo | null; po_source_message: string | null;
};
type Props = { runId: string; updatedAt: string; view: 'matched-po' | 'source'; checks: Check[] };

const value = (input: unknown) => input === undefined || input === null || input === '' ? 'Not recorded' : String(input);
const money = (amount: string | undefined, currency: string | undefined) => {
  if (amount === undefined || amount === null || amount === '') return 'Not recorded';
  const numeric = Number(amount);
  if (!Number.isFinite(numeric)) return amount;
  if (!currency) return amount;
  try { return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(numeric); }
  catch { return `${currency || ''} ${amount}`.trim(); }
};
const timestamp = (input?: string) => input ? new Date(input).toLocaleString() : 'Not recorded';

export default function RunDocuments({ runId, updatedAt, view, checks }: Props) {
  const [state, setState] = useState<{ key: string; data: Context | null; error: string }>({ key: '', data: null, error: '' });
  const [retry, setRetry] = useState(0);
  const [lineSearch, setLineSearch] = useState('');
  const [checkSearch, setCheckSearch] = useState('');
  const key = `${runId}:${updatedAt}`;

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/documents`, { signal: controller.signal });
        if (!response.ok) {
          let message = `Document details could not be loaded (${response.status}).`;
          try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail; } catch { /* retain fallback */ }
          throw new Error(message);
        }
        const data = await response.json() as Context;
        if (!controller.signal.aborted) setState({ key, data, error: '' });
      } catch (error) {
        if (!controller.signal.aborted) setState({ key, data: null, error: (error as Error).message });
      }
    }
    setState(previous => ({ ...previous, error: '' }));
    void load();
    return () => controller.abort();
  }, [runId, key, retry]);

  useEffect(() => { setLineSearch(''); setCheckSearch(''); }, [runId, state.data?.matched_po?.po_number]);
  const data = state.key === key ? state.data : null;
  const error = state.key === key ? state.error : '';
  if (error) return <div className="run-documents"><div className="alert error" role="alert"><AlertTriangle size={18} /><span>{error}</span></div><button className="button subtle small" onClick={() => setRetry(current => current + 1)}><RefreshCw size={14} />Retry document details</button></div>;
  if (!data) return <div className="loading-state" role="status"><LoaderCircle className="spin" size={21} />Loading {view === 'source' ? 'uploaded document' : 'matched purchase order'}…</div>;

  if (view === 'source') return <div className="run-documents">
    {data.invoice_source && data.invoice_source_message && <p className="document-source-note">{data.invoice_source_message}</p>}
    {data.invoice_source ? <DocumentPreview key={data.invoice_source.preview_url} source={data.invoice_source} title="Uploaded document" /> : <div className="empty document-unavailable"><FileText size={28} /><h3>No uploaded document available</h3><p>{data.invoice_source_message || 'The original file was not retained for this run.'}</p></div>}
    {data.processed_input && <div className="document-corrected-input"><div><h3>Corrected input used for this run</h3><p>{data.processed_input.filename}</p></div><a className="button subtle small" href={data.processed_input.download_url} download><ArrowDownToLine size={15} />Download corrected input</a></div>}
  </div>;

  const po = data.matched_po;
  if (!po) return <div className="empty document-unavailable"><FolderOpen size={28} /><h3>{['queued', 'processing'].includes(data.status) ? 'PO matching is in progress' : 'No matched purchase order'}</h3><p>{data.match_message || 'No purchase order was matched to this invoice. See Decision & checks for the matching result.'}</p></div>;
  const vendor = data.matched_vendor;
  const sameVendor = !!vendor?.id && !!po.vendor_id && vendor.id.trim().toLowerCase() === po.vendor_id.trim().toLowerCase();
  const vendorState = vendor?.approved ? 'approved' : vendor?.approval_status === 'pending' ? 'pending' : vendor ? 'rejected' : 'unverified';
  const poName = po.vendor_name || (sameVendor ? vendor?.name : undefined) || po.vendor_id;
  const lines = po.line_items || [];
  const visibleLines = indexedMatches(lines, lineSearch, line => [line.description, line.sku, line.quantity, line.unit_price, line.amount, money(line.unit_price, po.currency), money(line.amount, po.currency)]);
  const poChecks = checks.filter(check => check.code.startsWith('po_') || check.code.startsWith('vendor_'));
  const visibleChecks = indexedMatches(poChecks, checkSearch, check => [check.label, check.code, check.status, check.message, check.blocking && check.status !== 'pass' ? 'Blocks approval' : '']);
  const blocking = poChecks.filter(check => check.blocking && check.status !== 'pass').length;
  const fields: [string, string][] = [
    ['PO number', value(po.po_number)], ['PO vendor', value(poName)], ['Vendor ID', value(po.vendor_id)],
    ['PO date', value(po.po_date)], ['Currency', value(po.currency)], ['Order status', value(po.status)],
    ['Subtotal', money(po.subtotal, po.currency)], ['Tax', money(po.tax, po.currency)], ['PO total', money(po.total, po.currency)],
    ['Committed before this evaluation', money(po.committed, po.currency)], ['PO authorization', value(po.approval_status)], ['Authorized by', value(po.approved_by)],
    ['PO validation', value(po.validation_status)], ['Reviewed by', value(po.reviewer || po._validation?.reviewer)], ['Reviewed at', timestamp(po.validated_at || po._validation?.at)],
  ];
  return <div className="run-documents matched-po-details">
    <div className="matched-po-heading"><FolderOpen size={24} /><div><h3>{value(po.po_number)}</h3><p>{value(poName)}</p></div></div>
    <p className="matched-po-note">PO snapshot at invoice evaluation</p>
    {data.match_message && <p className="document-source-note">{data.match_message}</p>}
    <div className="matched-po-badges"><span className={`badge po-badge ${po.validation_status || 'unverified'}`}>{po.validation_status === 'validated' ? 'Validated PO' : po.validation_status === 'unverified' ? 'Unverified PO' : 'PO validation not recorded'}</span><span className={`badge ${po.status || 'unverified'}`}>{po.status ? `${po.status[0].toUpperCase()}${po.status.slice(1)} order` : 'Order status not recorded'}</span><span className={`badge po-badge ${po.approval_status || 'unverified'}`}>{po.approval_status ? `PO authorization: ${po.approval_status}` : 'PO authorization not recorded'}</span></div>
    <div className="fields-grid">{fields.map(([label, content]) => <div className="data-field" key={label}><small>{label}</small><strong>{content}</strong></div>)}</div>
    {po.description && <p className="matched-po-description">{po.description}</p>}
    <div className="matched-po-vendor"><ShieldCheck size={19} /><div><strong>Invoice vendor at evaluation: {value(vendor?.name || vendor?.id)}</strong></div><span className={`badge po-badge ${vendorState}`}>{vendor ? vendorState === 'approved' ? 'Approved vendor' : vendorState === 'pending' ? 'Pending vendor review' : 'Rejected vendor' : 'Vendor unresolved'}</span></div>
    {vendor && po.vendor_id && vendor.id && !sameVendor && <div className="alert warning"><AlertTriangle size={18} /><span>The matched PO belongs to a different vendor from the invoice. See the vendor consistency check below.</span></div>}
    <section className="matched-po-line-items"><h3 className="subheading">PO line items</h3><ListSearch query={lineSearch} onQueryChange={setLineSearch} label="Search matched PO line items" placeholder="Description, SKU, quantity, or amount…" matched={visibleLines.length} total={lines.length} />
      {lines.length ? <><div className="table-scroll"><table className="line-table"><thead><tr><th>Description</th><th>SKU</th><th>Quantity</th><th>Unit price</th><th>Amount</th></tr></thead><tbody>{visibleLines.map(({ item, index }) => <tr key={index}><td>{value(item.description)}</td><td>{item.sku || '—'}</td><td>{value(item.quantity)}</td><td>{money(item.unit_price, po.currency)}</td><td>{money(item.amount, po.currency)}</td></tr>)}</tbody></table></div>{!visibleLines.length && <p className="list-search-empty">No PO line items match your search.</p>}</> : <p className="document-source-note">No line items were recorded in this PO snapshot.</p>}
    </section>
    {!!poChecks.length && <section className="matched-po-checks"><h3 className="subheading">PO and vendor checks for this invoice</h3><ListSearch query={checkSearch} onQueryChange={setCheckSearch} label="Search matched PO checks" placeholder="Check, result, or explanation…" matched={visibleChecks.length} total={poChecks.length} hint={`${blocking} check${blocking === 1 ? '' : 's'} block approval.`} /><div className="checks-list">{visibleChecks.map(({ item: check, index }) => <div className={`check-row ${check.status}`} key={`${check.code}-${index}`}><span>{check.status === 'pass' ? <CheckCircle2 size={18} /> : check.status === 'fail' ? <XCircle size={18} /> : <AlertTriangle size={18} />}</span><div><strong>{check.label}</strong><p>{check.message}</p></div>{check.blocking && check.status !== 'pass' && <span className="hard-block">Blocks approval</span>}</div>)}</div>{!visibleChecks.length && <p className="list-search-empty">No checks match your search.</p>}</section>}
    {po._validation?.comment && <p className="matched-po-note"><strong>PO review evidence:</strong> {po._validation.comment}</p>}
    {data.po_source && data.po_source_message && <p className="document-source-note">{data.po_source_message}</p>}
    {data.po_source ? <DocumentPreview key={data.po_source.preview_url} source={data.po_source} title={data.po_source.source_role === 'registered_record' ? 'Reviewed PO record' : 'Uploaded PO document'} /> : <div className="document-unavailable"><FileText size={23} /><p>{data.po_source_message || 'No original PO document is attached to this recorded match. The stored PO details are shown above.'}</p></div>}
  </div>;
}
