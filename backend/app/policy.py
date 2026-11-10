"""Explainable AP rules. Models extract facts; this module makes decisions.

This engine is pure: callers must evaluate and reserve PO funds in a single
serialized transaction. No external services or payments are called here.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import re

from .models import InvalidNumber, decimal_value, decimal_string
from .calculations import calculate_rules, currency_unit
from .po_matching import reconcile_po

DEFAULT_POLICY = {
    "tolerance_percent": "2",
    "tolerance_absolute": "5",
    "min_confidence": 0.85,
}


def identity(value) -> str:
    """Case/spacing/punctuation normalization for vendor names and invoice IDs."""
    return re.sub(r"[^\w]", "", str(value or "").casefold(), flags=re.UNICODE)


def reference(value) -> str:
    return str(value or "").strip().casefold()


def _number(value):
    try:
        return decimal_value(value)
    except InvalidNumber:
        return None


def _aliases(vendor):
    aliases = vendor.get("aliases", [])
    if not isinstance(aliases, list):
        aliases = []
    return {identity(vendor.get("name")), *(identity(alias) for alias in aliases)} - {""}


def evaluate_invoice(invoice: dict, vendors: list[dict], purchase_orders: list[dict], prior_runs: list[dict], policy: dict | None = None, source_sha256: str = "") -> dict:
    settings = {**DEFAULT_POLICY, **(policy or {})}
    checks = []
    matched_vendor = None
    matched_po = None
    duplicate_of = None
    po_reconciliation = None
    rejection = False

    def check(code, label, status, message, blocking=False):
        checks.append({"code": code, "label": label, "status": status, "message": message, "blocking": bool(blocking)})

    # No incomplete or malformed critical extraction may auto-approve.
    required = [name for name in ("invoice_number", "invoice_date", "currency", "total") if invoice.get(name) in (None, "")]
    if not invoice.get("vendor_name") and not invoice.get("vendor_id"):
        required.append("vendor_name or vendor_id")
    if required:
        check("required_fields", "Required invoice fields", "fail", "Missing: " + ", ".join(required) + ". Correct the invoice data before approval.", True)
    else:
        check("required_fields", "Required invoice fields", "pass", "Invoice identity, vendor, date, currency and total are present.")
    warnings = invoice.get("normalization_warnings") or []
    if warnings:
        check("normalization", "Unambiguous field formats", "fail", "; ".join(str(w) for w in warnings), True)
    else:
        check("normalization", "Unambiguous field formats", "pass", "No malformed or conflicting fields were detected.")

    confidence = _number(invoice.get("extraction_confidence"))
    min_confidence = _number(settings.get("min_confidence"))
    method = str(invoice.get("extraction_method", "")).casefold()
    trusted_structured = method in {"structured", "json", "csv", "form", "manual", "structured_json", "structured_csv"}
    if min_confidence is None or not 0 <= min_confidence <= 1:
        check("policy_config", "Policy configuration", "fail", "Minimum confidence must be between 0 and 1.", True)
        min_confidence = Decimal("0.85")
    if confidence is None and trusted_structured:
        confidence = Decimal(1)
    if confidence is None or confidence < min_confidence or confidence > 1:
        check("extraction_confidence", "Extraction confidence", "warn", f"Extraction confidence is {str(confidence) if confidence is not None else 'unavailable'}; required threshold is {min_confidence}. Verify the source and correct the data.", True)
    else:
        check("extraction_confidence", "Extraction confidence", "pass", f"Extraction confidence {confidence} meets threshold {min_confidence}.")
    # Confidence is supporting metadata, not proof. Model/self-assigned scores
    # alone never make a document trustworthy.
    if not trusted_structured:
        evidence = invoice.get("field_evidence", invoice.get("evidence", {}))
        if isinstance(evidence, list):
            evidence = {item.get("field"): item.get("quote", item.get("value")) for item in evidence if isinstance(item, dict)}
        evidence = evidence if isinstance(evidence, dict) else {}
        core_fields = ["invoice_number", "invoice_date", "currency", "total"]
        core_fields.append("vendor_name" if invoice.get("vendor_name") else "vendor_id")
        absent = [field for field in core_fields if not evidence.get(field)]
        if absent:
            check("extraction_evidence", "Source evidence", "warn", "Source evidence is missing for " + ", ".join(absent) + ". Verify these fields before approval.", True)
        else:
            check("extraction_evidence", "Source evidence", "pass", "Core extracted fields have source evidence; deterministic checks still apply.")

    invoice_date = None
    for field, label in (("invoice_date", "Invoice date"), ("due_date", "Due date")):
        value = invoice.get(field)
        if not value:
            continue
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
                raise ValueError("date format")
            parsed = date.fromisoformat(str(value))
            if field == "invoice_date":
                invoice_date = parsed
                if parsed > date.today() + timedelta(days=1):
                    check("future_date", label, "warn", "Invoice date is in the future; confirm with the vendor.", True)
                else:
                    check("invoice_date", label, "pass", "Invoice date is valid.")
            elif invoice_date and parsed < invoice_date:
                check("due_date", label, "warn", "Due date is before the invoice date.", True)
        except (ValueError, TypeError):
            check(field, label, "fail", "Date must be a real calendar date in YYYY-MM-DD format.", True)

    currency = str(invoice.get("currency") or "").upper()
    if currency and not re.fullmatch(r"[A-Z]{3}", currency):
        check("currency_format", "Currency format", "fail", "Use an explicit three-letter currency code, such as USD or INR.", True)
    total = _number(invoice.get("total"))
    subtotal = _number(invoice.get("subtotal"))
    tax = _number(invoice.get("tax"))
    if total is None or total <= 0:
        check("positive_total", "Invoice amount", "fail", "Invoice total must be a valid positive amount. Credit notes use a separate workflow.", True)
    else:
        check("positive_total", "Invoice amount", "pass", f"Invoice total is {decimal_string(total)} {currency}.")
    unit = currency_unit(currency)
    if total is not None and total != total.quantize(unit):
        check("currency_precision", "Currency precision", "fail", f"Total has more precision than supported for {currency or 'this currency'}.", True)
    calculation = calculate_rules(invoice)
    checks.extend(calculation['checks'])
    lines = invoice.get('line_items') or []

    # Vendor IDs and exact known aliases are authoritative. Fuzzy matches never
    # grant approval; conflicting ID/name pairs are surfaced explicitly.
    vendor_id = reference(invoice.get("vendor_id"))
    name = identity(invoice.get("vendor_name"))
    if vendor_id:
        vendor_matches = [v for v in vendors if reference(v.get("id")) == vendor_id]
    else:
        vendor_matches = [v for v in vendors if name and name in _aliases(v)]
    if len(vendor_matches) != 1:
        check("vendor_match", "Approved vendor", "fail", "Vendor is unknown or matches multiple procurement records. Resolve the vendor identity.", True)
    else:
        matched_vendor = vendor_matches[0]
        if name and name not in _aliases(matched_vendor):
            check("vendor_identity", "Vendor identity", "fail", "Vendor name conflicts with the supplied vendor ID or its registered aliases.", True)
        if matched_vendor.get("approved") is not True:
            pending = matched_vendor.get('approval_status') == 'pending'
            rejection = not pending
            check("vendor_approved", "Approved vendor", "warn" if pending else "fail", f"{matched_vendor.get('name', 'Vendor')} is awaiting separate vendor review." if pending else f"{matched_vendor.get('name', 'Vendor')} is not approved for purchasing.", True)
        else:
            check("vendor_approved", "Approved vendor", "pass", f"Matched approved vendor {matched_vendor.get('name', matched_vendor.get('id'))}.")

    # Approved and pending invoices reserve their vendor/invoice identity.
    # Rejected invoices may be corrected and resubmitted.
    invoice_id = identity(invoice.get("invoice_number"))
    for prior in prior_runs:
        if prior.get("decision") not in {"approved", "needs_review"}:
            continue
        old = prior.get("invoice") or {}
        same_bytes = bool(source_sha256 and source_sha256 == prior.get("source_sha256"))
        old_vendor_id = reference(old.get("vendor_id"))
        if not old_vendor_id and prior.get("matched_vendor"):
            old_vendor_id = reference(prior["matched_vendor"].get("id"))
        if matched_vendor:
            vendor_same = old_vendor_id == reference(matched_vendor.get("id")) or identity(old.get("vendor_name")) in _aliases(matched_vendor)
        else:
            vendor_same = bool(vendor_id and vendor_id == old_vendor_id) or bool(name and name == identity(old.get("vendor_name")))
        same_identity = bool(invoice_id and invoice_id == identity(old.get("invoice_number")) and vendor_same)
        if same_bytes or same_identity:
            duplicate_of = str(prior.get("id", "unknown"))
            rejection = True
            reason = "Identical source content" if same_bytes else "The same vendor and invoice number"
            check("duplicate", "Duplicate detection", "fail", f"{reason} already exists in run {duplicate_of}. Review or correct the original run.", True)
            break
    if duplicate_of is None:
        check("duplicate", "Duplicate detection", "pass", "No prior run with the same source content or vendor/invoice identity.")

    po_reference = reference(invoice.get("po_number"))
    if po_reference:
        po_matches = [po for po in purchase_orders if reference(po.get("po_number")) == po_reference]
        if len(po_matches) == 1:
            matched_po = po_matches[0]
            check("po_match", "Purchase order match", "pass", f"Explicit reference matched {matched_po.get('po_number')}.")
        else:
            check("po_match", "Purchase order match", "fail", "The explicit PO reference is missing from procurement records or is not unique.", True)
    elif matched_vendor:
        candidates = [po for po in purchase_orders if reference(po.get("vendor_id")) == reference(matched_vendor.get("id")) and str(po.get("currency", "")).upper() == currency and po.get("status") == "open"]
        if len(candidates) == 1:
            matched_po = candidates[0]
            check("po_match", "Purchase order match", "pass", f"No PO reference supplied; only one open PO matches this vendor and currency: {matched_po.get('po_number')}.")
        elif len(candidates) > 1:
            check("po_ambiguous", "Purchase order match", "warn", "Multiple open purchase orders match this vendor and currency: " + ", ".join(str(po.get("po_number")) for po in candidates) + ". Select the correct PO.", True)
        else:
            check("po_match", "Purchase order match", "warn", "No open purchase order matches this vendor and currency.", True)
    else:
        check("po_match", "Purchase order match", "warn", "A known vendor or explicit PO is needed before matching.", True)

    if matched_po:
        if matched_po.get('validation_status') == 'unverified':
            check('po_validation', 'Validated purchase order', 'fail', 'This PO has not passed the required PO-field checks and authorization review. Validate it in Procurement first.', True)
        elif matched_po.get('validation_status') == 'validated':
            check('po_validation', 'Validated purchase order', 'pass', 'PO fields and purchasing authorization were reviewed independently of vendor approval.')
        if matched_po.get('approval_status') not in (None, 'approved'):
            check('po_authorization', 'PO purchasing approval', 'fail', 'This PO is pending or rejected; it cannot authorize invoice approval.', True)
        if matched_vendor and reference(matched_po.get("vendor_id")) != reference(matched_vendor.get("id")):
            check("po_vendor", "PO vendor consistency", "fail", "The purchase order belongs to a different vendor.", True)
        else:
            check("po_vendor", "PO vendor consistency", "pass", "PO vendor agrees with the matched invoice vendor." if matched_vendor else "Vendor identity must be resolved before approval.")
        if str(matched_po.get("currency", "")).upper() != currency:
            check("po_currency", "PO currency consistency", "fail", f"Invoice currency {currency or 'missing'} differs from PO currency {matched_po.get('currency')}. No automatic currency conversion is allowed.", True)
        else:
            check("po_currency", "PO currency consistency", "pass", f"Both invoice and PO use {currency}.")
        if matched_po.get("status") != "open":
            check("po_status", "PO availability", "fail", "The purchase order is closed or not available for invoicing.", True)
        else:
            check("po_status", "PO availability", "pass", "Purchase order is open.")
        po_total, committed = _number(matched_po.get("total")), _number(matched_po.get("committed", "0"))
        percent, absolute = _number(settings.get("tolerance_percent")), _number(settings.get("tolerance_absolute"))
        if po_total is None or committed is None or po_total <= 0 or committed < 0:
            check("po_balance", "Cumulative PO balance", "fail", "Procurement contains an invalid PO amount or committed balance.", True)
        elif percent is None or absolute is None or percent < 0 or absolute < 0:
            check("tolerance", "Tolerance policy", "fail", "Tolerance percent and absolute tolerance must be finite nonnegative numbers.", True)
        elif total is not None and total > 0:
            allowance = max(absolute, po_total * percent / Decimal(100))
            limit = po_total + allowance
            cumulative = committed + total
            detail = f"Committed {committed} + invoice {total} = {cumulative} {currency}; PO {po_total}, allowed total {limit} (tolerance {allowance})."
            if cumulative > limit:
                check("po_balance", "Cumulative PO balance", "fail", detail + " This invoice would exceed the remaining authorized balance.", True)
            else:
                check("po_balance", "Cumulative PO balance", "pass", detail)
            if cumulative > po_total and cumulative <= limit:
                check("within_tolerance", "Tolerance applied", "pass", "The cumulative amount exceeds the original PO but remains within the configured tolerance.")
        po_reconciliation = reconcile_po(invoice, matched_po, prior_runs, calculation['products'], check, unit)

    if rejection:
        decision = "rejected"
    elif any(item["status"] in {"warn", "fail"} for item in checks):
        decision = "needs_review"
    else:
        decision = "approved"
    reasons = [item["message"] for item in checks if item["status"] in {"warn", "fail"}]
    summary = "All applicable controls passed. Invoice is approved for the AP queue; no payment has been made." if decision == "approved" else ("Rejected: " if decision == "rejected" else "Review required: ") + " ".join(reasons[:3])
    return {"decision": decision, "summary": summary, "checks": checks, "matched_po": matched_po, "matched_vendor": matched_vendor, "duplicate_of": duplicate_of, "calculation": calculation, "po_reconciliation": po_reconciliation}
