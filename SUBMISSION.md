# LedgerFlow — PS-1 submission

**Invoice processing: from PDF to a reasoned decision.**

LedgerFlow accepts an invoice, extracts its fields, checks the arithmetic and procurement records, and returns Approved, Needs review or Rejected. The interface exposes actual processing stages, source documents, exceptions, review actions and run history. Approval records a PO commitment; it does not send a payment.

## Run and rehearse

Requirements: Python 3.11+, Node.js 20.19+ or 22.12+, npm and uv. Tesseract is needed for local OCR. The clean PDF and structured examples need no model key.

```bash
./scripts/start-demo.sh
```

Open **http://127.0.0.1:8001**. This creates a fresh rehearsal database with fictional procurement records and disables OpenRouter by default. It does not erase or change the normal workspace at port 8000. Stop with Ctrl+C. Every launch creates another rehearsal directory under `data/rehearsals/`.

For the regular workspace, use `./scripts/start.sh`. Optional OpenRouter configuration, Docker and platform setup are in [README](README.md). Hosted setup and reviewer access are documented in the [Render deployment guide](docs/render-deployment.md).

## Evaluation coverage

| Requirement | Implementation and demonstration |
| --- | --- |
| Real input to real output | Upload `samples/01-clean-invoice.pdf`; it approves for USD 1,100 in a fresh rehearsal. Download JSON, CSV or HTML. |
| Visible execution | The run drawer displays persisted receive, extract, validate, match, decide and save events. Fast runs may finish before the next screen refresh. |
| Dashboard and history | Overview counts, searchable invoice history, decisions, original sources and exports. |
| Edge 1: cross-format duplicate | Upload the clean PDF, then `01-clean-invoice.json`. The second is rejected despite its different file format. |
| Edge 2: split PO overage | Settings → Sample runs → Split PO overage. USD 600 approves; USD 500 then exceeds the shared USD 1,000 order and ordered quantity. |
| Edge 3: ambiguous PO | Settings → Sample runs → Ambiguous PO. Two candidate orders require an explicit reviewed selection. |
| Edge 4: missing identity and conflicting total | Settings → Sample runs → Incomplete invoice. Missing invoice number and inconsistent total require correction. |
| Messy document support | PDF text, OCR and optional OpenRouter vision; scans route to source review. The sample scan and verified correction exercise that flow. Handwriting accuracy varies and requires review. |
| Product arithmetic | Independent quantity × price − discount, tax, printed tax/line comparisons, invoice reconciliation and rounding. Tax may be split or combined. |
| Procurement controls | Separate vendor approval and PO validation; authorization, currency, net prices, cumulative approved quantities and amounts across partial invoices. |
| Human review and explanation | Source beside editable fields, live verification, reasoned policy checks, audited corrections and transactionally rechecked approvals. |

## Design choices and limits

AI extracts source facts; deterministic Decimal arithmetic and policy make decisions. Unknown or contradictory values stay visible for review. A printed tax base cannot override the independently computed product base. Exact supplier/product matching avoids silently approving a fuzzy match; a reviewer can allocate unmatched products explicitly.

The procurement register is an imported snapshot, not a live ERP connection. EML files are accepted, but no mailbox is monitored. Quantities are invoiced quantities, not physical receipt confirmation. Vendor approval records an operator's review, not external identity/bank verification. Credit notes, tax-compliance certification, actual payments, individual user authorization and multi-tenant production operation are outside this case study. Hosted access uses shared reviewer credentials by default; setting `LEDGERFLOW_PUBLIC_DEMO=true` explicitly enables access without a password. Public visitors share the same documents and can edit invoice/procurement records, so use fictional samples and keep OpenRouter disabled for that demonstration. If enabled, visitor extraction requests use the owner's configured provider key. Model extraction is not perfect; the editable review is the recovery path.

The Render Free configuration uses temporary storage, so uploaded documents and run history may reset after a restart, redeployment or idle shutdown. Fictional starter procurement records are recreated on a fresh start. This is a demonstration workspace, not durable accounting storage.

## Submission materials

- [Word-for-word five-minute narration](docs/demo-narration.md), with screen actions.
- [Demo preparation and fallback steps](docs/demo.md).
- [Process map and architecture](docs/architecture.md).
- [Submission message draft](docs/submission-message.txt).
- [Release verification](docs/submission-verification.md).
- [Render deployment and access](docs/render-deployment.md).
- Reproducible smoke check: `uv run python -m scripts.verify_submission`.
- Full checks: `./scripts/test.sh`.
- Build the source archive: `uv run python -m scripts.package_submission`.

## Final handoff status

The source repository is [sudharsan319/ledgerflow-ps1](https://github.com/sudharsan319/ledgerflow-ps1). **Render deployment is being prepared; a live service has not yet been verified.** A localhost URL works only on the machine running the app and must not be used as the evaluator's process link.

The candidate guide requests a live, runnable process link plus a video of five minutes or less. Before submission, verify the deployed URL and source repository, and check access as the intended recipient. For protected access, provide reviewer credentials privately; for public demo mode, confirm that the latest deployment opens without credentials. **The user still needs to record the walkthrough in their own voice and upload the video.** Use the prepared narration, replace the submission draft's URL placeholders, and verify the video duration and sharing permissions. No video or submission has been sent.
