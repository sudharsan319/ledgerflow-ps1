# Five-minute walkthrough narration

Use a fresh rehearsal: run `./scripts/start-demo.sh`, then open `http://127.0.0.1:8001`. The fictional seed vendors and POs are already present. Open the two clean invoice files in your file picker: `samples/01-clean-invoice.pdf` and `samples/01-clean-invoice.json`. Keep this script off the recorded screen. Speak the quoted paragraphs; the screen actions are not narration. Aim for 4:45–4:55, leaving room for clicks.

## 0:00–0:30 — The problem

**Screen:** Overview, then briefly Procurement → Purchase orders.

“Hi, this is LedgerFlow, my solution to problem statement one: invoice processing from PDF to decision.

The problem is that an accounts-payable team has to read different invoice layouts, check their numbers, find the right purchase order and decide what needs attention. LedgerFlow automates those checks and gives the reviewer a clear explanation of the result. I’ll demonstrate a working PDF submission, followed by duplicate and split-invoice cases.”

## 0:30–1:25 — A PDF moves through the real process

**Screen:** Process invoices → upload `01-clean-invoice.pdf` → Start processing. Show the stage strip, Decision & checks, then Uploaded document.

“I’m uploading an actual PDF. These are fictional demonstration records, but they go through the same processing pipeline as any uploaded document.

The system records the input, extracts the invoice fields, validates them, matches the purchase order and saves a decision. These stages come from execution events stored by the backend.

This invoice has ten units at one hundred dollars each, plus one hundred dollars in tax. The total is eleven hundred dollars. The vendor is approved, the purchase order is eligible, and the applicable checks pass, so the invoice is approved. That reserves purchase-order capacity; it does not make a payment.”

## 1:25–2:00 — Explain the arithmetic and PO checks

**Screen:** Calculations, Matched PO, then Review fields → PO checks if time allows. This approved invoice is read-only.

“The calculation checks are independent of the extraction. Product amounts are calculated from quantity and unit price, with discounts applied before tax. Calculated taxes and totals are compared with the printed values.

For procurement, the system checks product prices and cumulative quantities, as well as the total order value. A single purchase order can support several invoices. These are billed quantities; confirming physical delivery would require a goods-receipt record.”

## 2:00–2:40 — Cross-format duplicate

**Screen:** Close the run. Upload `01-clean-invoice.json`. Show its rejected decision and duplicate check.

“Now I’m submitting the same invoice as JSON. The file is different, but the supplier and invoice identity are unchanged.

The system rejects it as a duplicate and links back to the original. It checks both file identity and business identity, so changing the file format does not create another approved commitment. This prevents the same invoice from being processed twice.”

## 2:40–3:35 — Several invoices against one PO

**Screen:** Close the run → Settings → expand Sample runs → Split PO overage. Open the first and second runs from history. Show the second run's PO balance and quantity failures.

“This scenario creates a one-thousand-dollar purchase order for ten units. The first invoice bills six units for six hundred dollars and is approved.

The second bills another five units for five hundred dollars. Each invoice fits the order on its own, but together they bill eleven units and eleven hundred dollars. That exceeds the ordered quantity and the permitted amount, so the second invoice needs review.

The approval step rechecks available capacity inside a database transaction. Two simultaneous approvals cannot both spend the same remaining capacity.”

## 3:35–4:25 — Human correction

**Screen:** Settings → Sample runs → Incomplete invoice → Review fields. Show source and editable headers. Change total from `1250` to `1100`, then click Check corrections. Leave the unknown invoice number blank. This is a preview; do not save or claim approval.

“When information is missing or inconsistent, the system explains the exception instead of guessing an approval.

Here, the invoice number is missing and the printed total conflicts with the components. The source sits beside the editable fields. Changing the total resolves the arithmetic, but the missing invoice number still needs verification from the supplier. The system keeps that issue open.

Saving a verified correction creates a linked replacement run and records the reviewer, reason and changes. It reruns the same financial controls, so human review cannot bypass a duplicate or an exceeded purchase order.”

## 4:25–4:55 — Output and design choice

**Screen:** Audit trail → JSON/CSV export → close to Overview.

“Every decision retains the source, checks and review history. Outputs are available as JSON, CSV or a printable report, and the dashboard brings all runs together.

OpenRouter is optional for extracting varied document layouts. It does not decide approval; the calculation and procurement rules remain deterministic. Difficult scans and handwriting can still need correction.

The aim is to reduce repetitive checking while keeping exceptions visible and every decision explainable.”

## Before the take

- Rehearse once in a fresh instance. Restart `start-demo.sh` for a new take; it creates a separate directory without clearing your regular data.
- The shown happy-path PDF was verified with local extraction. This recording does not demonstrate a live AI call; do not describe it as one.
- Watch the actual result before narrating an approval. If a record differs, describe its real checks instead of reading an expected outcome.
- If behind schedule, shorten the correction section and end with audit/export. Keep the real PDF happy path and at least one executed edge case.
- Do not show `.env`, API keys or private invoices. No slides, artificial progress or pre-rendered decisions are needed.
