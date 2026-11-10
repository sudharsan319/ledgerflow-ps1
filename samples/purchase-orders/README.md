# Purchase-order upload samples

These are fictional test documents for the separate PO validation and vendor approval workflow.

## Try the complete PO

1. Open **Procurement → Upload POs**.
2. Choose `complete-purchase-order.xlsx`, `.csv`, `.json`, `.pdf`, `.png`, or `.txt`. All formats represent the same PO; start with one.
3. Compare the source with the editable header and line-item fields. For PDF/images, extraction is a suggestion and may require correction.
4. Click **Save & validate**. Supply your reviewer name and how you checked the order's authorization. Confirm the attestation, then **Confirm import**.
5. The PO shows **Validated PO**. Its new supplier, **Sample Stationery Ltd**, remains **Pending review**. PO upload never approves a supplier.
6. In **Vendors → Review vendor**, independently review the supplier, enter reviewer/reason, check the independent-review confirmation, and approve or reject it. Approval here does not validate any other PO.
7. Invoice matching requires both an approved vendor and a validated, approved, open PO, plus the existing invoice controls.

For an end-to-end check, upload `matching-invoice.json` through the **invoice uploader** after step 5, while the supplier is still pending. Expect **Needs review** with a vendor-approval block. After step 6 approves the vendor, open that invoice's review action and approve it: the server rechecks all rules, and this fictional USD 550 invoice should approve against the USD 1,100 PO. A repeated submission is blocked as a duplicate. If you approve the vendor before the first invoice submission, that structured sample should approve directly. These expectations assume a fresh PO/sample invoice number and no prior commitments on this PO.

The authorization and vendor-review actions on these files are fictional demo decisions. They are not evidence about a real supplier.

## Required fields

PO number, vendor name, PO issue date, explicit currency, subtotal, tax, total, PO approval status, open/closed status, and at least one line with description, quantity, unit price and amount. Unknown values stay empty; use explicit tax 0 when appropriate. Dates use YYYY-MM-DD. Line amounts must sum to subtotal, and subtotal + tax must match the total.

For CSV/Excel, repeat PO-level values on each line-item row using the template headers. Matching PO numbers group into one PO. Conflicting repeated header values are rejected. Excel formulas must be replaced with their reviewed values before upload. The app downloads ready-to-use CSV, XLSX and JSON templates from Procurement.

## Negative cases

- `missing-required-fields.json`: missing PO date and tax; import is blocked until corrected and validated.
- `arithmetic-mismatch.json`: total disagrees with subtotal + tax; import is blocked until corrected.
- Uploading an invoice into the PO uploader should fail when the source is clearly an invoice. Upload the purchasing document separately.
- Re-importing a changed existing PO requires the explicit update checkbox. Its committed balance is never reset, and a committed PO cannot change vendor/currency or shrink below commitments.

Existing legacy POs without field validation and review evidence now show **Unverified**. Use **Review / validate** to complete and authorize them. Previous invoice decisions and commitments remain in history.

Regenerate these samples with `uv run python scripts/generate_po_samples.py`. No samples are imported automatically.
