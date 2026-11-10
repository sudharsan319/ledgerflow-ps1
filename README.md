# LedgerFlow

A runnable invoice-processing application for the ASA PS-1 case study. It accepts real documents, extracts invoice fields, checks procurement rules, and records an explainable **approved**, **needs review**, or **rejected** decision. Approval reserves PO capacity; it never executes payment.

The React interface includes a dashboard, live run stages, searchable history, extraction evidence, human review/correction, multi-format PO uploads, independent vendor review, policy settings, and downloadable decision reports. The backend is Python/FastAPI with SQLite and a bounded worker queue. Run it locally or use the included Render deployment configuration with a shared reviewer login.

Submission materials: [checklist and coverage](SUBMISSION.md), [five-minute narration](docs/demo-narration.md), and [demo preparation](docs/demo.md). For a fresh recording workspace, run `./scripts/start-demo.sh` and open `http://127.0.0.1:8001`. Your regular database is preserved.

## Run locally

Requirements: Python 3.11+, Node.js 20.19+ (or 22.12+), npm, and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
# From this directory
test -e .env || cp .env.example .env
./scripts/start.sh
```

Open **http://127.0.0.1:8000**. API docs: **http://127.0.0.1:8000/docs**. Stop with Ctrl+C. The start script installs the locked Python dependencies, installs frontend packages if absent, builds the frontend, and launches one local server. Run `npm --prefix frontend ci` when frontend dependencies change.

For local OCR, install Tesseract. Ubuntu/Debian: `sudo apt-get install tesseract-ocr`; macOS: `brew install tesseract`. The app reports OCR availability in Settings. English OCR is included by default; other languages need their Tesseract language packs and an adapter change. Docker includes English OCR.

Docker alternative (requires Docker Compose v2):

```bash
docker compose up --build
```

Without Compose, use `docker build -t ledgerflow:local .` and then `docker run --rm -p 127.0.0.1:8000:8000 -v ledgerflow-data:/app/data --env-file .env ledgerflow:local`.

Docker exposes the app only on `127.0.0.1:8000`; a named volume retains the database and source files. `docker compose down` preserves data. Run **one backend process / worker**: this implementation uses an in-process queue and startup recovery, not a distributed job system.

For development:

```bash
uv sync --frozen
uv run uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
# Separate terminal:
npm --prefix frontend ci
npm --prefix frontend run dev
```

## Run on Render

The included `render.yaml` defines one Docker web service on Render's Free plan. Follow the [Render deployment guide](docs/render-deployment.md) for setup, reviewer credentials and verification. It starts with fictional procurement records; the local database, uploaded documents and `.env` are excluded from the deployment image and source package. This configuration is provided for deployment; it does not imply that a live service has already been created.

Hosted access requires the username `reviewer` and a generated secret password. Share the credentials privately with the evaluator. Only `/healthz` is public. Free-plan storage is temporary: uploaded files and run history can reset after a restart, redeployment or idle shutdown. The service may also need time to wake before a demonstration. OpenRouter is disabled by default; configure its key in Render's secret environment settings if needed.

## Optional OpenRouter extraction

Edit the local `.env` file, then restart the backend:

```dotenv
ENABLE_OPENROUTER=true
OPENROUTER_API_KEY=your-key-here
OPENROUTER_MODEL=openai/gpt-4o-mini
```

The model must support strict JSON-schema outputs and, for scanned inputs, images. Model availability and cost depend on your OpenRouter account. The key stays on the server and is excluded from Git; the UI never receives it. When enabled, document text/images are sent to OpenRouter for extraction. JSON and CSV already contain structured data and are processed locally; PO uploads also support local XLSX parsing. Keep the flag false to use only local processing.

The implementation follows OpenRouter's [structured-output contract](https://openrouter.ai/docs/guides/features/structured-outputs) and [image-input format](https://openrouter.ai/docs/guides/overview/multimodal/image-understanding). It uses a fixed API destination, bounded timeouts and retries, explicit schemas, source evidence, and deterministic post-validation. A provider failure retains local extraction and routes the invoice to review. Model-reported confidence is never accepted as proof. A live paid model call is not part of the automated test suite.

## Inputs and outputs

| Invoice input | Behavior |
| --- | --- |
| PDF | Extract text; OCR pages without useful embedded text; optional model extraction. |
| PNG / JPEG / WEBP / TIFF | Local OCR or optional vision extraction; human verification required. |
| JSON | One invoice, an invoice array, or `{ "invoices": [...] }`. |
| CSV | One invoice per row; optional `line_items` column containing a JSON array. |
| TXT / Markdown | Labeled fields such as `Invoice number: INV-1001`; optional pipe-separated line-item rows. |
| EML | Process supported attachments, or a plain-text body when no supported attachments exist. |

Each output includes normalized fields, evidence, rule checks, a decision, source fingerprint, timestamps, policy snapshot and review history. Export an individual run as JSON, CSV, or a printable HTML report (use your browser's print-to-PDF if needed). Export all runs as JSON or CSV. Original source files are downloadable from a run.

Search bars filter invoice, PO, vendor, upload and review lists as you type, including line items, checks, extraction notes and audit events. Searches ignore case and accents and require every entered word to match somewhere in the record. Each list has its own query, a result count and a clear button. Invoice searches cover all runs, including older records outside the overview's eight recent rows. Search only changes what is displayed: selected uploads, PO import batches, approval checks, commitments and the **Export all CSV** download still use their complete data.

Open a processing run to see **Matched PO** and **Uploaded document** beside **Extracted data** and **Audit trail**. Matched PO shows the order snapshot used for that decision, its line items and checks, and its uploaded source when one is attached. This historical snapshot does not change when the procurement register is updated. Uploaded document displays the original PDF/image pages or plain text, with page navigation, zoom and download controls. Corrected runs retain the original upload and offer the corrected input separately. Seed/legacy POs without an attached file explain that only the stored record is available. Previews show up to 20 pages or 100,000 text characters; spreadsheets and other unsupported preview formats can be downloaded. Previewing never calls the extraction model or changes the decision.

Limits: ten files per submission, 10 MB each, 50 MB request size, 20 pages per PDF, 100 invoices per JSON/CSV/email batch, 100,000 characters of text, and 25 megapixels per image. A PDF/document represents one invoice; use separate files or structured batches for multiple invoices. Unsupported/corrupt inputs fail with a visible error instead of becoming approvals. Scanned or uncertain extractions stay in review until a person verifies and resubmits corrected fields.

## Review individual invoice fields

Open a completed invoice and choose **Review fields**. The source PDF/image or text appears beside editable tables. **Invoice fields** contains extracted header values; **Products** has one row per product with quantity, unit price, discount, tax and printed line total. Separate product tax components appear inside that product’s Tax cell. **Invoice taxes** and **Adjustments** contain document-level entries. There are no per-field evidence/check panels or shown/not-shown questions. The extraction method is labeled once; calculation metadata stays internal and is preserved on submission. Use **Add field** or **Add column** if extraction missed a value you need to enter.

Absent taxes and discounts use zero. A printed rate without an amount stays available for calculation, rather than receiving a contradictory zero amount. Missing identity, quantities, prices and printed invoice totals remain missing and subject to policy checks.

Each product’s **Tax** cell shows the combined rate and an editable breakdown of its supplied taxes, including CGST/SGST names, rates and amounts. **Tax amount** shows the combined amount. There are no standalone CGST/SGST columns or separate product-tax table. A combined GST alone stays combined. Derived totals are calculated locally; edits in the Tax cell refresh the existing live verification.

When product taxes are listed separately, there are no additional printed/extracted combined-tax inputs. The calculator sums only the component rows; the main rate and amount are calculated outputs. Redundant combined values from older extractions are removed from the review draft and correction payload automatically. Saved source records remain available for audit, and saving the correction records those removals. Invoice-level total-tax and grand-total checks still run independently.

Missing tax-basis settings can be resolved from a separately stated taxable value, or independent printed tax amounts/rates and product values, when the interpretation is unique. Optional tax-inclusion columns are available through Add product column when source review is still needed. When a line cannot be calculated, its verification cell explains the blocking issue.

Every edit refreshes **Verification** after a short typing pause. **Matched**, **Not matched**, or **Incomplete** describes that product's own arithmetic, including discount and tax checks. **Line verification** compares printed tax/line totals with independently calculated values and displays the recomputed invoice total. Taxes are calculated after discount; printed taxable bases cannot override that calculation. Unused zero discount counterparts do not contradict a nonzero amount or percentage. A changed draft immediately hides stale results. Calculation failures show **Unavailable**, never a previous green badge. Live verification uses no AI calls, saves nothing and does not approve the invoice. Printed totals are only changed by your edits.

Search filters displayed values only; all rows are preserved and submitted. Switching detail tabs preserves the draft. **Reset edits** restores the extracted values. **Check corrections** runs the current policy without saving, approving or consuming PO capacity. Editing afterward marks that preview stale. After verifying the submitted values against the source, enter your name and reason, tick the verification checkbox and choose **Save & reprocess**. An unchanged invoice can also be source-verified and reprocessed. Before/after changes are recorded in **Audit trail**.

Reprocessing retains the original source and invoice, creates a replacement run, and records field changes in both runs’ **Audit trail**. Vendor approval, validated PO requirements, duplicates and amount checks still apply. Approved or superseded runs are read-only; concurrent invoice changes reject stale submissions. This flow uses no additional AI calls. Source quotes are extractor-provided evidence, not image-coordinate highlights.

## Product calculations

Open **Calculations** to see each product's quantity, price per unit, tax and total, followed by the invoice equations. Missing subtotals and tax totals are calculated from complete product data. Absent tax columns use zero; unreadable or invalid tax values require review. Separate CGST/SGST/IGST entries can belong to each product, including a single stated tax component. Tax at invoice level is counted once, and tax-inclusive amounts are supported.

The AI calculation feature has been removed. OpenRouter remains available for document extraction; calculations and policy checks run locally. See the [calculation contract](docs/calculations.md). Existing historical decisions and approvals are preserved.

## Purchase orders and vendor approval

Open **Procurement → Upload POs** to upload **PDF, PNG/JPEG/WEBP/TIFF, CSV, XLSX, JSON, or TXT**. Up to five files are accepted per submission, 10 MB each. Download populated **CSV, XLSX, or JSON templates** from the PO upload dialog. For CSV/Excel, repeat PO-level values on each line-item row; matching PO numbers group the lines. Replace Excel formulas with reviewed values before upload.

Every upload starts as a draft. Review the downloaded source, extraction evidence, and editable header/line-item forms, then **Save & validate**. To **Confirm import**, enter the reviewer and authorization evidence and confirm that the PO's stated authorization status was independently checked. Edited drafts must be saved and validated again. Updating an existing PO requires an explicit opt-in; imports preserve invoice commitments and cannot change a committed PO's vendor/currency or lower its total below commitments.

In the PO register, open **Review / validate** and switch between the **PO details** and **Source PO** tabs, using the same tab layout as invoice reviews. The PO search sits at the top right of the details view. Switching tabs preserves unsaved edits and the search. PDFs and images support page previews; original downloads remain available. Reviewing a registered PO again preserves its link to the original upload and exposes its generated record snapshot separately. Seed, legacy, or missing-source records explain why no original document is available.

Required PO fields are `po_number`, `vendor_name`, `po_date` (YYYY-MM-DD), `currency` (explicit three-letter code), `subtotal`, `tax` (explicit zero is allowed), `total`, `approval_status` (`approved`, `pending`, or `rejected`), `status` (`open` or `closed`), and at least one `line_items` entry with `description`, `quantity`, `unit_price`, and `amount`. The policy checks line arithmetic, the sum of line amounts against subtotal, and subtotal + tax against total. Unknown values must be reviewed rather than invented.

**Validated PO** and **Approved vendor** are separate statuses. A PO must also be approved and open to be eligible for invoice matching. New suppliers created during PO import remain **Pending review**; existing vendor approval is unchanged. In **Vendors → Review vendor**, record an independent supplier review, its evidence, and an explicit approve/reject decision. This records the operator's onboarding review; it does not perform external identity or bank verification.

Legacy PO records without the required validated fields and review evidence show **Unverified PO**. Use **Review / validate** before evaluating new invoices against them. Previous invoice decisions and commitments remain intact. Start with the [PO upload samples and manual test instructions](samples/purchase-orders/README.md), including complete documents in six formats and intentionally invalid examples.

## Decision rules

1. Validate identity, date, currency, positive amounts, formats, source evidence and extraction confidence.
2. Reconcile subtotal + tax, line quantities/prices and line-item totals using Decimal arithmetic.
3. Match an approved vendor by ID or exact registered alias; fuzzy name matching cannot approve.
4. Block repeated source content or vendor/invoice identity already approved or awaiting review.
5. Match an explicit PO, or the unique open PO for the vendor and currency. Ambiguity requires review; the selected PO must then pass validation and authorization checks.
6. Require a validated, authorized, open PO; check vendor/currency consistency, product allocations, net unit prices, and cumulative amount/quantity commitments across partial invoices.
7. Approve only if all applicable controls pass; reject duplicates and rejected vendors. Pending vendor approval and other resolvable exceptions require review.

PO amounts are **gross invoice totals including tax**, in the PO's currency. No exchange-rate conversion is attempted. The cumulative limit is `PO total + max(absolute tolerance, PO total × percentage / 100)`. Default tolerance: 2% or 5 units of the PO currency, whichever is larger. It applies once to the PO total, not anew for every invoice. Only approved runs consume balance; pending runs reserve their invoice identity. Rejecting an approved run releases its commitment. Credit notes and actual payments are outside this workflow.

A review approval re-runs the rules inside the same SQLite write transaction that records the commitment. It cannot override duplicates, arithmetic errors, unapproved vendors, missing PO validation/authorization, or overspending. A reviewer can resolve PO ambiguity by selecting a valid PO. **Correct & reprocess** preserves the original record and source, records the reviewer and reason, supersedes the original, and evaluates a new run. Correcting an approved invoice first requires rejecting it so its commitment is explicitly released.

Extraction confidence is a conservative routing heuristic, not a calibrated probability. Structured transport scores 1 because it is read losslessly, not because the invoice is intrinsically correct. OCR/vision-only extraction remains below the automatic approval threshold. Human-verified corrections are structured input and still pass every financial control.

## Demo and procurement data

Open **Settings → Sample runs** for repeatable demonstrations. Five reproducible scenarios cover clean approval, a repeated invoice, cumulative split-invoice overage, ambiguous PO matching, and missing fields/conflicting totals. Each scenario uses a unique isolated demo vendor/PO, so it can be repeated without resetting the database. Demo records are clearly labeled and remain in the audit history.

The procurement screen starts with fictional Acme Cloud Services and Northstar Office Supply records. Seed and demo POs are complete, prevalidated fictional fixtures. Use the PO upload/review workflow for your own documents. The raw `/api/procurement/import` JSON API remains for compatibility: it cannot grant PO validation or change vendor approval, and new vendors remain pending. Approved commitments are ledger-derived and cannot be overwritten. Policy changes affect subsequent evaluations; every completed run keeps the policy snapshot used at decision time.

See [samples](samples/) for real input files, [the demo script](docs/demo.md) for a five-minute walkthrough, and [the architecture](docs/architecture.md) for the process map. Regenerate samples with `uv run python scripts/generate_samples.py`.

For downloaded internet examples, including handwriting, see the [internet test fixtures and historical upload observations](samples/internet/README.md). The source repository includes seven public sample files with publisher attribution, five historical test PO records, and separate correction fixtures. The documented observations predate the required PO review policy; legacy test POs need **Review / validate** before reproducing the same invoice outcomes. Historical runtime result files and the local ZIP are not included in the published source. Reproduce checks with `uv run python scripts/verify_internet_samples.py --local`; the runner uses isolated databases, so benchmarking does not spend your workspace's PO balances.

## Verify

```bash
./scripts/test.sh
```

Tests exercise extraction, malformed/provider inputs, monetary normalization, business rules, duplicate and cumulative balance behavior, live API/worker integration, concurrency, review/correction, exports, atomic imports, and restart recovery. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` isolates tests from unrelated globally installed pytest plugins.

## Data and operational boundary

All local records and original inputs live under `data/` (or `LEDGERFLOW_DATA_DIR`), ignored by Git. Keep that directory private and back it up as a unit while the service is stopped. To start fresh, stop the app and move the data directory aside; the next start seeds a clean database. No deletion/reset endpoint is exposed.

This is a complete single-workspace case-study application, not a production multi-tenant AP system. Reviewer names are self-attested audit labels. Local startup binds to loopback; the hosted configuration requires a shared reviewer login and explicitly trusts the assigned Render origin. Unexpected Host/Origin values are rejected. Production operation would additionally require individual authentication and authorization, tenant isolation, a durable external queue, malware scanning/sandboxing for untrusted documents, retention controls and operational monitoring. Startup recovery marks interrupted runs failed, making resubmission explicit. Procurement is an imported snapshot rather than a live ERP connector. Vendor approval is an operator decision. Bank verification, goods receipts, tax compliance and sending payments are not implemented.

### Live PO product checks

In **Review fields → PO checks**, review the order value and cumulative amount, then each product's ordered, already invoiced, current and remaining quantity. Unique SKU or exact normalized-description matches are automatic; select a PO product for ambiguous/unmatched descriptions. These selections survive filtering and are audited on Save & reprocess. Net prices are compared after discounts and tax exclusion. Partial invoices are supported; duplicated product rows cannot bypass quantity limits. Only approved invoices consume capacity, and approval rechecks under a serialized transaction. PO updates cannot erase consumed quantities. These are invoiced quantities; physical receipt still needs a goods-receipt record.
