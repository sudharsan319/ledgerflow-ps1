# Calculation regression examples

Open a completed invoice’s **Calculations** tab for deterministic product and invoice checks. AI calculation generation has been removed; OpenRouter remains available for extraction.

These are fictional calculation examples. They reference the existing Acme USD PO. Procurement, duplicate and capacity checks run separately, so a reconciled calculation does not guarantee an approved invoice. Re-uploading a file intentionally triggers duplicate detection. No samples were imported into your workspace during development.

| File | Case | Expected rules total/status |
|---|---|---|
| `01-tax-exclusive.json` | Subtotal plus tax | 1180 / reconciled |
| `02-tax-inclusive.json` | Tax already included | 1180 / reconciled |
| `03-mixed-rates.json` | Separate tax groups | 223 / reconciled |
| `04-discount-and-shipping.json` | Discount and charge with declared tax base | 1121 / reconciled |
| `05-percentage-discount.json` | Percentage discount on explicit base | 1062 / reconciled |
| `06-explicit-rounding.json` | Signed round-off | 100 / reconciled |
| `07-real-mismatch.json` | Printed total deliberately differs | 1180 calculated vs 1250 printed / mismatch |
| `08-unknown-adjustment.json` | Unclear adjustment meaning | needs review |

These small files are arithmetic regression examples. For actual downloaded invoices use `samples/internet/`; the user's photo remains in `samples/manual-samples/`. The calculator does not infer a tax-inclusion mode by trying formulas until one matches. Use **Review fields** to correct source extraction, then **Save & reprocess**. The old `live-check.json` is historical evidence from the retired AI calculation feature, not current functionality.
