# Five-minute demo runbook

This is a runnable invoice-to-decision workflow for PS-1. The demonstration should show real inputs, actual execution, a difficult exception, and the evidence behind the outcome. The application records approvals and purchase-order commitments; it does not execute payments.

Read the [word-for-word narration](demo-narration.md) for the final five-minute take. The longer reference walkthrough below is for rehearsal and interview preparation.

## Before recording

1. Run `./scripts/start-demo.sh` and open `http://127.0.0.1:8001`. This creates an isolated rehearsal database without changing the regular workspace. Confirm Settings reports the expected extraction mode. A key is unnecessary for JSON, CSV, machine-readable PDF or clearly labeled text.
2. Open the dashboard and Procurement register. Keep `samples/01-clean-invoice.pdf`, `samples/02-scanned-invoice.png`, and `samples/02-scanned-verified.json` available.
3. Rehearse the **Settings → Sample runs** once. These are real pipeline submissions with newly created demo vendors/POs on every click. They do not share invoice identities or commitments with sample-file uploads, so repeated rehearsals remain useful.
4. For a fresh sample-file rehearsal, use a separate `LEDGERFLOW_DATA_DIR` with a separate app process. Existing approvals intentionally affect subsequent uploads; do not erase a workspace to make a demo pass.
5. If showing scans, install Tesseract or use the Docker image. Alternatively configure `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and `ENABLE_OPENROUTER=true` on the server, then restart. The chosen model must support structured output and image input. This sends document contents to OpenRouter. No API key should appear on screen.

## Suggested recording: 0:00-5:00

| Time | Screen/action | Narrative |
| --- | --- | --- |
| 0:00-0:30 | Overview, then Procurement | "AP receives inconsistent invoice files. This workspace accepts documents and structured data, checks them against approved vendors and purchase orders, and preserves the explanation for every decision." |
| 0:30-1:25 | Add invoice, upload `01-clean-invoice.pdf`; open its run | "This is a real PDF. The service records intake, extraction, validation, PO matching and the decision. Here are the source snippets, amounts and arithmetic checks. USD 1,100 is now committed against PO-2026-001." Local execution can finish faster than a polling interval; completed stages are still actual persisted work, not animation. |
| 1:25-2:00 | Upload `01-clean-invoice.json` | "The file format changed, but the vendor and invoice number are the same. The duplicate is rejected, and no second commitment is created. File fingerprints also catch byte-identical uploads." |
| 2:00-2:50 | Run **Settings → Sample runs → Split PO overage** and inspect both runs | "The first USD 600 invoice commits against this USD 1,000 demo PO. The next USD 500 would bring the cumulative amount to USD 1,100, beyond the USD 1,020 allowance. Each invoice fits alone; their combined value does not. Approval and commitment calculation run in one database transaction." |
| 2:50-3:35 | Run **Ambiguous PO**; open the review form | "Two approved POs are plausible. The system does not guess. A reviewer can select the verified PO and record their name and reason. Approval reruns the current checks, including available capacity." Select the intended PO and approve only as the fictional reviewer for this demo. |
| 3:35-4:25 | Upload the scanned PNG; inspect extraction and corrections | "Scans introduce recognition risk. The system flags uncertainty instead of silently approving guessed facts. A reviewer compares the source, edits the invoice fields, and submits a verified correction with an explanation. The new run links to the original and reruns every policy check." Use `02-scanned-verified.json` as the transcription after comparing it with the source image. |
| 4:25-5:00 | Open audit trail and download JSON or CSV; show history | "Every run retains source identity, extraction evidence, rule results and human review. JSON supports downstream systems, CSV supports operations, and printable HTML supports a readable decision report. OpenRouter can improve extraction across layouts, while the decision rules stay local and deterministic." |

Keep the first take under five minutes. If an API call is slow, the scanned example is optional in the recording; demonstrate the happy path and two difficult policy cases first. Do not replace slow processing with fabricated progress. The same examples remain available for the live interview.

## OCR correction walkthrough

The scanned invoice has identity `ACME-SCAN-1109`, distinct from the clean invoice, so a completed happy-path run does not itself make this correction a duplicate.

1. Upload either the scanned PNG or PDF, not both.
2. Inspect the source and extraction evidence. The default confidence threshold is 0.75, so a scan's score alone may pass. In the supplied two-column scan, local OCR reads the amounts and PO but may merge neighboring labels, leaving identity/date/currency unresolved. Show those specific errors before correcting them. Source evidence and financial policy checks still run independently. If Tesseract is absent and OpenRouter is disabled, the run explains that no readable text was available.
3. In the run's correction editor, enter the values in `samples/02-scanned-verified.json` only after visually verifying them. Enter a reviewer name and a reason such as "Compared every core field and line amount with the original scan."
4. Submit the correction. The original becomes superseded, the replacement retains an audit link, and deterministic checks execute again. A true duplicate, wrong currency, bad arithmetic or exceeded PO capacity still prevents approval.
5. Inspect the replacement and original audit trails. Do not claim that model confidence is a calibrated probability; it is a conservative routing signal supported by field evidence and policy checks.

If using the API directly, send `POST /api/runs/{id}/correct` with `{"invoice": {...}, "reviewer": "Your name", "comment": "Verification evidence"}`. The response contains the new queued run. Corrections do not overwrite the original record.

## What to explain in the interview

- **Input boundary:** one invoice per PDF/image/text document; JSON and CSV support batches. A consistent invoice schema allows more adapters without rewriting financial rules. EML attachments are supported by the backend parser.
- **Extraction boundary:** local text parsing is conservative and best with explicit labels. Scans need OCR or an enabled vision model. Missing values remain missing; the system does not invent them.
- **AI boundary:** OpenRouter extracts facts into a strict schema. Source evidence and local checks constrain those facts. No model receives a payment tool or authority to approve an invoice.
- **Financial rules:** exact approved vendor matching with declared aliases; explicit PO references or a unique vendor/currency candidate; required fields; dates; arithmetic; duplicates; currency; PO status; cumulative commitments with tolerance. No automatic FX conversion.
- **Human boundary:** reviewers may resolve ambiguity and provide verified corrections. Hard policy blocks require corrected data or procurement changes, not a blind approval override.
- **Operational boundary:** SQLite persistence, bounded local execution, restart recovery, source retention and exports make the case study runnable. A wider deployment would need authentication, access controls, backups, observability and multi-tenant isolation.

Record the actual demo video yourself when ready. This application stays local as requested; this runbook does not claim a recording or a hosted deployment.
