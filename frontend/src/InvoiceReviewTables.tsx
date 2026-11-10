import { Fragment, useState } from 'react';
import type { ChangeEvent } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { HEADER_FIELDS, ROW_FIELDS, hasValue, inputValue, reviewColumns, reviewValue, textValue } from './invoiceReviewState';
import { verificationLabel } from './liveLinePreview';
import type { ProductCalculation } from './liveLinePreview';
import type { DraftRow, FieldDefinition, Group, RecordValue } from './invoiceReviewState';

type InputProps = { field: FieldDefinition; value: unknown; label?: string; disabled: boolean; readOnly?: boolean; onChange: (value: unknown) => void };
export function ReviewInput({ field, value, label = field.label, disabled, readOnly = false, onChange }: InputProps) {
  const content = textValue(value);
  const common = { 'aria-label': label, disabled, readOnly, value: content, onChange: (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => onChange(inputValue(field, event.target.value)) };
  return field.kind === 'select'
    ? <select {...common}>{field.options?.map(([value, title]) => <option key={value} value={value}>{title}</option>)}{!field.options?.some(([value]) => value === content) && <option value={content}>{content || '—'}</option>}</select>
    : <input {...common} type="text" inputMode={field.kind === 'decimal' ? 'decimal' : undefined} autoComplete="off" spellCheck={false} />;
}

function AddColumn({ available, disabled, onAdd, label = 'Add column' }: { available: FieldDefinition[]; disabled: boolean; onAdd: (key: string) => void; label?: string }) {
  return available.length ? <select className="invoice-review-add-column" aria-label={label} value="" disabled={disabled} onChange={event => onAdd(event.target.value)}><option value="">{label}…</option>{available.map(field => <option key={field.key} value={field.key}>{field.label}</option>)}</select> : null;
}

export function InvoiceHeaderTable({ fields, invoice, disabled, onChange, onAdd, onFocus, onBlur }: {
  fields: FieldDefinition[]; invoice: RecordValue; disabled: boolean; onChange: (key: string, value: unknown) => void; onAdd: (key: string) => void;
  onFocus: (key: string) => void; onBlur: (key: string) => void;
}) {
  return <><div className="invoice-review-list-heading"><h4>Invoice fields</h4><AddColumn label="Add field" available={HEADER_FIELDS.filter(field => (field.manualOnly || !hasValue(invoice[field.key])) && !fields.some(item => item.key === field.key))} disabled={disabled} onAdd={onAdd} /></div>
    <div className="table-scroll"><table className="invoice-review-table invoice-review-header-table"><thead><tr><th scope="col">Field</th><th scope="col">Value</th></tr></thead><tbody>{fields.map(field => <tr key={field.key} onFocusCapture={() => onFocus(field.key)} onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) onBlur(field.key); }}><th scope="row">{field.label}</th><td><ReviewInput field={field} value={invoice[field.key]} disabled={disabled} onChange={value => onChange(field.key, value)} /></td></tr>)}</tbody></table></div>
    {!fields.length && <p className="list-search-empty">No invoice fields match your search.</p>}
  </>;
}

type RowsProps = {
  group: Group; rows: DraftRow[]; allRows: DraftRow[]; originals: RecordValue[]; disabled: boolean; label: string;
  onUpdate: (id: string, key: string, value: unknown) => void; onRemove: (id: string) => void;
  onFocus: (id: string) => void; onBlur: (id: string) => void;
  products?: ProductCalculation[]; verificationUnavailable?: boolean;
  onTaxChange?: (lineId: string, action: 'add' | 'remove' | 'update', taxId?: string, key?: string, value?: unknown) => void;
};
function ProductTaxCell({ row, original, index, product, disabled, value, onUpdate, onTaxChange }: {
  row: DraftRow; original: RecordValue; index: number; product?: ProductCalculation; disabled: boolean; value: unknown;
  onUpdate: RowsProps['onUpdate']; onTaxChange: RowsProps['onTaxChange'];
}) {
  const [showBases, setShowBases] = useState(false);
  const taxes = row.taxes || [];
  const originalTaxes = Array.isArray(original.tax_components) ? original.tax_components as RecordValue[] : [];
  const rateField = ROW_FIELDS.line_items.find(field => field.key === 'tax_rate')!;
  const baseField = ROW_FIELDS.tax_components.find(field => field.key === 'taxable_amount')!;
  return <div className="invoice-product-tax-cell">
    <div className="invoice-product-tax-total"><ReviewInput field={rateField} value={value} readOnly={!!taxes.length} label={`Product ${index + 1}: Tax (%)`} disabled={disabled} onChange={value => onUpdate(row.id, 'tax_rate', value)} /><span>%</span></div>
    {!!taxes.length && <div className="invoice-product-tax-split" role="group" aria-label={`Product ${index + 1} tax breakdown`}>
      <div className="invoice-product-tax-labels" aria-hidden="true"><span>Tax</span><span>%</span><span>Amount</span><span /></div>
      {taxes.map((tax, taxIndex) => {
        const originalTax = tax.originalIndex == null ? {} : originalTaxes[tax.originalIndex] || {};
        const resolved = product?.resolved_tax_components?.find(component => component.index === taxIndex);
        return <div key={tax.id} className="invoice-product-tax-component">
          <div className="invoice-product-tax-inputs">{ROW_FIELDS.tax_components.filter(field => field.key !== 'taxable_amount').map(field => <ReviewInput key={field.key} field={field} value={hasValue(tax.value[field.key]) || field.key === 'label' ? tax.value[field.key] : resolved?.[field.key as 'rate' | 'amount']} label={`Product ${index + 1} tax ${taxIndex + 1}: ${field.label}`} disabled={disabled || !onTaxChange} onChange={value => onTaxChange?.(row.id, 'update', tax.id, field.key, value)} />)}<button type="button" className="icon-button" disabled={disabled || !onTaxChange} aria-label={`Remove product ${index + 1} tax ${taxIndex + 1}`} onClick={() => onTaxChange?.(row.id, 'remove', tax.id)}><Trash2 size={12} /></button></div>
          {(showBases || hasValue(tax.value.taxable_amount) || hasValue(originalTax.taxable_amount)) && <label className="invoice-product-tax-base">Printed taxable base<ReviewInput field={baseField} value={tax.value.taxable_amount} label={`Product ${index + 1} tax ${taxIndex + 1}: Taxable base`} disabled={disabled || !onTaxChange} onChange={value => onTaxChange?.(row.id, 'update', tax.id, 'taxable_amount', value)} /></label>}
        </div>;
      })}
    </div>}
    {!taxes.length && product?.tax_from_document && <div className="invoice-product-tax-inherited">{(['cgst', 'sgst'] as const).filter(key => { const tax = product.tax_summary[key]; return tax.rate == null || tax.amount == null || Number(tax.rate) !== 0 || Number(tax.amount) !== 0; }).map(key => <div key={key}><span>{key.toUpperCase()}</span><span>{product.tax_summary[key].rate ?? '—'}%</span><span>{product.tax_summary[key].amount ?? '—'}</span></div>)}</div>}
    {onTaxChange && <div className="invoice-product-tax-tools"><button type="button" className="text-button" disabled={disabled || taxes.length >= 20} aria-label={`Add tax to product ${index + 1}`} onClick={() => onTaxChange(row.id, 'add')}><Plus size={12} />Add tax</button>{!!taxes.length && !showBases && !taxes.every(tax => hasValue(tax.value.taxable_amount)) && <button type="button" className="text-button" disabled={disabled} onClick={() => setShowBases(true)}>Add taxable base</button>}</div>}
  </div>;
}

export function InvoiceRowsTable({ group, rows, allRows, originals, disabled, label, onUpdate, onRemove, onFocus, onBlur, onTaxChange, products, verificationUnavailable = false }: RowsProps) {
  const [addedColumns, setAddedColumns] = useState<string[]>([]);
  const columns = reviewColumns(group, [...originals, ...allRows.map(row => row.value)], [...addedColumns, ...(group === 'line_items' ? ['tax', 'tax_rate'] : []), ...(group === 'tax_components' && allRows.some(row => row.originalIndex == null) ? ['amount', 'rate'] : [])]);
  const available = ROW_FIELDS[group].filter(field => !columns.some(column => column.key === field.key));
  return <>
    {!!allRows.length && <div className="invoice-review-column-tools"><AddColumn label={`Add ${label.toLowerCase()} column`} available={available} disabled={disabled} onAdd={key => setAddedColumns(previous => [...previous, key])} /></div>}
    {!!rows.length && <div className="table-scroll" tabIndex={0} aria-label={`${label} editable table`}><table className="invoice-review-table"><thead><tr><th scope="col">#</th>{columns.map(field => <th scope="col" key={field.key}>{group === 'line_items' && field.key === 'tax_rate' ? 'Tax' : field.label}</th>)}{group === 'line_items' && <th scope="col" className="invoice-verification-cell">Verification</th>}<th scope="col"><span className="sr-only">Actions</span></th></tr></thead><tbody>
      {rows.map(row => {
        const index = allRows.findIndex(item => item.id === row.id);
        const product = products?.find(item => item.index === index);
        const original = row.originalIndex == null ? {} : originals[row.originalIndex] || {};
        return <tr key={row.id} onFocusCapture={() => onFocus(row.id)} onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) onBlur(row.id); }}>
          <th scope="row">{index + 1}</th>{columns.map(field => {
            const isTax = field.key === 'tax' || field.key === 'tax_rate';
            const combined = group === 'line_items' && isTax && !!row.taxes?.length;
            const derived = group === 'line_items' && isTax && (!hasValue(row.value[field.key]) || field.key === 'tax' && row.value.tax_status === 'not_stated' && Number(row.value.tax) === 0);
            const value = combined || derived ? product?.tax_summary[field.key as 'tax' | 'tax_rate'] : reviewValue(row.value, field.key);
            if (group === 'line_items' && field.key === 'tax_rate') return <td key={field.key} className="invoice-product-tax-column"><ProductTaxCell row={row} original={original} index={index} product={product} disabled={disabled} value={value} onUpdate={onUpdate} onTaxChange={onTaxChange} /></td>;
            return <td key={field.key} className={field.key === 'description' || field.key === 'label' ? 'invoice-review-description-cell' : ''}><ReviewInput field={field} value={value} readOnly={combined} label={`${label} ${index + 1}: ${field.label}`} disabled={disabled} onChange={value => onUpdate(row.id, field.key, value)} /></td>;
          })}
          {group === 'line_items' && <td className="invoice-verification-cell"><LineVerificationStatus product={product} unavailable={verificationUnavailable} /></td>}
          <td><div className="invoice-review-row-tools"><button type="button" className="icon-button" disabled={disabled} aria-label={`Remove ${label.toLowerCase()} ${index + 1}`} onClick={() => onRemove(row.id)}><Trash2 size={14} /></button></div></td>
        </tr>;
      })}
    </tbody></table></div>}
    {!rows.length && <p className="list-search-empty">{allRows.length ? 'No rows match your search.' : `No ${label.toLowerCase()} recorded.`}</p>}
  </>;
}

export function LineVerificationStatus({ product, unavailable = false, details = false }: { product?: ProductCalculation; unavailable?: boolean; details?: boolean }) {
  return <div className="invoice-line-verification" aria-live="polite"><span className={`invoice-line-badge ${product?.verification.status || 'pending'}`}>{verificationLabel(product, unavailable)}</span>{product && <small>{product.verification.calculated_amount == null ? 'Calculation unavailable' : `Calculated: ${product.verification.calculated_amount}`} {product.verification.difference != null && <> · Difference: {product.verification.difference}</>}</small>}{(details ? product?.verification.messages : product?.verification.messages.slice(0, 1))?.map((message, index) => <small key={index}>{message}</small>)}</div>;
}
export function LineVerificationTable({ rows, allRows, products, unavailable }: { rows: DraftRow[]; allRows: DraftRow[]; products?: ProductCalculation[]; unavailable: boolean }) {
  return <div className="table-scroll"><table className="invoice-review-table invoice-verification-table"><thead><tr><th>#</th><th>Product</th><th>Printed tax</th><th>Calculated tax</th><th>Printed line total</th><th>Calculated line total</th><th>Difference</th><th>Verification</th></tr></thead><tbody>{rows.map(row => {
    const index = allRows.findIndex(item => item.id === row.id);
    const product = products?.find(item => item.index === index);
    return <Fragment key={row.id}><tr><th scope="row">{index + 1}</th><td>{textValue(row.value.description)}</td><td>{product?.printed_tax ?? '—'}</td><td>{product?.calculated_tax ?? '—'}</td><td>{textValue(row.value.amount) || '—'}</td><td>{product?.verification.calculated_amount ?? '—'}</td><td>{product?.verification.difference ?? '—'}</td><td><LineVerificationStatus product={product} unavailable={unavailable} details /></td></tr>{product?.verification.formula && <tr><td colSpan={8} className="invoice-line-formula">{product.verification.formula} = {product.verification.calculated_amount} · Rounding allowance: {product.verification.tolerance}</td></tr>}</Fragment>;
  })}</tbody></table>{!rows.length && <p className="list-search-empty">No products match your search.</p>}</div>;
}
