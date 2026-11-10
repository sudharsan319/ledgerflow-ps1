export type LineVerification = {
  status: 'matched' | 'not_matched' | 'incomplete';
  calculated_amount: string | null; printed_amount: string | null; difference: string | null;
  tolerance: string; formula: string | null; messages: string[];
};
export type ProductCalculation = {
  calculated_net?: string | null; calculated_tax?: string | null; calculated_gross?: string | null; printed_tax?: string | null;
  basis_notes?: string[]; amount_tax_mode?: string; unit_price_tax_mode?: string;
  tax_from_document?: boolean;
  resolved_tax_components?: { index: number; label: string; rate: string | null; amount: string | null }[];
  index: number; description: string; tax: string | null; total: string | null;
  tax_summary: { tax: string | null; tax_rate: string | null; cgst: { rate: string | null; amount: string | null }; sgst: { rate: string | null; amount: string | null } };
  verification: LineVerification;
};
export type POOption = { ref: string; index: number; description: string; sku?: string; quantity: string; unit_price: string };
export type POAllocation = { index: number; po_line_ref: string | null; description: string; po_description?: string; ordered_quantity?: string; previously_invoiced?: string; current_quantity?: string; cumulative_quantity?: string; remaining_quantity?: string; po_unit_price?: string; invoice_net_unit_price?: string | null; status: string; messages: string[] };
export type POReconciliation = { po_number: string; currency: string; options: POOption[]; lines: POAllocation[]; ordered_total: string; previously_invoiced: string; current_total: string; cumulative_total: string | null; remaining_amount: string | null; amount_status: string; prior_invoice_count: number; receipt_message: string; quantity_status: string; message?: string };
export type PreviewCheck = { code: string; label: string; status: string; message: string; blocking: boolean };
export type LinePreview = { preview_only: true; rules: { products: ProductCalculation[]; status?: string; checks?: PreviewCheck[]; computed_totals?: { net: string; tax: string; adjustments: string; total: string; tolerance: string } | null }; po_reconciliation?: POReconciliation | null; po_checks?: PreviewCheck[] };
export type LiveLineState = { input: string; data?: LinePreview; error?: string };

/** Cancel obsolete work and ignore responses even if a transport ignores abort. */
export function scheduleLinePreview<T>(request: (signal: AbortSignal) => Promise<T>, receive: (value: T) => void, fail: (message: string) => void, delay = 180): () => void {
  const controller = new AbortController();
  let cancelled = false;
  const timer = setTimeout(async () => {
    try {
      const result = await request(controller.signal);
      if (!cancelled) receive(result);
    } catch (error) {
      if (!cancelled) fail(error instanceof Error ? error.message : 'Line verification is unavailable.');
    }
  }, delay);
  return () => { cancelled = true; clearTimeout(timer); controller.abort(); };
}
export function currentLinePreview(state: LiveLineState | null, input: string): LiveLineState | null {
  return state?.input === input ? state : null;
}
export function verificationLabel(product?: ProductCalculation, unavailable = false): string {
  if (!product) return unavailable ? 'Unavailable' : 'Checking…';
  return { matched: 'Matched', not_matched: 'Not matched', incomplete: 'Incomplete' }[product.verification.status];
}
