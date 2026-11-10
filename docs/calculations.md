# Product and invoice calculations

## Deterministic rules (version 3.4)

The calculator reads each product's quantity, price per unit, printed amount and stated taxes. It checks the product amount, resolves product taxes, and reconciles the invoice total. Missing product amounts can be calculated from quantity × unit price less a stated discount when the price and amount use the same known tax basis. Missing subtotals and total tax can be derived from complete product data. Derived numbers appear in calculation steps; they do not replace extracted source amounts.

`result.calculation.products` shows each product's quantity, unit price, resolved tax and total. Tax already included in a product amount is counted once. Tax printed only at invoice level is used once; for a single product it belongs to that product. For multiple products without their own tax breakdown it remains a separate invoice tax, rather than being arbitrarily distributed.

Absent tax fields and absent product discounts default to zero. A discount supplied only as a percentage retains an absent amount so the calculator can derive it; a stated amount does not gain a conflicting zero percentage. Quantity, price, date, identity, printed invoice total and other missing facts do not default to zero. An explicitly unreadable tax, incomplete tax component, invalid number or uncertain tax basis requires review. A final-total match cannot override a failed product or tax check.

Per-product taxes may be a combined amount/rate, separate CGST/SGST/IGST entries, or just one printed component. Only the supplied components are used; a missing counterpart is never invented or doubled. A rate requires a known taxable base. Rates on the same product are applied to its calculated net amount after discount. Printed component taxable bases are independent cross-checks; they cannot override the calculated product base. Tax regimes that intentionally tax only part of a product require review rather than silently changing the base. Inclusive rates are backed out of the gross product amount. Component amounts/rates are checked against their bases; their sum is the product tax. Document tax summaries and invoice totals are cross-checked independently, never added as duplicate charges.

Discounts, document charges, signed round-off and bounded displayed unit-rate precision remain supported. A non-rounding document adjustment with only product tax rates needs an explicit final tax summary/base: the calculator does not guess how a document discount or charge changes the taxable amount. Unknown product tax bases can be resolved from a consistent separately stated taxable value, or from a unique interpretation supported by the product quantity, price, discount, printed tax amount/rate and printed product total. Quantity × unit price after discount and the printed product amount are each compared with that explicit base and base plus component tax; a mode is resolved only if exactly one interpretation fits within one currency rounding unit. Conflicting printed bases still fail their own checks even if independent tax/rate facts resolve an interpretation. Incomplete or ambiguous evidence stays unresolved. The invoice grand total is never used to select a basis, and explicit modes are never overwritten.

Product tax component calculations prefer the discounted quantity × unit price base when its tax basis is known, so changing quantity/price recalculates rate-only taxes independently of the printed product total. Printed component bases and tax amounts remain comparison targets. Rate-derived tax, rather than an incorrect printed tax, is used to calculate the expected line total. Editing quantity, price or discount therefore updates the tax calculation without retaining a stale extracted base. When product component rows exist, those rows are the only product-tax inputs. Redundant line `tax` / `tax_rate` values and their evidence fields are excluded from normalization, review drafts and calculation working copies. The original stored extraction is retained for audit; corrections record the removed fields. Rate/amount totals are derived from the components, without adding them again or checking an unrelated copied component as an aggregate. Incomplete components remain incomplete and cannot fall back to the redundant values. A product with no component rows continues to use its single tax amount/rate.

Each `products[]` entry also contains:

- `tax_summary`: combined `tax_rate` and `tax`, plus `cgst`/`sgst` objects with `rate` and `amount`. Absent CGST/SGST entries have zero summary values without being added to the source; the review shows only supplied components inside the product’s Tax cell. Unknown or invalid component values stay null. Amount-only components can supply a calculated rate when a taxable base is known.
- `resolved_tax_components`: original component indices, resolved source amounts, independently calculated amounts and taxable-base comparisons.
- `calculated_net`, `calculated_tax`, `calculated_gross`, `calculated_net_unit_price` and `printed_tax`: separate calculated and source values. The net unit price is calculated before whole-line currency rounding for PO comparisons.
- At invoice level, `computed_totals` rebuilds net, tax, adjustments and total from calculated products. Those totals are cross-checked independently of the original subtotal-plus-tax equation. Document tax is counted once; permitted printed-unit-price rounding is retained.
- `verification`: `matched`, `not_matched` or `incomplete`, with `calculated_amount`, `printed_amount`, `difference`, `tolerance`, `formula` and messages. The calculated amount uses the printed line's tax basis. Every related line/discount/tax check must pass for a matched badge; missing quantity, price or printed amount cannot earn that badge. A matched badge is an arithmetic result, not vendor or invoice approval.

For a single product, separately stated document taxes can supply its summary once. Multi-product invoices do not copy document tax components into each product. A correct final invoice total cannot conceal offsetting incorrect product amounts.

## Fields

- Product `quantity`, `unit_price`, `amount`: decimal strings. `amount` is the source's printed product-row amount; `amount_tax_mode` says whether it includes tax. Computed gross product total appears separately in calculation output.
- Product `tax`, `tax_rate`: single-tax amount/rate inputs only when the product has no `tax_components`. When components exist, AI extraction leaves those fields null and normalization removes them, along with any redundant `tax_evidence` / `tax_rate_evidence` metadata. Component totals are calculation outputs, not additional review inputs.
- Product `tax_components`: up to 20 entries of `{label, amount, rate, taxable_amount, evidence}`. Use the exact product-row tax cells. A table with only a GST % column uses `tax_rate`; document tax-summary rows must not be copied into products.
- Invoice `tax_components`: the separately printed document tax summary; up to 100 entries with the same shape. Repeated taxable bases are never summed into a fabricated subtotal.
- Invoice/product `tax_status`: `not_stated`, `stated`, or `unclear`. New AI extraction distinguishes absent tax from unreadable tax. A zero with `not_stated` is a default, not a printed aggregate that overrides product taxes.
- `tax_mode`, product `amount_tax_mode`/`unit_price_tax_mode`: `exclusive`, `inclusive`, or `unknown`. Legacy absent metadata retains the original exclusive convention. An unknown basis on a zero-tax document is equivalent for arithmetic; it does not require guessing a tax amount.
- `subtotal_scope`: `before_adjustments`, `after_adjustments`, or `unknown`. With no document adjustments, before/after are equivalent.
- `taxable_subtotal`: separately printed tax-exclusive subtotal.
- Product `discount_amount`, `discount_percent`, `unit_price_precision`: discounts and verified printed unit-price precision (0–6 decimals).
- Document `adjustments`: `{label, kind, amount, percent, base_amount, included_in_subtotal, evidence}`. Kind is `charge`, `discount`, `rounding`, or `unknown`; discounts are positive deductions, rounding can be signed. Percentages need an explicit base. Unknown meaning/inclusion requires review.

JSON and CSV-with-JSON-array cells retain nested tax fields. The PDF/image extraction model receives the same schema. Simple local labeled-text extraction is still limited; images and uncertain tables need source review.

## Review and policy

**Review fields** shows the source beside editable tables with extracted business values. Product tax rows can be added, edited or removed. Tax-status and precision metadata, evidence panels and per-field check details remain outside the default table. For unresolved tax interpretation, optional **Unit price includes tax** and **Line total includes tax** columns are available through Add product column. A uniquely resolved basis is retained in the draft before later edits, and audited when saved. Optional missing business values can be entered with **Add field** or **Add column**. Search filters only the view. **Check corrections** is read-only; **Save & reprocess** creates an audited replacement and runs all current financial, vendor, PO and duplicate controls.

Calculation reconciliation is not vendor approval, GSTIN verification, a statutory rate check, or a payment. Goods receipt and tax-law compliance remain outside this local app.

## API and history

`GET /api/runs/{id}/calculations` returns `{run_id, invoice_fingerprint, rules, rules_basis, superseded}`. Current-version saved calculations use `decision_snapshot`; older versions receive a read-only `current_rules_preview`. Existing decisions, approvals and balances are unchanged until explicit review/reprocessing.

`GET /api/runs/{id}/review-context` retains the exact saved `invoice` and fingerprint and additionally returns a `review_invoice` draft with supported tax-basis defaults and redundant product aggregate fields removed when component taxes exist. The draft keeps those semantics stable while amounts are edited. Merely opening review does not write the draft. Saved corrections audit inferred basis changes alongside user corrections.

`POST /api/runs/{id}/line-preview` accepts the draft invoice and optional `expected_invoice_fingerprint` and returns `{run_id, rules, po_reconciliation, po_checks, preview_only: true}`. It runs the same normalization and Decimal rules as correction/reprocessing, with no model calls, database writes or procurement changes. The frontend waits 180 ms after typing, cancels obsolete requests and ignores out-of-order responses; every edit invalidates a prior match badge immediately. The live preview reads a consistent current procurement snapshot and evaluates PO quantities, net unit prices and cumulative amounts as well. **Check corrections** displays the complete prospective policy decision. Neither preview reserves funds or quantities; approval and reprocessing recheck under the existing write transaction.

AI calculation generation, its endpoint, comparison UI and CSV/HTML columns have been removed. AI document extraction remains available through the configured OpenRouter account. Historical AI analysis already stored in run JSON/audit history is retained, but no new AI calculation is generated.

The rules result includes version, printed/calculated total, difference, effective subtotal/tax, per-product totals, defaults, steps, checks and limitations. JSON exports retain the full record; CSV and HTML report the deterministic result.

## PO quantities and partial invoices

**PO checks** in field review separates invoice arithmetic from procurement verification. A product is allocated by a unique exact SKU, a unique normalized exact description, or an explicit reviewer selection stored as `po_line_ref`. HSN is not treated as a SKU. Fuzzy or ambiguous names never auto-allocate. A selection is tied to PO/product identity and invalidates if that identity or row position changes. Conflicting SKUs remain blocked even with a selection.

Every allocated PO product reports ordered, previously invoiced, current, cumulative and remaining quantities. Repeated invoice rows allocated to the same PO product are summed before checking the limit. Only approved, unsuperseded runs consume quantity and amount; pending/rejected invoices do not. Rejection releases commitments, and correcting an approved run requires rejection first. Legacy approved invoices that cannot be allocated are explicitly flagged, rather than assuming their quantities were zero. No product rows means quantity verification is unavailable, not verified.

Partial invoices need not each equal the full PO value. Their cumulative amount is compared with the PO and configured monetary tolerance; remaining value and partial/full/over status are displayed separately. Quantity limits have no monetary-tolerance allowance. Product price compares the effective net unit price after discount and tax exclusion with the PO net price. PO amendments cannot remove, reorder, rename or reduce a consumed product below its invoiced quantity.

Approval uses the existing serialized transaction to recheck quantities and amounts against current approved history. The UI preview can become outdated if another invoice is approved; it is never an approval reservation. Invoiced quantities do not prove receipt: this app does not yet collect goods-receipt or delivery-note quantities.
