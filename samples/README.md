# Fictional invoice fixtures

These fixtures exercise the real upload and extraction pipeline. All organizations, addresses and invoices are synthetic. No payment is due.

A fresh rehearsal (`./scripts/start-demo.sh`, port 8001) contains the fictional vendors and validated purchase orders needed here. The regular workspace may contain different records. `procurement.json` documents the starter identities and amounts; raw import does not grant PO validation or supplier approval.

| Fixture | Expected result with the default policy and unused procurement records |
| --- | --- |
| `01-clean-invoice.pdf`, `.txt`, `.json` | Approved for USD 1,100. These are the **same invoice** in three formats; upload one first, then another to demonstrate duplicate detection. |
| `02-scanned-invoice.png`, `.pdf` | Needs review because OCR or vision extraction is deliberately capped below the default confidence threshold. If local OCR is unavailable, the run identifies the missing extraction dependency. |
| `02-scanned-verified.json` | Human-verified data for the scanned invoice; use this in the correction editor after comparing it with the image. Uploading it separately also exercises structured input. |
| `03-missing-and-conflicting-fields.json`, `.txt` | Needs review: missing invoice identity and USD 1,500 total against USD 1,000 subtotal plus USD 100 tax. |
| `04-split-first.json` then `04-split-second.json` | First USD 8,000 invoice approves; the second USD 5,000 invoice exceeds the USD 12,000 PO plus USD 240 tolerance and needs review. The second invoice alone can approve. |
| `05-ambiguous-po.json` | Needs review: two open POs exist for Northstar, with no explicit reference. For this fictional fixture, `PO-2026-003` contains the matching Office supplies product; select it during review. The billed fraction is 0.1 of that bundled line. |
| `06-batch-invoices.csv` or `.json` | Two invoice runs, one for each vendor; both approve if there is remaining capacity. These two files contain the same invoice identities, so uploading both produces duplicates. |
| `procurement.json` | Procurement import fixture, **not** an invoice upload. |

Fixtures use the same persistent procurement register. Earlier approvals consume capacity; repeat uploads are duplicates. To rehearse without managing existing commitments, use **Settings → Sample runs** instead: every click creates unique demo procurement records and invoice identities.

For a clean fixture rehearsal, use `./scripts/start-demo.sh` or launch a separate instance with a new `LEDGERFLOW_DATA_DIR`; do not delete an existing workspace. Keep only one process per data directory. Scanned fixtures are one-page images; the scanned PDF contains no text layer. Actual Tesseract verification extracts their PO and amounts, while the two-column layout can merge neighboring labels and leave identity/date/currency fields unresolved. That partial extraction intentionally requires a source-verified correction. Local OCR needs the Tesseract executable, included in the Docker image. OpenRouter is optional and requires explicit server configuration.

Regenerate the files from the repository root:

```bash
uv run python scripts/generate_samples.py
```
