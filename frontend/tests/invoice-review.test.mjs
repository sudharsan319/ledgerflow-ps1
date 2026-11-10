import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

const bundle = await build({ entryPoints: [new URL('../src/invoiceReviewState.ts', import.meta.url).pathname], bundle: true, write: false, format: 'esm', platform: 'node' });
const { createDraft, buildInvoice, updateHeader, updateRow, removeRow, appendRow, changesBetween, inputValue, changeLineTax } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const source = {
  invoice_number: 'PHOTO-1', tax: '670.98', total: '6674.00', tax_mode: 'inclusive',
  line_items: [
    { description: 'First product', quantity: '100', unit_price: '3.63', amount: '363.30', unit_price_precision: 2 },
    { description: 'Second product', quantity: '12', unit_price: '27.38', amount: '328.51', tax_rate: '18' },
  ],
  tax_components: [{ label: 'CGST', amount: '335.49', evidence: 'CGST 335.49' }, { label: 'SGST', amount: '335.49' }],
  adjustments: [{ kind: 'rounding', amount: '0.34', included_in_subtotal: false }],
  field_evidence: { total: 'Net Total 6674.00' }, extraction_confidence: .8,
};

test('editing a searched row retains all other rows and the immutable source', () => {
  const original = structuredClone(source);
  let draft = createDraft(source);
  const visible = draft.groups.line_items.filter(row => row.value.description.includes('Second'));
  draft = updateRow(draft, 'line_items', visible[0].id, 'quantity', '14');
  draft = updateHeader(draft, 'tax', '675.00');
  const payload = buildInvoice(draft);
  assert.equal(payload.line_items.length, 2);
  assert.deepEqual(payload.line_items[0], source.line_items[0]);
  assert.equal(payload.line_items[1].quantity, '14');
  assert.equal(payload.line_items[1].tax_rate, '18');
  assert.deepEqual(payload.tax_components, source.tax_components);
  assert.deepEqual(payload.adjustments, source.adjustments);
  assert.deepEqual(source, original);
  assert.deepEqual(changesBetween(source, payload).map(change => change.field).sort(), ['line_items.1.quantity', 'tax']);
});

test('deleting a filtered row and adding another preserves original row identities', () => {
  let draft = createDraft(source);
  const firstId = draft.groups.line_items[0].id;
  const secondId = draft.groups.line_items[1].id;
  draft = removeRow(draft, 'line_items', firstId);
  assert.equal(draft.groups.line_items[0].originalIndex, 1);
  draft = appendRow(draft, 'line_items');
  const newId = draft.groups.line_items[1].id;
  assert.notEqual(newId, firstId);
  assert.notEqual(newId, secondId);
  assert.equal(draft.groups.line_items[1].originalIndex, null);
  draft = updateRow(draft, 'line_items', secondId, 'amount', '400');
  assert.equal(buildInvoice(draft).line_items[0].description, 'Second product');
  assert.equal(buildInvoice(draft).line_items[0].amount, '400');
  assert.equal(buildInvoice(draft).line_items[1].amount, null);
});

test('removing taxes or adjustments affects only the selected entry', () => {
  let draft = createDraft(source);
  draft = removeRow(draft, 'tax_components', draft.groups.tax_components[0].id);
  draft = updateRow(draft, 'tax_components', draft.groups.tax_components[0].id, 'amount', '336');
  draft = appendRow(draft, 'adjustments');
  const newId = draft.groups.adjustments[1].id;
  draft = updateRow(draft, 'adjustments', newId, 'kind', 'discount');
  draft = updateRow(draft, 'adjustments', newId, 'amount', '20');
  draft = updateRow(draft, 'adjustments', newId, 'included_in_subtotal', false);
  const payload = buildInvoice(draft);
  assert.equal(payload.tax_components.length, 1);
  assert.equal(payload.tax_components[0].label, 'SGST');
  assert.equal(payload.tax_components[0].amount, '336');
  assert.deepEqual(payload.adjustments[0], source.adjustments[0]);
  assert.equal(payload.adjustments[1].amount, '20');
  assert.equal(payload.adjustments[1].included_in_subtotal, false);
});

test('unrecorded optional values stay absent until explicitly supplied', () => {
  const legacy = { invoice_number: 'OLD-1', line_items: [] };
  let draft = createDraft(legacy);
  assert.deepEqual(buildInvoice(draft), legacy);
  draft = updateHeader(draft, 'tax_mode', 'unknown');
  assert.equal(buildInvoice(draft).tax_mode, 'unknown');
  draft = updateHeader(draft, 'tax_mode', undefined);
  assert.deepEqual(buildInvoice(draft), legacy);
  assert.deepEqual(changesBetween(legacy, buildInvoice(draft)), []);
});

test('decimal text preserves precision and explicit blank/unknown/false values', () => {
  const amount = '12345678901234567890.000000000001';
  assert.equal(inputValue({ key: 'total', kind: 'decimal' }, amount), amount);
  assert.equal(inputValue({ key: 'tax', kind: 'decimal' }, '0'), '0');
  assert.equal(inputValue({ key: 'tax', kind: 'decimal' }, ''), null);
  assert.equal(inputValue({ key: 'tax_mode', kind: 'select' }, 'unknown'), 'unknown');
  assert.equal(inputValue({ key: 'tax_mode', kind: 'select' }, ''), undefined);
  assert.equal(inputValue({ key: 'included_in_subtotal', kind: 'boolean' }, 'false'), false);
  assert.equal(inputValue({ key: 'included_in_subtotal', kind: 'boolean' }, ''), null);
  assert.equal(inputValue({ key: 'unit_price_precision', kind: 'integer' }, '2'), 2);
  assert.equal(inputValue({ key: 'unit_price_precision', kind: 'integer' }, 'invalid'), 'invalid');
});

test('audit preview includes deleted rows and ignores extraction metadata', () => {
  const after = structuredClone(source);
  after.line_items.pop();
  after.extraction_confidence = 1;
  after.field_evidence.total = 'Reviewer-provided value';
  assert.deepEqual(changesBetween(source, after), [{ field: 'line_items.1', before: source.line_items[1], after: null }]);
  assert.deepEqual(buildInvoice(createDraft(source)), source);
});


test('product tax edits retain all products and original component identities', () => {
  const original = structuredClone(source);
  original.line_items[0].tax_components = [{ label: 'CGST', rate: '9', amount: '9' }, { label: 'SGST', rate: '9', amount: '9' }];
  let draft = createDraft(original);
  const lineId = draft.groups.line_items[0].id;
  const [cgst, sgst] = draft.groups.line_items[0].taxes;
  draft = changeLineTax(draft, lineId, 'remove', cgst.id);
  assert.equal(draft.groups.line_items[0].taxes[0].originalIndex, 1);
  draft = changeLineTax(draft, lineId, 'update', sgst.id, 'amount', '10');
  draft = changeLineTax(draft, lineId, 'add');
  const newTax = draft.groups.line_items[0].taxes[1];
  assert.equal(newTax.originalIndex, null);
  draft = changeLineTax(draft, lineId, 'update', newTax.id, 'label', 'Cess');
  draft = changeLineTax(draft, lineId, 'update', newTax.id, 'amount', '1');
  const payload = buildInvoice(draft);
  assert.equal(payload.line_items[0].tax_components[0].label, 'SGST');
  assert.equal(payload.line_items[0].tax_components[0].amount, '10');
  assert.equal(payload.line_items[0].tax_components[1].amount, '1');
  assert.deepEqual(payload.line_items[1], original.line_items[1]);
  assert.equal(original.line_items[0].tax_components[0].amount, '9');
});

test('replacing a combined tax with components does not resurrect the old rate on removal', () => {
  let draft = createDraft(source);
  const id = draft.groups.line_items[1].id;
  draft = changeLineTax(draft, id, 'add');
  draft = changeLineTax(draft, id, 'remove', draft.groups.line_items[1].taxes[0].id);
  const payload = buildInvoice(draft);
  assert.deepEqual(payload.line_items[1].tax_components, []);
  assert.equal(payload.line_items[1].tax_rate, undefined);
  assert.equal(payload.line_items[1].tax_status, 'not_stated');
  assert.equal(source.line_items[1].tax_rate, '18');
  assert.deepEqual(payload.line_items[0], source.line_items[0]);
});


test('entering a discount replaces a zero counterpart without losing other invoice data', () => {
  for (const [key, other] of [['discount_amount', 'discount_percent'], ['discount_percent', 'discount_amount']]) {
    const original = { ...source, line_items: [{ ...source.line_items[0], discount_amount: '0', discount_percent: '0' }] };
    const draft = createDraft(original);
    const payload = buildInvoice(updateRow(draft, 'line_items', draft.groups.line_items[0].id, key, '10'));
    assert.equal(payload.line_items[0][key], '10');
    assert.equal(payload.line_items[0][other], null);
    assert.equal(original.line_items[0][other], '0');
    assert.deepEqual(payload.tax_components, source.tax_components);
  }
});

test('correcting unreadable invoice tax components updates internal status without a question', () => {
  const original = { ...source, tax_status: 'unclear' };
  let draft = createDraft(original);
  draft = updateRow(draft, 'tax_components', draft.groups.tax_components[0].id, 'amount', '335.49');
  assert.equal(buildInvoice(draft).tax_status, 'stated');
  assert.equal(original.tax_status, 'unclear');
});

// Render the actual table components with the same React instance as the renderer.
const { createRequire } = await import('node:module');
const { createElement } = await import('react');
const { renderToStaticMarkup } = await import('react-dom/server');
const tableBundle = await build({ entryPoints: [new URL('../src/InvoiceReviewTables.tsx', import.meta.url).pathname], bundle: true, write: false, format: 'cjs', platform: 'node', jsx: 'automatic', external: ['react', 'react/jsx-runtime'] });
const module = { exports: {} };
new Function('require', 'module', 'exports', tableBundle.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports);
const { InvoiceRowsTable } = module.exports;
const noOp = () => {};

test('product table renders editable inline taxes, without technical review questions', () => {
  const invoice = { ...source, line_items: [{ ...source.line_items[0], discount_amount: '0', discount_percent: '0', tax_status: 'stated', amount_tax_mode: 'inclusive', tax_components: [{ label: 'CGST', rate: '9', amount: '9' }], evidence: 'Private extractor note' }] };
  const draft = createDraft(invoice);
  const html = renderToStaticMarkup(createElement(InvoiceRowsTable, { group: 'line_items', rows: draft.groups.line_items, allRows: draft.groups.line_items, originals: invoice.line_items, disabled: false, label: 'Product', onUpdate: noOp, onRemove: noOp, onFocus: noOp, onBlur: noOp, onTaxChange: noOp }));
  assert.equal((html.match(/<table\b/g) || []).length, 1);
  assert.doesNotMatch(html, /<th[^>]*>(?:CGST|SGST)<\/th>|invoice-review-nested-taxes/);
  assert.match(html, /aria-label="Product 1: Quantity"[^>]*value="100"/);
  assert.match(html, /aria-label="Product 1: Discount"[^>]*value="0"/);
  assert.match(html, /value="CGST"/);
  assert.doesNotMatch(html, /<details|Check details|Originally extracted|Private extractor note|Tax information|tax_status|Printed unit-price decimals|Line amount tax basis|Not shown|Shown/);
  // A missing sibling tax is never fabricated.
  assert.doesNotMatch(html, /value="SGST"/);
});

test('a searched table keeps original product numbers and read-only rows cannot be edited', () => {
  const draft = createDraft(source);
  const html = renderToStaticMarkup(createElement(InvoiceRowsTable, { group: 'line_items', rows: [draft.groups.line_items[1]], allRows: draft.groups.line_items, originals: source.line_items, disabled: true, label: 'Product', onUpdate: noOp, onRemove: noOp, onFocus: noOp, onBlur: noOp }));
  assert.match(html, /aria-label="Product 2: Quantity" disabled=""/);
  assert.doesNotMatch(html, /value="First product"/);
  assert.match(html, /value="Second product"/);
  assert.deepEqual(buildInvoice(draft), source);
});

const liveBundle = await build({ entryPoints: [new URL('../src/liveLinePreview.ts', import.meta.url).pathname], bundle: true, write: false, format: 'esm', platform: 'node' });
const { scheduleLinePreview, currentLinePreview, verificationLabel } = await import(`data:text/javascript;base64,${Buffer.from(liveBundle.outputFiles[0].text).toString('base64')}`);
const calculatedProduct = (index = 0, status = 'matched') => ({
  index, description: 'Goods', tax: '36', total: '236',
  resolved_tax_components: [{ index: 0, label: 'CGST', rate: '9', amount: '18' }, { index: 1, label: 'SGST', rate: '9', amount: '18' }],
  tax_summary: { tax: '36', tax_rate: '18', cgst: { rate: '9', amount: '18' }, sgst: { rate: '9', amount: '18' } },
  verification: { status, calculated_amount: '200', printed_amount: '200', difference: '0', tolerance: '0.01', formula: '2 × 100', messages: [] },
});

test('split taxes populate the line totals and present an automatic match badge', () => {
  const invoice = { ...source, line_items: [{ description: 'Goods', quantity: '2', unit_price: '100', amount: '200', tax_components: [{ label: 'CGST', rate: '9' }, { label: 'SGST', rate: '9' }] }] };
  const draft = createDraft(invoice);
  const html = renderToStaticMarkup(createElement(InvoiceRowsTable, { group: 'line_items', rows: draft.groups.line_items, allRows: draft.groups.line_items, originals: invoice.line_items, disabled: false, label: 'Product', onUpdate: noOp, onRemove: noOp, onFocus: noOp, onBlur: noOp, onTaxChange: noOp, products: [calculatedProduct()] }));
  assert.match(html, /aria-label="Product 1: Tax \(%\)"[^>]*value="18"/);
  assert.match(html, /aria-label="Product 1: Tax amount"[^>]*value="36"/);
  assert.match(html, />Matched</);
  const taxCell = html.match(/class="invoice-product-tax-column">([\s\S]*?)<\/td>/)?.[1];
  assert.ok(taxCell);
  assert.match(taxCell, /aria-label="Product 1 tax breakdown"/);
  assert.match(taxCell, /aria-label="Product 1 tax 1: Tax name"[^>]*value="CGST"/);
  assert.match(taxCell, /aria-label="Product 1 tax 1: Rate \(%\)"[^>]*value="9"/);
  assert.match(taxCell, /aria-label="Product 1 tax 1: Amount"[^>]*value="18"/);
  assert.match(taxCell, /aria-label="Product 1 tax 2: Tax name"[^>]*value="SGST"/);
  assert.equal((html.match(/<table\b/g) || []).length, 1);
  assert.doesNotMatch(html, /<th[^>]*>(?:CGST|SGST)<\/th>/);
  assert.deepEqual(buildInvoice(draft), invoice); // Displayed sums are not invented source fields.
});

test('one combined tax stays in Tax without extra GST columns or fabricated breakdown rows', () => {
  const invoice = { ...source, line_items: [{ description: 'Goods', quantity: '2', unit_price: '100', amount: '200', tax_rate: '18', tax: null }] };
  const draft = createDraft(invoice);
  const result = calculatedProduct();
  result.tax_summary.cgst = result.tax_summary.sgst = { rate: '0', amount: '0' };
  const html = renderToStaticMarkup(createElement(InvoiceRowsTable, { group: 'line_items', rows: draft.groups.line_items, allRows: draft.groups.line_items, originals: invoice.line_items, disabled: false, label: 'Product', onUpdate: noOp, onRemove: noOp, onFocus: noOp, onBlur: noOp, onTaxChange: noOp, products: [result] }));
  assert.match(html, /aria-label="Product 1: Tax amount"[^>]*value="36"/);
  assert.match(html, /aria-label="Product 1: Tax \(%\)"[^>]*value="18"/);
  assert.doesNotMatch(html, /CGST|SGST|aria-label="Product 1 tax breakdown"/);
  assert.equal(buildInvoice(draft).line_items[0].tax_components, undefined);
});

test('a changed draft immediately hides stale matched results until the new calculation arrives', () => {
  const saved = { input: 'old invoice', data: { preview_only: true, rules: { products: [calculatedProduct()] } } };
  assert.equal(currentLinePreview(saved, 'edited invoice'), null);
  assert.equal(verificationLabel(currentLinePreview(saved, 'edited invoice')?.data.rules.products[0]), 'Checking…');
  assert.equal(verificationLabel(undefined, true), 'Unavailable');
  assert.equal(verificationLabel(calculatedProduct(0, 'not_matched')), 'Not matched');
  assert.equal(verificationLabel(currentLinePreview(saved, 'old invoice').data.rules.products[0]), 'Matched');
});

test('out-of-order live calculation responses cannot replace the latest edit', async () => {
  const results = [];
  let started, resolveOld, oldSignal;
  const oldStarted = new Promise(resolve => { started = resolve; });
  const cancelOld = scheduleLinePreview(signal => {
    oldSignal = signal; started();
    return new Promise(resolve => { resolveOld = resolve; });
  }, value => results.push(value), error => assert.fail(error), 0);
  await oldStarted;
  cancelOld();
  assert.equal(oldSignal.aborted, true);
  await new Promise(resolve => {
    scheduleLinePreview(async () => 'new invoice', value => { results.push(value); resolve(); }, error => assert.fail(error), 0);
  });
  resolveOld('old invoice');
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(results, ['new invoice']);
});

test('debounced checks can be cancelled before request and failures clear verification', async () => {
  let calls = 0;
  const cancel = scheduleLinePreview(async () => { calls++; }, () => assert.fail('Cancelled response'), () => assert.fail('Cancelled error'), 0);
  cancel();
  const message = await new Promise(resolve => scheduleLinePreview(async () => { throw new Error('Network unavailable'); }, () => assert.fail('Unexpected success'), resolve, 0));
  assert.equal(calls, 0);
  assert.equal(message, 'Network unavailable');
});

test('separate taxes remove redundant total inputs and payload fields while preserving the source', () => {
  const original = { ...source, line_items: [{ description: 'Goods', quantity: '2', unit_price: '100', amount: '200', tax: '30', tax_components: [{ label: 'CGST', rate: '9' }, { label: 'SGST', rate: '9' }] }] };
  let draft = createDraft(original);
  assert.equal(draft.groups.line_items[0].value.tax, undefined);
  const html = renderToStaticMarkup(createElement(InvoiceRowsTable, { group: 'line_items', rows: draft.groups.line_items, allRows: draft.groups.line_items, originals: original.line_items, disabled: false, label: 'Product', onUpdate: noOp, onRemove: noOp, onFocus: noOp, onBlur: noOp, onTaxChange: noOp, products: [calculatedProduct()] }));
  assert.doesNotMatch(html, /Extracted total|Printed combined/);
  assert.equal(buildInvoice(draft).line_items[0].tax, undefined);
  assert.equal(original.line_items[0].tax, '30');
  assert.match(html, /aria-label="Product 1: Tax amount"[^>]*value="36"/);
});

test('an unavailable equation explains the blocker instead of showing a bare calculated dash', () => {
  const product = calculatedProduct(0, 'incomplete');
  product.verification.calculated_amount = null;
  product.verification.difference = null;
  product.verification.messages = ['Tax basis could not be determined from the extracted values.'];
  const html = renderToStaticMarkup(createElement(module.exports.LineVerificationStatus, { product }));
  assert.match(html, /Calculation unavailable/);
  assert.match(html, /Tax basis could not be determined/);
  assert.doesNotMatch(html, /Calculated: —/);
});

test('PO allocation edits preserve filtered products and the immutable source', () => {
  let draft = createDraft(source);
  const id = draft.groups.line_items[1].id;
  draft = updateRow(draft, 'line_items', id, 'po_line_ref', 'confirmed-po-product');
  const payload = buildInvoice(draft);
  assert.equal(payload.line_items.length, 2);
  assert.equal(payload.line_items[1].po_line_ref, 'confirmed-po-product');
  assert.equal(source.line_items[1].po_line_ref, undefined);
  assert.deepEqual(changesBetween(source, payload).map(c => c.field), ['line_items.1.po_line_ref']);
  draft = removeRow(draft, 'line_items', draft.groups.line_items[0].id);
  assert.equal(buildInvoice(draft).line_items[0].po_line_ref, 'confirmed-po-product');
});

test('line verification compares printed and independently calculated tax', () => {
  const draft = createDraft(source);
  const product = { ...calculatedProduct(), printed_tax: '99', calculated_tax: '63', verification: { ...calculatedProduct().verification, status: 'not_matched', calculated_amount: '413', printed_amount: '449', difference: '-36' } };
  const html = renderToStaticMarkup(createElement(module.exports.LineVerificationTable, { rows: [draft.groups.line_items[0]], allRows: draft.groups.line_items, products: [product], unavailable: false }));
  assert.ok(html.includes('Printed tax') && html.includes('Calculated tax'));
  assert.ok(html.includes('<td>99</td>') && html.includes('<td>63</td>'));
  assert.ok(html.includes('Not matched'));
});

const poBundle = await build({ entryPoints: [new URL('../src/POVerificationTable.tsx', import.meta.url).pathname], bundle: true, write: false, format: 'cjs', platform: 'node', jsx: 'automatic', external: ['react', 'react/jsx-runtime'] });
const poModule = { exports: {} };
new Function('require', 'module', 'exports', poBundle.outputFiles[0].text)(createRequire(import.meta.url), poModule, poModule.exports);

test('PO review shows cumulative invoice quantities without claiming goods were received', () => {
  const draft = createDraft(source);
  const data = { po_number: 'PO-PARTIAL', currency: 'INR', ordered_total: '100', previously_invoiced: '40', current_total: '30', cumulative_total: '70', remaining_amount: '30', amount_status: 'partially_invoiced', prior_invoice_count: 1, receipt_message: 'Invoiced quantities are not proof of delivery.', options: [{ ref: 'po-product', index: 0, description: 'Ordered product', quantity: '10', unit_price: '10' }], lines: [{ index: 0, po_description: 'Ordered product', ordered_quantity: '10', previously_invoiced: '4', current_quantity: '3', cumulative_quantity: '7', remaining_quantity: '3', status: 'matched', messages: [] }] };
  const html = renderToStaticMarkup(createElement(poModule.exports.default, { data, rows: [draft.groups.line_items[0]], allRows: draft.groups.line_items, disabled: true, checking: false, onSelect: noOp }));
  assert.ok(html.includes('Previously invoiced') && html.includes('Cumulative quantity') && html.includes('Remaining quantity'));
  assert.ok(html.includes('partially invoiced') && html.includes('not proof of delivery'));
  assert.ok(html.includes('Product 1: PO product') && html.includes('disabled=""'));
  const pending = renderToStaticMarkup(createElement(poModule.exports.default, { rows: [], allRows: [], disabled: false, checking: true, onSelect: noOp }));
  assert.ok(pending.includes('Checking the current PO'));
  assert.ok(!pending.includes('PO-PARTIAL'));
});

const deletionBundle = await build({ entryPoints: [new URL('../src/invoiceDeletion.ts', import.meta.url).pathname], bundle: true, write: false, format: 'esm', platform: 'node' });
const { loadDeletionPreview, deleteInvoiceUpload } = await import(`data:text/javascript;base64,${Buffer.from(deletionBundle.outputFiles[0].text).toString('base64')}`);
const confirmationBundle = await build({ entryPoints: [new URL('../src/DeleteInvoiceDialog.tsx', import.meta.url).pathname], bundle: true, write: false, format: 'cjs', platform: 'node', jsx: 'automatic', external: ['react', 'react/jsx-runtime'] });
const confirmationModule = { exports: {} };
new Function('require', 'module', 'exports', confirmationBundle.outputFiles[0].text)(createRequire(import.meta.url), confirmationModule, confirmationModule.exports);
const deletionPreview = { run_ids: ['upload-1', 'correction-1'], count: 2, filenames: ['invoice.pdf', 'corrected-invoice.json'], approved_count: 1, can_delete: true, reason: null, fingerprint: 'reviewed-upload-version' };

test('upload deletion confirmation shows its related records and PO impact', () => {
  const html = renderToStaticMarkup(createElement(confirmationModule.exports.DeleteInvoiceConfirmation, { preview: deletionPreview, busy: false, onConfirm: noOp }));
  assert.match(html, /2 processing records/);
  assert.match(html, /invoice\.pdf/);
  assert.match(html, /corrected-invoice\.json/);
  assert.match(html, /batch invoices and corrected versions/);
  assert.match(html, /1 approved invoice will be released/);
  assert.match(html, /cannot be undone/);
  assert.match(html, /Delete upload permanently/);
  const blocked = renderToStaticMarkup(createElement(confirmationModule.exports.DeleteInvoiceConfirmation, { preview: { ...deletionPreview, can_delete: false, reason: 'Wait for processing to finish.' }, busy: false, onConfirm: noOp }));
  assert.match(blocked, /Wait for processing to finish/);
  assert.match(blocked, /<button[^>]*disabled=""/);
  const busy = renderToStaticMarkup(createElement(confirmationModule.exports.DeleteInvoiceConfirmation, { preview: deletionPreview, busy: true, onConfirm: noOp }));
  assert.match(busy, /<button[^>]*disabled=""/);
});

test('deletion requires a matching reviewed preview and sends its fingerprint', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options });
    return Response.json(options?.method === 'DELETE' ? { deleted_run_ids: deletionPreview.run_ids, count: 2 } : deletionPreview);
  });
  const preview = await loadDeletionPreview('upload-1');
  assert.equal(calls[0].url, '/api/runs/upload-1/deletion-preview');
  assert.equal(calls.length, 1); // Loading the dialog is read-only.
  const result = await deleteInvoiceUpload('upload-1', preview);
  assert.equal(calls[1].url, '/api/runs/upload-1');
  assert.equal(calls[1].options.method, 'DELETE');
  assert.deepEqual(JSON.parse(calls[1].options.body), { expected_fingerprint: preview.fingerprint });
  assert.deepEqual(result.deleted_run_ids, ['upload-1', 'correction-1']);
  await assert.rejects(deleteInvoiceUpload('another-upload', preview), /latest deletion details/);
  await assert.rejects(deleteInvoiceUpload('upload-1', { ...preview, can_delete: false, reason: 'Still processing.' }), /Still processing/);
  assert.equal(calls.length, 2);
});

test('a stale deletion confirmation reports the conflict without retrying the deletion', async t => {
  let requests = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    requests++;
    return Response.json({ detail: 'Upload changed. Review the deletion details again.' }, { status: 409 });
  });
  await assert.rejects(deleteInvoiceUpload('upload-1', deletionPreview), /Review the deletion details again/);
  assert.equal(requests, 1);
});
