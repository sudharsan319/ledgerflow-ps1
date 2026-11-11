# LedgerFlow — process map and architecture

Invoice input (PDF / PNG / JPEG / JSON / CSV / text)
→ bounded file ingestion + SHA-256 identity
→ document text / OCR / optional OpenRouter structured extraction
→ normalized invoice and field evidence
→ required-field, arithmetic, vendor and duplicate validation
→ explicit PO or unique open vendor/currency PO matching
→ reviewed PO validation, authorization, currency, tolerance, cumulative commitment and line-item checks
→ approved / needs_review / rejected, with rule-by-rule explanation
→ durable history, review audit trail, JSON / CSV / printable HTML outputs.

The Python/FastAPI service owns SQLite persistence locally or PostgreSQL persistence when `DATABASE_URL` is configured, a bounded worker queue, input parsing, optional model access, and deterministic policy. The React UI shows actual persisted stage events and run history. OpenRouter only extracts data; it cannot approve invoices, authorize POs, approve vendors, or call actions. Local structured inputs work without an API key. Free-form documents use a conservative parser and optional local OCR, or OpenRouter when explicitly configured on the server.

The runtime is a single process with SQLite or PostgreSQL and bounded invoice and PO extraction queues, each with one worker. Decision and PO reservation are one serialized database transaction. Restart recovery marks unfinished runs and PO uploads failed, so interrupted work is visible. Approval creates a commitment, not a payment; human rejection releases a commitment. Manual approval cannot bypass hard policy blocks.

## Purchase-order intake and independent vendor review

PO input (PDF / PNG / JPEG / WEBP / TIFF / CSV / XLSX / JSON / TXT)
→ bounded upload + retained source and SHA-256 identity
→ local structured parsing, document text/OCR, or optional OpenRouter extraction
→ persisted draft with field evidence and actual processing events
→ operator edits header/line items and runs **Save & validate**
→ required-field and Decimal arithmetic checks
→ revision-checked **Confirm import** with reviewer, evidence and authorization attestation
→ atomic registration as a **Validated PO**, preserving commitments.

The PO policy requires a PO number, vendor name, issue date, explicit currency, subtotal, tax, total, approval status, open/closed status, and one or more lines with description, quantity, unit price and amount. Checks reconcile each line, the subtotal and the gross total. CSV/XLSX group repeated PO numbers into line items; templates are downloadable from Procurement. Structured inputs are parsed locally. Document extraction is advisory, and every uploaded PO requires explicit human confirmation before registration.

A structurally valid PO can still be pending, rejected or closed and therefore ineligible for invoices. **Approved vendor** is a separate state, changed only through independent supplier review with a reviewer, reason and attestation. PO imports retain existing supplier approval and create new vendors as pending. Invoice approval requires both an approved vendor and a validated, approved, open PO, followed by the normal invoice checks.

Draft validation and confirmation use revision checks to prevent stale edits from overwriting a newer review. Changed existing POs require explicit update opt-in. A committed PO cannot change vendor/currency or shrink below committed value. Legacy POs without required fields and review evidence remain unverified until **Review / validate** is completed; historical invoice results and commitments are preserved. The raw JSON import endpoint is retained for compatibility and cannot grant validated status or change vendor approval.

See the [PO upload sample pack](../samples/purchase-orders/README.md) for complete documents, negative cases, and the manual review sequence. Seed/demo records are explicitly fictional, complete, prevalidated fixtures.

## Representative demo scenarios

1. Happy path: approved vendor, validated and approved open PO, matching currency and totals.
2. Duplicate: repeated vendor/invoice identity, including changed source formatting.
3. Split invoice: cumulative invoice commitments exceed the PO plus configured tolerance.
4. Ambiguous PO: no explicit reference and two viable POs; human review required.
5. Uncertain extraction / conflicting arithmetic: missing fields or inconsistent totals require review.

## Runtime boundary

This is a locally runnable case-study application with an optional local Docker runtime. Invoice data and procurement records are stored locally. OpenRouter receives invoice or PO document contents only when the operator enables it. Structured JSON/CSV and PO spreadsheets are processed locally. Reviewer identities are self-attested local audit labels; vendor approval records an operator decision, not external identity or bank verification. Public multi-user deployment requires authentication configuration, TLS, restricted network access, durable storage/backups and tenant isolation. No payment or outbound email is performed.
