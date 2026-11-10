# Internet invoice test pack — what to upload and expect

Downloaded on **9 November 2026**. This pack contains **seven original files representing four distinct invoices and one receipt**. The Larkfield digital PDF, scanned PDF, and scanned JPG are the same invoice in three formats. Source downloads are unmodified; manually transcribed corrections live separately.

Five fictional test vendor/PO records have been imported into your local LedgerFlow workspace. Four vendors are approved for the test; the unidentified receipt issuer is deliberately unapproved and its placeholder PO is closed. Your existing invoice history was preserved. The local backend was restarted and verified to have OpenRouter enabled with `openai/gpt-4o-mini`. No key is stored in this pack.

**Compatibility with the current PO review policy:** The observed results below and their result files predate the requirement for a reviewed, validated PO. Imported legacy test POs now appear as **Unverified PO**. Before expecting the same corrected-invoice outcomes, open **Procurement → Review / validate** for each relevant PO, complete its required fields, and confirm the reviewed authorization. Existing vendor approval is retained; any newly imported vendor remains pending and needs a separate **Vendors → Review vendor** decision. Previous invoice decisions and commitments are preserved. See the [new PO upload samples](../purchase-orders/README.md) for the current workflow. Historical results below remain unchanged.

## Start with these uploads

Open **http://127.0.0.1:8000**, click **Process invoices**, then choose a file from [originals](originals/). If the app is stopped, run `./scripts/start.sh` from the project root.

The following are **observed live OpenRouter results**, not promises about every future model response. Each row was tested with a fresh temporary database and the same test POs, so the baseline has no previous duplicate or spent PO balance. Your real workspace preserves previous runs, so subsequent uploads can instead trigger duplicate or balance checks.

| Upload | Verified total | Matching test PO | Observed first upload | What to do next |
| --- | --- | --- | --- | --- |
| [Acme commercial PDF](originals/printed-acme-commercial.pdf) | USD 158.75 | INTERNET-PO-ACME-001 | Needs review; model extracted identity, vendor, total and inferred the unique PO, but confidence was0.80. | Compare the source and use [Acme correction](corrections/acme_widget.json) with **Correct & reprocess**. Observed result: Approved. |
| [Larkfield digital tax PDF](originals/printed-larkfield-digital.pdf) | INR 17,348.36 | PO-77310 | Needs review; total and PO correct, confidence 0.80. | Use the [Larkfield correction](corrections/larkfield.json) after verification. Observed result: Approved. |
| [Larkfield scanned JPG](originals/printed-larkfield-scanned.jpg) | INR 17,348.36 | PO-77310 | Needs review; total correct, plus a supplier-ID mismatch in this model run. | Remove the incorrect inferred supplier ID using the same Larkfield correction. If another Larkfield variant already exists, resolve that original rather than creating another payable invoice. |
| [Larkfield scanned PDF](originals/printed-larkfield-scanned.pdf) | INR 17,348.36 | PO-77310 | Needs review; correct total/PO, scan confidence 0.80. | Same Larkfield correction, subject to duplicate protection. |
| [Two-page Lindqvist scan with annotations](originals/printed-parseur-multipage.pdf) | EUR 45,078.62 | 4525768868 | Needs review; total correct, supplier-ID mismatch, and an incorrect line 7 unit price caught by arithmetic validation. | Verify both pages and use [Lindqvist correction](corrections/lindqvist.json). Observed result: Approved. The source's “approved” stamp does not authorize the app. |
| [Handwritten invoice photo](originals/handwritten-rillion-invoice.png) | 1,470.00; source only says $ | INTERNET-PO-HANDWRITTEN-001 | Needs review; model read invoice 0001 and total 1470, but suggested USD without an explicit ISO code in the source. | Confirm currency with the supplier. For this fictional test only, the PO assumes USD; [the correction](corrections/handwritten-rillion-confirmed-usd.json) includes that explicit assumption. After verification/confirmation: Approved. |
| [Handwritten rent receipt](originals/handwritten-ocrspace-rent-receipt.jpg) | 1,000.00; currency unconfirmed | INTERNET-PO-RECEIPT-HOLD (closed placeholder) | Needs review; model read the amount but incorrectly suggested payer John Smith as supplier and receipt 008 as an invoice number. Vendor/PO/tax checks blocked approval. | Keep unapproved. Obtain a valid invoice and issuer details. John Smith must not be registered as the supplier based on this receipt. No approval-ready correction is supplied. |

## What the statuses mean in this pack

- **Needs review:** extraction or business evidence is unresolved. A correct total and matching PO do not remove that requirement.
- **Approved:** an invoice passes the rules and reserves its total against its PO. No payment is sent.
- **Rejected:** an identified duplicate or explicitly disallowed vendor is blocked.

All seven original files returned **Needs review** in the live model run. This is the current conservative design: OCR/vision outputs are capped at 0.80, below the default 0.85 approval threshold. Even the two digital PDFs landed at 0.80 because exact source-grounding checks do not treat normalized dates/currency representations as verbatim evidence. The local-only labeled parser also required review for all seven layouts and did not recover their totals reliably. OpenRouter substantially improved extraction, but this test set did **not** demonstrate unattended approval from arbitrary original documents.

The model extracted the correct total for seven out of seven **files** in this small fixed pack, including three encodings of the same Larkfield invoice. This is not a general accuracy estimate. It also made meaningful field mistakes. You must verify all core fields, not just the total.

## Correct and reprocess

1. Open the run and inspect **Source file**, **Extracted data**, and the failed checks.
2. In **Decision**, choose **Correct & reprocess**.
3. Compare the corresponding correction JSON with the original. Replace the editor contents with that JSON; do not merely upload the correction as a new invoice while the original is still pending.
4. Enter your reviewer name and a specific reason. For the handwritten invoice, explicitly record how currency was confirmed; using USD here is a disclosed test assumption, not a fact established by the photo.
5. Submit. LedgerFlow marks the old run superseded, keeps its source and audit history, and evaluates a new run.

The correction files preserve source invoice numbers and the original financial figures. Currency for Larkfield is corroborated by its publisher answer key. The handwritten USD assumption is the only added financial-context assumption; mock vendor approval and PO budgets are test procurement records throughout.

## Duplicate and split-invoice tests

After approving a corrected invoice:

- Upload its same correction JSON again through structured input: **Rejected**, because the same vendor/invoice identity already exists. This was observed for all six invoice-file cases in both extraction modes.
- Submit a copy with only `invoice_number` changed to a new number, retaining the full original total: **Needs review**, because the original approval has already consumed the PO budget. The `po_balance` check blocks approval. This was observed for all six invoice-file cases in both modes.
- Upload a different Larkfield format: it still represents the same vendor/invoice identity. Do not pay it again. Model variation can affect identification, so inspect the reported duplicate and vendor checks.

The PO amounts equal the corresponding gross invoice totals. Default cumulative allowance is 2% or 5 currency units, whichever is larger. The overage probe uses another full-size invoice, so it exceeds the remaining allowance despite having a new invoice number.

## Procurement records and repeatable checks

[procurement.json](procurement.json) preserves the five historical records. [reviewable-purchase-orders.json](reviewable-purchase-orders.json) is the new, **explicitly synthetic** fixture for the current PO policy. It preserves the original PO identifiers, currencies, budgets and existing line items, and documents the added fictional issue dates, subtotal/tax assumptions and purchasing status. Upload this file through **Procurement → Upload POs**, review its assumptions, then confirm the PO draft. Updating an existing legacy PO requires the explicit update option. **PO confirmation does not approve a vendor**; use the separate vendor review action when testing the fictional supplier decisions.

The receipt placeholder remains closed with pending purchasing approval, and its unidentified vendor is rejected only by a separate explicit test review. Source PO references are retained for Larkfield and Lindqvist; Acme and the handwritten invoice use explicitly created test POs because their source documents have no PO number.

To prepare a draft in another running local workspace:

```bash
uv run python scripts/import_internet_procurement.py
```

The importer backs up the existing procurement snapshot under the ignored `data/backups/` directory, refuses conflicting vendor identities, and creates an **unconfirmed draft by default**. It does not register POs or change vendor approval. For a dedicated test workspace, the optional actions require separate flags and reviewer names:

```bash
uv run python scripts/import_internet_procurement.py \
  --confirm-test-pos --reviewer "PO fixture reviewer" \
  --review-test-vendors --vendor-reviewer "Supplier fixture reviewer"
```

Add `--allow-test-po-updates` only when deliberately replacing legacy fixture POs with their completed versions. Existing invoice commitments remain protected. The vendor flag records four fictional approvals and one rejection independently of PO confirmation; omit it to keep newly created vendors pending.

To rerun isolated checks without changing your workspace:

```bash
# Local OCR/parser only; does not send a provider key
uv run python scripts/verify_internet_samples.py --local

# Live model calls; reads .env and incurs normal OpenRouter usage
uv run python scripts/verify_internet_samples.py --openrouter

# A single sample
uv run python scripts/verify_internet_samples.py --openrouter --case handwritten-rillion-invoice
```

The runner creates a fresh temporary database per original, uploads and confirms the complete synthetic POs through the audited API, then separately records fictional vendor review decisions. It uploads the actual invoice, records the decision, verifies a correction where supplied, then tests duplicate and cumulative overage behavior. Fixture setup and its review provenance are saved alongside each case. No test invoice runs are added to your actual workspace.

New runs write to a fresh `results/<mode>/<timestamp>/` directory. Use `--output /path/to/new-or-empty-directory` to choose another destination. The historical summaries linked below are preserved; reruns do not overwrite them. Provider-specific warnings, extracted fields, original decisions and corrected decisions remain in each run's results.

- [Live OpenRouter results](results/openrouter/summary.json)
- [Local-only results](results/local/summary.json)
- [Case manifest](manifest.json)
- [Raw source transcriptions and uncertainties](handwritten-transcriptions.json)
- [Printed source transcriptions](printed-transcriptions.json)

## Sources and reuse

- [FileDone digital invoice](https://filedone.ai/sample-files/sample-invoice-pdf) and [scanned versions](https://filedone.ai/sample-files/sample-scanned-invoice): fictional, CC0. The scans are publisher-created scan simulations, not photographs of a real transaction.
- [ExamplePDF commercial invoice](https://examplepdf.com/examples/invoice/): fictional; internal testing/development/evaluation use permitted under its stated terms.
- [Parseur two-page invoice](https://parseur.com/blog/sample-pdf-invoice): fictional public extraction sample with stamps/annotations. The printed invoice is not a fully handwritten document.
- [Rillion handwritten invoice](https://www.rillion.com/finance-tools/automated-invoice-scanning-ocr-tool/): publicly provided hand-drawn demonstration. No explicit open asset license found.
- [OCR.space handwritten receipt](https://ocr.space/handwriting): public OCR demonstration. No explicit open asset license found; it is a receipt, not an invoice.

Downloads are retained locally for the requested evaluation. They have not been republished or hosted. [Printed provenance](printed-sources.json) and [handwritten provenance](handwritten-sources.json) record the exact source/download URLs, hashes and rights notes.
