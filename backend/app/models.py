"""Conservative invoice normalization shared by ingestion and policy.

Amounts remain decimal strings across API and storage boundaries. No binary floats
are used for arithmetic, and malformed fields are retained as validation warnings.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Any


class InvalidNumber(ValueError):
    pass


def decimal_value(value: Any, *, allow_negative: bool = True) -> Decimal:
    """Parse unambiguous decimal or comma-grouped numbers; reject NaN/infinity.

    Accepted: 1234.50, "1,234.50", "1 234.50", "1.234,50", "1234,50".
    A sole comma followed by three digits is ambiguous (1,234), so is rejected.
    Currency symbols and expressions are deliberately not silently stripped.
    """
    if isinstance(value, bool) or value is None:
        raise InvalidNumber("expected a finite number")
    text = str(value).strip().replace("\u00a0", " ").replace("\u202f", " ")
    if not text or len(text) > 64:
        raise InvalidNumber("expected a finite number")
    if " " in text:
        if not re.fullmatch(r"[+-]?\d{1,3}(?: \d{3})+(?:[.,]\d{1,6})?", text):
            raise InvalidNumber("ambiguous number grouping")
        text = text.replace(" ", "")
    if "," in text and "." in text:
        if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+\.\d{1,6}", text):
            text = text.replace(",", "")
        elif re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+,\d{1,6}", text):
            text = text.replace(".", "").replace(",", ".")
        else:
            raise InvalidNumber("ambiguous decimal separators")
    elif "," in text:
        if re.fullmatch(r"[+-]?\d+,\d{1,2}", text):
            text = text.replace(",", ".")
        elif re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3}){2,}", text):
            text = text.replace(",", "")
        else:
            raise InvalidNumber("ambiguous comma separator; use an ungrouped decimal")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d{1,6})?", text):
        raise InvalidNumber("expected an ordinary decimal with at most six decimal places")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise InvalidNumber("expected a finite number") from exc
    if not number.is_finite() or abs(number) > Decimal("1000000000000"):
        raise InvalidNumber("number is outside the supported range")
    if not allow_negative and number < 0:
        raise InvalidNumber("must not be negative")
    return number


def decimal_string(value: Decimal) -> str:
    return format(value, "f")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, bool)):
        return None
    return str(value).strip() or None


def normalize_invoice(raw: dict) -> dict:
    """Normalize a single invoice without inventing dates, totals, or currency."""
    if not isinstance(raw, dict):
        raise ValueError("An invoice must be a JSON object")
    warnings = [str(v) for v in raw.get("normalization_warnings", [])] if isinstance(raw.get("normalization_warnings"), list) else []
    aliases = {
        "invoice_number": ("invoice_number", "invoice_id", "invoice_no"),
        "vendor_name": ("vendor_name", "vendor", "supplier_name", "supplier"),
        "vendor_id": ("vendor_id", "supplier_id"),
        "po_number": ("po_number", "po_reference", "purchase_order", "purchase_order_number"),
        "invoice_date": ("invoice_date", "date", "issue_date"),
        "due_date": ("due_date",),
        "currency": ("currency", "currency_code"),
        "subtotal": ("subtotal", "sub_total", "net_amount"),
        "tax": ("tax", "tax_amount", "total_tax"),
        "total": ("total", "total_amount", "grand_total", "amount_due"),
    }
    invoice = {}
    for field, names in aliases.items():
        candidates = [(name, raw[name]) for name in names if raw.get(name) not in (None, "")]
        value = candidates[0][1] if candidates else None
        if len(candidates) > 1 and len({str(v).strip() for _, v in candidates}) > 1:
            warnings.append(f"Conflicting aliases for {field}: {', '.join(n for n, _ in candidates)}")
        if field in {"subtotal", "tax", "total"}:
            if value is None:
                invoice[field] = None
            else:
                try:
                    invoice[field] = decimal_string(decimal_value(value))
                except InvalidNumber as exc:
                    invoice[field] = None
                    warnings.append(f"Invalid {field}: {exc}")
        else:
            invoice[field] = _text(value)
            if value is not None and invoice[field] is None:
                warnings.append(f"Invalid {field}: expected text")
    if invoice["currency"]:
        invoice["currency"] = invoice["currency"].upper()
    for field in ("invoice_date", "due_date"):
        value = invoice[field]
        if value:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError("Expected ISO date")
                date.fromisoformat(value)
            except ValueError:
                warnings.append(f"Invalid {field}: use a real date in YYYY-MM-DD format")
    raw_lines = raw.get("line_items", raw.get("items", []))
    if not isinstance(raw_lines, list):
        warnings.append("Invalid line_items: expected a list")
        raw_lines = []
    if len(raw_lines) > 1000:
        warnings.append("Too many line items: maximum is 1000")
        raw_lines = raw_lines[:1000]
    invoice["line_items"] = []
    for index, raw_line in enumerate(raw_lines):
        if not isinstance(raw_line, dict):
            warnings.append(f"Invalid line item {index + 1}: expected an object")
            continue
        line = {"description": _text(raw_line.get("description", raw_line.get("name"))) or ""}
        for field, alias in (("quantity", "qty"), ("unit_price", "price"), ("amount", "line_total")):
            value = raw_line.get(field, raw_line.get(alias))
            if value in (None, ""):
                line[field] = None
            else:
                try:
                    line[field] = decimal_string(decimal_value(value))
                except InvalidNumber as exc:
                    line[field] = None
                    warnings.append(f"Invalid line {index + 1} {field}: {exc}")
        for field in ("sku", "tax", "tax_rate", "po_line_ref"):
            if field in raw_line:
                line[field] = _text(raw_line[field])
        invoice["line_items"].append(line)
    from .calculation_schema import normalize_financials
    normalize_financials(raw, invoice, warnings)
    # Preserve evidence and provenance. Absence of confidence is handled by policy,
    # not silently upgraded to a successful extraction.
    for field in ("extraction_method", "extraction_source", "field_evidence", "evidence", "warnings", "notes"):
        if field in raw:
            invoice[field] = raw[field]
    confidence = raw.get("extraction_confidence", raw.get("confidence"))
    if confidence is not None:
        try:
            numeric = decimal_value(confidence)
            if not Decimal(0) <= numeric <= Decimal(1):
                raise InvalidNumber("must be between zero and one")
            invoice["extraction_confidence"] = float(numeric)
        except InvalidNumber as exc:
            invoice["extraction_confidence"] = 0.0
            warnings.append(f"Invalid extraction_confidence: {exc}")
    invoice["normalization_warnings"] = list(dict.fromkeys(warnings))
    return invoice


def invoice_fingerprint(invoice):
    import hashlib
    import json
    return hashlib.sha256(json.dumps(invoice, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
