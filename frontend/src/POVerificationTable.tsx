import type { DraftRow } from './invoiceReviewState';
import { textValue } from './invoiceReviewState';
import type { POReconciliation, PreviewCheck } from './liveLinePreview';

export default function POVerificationTable({ data, checks, rows, allRows, disabled, checking, onSelect }: {
  data?: POReconciliation | null; checks?: PreviewCheck[]; rows: DraftRow[]; allRows: DraftRow[];
  disabled: boolean; checking: boolean; onSelect: (id: string, value: string | null) => void;
}) {
  if (!data) return <div><p>{checking ? 'Checking the current PO and its invoice history…' : 'No PO matched. Set the supplier and PO reference in Invoice fields.'}</p>{checks?.map(c => <p key={c.code}>{c.message}</p>)}</div>;
  return <section aria-label="Live PO checks">
    <h4>{data.po_number} · {data.currency}</h4>
    <div className="table-scroll"><table className="invoice-review-table"><thead><tr><th>PO value</th><th>Previously invoiced</th><th>This invoice</th><th>Cumulative amount</th><th>Remaining amount</th><th>Amount status</th></tr></thead><tbody><tr><td>{data.ordered_total}</td><td>{data.previously_invoiced}</td><td>{data.current_total}</td><td>{data.cumulative_total ?? '—'}</td><td>{data.remaining_amount ?? '—'}</td><td>{data.amount_status.replaceAll('_', ' ')}</td></tr></tbody></table></div>
    <p className="field-help">{data.prior_invoice_count} earlier approved invoice(s)</p>
    <div className="table-scroll"><table className="invoice-review-table"><thead><tr><th>Invoice product</th><th>PO product</th><th>Ordered</th><th>Previously invoiced</th><th>This invoice</th><th>Cumulative quantity</th><th>Remaining quantity</th><th>Net unit price / PO price</th><th>Verification</th></tr></thead><tbody>{rows.map(row => {
      const index = allRows.findIndex(r => r.id === row.id), allocation = data.lines.find(l => l.index === index);
      const selected = textValue(row.value.po_line_ref);
      return <tr key={row.id}><td>{index + 1}. {textValue(row.value.description)}</td><td><select aria-label={`Product ${index+1}: PO product`} value={selected} disabled={disabled} onChange={e => onSelect(row.id, e.target.value || null)}><option value="">{allocation?.po_description ? `Automatic: ${allocation.po_description}` : 'Select matching PO product…'}</option>{selected && !data.options.some(o => o.ref===selected) && <option value={selected}>Previous selection is unavailable</option>}{data.options.map(o => <option key={o.ref} value={o.ref}>{o.index+1}. {o.description}{o.sku ? ` (${o.sku})` : ''}</option>)}</select></td><td>{allocation?.ordered_quantity ?? '—'}</td><td>{allocation?.previously_invoiced ?? '—'}</td><td>{textValue(row.value.quantity) || '—'}</td><td>{allocation?.cumulative_quantity ?? '—'}</td><td>{allocation?.remaining_quantity ?? '—'}</td><td>{allocation?.invoice_net_unit_price ?? '—'} / {allocation?.po_unit_price ?? '—'}</td><td><strong>{allocation?.status.replaceAll('_',' ') ?? 'Not checked'}</strong>{allocation?.messages.map((message,i)=><p className="field-help" key={i}>{message}</p>)}</td></tr>;
    })}</tbody></table></div>
    {data.message && <p>{data.message}</p>}
    {!rows.length && <p className="list-search-empty">No products match your search.</p>}
    {checks?.filter(c => c.status !== 'pass').map(c => <p className="field-help" key={c.code}>{c.message}</p>)}
    <p className="field-help">{data.receipt_message}</p>
  </section>;
}
