import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Check, FileText, LoaderCircle, Plus, RefreshCw, RotateCcw } from 'lucide-react';
import RunDocuments from './RunDocuments';
import POVerificationTable from './POVerificationTable';
import { InvoiceHeaderTable, InvoiceRowsTable, LineVerificationTable } from './InvoiceReviewTables';
import { ListSearch } from './ListSearch';
import { matchesSearch } from './search';
import { currentLinePreview, scheduleLinePreview, verificationLabel } from './liveLinePreview';
import type { LinePreview, LiveLineState } from './liveLinePreview';
import { appendRow, changeLineTax, reviewSearchValues, buildInvoice, changesBetween, createDraft, fieldLabel, GROUPS, HEADER_FIELDS, hasValue, removeRow, textValue, updateHeader, updateRow } from './invoiceReviewState';
import type { DraftRow, FieldChange, Group, RecordValue, ReviewDraft } from './invoiceReviewState';

type CheckIssue = { code: string; label: string; status: string; message: string; blocking: boolean };
type Issues = { fields: Record<string, CheckIssue[]>; general: CheckIssue[] };
type Comparison = { field: string; local_value: unknown; ai_value: unknown; local_evidence?: string; ai_evidence?: string; disagrees: boolean; origin: string };
type Context = { run_id: string; updated_at: string; invoice: RecordValue; review_invoice?: RecordValue; invoice_fingerprint: string; comparisons: Comparison[]; evidence: RecordValue; raw_text: string; warnings: string[]; issues: Issues; editable: boolean; read_only_reason: string | null };
type Preview = { invoice: RecordValue; result: { decision: string; summary: string; checks: CheckIssue[] }; issues: Issues; changes: FieldChange[]; message: string };
type Props = { runId: string; onReprocessed: (id: string) => void; onDirtyChange: (value: boolean) => void; onBusyChange: (value: boolean) => void; initialReviewer?: string; initialComment?: string };
type Section = 'fields' | Group | 'verification' | 'po';
const groupNames: Record<Group, string> = { line_items: 'Products', tax_components: 'Invoice taxes', adjustments: 'Adjustments' };
async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, body === undefined ? { signal } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`); }
  return response.json();
}

export function InvoiceChangeList({ changes, label = 'Search field changes' }: { changes: FieldChange[]; label?: string }) {
  const [query, setQuery] = useState('');
  const visible = changes.filter(change => matchesSearch(query, change.field, fieldLabel(change.field), change.before, change.after));
  return <div className="invoice-change-list"><ListSearch query={query} onQueryChange={setQuery} label={label} placeholder="Field, original value, or correction…" matched={visible.length} total={changes.length} />{visible.length ? <div className="table-scroll"><table className="line-table"><thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead><tbody>{visible.map((change, index) => <tr key={`${change.field}-${index}`}><td>{fieldLabel(change.field)}</td><td>{textValue(change.before) || 'Not recorded'}</td><td>{textValue(change.after) || 'Cleared / not recorded'}</td></tr>)}</tbody></table></div> : <p className="list-search-empty">{changes.length ? 'No changes match your search.' : 'No field values changed.'}</p>}</div>;
}

export default function InvoiceFieldReview({ runId, onReprocessed, onDirtyChange, onBusyChange, initialReviewer = '', initialComment = '' }: Props) {
  const [context, setContext] = useState<Context | null>(null), [draft, setDraft] = useState<ReviewDraft | null>(null), [loadError, setLoadError] = useState(''), [retry, setRetry] = useState(0);
  const [live, setLive] = useState<LiveLineState | null>(null), [liveRetry, setLiveRetry] = useState(0);
  const [addedFields, setAddedFields] = useState<string[]>([]);
  const [section, setSection] = useState<Section>('fields'), [queries, setQueries] = useState<Record<string, string>>({}), [focused, setFocused] = useState('');
  const [reviewer, setReviewer] = useState(initialReviewer), [comment, setComment] = useState(initialComment), [verified, setVerified] = useState(false);
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [preview, setPreview] = useState<{ input: string; data: Preview } | null>(null), [resetPrompt, setResetPrompt] = useState(false);
  const mounted = useRef(true);
  const initialReview = useRef({ reviewer: initialReviewer, comment: initialComment });
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoadError('');
    api<Context>(`/runs/${encodeURIComponent(runId)}/review-context`, undefined, controller.signal).then(data => { if (!controller.signal.aborted) { setContext(data); setDraft(createDraft(data.review_invoice || data.invoice)); } }).catch(error => { if (!controller.signal.aborted) setLoadError(error.message); });
    return () => controller.abort();
  }, [runId, retry]);
  const invoice = draft ? buildInvoice(draft) : {};
  const input = JSON.stringify(invoice);
  const currentLive = currentLinePreview(live, input);
  const liveProducts = currentLive?.data?.rules.products;
  const liveError = currentLive?.error;
  const liveRules = currentLive?.data?.rules;
  const livePO = currentLive?.data?.po_reconciliation;
  useEffect(() => {
    if (!currentLive?.data) return;
    setDraft(previous => {
      if (!previous || JSON.stringify(buildInvoice(previous)) !== currentLive.input) return previous;
      let changed = false;
      const line_items = previous.groups.line_items.map((row, index) => {
        const product = currentLive.data?.rules.products.find(p => p.index === index);
        if (!product?.basis_notes?.length) return row;
        const value = { ...row.value };
        for (const key of ['amount_tax_mode', 'unit_price_tax_mode'] as const) {
          if (value[key] === 'unknown' && ['inclusive', 'exclusive'].includes(product[key] || '')) { value[key] = product[key]; changed = true; }
        }
        return { ...row, value };
      });
      return changed ? { ...previous, groups: { ...previous.groups, line_items } } : previous;
    });
  }, [currentLive?.data]);
  useEffect(() => {
    if (!context || !draft) return;
    return scheduleLinePreview(signal => api<LinePreview>(`/runs/${encodeURIComponent(runId)}/line-preview`, { invoice: JSON.parse(input), expected_invoice_fingerprint: context.invoice_fingerprint }, signal), data => setLive({ input, data }), error => setLive({ input, error }));
  }, [runId, context?.invoice_fingerprint, !!draft, input, liveRetry]);
  const changes = context && draft ? changesBetween(context.review_invoice || context.invoice, invoice) : [];
  const dirty = !!changes.length || !!verified || reviewer !== initialReview.current.reviewer || comment !== initialReview.current.comment;
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { onBusyChange(!!busy); }, [busy, onBusyChange]);
  useEffect(() => {
    if (!dirty) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', guard); return () => window.removeEventListener('beforeunload', guard);
  }, [dirty]);
  function edit(next: ReviewDraft) { setDraft(next); setVerified(false); setError(''); }
  function reset() { if (context) setDraft(createDraft(context.review_invoice || context.invoice)); setVerified(false); setPreview(null); setError(''); setResetPrompt(false); setFocused(''); setAddedFields([]); }
  const currentPreview = preview?.input === input ? preview.data : null;
  async function checkCorrections() {
    if (!context || !draft) return;
    setBusy('preview'); onBusyChange(true); setError('');
    try {
      const data = await api<Preview>(`/runs/${encodeURIComponent(runId)}/correction-preview`, { invoice, expected_invoice_fingerprint: context.invoice_fingerprint });
      if (mounted.current) setPreview({ input, data });
    } catch (error) { if (mounted.current) setError((error as Error).message); }
    finally { if (mounted.current) { setBusy(''); onBusyChange(false); } }
  }
  async function submit() {
    if (!context || !verified || !reviewer.trim() || comment.trim().length < 3) return;
    setBusy('submit'); onBusyChange(true); setError('');
    try {
      const data = await api<{ runs: { id: string }[] }>(`/runs/${encodeURIComponent(runId)}/correct`, { invoice, reviewer: reviewer.trim(), comment: comment.trim(), expected_invoice_fingerprint: context.invoice_fingerprint });
      if (!data.runs?.[0]?.id) throw new Error('No corrected run was returned. Reload run history before retrying.');
      if (mounted.current) { onDirtyChange(false); onBusyChange(false); onReprocessed(data.runs[0].id); }
    } catch (error) { if (mounted.current) setError((error as Error).message); }
    finally { if (mounted.current) { setBusy(''); onBusyChange(false); } }
  }
  if (loadError) return <div><div className="alert error" role="alert"><AlertTriangle size={18} />{loadError}</div><button className="button subtle small" onClick={() => setRetry(retry + 1)}><RefreshCw size={15} />Retry review</button></div>;
  if (!context || !draft) return <div className="loading-state"><LoaderCircle className="spin" size={21} />Loading field review…</div>;
  const disabled = !!busy || !context.editable;
  const query = queries[section] || '';
  const headerFields = HEADER_FIELDS.filter(field => addedFields.includes(field.key) || !field.manualOnly && (hasValue(context.invoice[field.key]) || hasValue(draft.base[field.key])));
  const headerMatches = headerFields.filter(field => matchesSearch(queries.fields || '', field.label, draft.base[field.key]));
  const headerVisible = headerFields.filter(field => field.key === focused || headerMatches.includes(field));
  const filteredRows = Object.fromEntries(GROUPS.map(group => [group, draft.groups[group].filter((row, index) => matchesSearch(queries[group] || '', reviewSearchValues(group, row), group === 'line_items' ? liveProducts?.[index]?.tax_summary : null, group === 'line_items' ? verificationLabel(liveProducts?.[index], !!liveError) : null, `row ${index + 1}`))])) as Record<Group, DraftRow[]>;
  const verificationRows = draft.groups.line_items.filter((row, index) => matchesSearch(queries.verification || '', reviewSearchValues('line_items', row), verificationLabel(liveProducts?.[index], !!liveError), liveProducts?.[index]?.verification));
  const poRows = draft.groups.line_items.filter((row, index) => matchesSearch(queries.po || '', reviewSearchValues('line_items', row), livePO?.lines.find(l => l.index === index)));
  const matched = section === 'po' ? poRows.length : section === 'fields' ? headerMatches.length : section === 'verification' ? verificationRows.length : filteredRows[section].length;
  const total = section === 'fields' ? headerFields.length : draft.groups[section === 'verification' || section === 'po' ? 'line_items' : section].length;
  const blur = (id: string) => setFocused(current => current === id ? '' : current);
  return <div className="invoice-field-review">
    <div className="invoice-review-heading"><div><h3>Review invoice</h3></div><span>{context.invoice.extraction_method === 'openrouter' ? 'AI-extracted data' : 'Extracted data'}</span></div>
    {!context.editable && <div className="alert warning"><AlertTriangle size={17} />{context.read_only_reason}</div>}
    <div className="invoice-review-layout">
      <aside className="invoice-review-source" aria-label="Source document for field review"><RunDocuments runId={runId} updatedAt={context.updated_at} view="source" checks={[]} /></aside>
      <div className="invoice-review-editor">
        <div className="invoice-review-section-tabs" role="group" aria-label="Invoice fields to review">{([['fields', 'Invoice fields'], ['line_items', `Products (${draft.groups.line_items.length})`], ['tax_components', `Invoice taxes (${draft.groups.tax_components.length})`], ['adjustments', `Adjustments (${draft.groups.adjustments.length})`], ['verification', 'Line verification'], ['po', 'PO checks']] as [Section, string][]).map(([key, title]) => <button key={key} type="button" className={section === key ? 'active' : ''} aria-pressed={section === key} onClick={() => { setSection(key); setFocused(''); }}>{title}</button>)}</div>
        <ListSearch query={query} onQueryChange={value => { setQueries(previous => ({ ...previous, [section]: value })); setFocused(''); }} label={`Search ${section === 'fields' ? 'invoice review fields' : section === 'verification' ? 'line verification' : section === 'po' ? 'PO products' : groupNames[section].toLowerCase()}`} placeholder="Search fields or values…" matched={matched} total={total} disabled={!!busy} />
        {liveError && <div className="alert warning" role="alert"><span>Line verification unavailable: {liveError}</span><button className="text-button" type="button" onClick={() => setLiveRetry(value => value + 1)}>Retry</button></div>}
        <div hidden={section !== 'po'}><POVerificationTable data={livePO} checks={currentLive?.data?.po_checks} rows={poRows} allRows={draft.groups.line_items} disabled={disabled} checking={!currentLive && !liveError} onSelect={(id, value) => edit(updateRow(draft, 'line_items', id, 'po_line_ref', value))} /></div>
        <div hidden={section !== 'verification'}>
          <h4>Invoice totals</h4>
          <div className="table-scroll"><table className="invoice-review-table"><thead><tr><th>Calculated net</th><th>Calculated tax</th><th>Adjustments</th><th>Calculated total</th><th>Printed total</th></tr></thead><tbody><tr><td>{liveRules?.computed_totals?.net ?? '—'}</td><td>{liveRules?.computed_totals?.tax ?? '—'}</td><td>{liveRules?.computed_totals?.adjustments ?? '—'}</td><td>{liveRules?.computed_totals?.total ?? '—'}</td><td>{textValue(invoice.total) || '—'}</td></tr></tbody></table></div>
          {liveRules?.checks?.filter(c => ['recomputed_total','recomputed_subtotal','recomputed_tax','arithmetic','line_sum','line_tax_sum','tax_components'].includes(c.code) && c.status !== 'pass').map(c => <p className="field-help" key={c.code}>{c.message}</p>)}
          <LineVerificationTable rows={verificationRows} allRows={draft.groups.line_items} products={liveProducts} unavailable={!!liveError} /></div>
        {preview && !currentPreview && <p className="invoice-review-stale">Correction check is out of date.</p>}
        {currentPreview && <div className={`alert ${currentPreview.result.checks.some(check => check.blocking && check.status !== 'pass') ? 'warning' : 'success'}`} role="status"><FileText size={18} /><div><strong>Preview: {currentPreview.result.decision.replaceAll('_', ' ')}</strong><p>{currentPreview.result.summary}</p><small>Preview only · Not saved</small></div></div>}
        <div hidden={section !== 'fields'}>
          <InvoiceHeaderTable fields={headerVisible} invoice={draft.base} disabled={disabled} onChange={(key, value) => edit(updateHeader(draft, key, value))} onAdd={key => { setAddedFields(previous => [...previous, key]); setQueries(previous => ({ ...previous, fields: '' })); }} onFocus={setFocused} onBlur={blur} />
        </div>
        {GROUPS.map(group => <div key={group} hidden={section !== group}>
          <div className="invoice-review-list-heading"><h4>{groupNames[group]}</h4><button className="button subtle small" type="button" disabled={disabled || draft.groups[group].length >= (group === 'line_items' ? 500 : 100)} onClick={() => { edit(appendRow(draft, group)); setQueries(previous => ({ ...previous, [group]: '' })); }}><Plus size={14} />Add {group === 'line_items' ? 'product' : group === 'tax_components' ? 'tax' : 'adjustment'}</button></div>
          <InvoiceRowsTable products={group === 'line_items' ? liveProducts : undefined} verificationUnavailable={!!liveError} group={group} rows={draft.groups[group].filter(row => filteredRows[group].includes(row) || focused === row.id)} allRows={draft.groups[group]} originals={Array.isArray(context.invoice[group]) ? context.invoice[group] as RecordValue[] : []} label={group === 'line_items' ? 'Product' : group === 'tax_components' ? 'Tax' : 'Adjustment'} disabled={disabled} onUpdate={(id, key, value) => edit(updateRow(draft, group, id, key, value))} onRemove={id => { edit(removeRow(draft, group, id)); setFocused(''); }} onFocus={setFocused} onBlur={blur} onTaxChange={group === 'line_items' ? (id, action, taxId, key, value) => edit(changeLineTax(draft, id, action, taxId, key, value)) : undefined} />
        </div>)}
        <div className="invoice-review-actions"><button className="button subtle small" type="button" disabled={disabled || !changes.length} onClick={() => setResetPrompt(true)}><RotateCcw size={14} />Reset edits</button><button className="button subtle small" type="button" disabled={disabled} onClick={() => void checkCorrections()}>{busy === 'preview' ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}Check corrections</button></div>
        {resetPrompt && <div className="invoice-review-reset" role="alert"><span>Restore all original extracted values?</span><button className="text-button" onClick={() => setResetPrompt(false)}>Keep edits</button><button className="button danger small" disabled={!!busy} onClick={reset}>Reset all fields</button></div>}
        {context.editable && <section className="invoice-review-submit"><h4>Record your review</h4><div className="review-fields"><label>Your name<input value={reviewer} disabled={!!busy} onChange={event => setReviewer(event.target.value)} maxLength={100} placeholder="Reviewer name" /></label><label>Correction / verification reason<textarea value={comment} disabled={!!busy} onChange={event => setComment(event.target.value)} maxLength={2000} rows={3} placeholder="Describe the source checks and corrections…" /></label></div><label className="po-checkbox"><input type="checkbox" checked={verified} disabled={!!busy} onChange={event => setVerified(event.target.checked)} /><span>I verified the submitted invoice fields against the source, including values unchanged or hidden by search.</span></label>{error && <div className="alert error" role="alert"><AlertTriangle size={17} />{error}</div>}<button className="button primary" type="button" disabled={!!busy || !verified || !reviewer.trim() || comment.trim().length < 3} onClick={() => void submit()}>{busy === 'submit' ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}{busy === 'submit' ? 'Reprocessing…' : 'Save & reprocess'}</button></section>}
      </div>
    </div>
  </div>;
}
