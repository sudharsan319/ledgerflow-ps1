#!/usr/bin/env python3
"""Create deterministic fictional invoice fixtures; no external data or model call."""
from __future__ import annotations

import csv
import io
import json
from decimal import Decimal
from pathlib import Path

import fitz
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"


def invoice(number: str = "ACME-2026-1109", total: str = "1100.00", subtotal: str = "1000.00", tax: str = "100.00", **changes) -> dict:
    result = {
        "invoice_number": number,
        "vendor_name": "Acme Cloud Services",
        "vendor_id": "vendor-acme",
        "po_number": "PO-2026-001",
        "invoice_date": "2026-11-09",
        "due_date": "2026-12-09",
        "currency": "USD",
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "line_items": [{"description": "Cloud services", "quantity": str(Decimal(subtotal) / 100), "unit_price": "100.00", "amount": subtotal}],
    }
    result.update(changes)
    return result


def labeled(data: dict) -> str:
    labels = {"invoice_number": "Invoice Number", "vendor_name": "Vendor", "vendor_id": "Vendor ID", "po_number": "PO Number", "invoice_date": "Invoice Date", "due_date": "Due Date", "currency": "Currency", "subtotal": "Subtotal", "tax": "Tax", "total": "Total"}
    lines = ["INVOICE", "Fictional evaluation fixture; no payment is due.", ""]
    lines += [f"{label}: {data[field]}" for field, label in labels.items() if data.get(field) is not None]
    lines += ["", "Description | Quantity | Unit price | Amount"]
    lines += [" | ".join(item[field] for field in ("description", "quantity", "unit_price", "amount")) for item in data.get("line_items", [])]
    return "\n".join(lines) + "\n"


def write_json(name: str, data: object) -> None:
    (OUT / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def make_pdf(data: dict) -> bytes:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    navy, teal, muted, pale = (0.09, 0.16, 0.24), (0.04, 0.40, 0.39), (0.39, 0.45, 0.52), (0.95, 0.97, 0.98)
    page.draw_rect(fitz.Rect(0, 0, 595, 15), color=None, fill=teal)
    page.insert_text((44, 65), "ACME / CLOUD", fontsize=19, fontname="hebo", color=navy)
    page.insert_text((44, 87), "Infrastructure for the teams building tomorrow.", fontsize=10, color=muted)
    page.insert_text((420, 65), "INVOICE", fontsize=21, fontname="hebo", color=teal)
    page.draw_line((44, 117), (551, 117), color=(0.85, 0.89, 0.91), width=0.8)
    page.insert_text((44, 146), "Vendor: " + data["vendor_name"], fontsize=11, fontname="hebo", color=navy)
    page.insert_text((44, 166), "Vendor ID: " + data["vendor_id"], fontsize=10, color=muted)
    page.insert_text((44, 187), "42 Example Avenue, Suite 300", fontsize=10, color=muted)
    page.insert_text((44, 204), "Fictional City, CA 90000", fontsize=10, color=muted)
    page.insert_text((44, 249), "BILL TO", fontsize=9, fontname="hebo", color=teal)
    page.insert_text((44, 270), "Example Operations Ltd.", fontsize=12, fontname="hebo", color=navy)
    page.insert_text((44, 290), "Accounts payable department", fontsize=10, color=muted)
    page.draw_rect(fitz.Rect(300, 135, 551, 305), color=None, fill=pale)
    metadata = [("Invoice Number", data["invoice_number"]), ("Invoice Date", data["invoice_date"]), ("Due Date", data["due_date"]), ("PO Number", data["po_number"]), ("Currency", data["currency"])]
    for index, (label, value) in enumerate(metadata):
        page.insert_text((315, 160 + index * 27), f"{label}: {value}", fontsize=10, color=navy)
    page.draw_rect(fitz.Rect(44, 341, 551, 373), color=None, fill=navy)
    page.insert_text((56, 362), "DESCRIPTION  |  QUANTITY  |  UNIT PRICE  |  AMOUNT", fontsize=10, fontname="hebo", color=(1, 1, 1))
    for index, item in enumerate(data['line_items']):
        row = " | ".join(str(item[key]) for key in ('description', 'quantity', 'unit_price', 'amount'))
        page.insert_text((56, 402 + index * 18), row, fontsize=10, color=navy)
    page.draw_line((44, 424), (551, 424), color=(0.85, 0.89, 0.91), width=0.8)
    page.insert_text((335, 464), "Subtotal: " + data["subtotal"], fontsize=11, color=navy)
    page.insert_text((335, 490), "Tax: " + data["tax"], fontsize=11, color=navy)
    page.draw_rect(fitz.Rect(320, 510, 551, 556), color=None, fill=pale)
    page.insert_text((335, 540), "Total: " + data["total"], fontsize=18, fontname="hebo", color=teal)
    page.insert_text((44, 623), "PAYMENT TERMS", fontsize=9, fontname="hebo", color=teal)
    page.insert_text((44, 645), "Net 30. Please quote the invoice number in any correspondence.", fontsize=10, color=muted)
    page.insert_text((44, 735), "FICTIONAL CASE-STUDY FIXTURE", fontsize=9, fontname="hebo", color=muted)
    page.insert_text((44, 754), "Created for evaluating invoice extraction and matching. No payment is due.", fontsize=9, color=muted)
    page.insert_text((520, 800), "1 / 1", fontsize=9, color=muted)
    content = document.tobytes(garbage=4, deflate=True)
    document.close()
    return content


def main() -> None:
    OUT.mkdir(exist_ok=True)
    clean = invoice()
    write_json("01-clean-invoice.json", clean)
    (OUT / "01-clean-invoice.txt").write_text(labeled(clean), encoding="utf-8")
    pdf = make_pdf(clean)
    (OUT / "01-clean-invoice.pdf").write_bytes(pdf)
    scanned = invoice("ACME-SCAN-1109")
    write_json("02-scanned-verified.json", scanned)
    with fitz.open(stream=make_pdf(scanned), filetype="pdf") as document:
        pixmap = document[0].get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False)
    image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("L")
    image = image.filter(ImageFilter.GaussianBlur(radius=0.30)).rotate(0.20, expand=False, fillcolor=255)
    image.save(OUT / "02-scanned-invoice.png")
    scan_document = fitz.open()
    page = scan_document.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=str(OUT / "02-scanned-invoice.png"))
    scan_document.save(OUT / "02-scanned-invoice.pdf", garbage=4, deflate=True)
    scan_document.close()

    missing = invoice("", "1500.00")
    del missing["invoice_number"]
    write_json("03-missing-and-conflicting-fields.json", missing)
    (OUT / "03-missing-and-conflicting-fields.txt").write_text(labeled(missing), encoding="utf-8")
    first = invoice("ACME-SPLIT-A", "8000.00", "8000.00", "0.00")
    second = invoice("ACME-SPLIT-B", "5000.00", "5000.00", "0.00")
    write_json("04-split-first.json", first)
    write_json("04-split-second.json", second)
    ambiguous = invoice("NS-AMBIGUOUS-01", "825.00", "750.00", "75.00", vendor_name="Northstar Office Supply", vendor_id="vendor-northstar", po_number=None, line_items=[{"description": "Office supplies", "quantity": "0.1", "unit_price": "7500.00", "amount": "750.00"}])
    write_json("05-ambiguous-po.json", ambiguous)
    rows = [invoice("BATCH-ACME-01", "650.00", "650.00", "0.00"), invoice("BATCH-NS-01", "1000.00", "1000.00", "0.00", vendor_name="Northstar Office Supply", vendor_id="vendor-northstar", po_number="PO-2026-002", line_items=[{"description": "Office equipment", "quantity": "0.2", "unit_price": "5000.00", "amount": "1000.00"}])]
    with (OUT / "06-batch-invoices.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "line_items": json.dumps(row["line_items"])})
    write_json("06-batch-invoices.json", {"invoices": rows})
    write_json("procurement.json", {
        "vendors": [
            {"id": "vendor-acme", "name": "Acme Cloud Services", "aliases": ["Acme Cloud", "Acme Cloud Services Ltd"], "approved": True},
            {"id": "vendor-northstar", "name": "Northstar Office Supply", "aliases": ["Northstar"], "approved": True},
        ],
        "purchase_orders": [
            {"po_number": "PO-2026-001", "vendor_id": "vendor-acme", "currency": "USD", "total": "12000.00", "status": "open", "description": "Cloud services annual agreement", "line_items": [{"description": "Cloud services", "quantity": "120", "unit_price": "100", "amount": "12000"}]},
            {"po_number": "PO-2026-002", "vendor_id": "vendor-northstar", "currency": "USD", "total": "5000.00", "status": "open", "description": "Office equipment"},
            {"po_number": "PO-2026-003", "vendor_id": "vendor-northstar", "currency": "USD", "total": "7500.00", "status": "open", "description": "Office supplies"},
        ],
    })
    print(f"Created 14 fictional sample files in {OUT}")


if __name__ == "__main__":
    main()
