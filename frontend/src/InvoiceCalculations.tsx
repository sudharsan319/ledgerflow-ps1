import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, FileText, LoaderCircle } from 'lucide-react';
import { ListSearch } from './ListSearch';
import { matchesSearch } from './search';

type Step = { id: string; label: string; formula: string; calculated: string; printed?: string | null; difference?: string | null; tolerance?: string; status: string; evidence?: string; references?: string[] };
type Check = { code: string; label: string; status: string; message: string; blocking: boolean };
type Product = { index: number; description: string; quantity: string | null; unit_price: string | null; tax: string | null; total: string | null; document_tax_separate: boolean };
type Analysis = { products?: Product[]; engine: string; version?: string; status: string; calculated_total?: string | null; printed_total?: string | null; difference?: string | null; currency?: string; steps?: Step[]; checks?: Check[]; warnings?: string[]; limitations?: string[]; defaults?: string[]; assumptions?: string[]; excluded?: { ref: string; reason: string; evidence: string }[]; error?: string; model?: string; completed_at?: string; plan?: unknown };
type Context = { run_id: string; invoice_fingerprint: string; rules: Analysis; rules_basis: string; superseded: boolean };
const labels: Record<string, string> = { reconciled: 'Reconciled', mismatch: 'Mismatch', needs_review: 'Needs review', matches_with_review: 'Arithmetic matches · review interpretation', error: 'Plan unavailable' };
const amount = (value?: string | null, currency?: string) => value == null ? '—' : `${value}${currency ? ` ${currency}` : ''}`;
async function request(path: string, signal: AbortSignal, method = 'GET'): Promise<Context> {
  const response = await fetch(path, { method, signal });
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`); }
  return response.json();
}
function Summary({ title, result }: { title: string; result: Analysis | null }) {
  return <section className="calculation-summary"><h3>{title}</h3><span className={`calculation-status ${result?.status || ''}`}>{result ? labels[result.status] || result.status : 'Not generated'}</span><dl><div><dt>Calculated total</dt><dd>{amount(result?.calculated_total, result?.currency)}</dd></div><div><dt>Printed total</dt><dd>{amount(result?.printed_total, result?.currency)}</dd></div><div><dt>Difference</dt><dd>{amount(result?.difference, result?.currency)}</dd></div></dl></section>;
}
function Details({ result, name }: { result: Analysis; name: string }) {
  const [query, setQuery] = useState('');
  const products = result.products || [];
  const visibleProducts = products.filter(product => matchesSearch(query, product.description, product.quantity, product.unit_price, product.tax, product.total));
  const steps = result.steps || [], checks = result.checks || [], exclusions = result.excluded || [], notes = [...(result.defaults || []), ...(result.warnings || []), ...(result.assumptions || []), ...(result.limitations || [])];
  const visibleSteps = steps.filter(step => matchesSearch(query, step.label, step.formula, step.calculated, step.printed, step.difference, step.status, step.evidence, step.references));
  const visibleChecks = checks.filter(check => matchesSearch(query, check.label, check.message, check.status));
  const visibleExclusions = exclusions.filter(item => matchesSearch(query, item.ref, item.reason, item.evidence));
  const visibleNotes = notes.filter(note => matchesSearch(query, note));
  const total = products.length + steps.length + checks.length + exclusions.length + notes.length;
  const matched = visibleProducts.length + visibleSteps.length + visibleChecks.length + visibleExclusions.length + visibleNotes.length;
  return <div className="calculation-details">
    {result.error && <div className="alert error" role="alert"><AlertTriangle size={17} />{result.error}</div>}
    {total > 0 && <ListSearch query={query} onQueryChange={setQuery} label={`Search ${name} calculations`} placeholder="Search equation, field, difference, or explanation…" matched={matched} total={total} />}
    {!!total && !matched && <p className="list-search-empty">No matching calculation details.</p>}
    {!!visibleProducts.length && <div className="table-scroll"><table className="line-table"><thead><tr><th>Product</th><th>Qty</th><th>Unit price</th><th>Product tax</th><th>Product total</th></tr></thead><tbody>{visibleProducts.map(product => <tr key={product.index}><td>{product.description}{product.document_tax_separate && <small className="field-help"> Invoice-level tax is applied separately</small>}</td><td>{product.quantity ?? '—'}</td><td>{product.unit_price ?? '—'}</td><td>{product.tax ?? 'Needs review'}</td><td>{product.total ?? 'Needs review'}</td></tr>)}</tbody></table></div>}
    {!!visibleSteps.length && <div className="table-scroll"><table className="line-table calculation-table"><thead><tr><th>Calculation</th><th>Computed</th><th>Printed</th><th>Difference</th></tr></thead><tbody>{visibleSteps.map(step => <tr key={step.id}><td><strong>{step.label}</strong><code>{step.formula}</code>{step.evidence && <small>Source note: {step.evidence}</small>}{step.tolerance && <small>Allowed rounding: {step.tolerance}</small>}</td><td>{step.calculated}</td><td>{step.printed ?? '—'}</td><td className={step.status === 'fail' ? 'calculation-mismatch' : ''}>{step.difference ?? '—'}</td></tr>)}</tbody></table></div>}
    {!!visibleChecks.length && <div className="checks-list">{visibleChecks.map(check => <div className={`check-row ${check.status}`} key={check.code}><span>{check.status === 'pass' ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}</span><div><strong>{check.label}</strong><p>{check.message}</p></div></div>)}</div>}
    {!!visibleNotes.length && <div className="calculation-notes">{visibleNotes.map((note, index) => <p key={index}>{note}</p>)}</div>}

  </div>;
}
export default function InvoiceCalculations({ runId, updatedAt, onShowSource, onCorrect }: { runId: string; updatedAt: string; onShowSource: () => void; onCorrect?: () => void }) {
  const [data, setData] = useState<Context | null>(null), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  useEffect(() => { const controller = new AbortController(); setError(''); request(`/api/runs/${encodeURIComponent(runId)}/calculations`, controller.signal).then(setData).catch(error => { if (!controller.signal.aborted) setError(error.message); }); return () => controller.abort(); }, [runId, updatedAt, retry]);
  const current = data?.run_id === runId ? data : null;
  return <div className="invoice-calculations">
    <div className="calculation-heading"><div><h3>Invoice calculations</h3></div><button className="button subtle small" onClick={onShowSource}><FileText size={15} />View source</button></div>
    {error && <div className="alert error" role="alert"><AlertTriangle size={17} /><span>{error}</span><button className="text-button" onClick={() => setRetry(retry + 1)}>Retry</button></div>}
    {!current && !error && <div className="loading-state"><LoaderCircle size={20} className="spin" />Loading calculations…</div>}
    {current && <>
      {current.rules_basis === 'current_rules_preview' && <p className="field-help">Current calculation preview · Original decision unchanged</p>}
      <Summary title="Invoice total" result={current.rules} />
      <Details result={current.rules} name="invoice" />
      {onCorrect && <div className="calculation-correct"><button className="button subtle small" onClick={onCorrect}>Review fields</button></div>}
    </>}
  </div>;
}
