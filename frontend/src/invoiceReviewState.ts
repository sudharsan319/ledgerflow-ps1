/** Review drafts retain source row identities and never use filtered arrays as payloads. */
export type RecordValue = Record<string, unknown>;
export type Group = 'line_items' | 'tax_components' | 'adjustments';
export type DraftRow = { id: string; originalIndex: number | null; value: RecordValue; taxes?: DraftRow[] };
export type ReviewDraft = { base: RecordValue; groups: Record<Group, DraftRow[]> };
export type FieldChange = { field: string; before: unknown; after: unknown };
export const GROUPS: Group[] = ['line_items', 'tax_components', 'adjustments'];
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
export const textValue = (value: unknown) => value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
export function componentTaxInput(value: RecordValue): RecordValue {
  if (!Array.isArray(value.tax_components) || !value.tax_components.length) return value;
  const result = { ...value };
  for (const key of ['tax', 'tax_rate', 'tax_evidence', 'tax_rate_evidence']) delete result[key];
  return result;
}
export function createDraft(invoice: RecordValue): ReviewDraft {
  const base = clone(invoice);
  return { base, groups: Object.fromEntries(GROUPS.map(group => [group, (Array.isArray(base[group]) ? base[group] as RecordValue[] : []).map((value, index) => ({ id: `${group}:${index}`, originalIndex: index, value: group === 'line_items' ? componentTaxInput(clone(value)) : clone(value), ...(group === 'line_items' && Array.isArray(value.tax_components) ? { taxes: (value.tax_components as RecordValue[]).map((tax, taxIndex) => ({ id: `${group}:${index}:tax:${taxIndex}`, originalIndex: taxIndex, value: clone(tax) })) } : {}) }))])) as Record<Group, DraftRow[]> };
}
export function updateHeader(draft: ReviewDraft, key: string, value: unknown): ReviewDraft {
  const base = { ...draft.base }; if (value === undefined) delete base[key]; else base[key] = value;
  if (key === 'tax' && 'tax_status' in base) base.tax_status = value == null || value === '' ? (draft.groups.tax_components.length ? 'stated' : 'not_stated') : 'stated';
  return { ...draft, base };
}
export function updateRow(draft: ReviewDraft, group: Group, id: string, key: string, value: unknown): ReviewDraft {
  const base = { ...draft.base };
  if (group === 'tax_components' && (key === 'amount' || key === 'rate')) base.tax_status = 'stated';
  return { ...draft, base, groups: { ...draft.groups, [group]: draft.groups[group].map(row => {
    if (row.id !== id) return row;
    const next = { ...row.value }; if (value === undefined) delete next[key]; else next[key] = value;
    if (group === 'line_items' && (key === 'discount_amount' || key === 'discount_percent')) {
      const other = key === 'discount_amount' ? 'discount_percent' : 'discount_amount';
      // A zero default is not a second, conflicting discount representation.
      if (value != null && value !== '' && Number(value) !== 0 && next[other] != null && Number(next[other]) === 0) next[other] = null;
    }
    if (group === 'line_items' && (key === 'tax' || key === 'tax_rate')) {
      if (key === 'tax_rate' && row.value.tax_status === 'not_stated' && row.value.tax === '0') next.tax = null;
      next.tax_status = value == null || value === '' ? (row.taxes?.length || next[key === 'tax' ? 'tax_rate' : 'tax'] != null ? 'stated' : 'not_stated') : 'stated';
    }
    return { ...row, value: next };
  }) } };
}
export function appendRow(draft: ReviewDraft, group: Group): ReviewDraft {
  let index = 1; while (draft.groups[group].some(row => row.id === `${group}:new:${index}`)) index++;
  const value: RecordValue = group === 'line_items' ? { description: '', quantity: null, unit_price: null, amount: null, discount_amount: '0', discount_percent: '0', tax: '0', tax_status: 'not_stated' } : group === 'tax_components' ? { label: '', amount: null, rate: null, taxable_amount: null, evidence: '' } : { label: '', kind: 'charge', amount: null, percent: null, base_amount: null, included_in_subtotal: false, evidence: '' };
  const base = { ...draft.base };
  if (group === 'tax_components' && base.tax_status === 'not_stated') { base.tax = null; base.tax_status = 'stated'; }
  return { ...draft, base, groups: { ...draft.groups, [group]: [...draft.groups[group], { id: `${group}:new:${index}`, originalIndex: null, value }] } };
}
export function removeRow(draft: ReviewDraft, group: Group, id: string): ReviewDraft {
  return { ...draft, groups: { ...draft.groups, [group]: draft.groups[group].filter(row => row.id !== id) } };
}
export function buildInvoice(draft: ReviewDraft): RecordValue {
  const invoice = clone(draft.base);
  for (const group of GROUPS) {
    if (group in draft.base || draft.groups[group].length) invoice[group] = draft.groups[group].map(row => rowValue(row));
    else delete invoice[group];
  }
  return invoice;
}
export function rowValue(row: DraftRow): RecordValue {
  return componentTaxInput({ ...clone(row.value), ...(row.taxes ? { tax_components: row.taxes.map(tax => clone(tax.value)) } : {}) });
}
export function changeLineTax(draft: ReviewDraft, lineId: string, action: 'add' | 'remove' | 'update', taxId?: string, key?: string, value?: unknown): ReviewDraft {
  return { ...draft, groups: { ...draft.groups, line_items: draft.groups.line_items.map(row => {
    if (row.id !== lineId) return row;
    let taxes = row.taxes || [];
    if (action === 'add') {
      let index = 1; while (taxes.some(tax => tax.id === `${lineId}:new-tax:${index}`)) index++;
      taxes = [...taxes, { id: `${lineId}:new-tax:${index}`, originalIndex: null, value: { label: '', rate: null, amount: null, taxable_amount: null } }];
    } else if (action === 'remove') taxes = taxes.filter(tax => tax.id !== taxId);
    else taxes = taxes.map(tax => tax.id === taxId && key ? { ...tax, value: { ...tax.value, [key]: value } } : tax);
    const lineValue = { ...row.value };
    if (taxes.length) for (const key of ['tax', 'tax_rate', 'tax_evidence', 'tax_rate_evidence']) delete lineValue[key];
    lineValue.tax_status = taxes.length || lineValue.tax != null || lineValue.tax_rate != null ? 'stated' : 'not_stated';
    return { ...row, value: lineValue, taxes };
  }) } };
}
export function getPath(value: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>((current, key) => current && typeof current === 'object' ? (current as RecordValue)[key] : undefined, value);
}
export function changesBetween(before: RecordValue, after: RecordValue): FieldChange[] {
  const ignored = new Set(['extraction_method', 'extraction_source', 'extraction_confidence', 'field_evidence', 'evidence', 'warnings', 'normalization_warnings', 'notes']);
  const changes: FieldChange[] = [];
  function visit(left: unknown, right: unknown, path: string) {
    if (JSON.stringify(left ?? null) === JSON.stringify(right ?? null)) return;
    if (Array.isArray(left) && Array.isArray(right)) {
      for (let index = 0; index < Math.max(left.length, right.length); index++) visit(left[index], right[index], `${path}.${index}`);
    } else if (left && right && typeof left === 'object' && typeof right === 'object' && !Array.isArray(left) && !Array.isArray(right)) {
      for (const key of new Set([...Object.keys(left), ...Object.keys(right)])) {
        if (!path && ignored.has(key)) continue;
        visit((left as RecordValue)[key], (right as RecordValue)[key], path ? `${path}.${key}` : key);
      }
    } else changes.push({ field: path, before: left ?? null, after: right ?? null });
  }
  visit(before, after, '');
  return changes;
}
export function fieldLabel(path: string): string {
  return path.split('.').map(part => /^\d+$/.test(part) ? String(Number(part) + 1) : part.replaceAll('_', ' ')).join(' › ');
}
export type FieldDefinition = { key: string; label: string; kind?: 'decimal' | 'integer' | 'select' | 'boolean'; options?: [string, string][]; help?: string; wide?: boolean; manualOnly?: boolean };
// The review UI exposes invoice values, not extraction/calculation metadata.
export const HEADER_FIELDS: FieldDefinition[] = [
  { key: 'invoice_number', label: 'Invoice number' }, { key: 'vendor_name', label: 'Supplier name' },
  { key: 'vendor_id', label: 'Supplier ID' }, { key: 'po_number', label: 'PO reference' },
  { key: 'invoice_date', label: 'Invoice date' }, { key: 'due_date', label: 'Due date' },
  { key: 'currency', label: 'Currency' }, { key: 'subtotal', label: 'Subtotal', kind: 'decimal' },
  { key: 'taxable_subtotal', label: 'Taxable subtotal', kind: 'decimal' },
  { key: 'tax', label: 'Total tax', kind: 'decimal' }, { key: 'total', label: 'Invoice total', kind: 'decimal' },
  { key: 'tax_mode', label: 'Subtotal includes tax', kind: 'select', manualOnly: true, options: [['unknown', 'Unresolved'], ['exclusive', 'No'], ['inclusive', 'Yes']] },
];
export const ROW_FIELDS: Record<Group, FieldDefinition[]> = {
  line_items: [
    { key: 'description', label: 'Product' }, { key: 'sku', label: 'SKU' },
    { key: 'quantity', label: 'Quantity', kind: 'decimal' }, { key: 'unit_price', label: 'Unit price', kind: 'decimal' },
    { key: 'discount_amount', label: 'Discount', kind: 'decimal' }, { key: 'discount_percent', label: 'Discount (%)', kind: 'decimal' },
    { key: 'tax_rate', label: 'Tax (%)', kind: 'decimal' }, { key: 'tax', label: 'Tax amount', kind: 'decimal' },
    { key: 'amount', label: 'Printed line total', kind: 'decimal' },
    { key: 'unit_price_tax_mode', label: 'Unit price includes tax', kind: 'select', manualOnly: true, options: [['unknown', 'Unresolved'], ['exclusive', 'No'], ['inclusive', 'Yes']] },
    { key: 'amount_tax_mode', label: 'Line total includes tax', kind: 'select', manualOnly: true, options: [['unknown', 'Unresolved'], ['exclusive', 'No'], ['inclusive', 'Yes']] },
  ],
  tax_components: [{ key: 'label', label: 'Tax name' }, { key: 'rate', label: 'Rate (%)', kind: 'decimal' }, { key: 'amount', label: 'Amount', kind: 'decimal' }, { key: 'taxable_amount', label: 'Taxable base', kind: 'decimal' }],
  adjustments: [{ key: 'label', label: 'Adjustment' }, { key: 'kind', label: 'Type', kind: 'select', options: [['charge', 'Charge'], ['discount', 'Discount'], ['rounding', 'Round-off'], ['unknown', 'Unclassified']] }, { key: 'amount', label: 'Amount', kind: 'decimal' }, { key: 'percent', label: 'Percentage', kind: 'decimal' }, { key: 'base_amount', label: 'Base amount', kind: 'decimal' }],
};
export const hasValue = (value: unknown) => value !== null && value !== undefined && value !== '';
export function reviewValue(record: RecordValue, key: string): unknown {
  if (hasValue(record[key])) return record[key];
  if (['discount_amount', 'discount_percent'].includes(key) && !hasValue(record.discount_amount) && !hasValue(record.discount_percent)) return '0';
  return record[key];
}
export function reviewColumns(group: Group, records: RecordValue[], added: string[] = []): FieldDefinition[] {
  const core = group === 'line_items' ? ['description', 'quantity', 'unit_price', 'amount'] : group === 'tax_components' ? ['label'] : ['label', 'kind', 'amount'];
  return ROW_FIELDS[group].filter(field => core.includes(field.key) || added.includes(field.key) || !field.manualOnly && records.some(record => {
    const value = reviewValue(record, field.key);
    // The zero amount already represents no discount; omit a redundant 0% column.
    return hasValue(value) && (field.key !== 'discount_percent' || Number(value) !== 0);
  }));
}
export function reviewSearchValues(group: Group, row: DraftRow): unknown[] {
  return [ROW_FIELDS[group].map(field => reviewValue(row.value, field.key)), ...(row.taxes || []).map(tax => reviewSearchValues('tax_components', tax))];
}
export function inputValue(field: FieldDefinition, value: string): unknown {
  if (field.kind === 'boolean') return value === 'true' ? true : value === 'false' ? false : null;
  if (field.kind === 'select') return value || undefined;
  if (field.kind === 'integer') return value === '' ? null : /^[0-6]$/.test(value) ? Number(value) : value;
  return value === '' ? null : value;
}
