from copy import deepcopy
from decimal import Decimal

import pytest

from backend.app.models import InvalidNumber, decimal_value, normalize_invoice
from backend.app.policy import evaluate_invoice


VENDORS = [
    {"id": "v1", "name": "Acme Supplies", "aliases": ["Acme Supplies Ltd."], "approved": True},
    {"id": "v2", "name": "Blocked Vendor", "aliases": [], "approved": False},
]
POS = [{"po_number": "PO-1", "vendor_id": "v1", "currency": "USD", "total": "1000", "committed": "0", "status": "open"}]


def invoice(**changes):
    raw = {
        "invoice_number": "INV-001", "vendor_name": "Acme Supplies", "po_number": "PO-1",
        "invoice_date": "2026-01-01", "currency": "USD", "subtotal": "900", "tax": "90",
        "total": "990", "line_items": [{"description": "Services", "quantity": "3", "unit_price": "300", "amount": "900"}],
        "extraction_method": "structured", "extraction_confidence": 1,
    }
    raw.update(changes)
    return normalize_invoice(raw)


def run(value=None, **kwargs):
    return evaluate_invoice(value or invoice(), kwargs.get("vendors", VENDORS), kwargs.get("pos", POS), kwargs.get("prior", []), kwargs.get("policy", {}), kwargs.get("sha", ""))


def rule(result, code):
    return next(check for check in result["checks"] if check["code"] == code)


def test_happy_path_explains_every_decision():
    result = run()
    assert result["decision"] == "approved"
    assert result["matched_po"]["po_number"] == "PO-1"
    assert all(check["status"] == "pass" for check in result["checks"])


def test_split_invoice_overcommit_is_blocking():
    pos = deepcopy(POS)
    pos[0]["committed"] = "100"
    result = run(pos=pos)
    assert result["decision"] == "needs_review"
    assert rule(result, "po_balance")["blocking"] is True
    assert "1090" in rule(result, "po_balance")["message"]


def test_tolerance_boundary_uses_decimal_arithmetic():
    result = run(invoice(total="1020", subtotal="1020", tax="0", line_items=[]))
    assert result["decision"] == "approved"
    result = run(invoice(total="1020.01", subtotal="1020.01", tax="0", line_items=[]))
    assert result["decision"] == "needs_review"
    assert rule(result, "po_balance")["status"] == "fail"


def test_duplicate_identity_normalizes_vendor_alias_and_invoice_punctuation():
    old = {"id": "run-original", "invoice": invoice(vendor_name="Acme Supplies Ltd.", invoice_number="inv 001"), "decision": "approved"}
    result = run(prior=[old])
    assert result["decision"] == "rejected"
    assert result["duplicate_of"] == "run-original"


def test_duplicate_content_blocks_changed_invoice_number():
    old = {"id": "same-source", "invoice": invoice(invoice_number="INV-OLD"), "source_sha256": "abc", "decision": "needs_review"}
    result = run(prior=[old], sha="abc")
    assert result["decision"] == "rejected"
    assert result["duplicate_of"] == "same-source"


def test_rejected_invoice_can_be_corrected_and_resubmitted():
    old = {"id": "run-original", "invoice": invoice(), "decision": "rejected", "source_sha256": "abc"}
    assert run(prior=[old], sha="abc")["decision"] == "approved"


def test_unknown_vendor_never_auto_approves():
    result = run(invoice(vendor_name="Acme Supples"))
    assert result["decision"] == "needs_review"
    assert rule(result, "vendor_match")["blocking"] is True


def test_unapproved_vendor_is_rejected():
    result = run(invoice(vendor_name="Blocked Vendor"))
    assert result["decision"] == "rejected"
    assert rule(result, "vendor_approved")["status"] == "fail"


def test_conflicting_vendor_id_and_name_blocks_approval():
    result = run(invoice(vendor_id="v1", vendor_name="Someone Else"))
    assert result["decision"] == "needs_review"
    assert rule(result, "vendor_identity")["blocking"] is True


def test_ambiguous_po_requires_selection():
    pos = POS + [{**POS[0], "po_number": "PO-2"}]
    result = run(invoice(po_number=None), pos=pos)
    assert result["decision"] == "needs_review"
    assert result["matched_po"] is None
    assert rule(result, "po_ambiguous")["blocking"] is True
    corrected = run(invoice(po_number="PO-2"), pos=pos)
    assert corrected["decision"] == "approved"


def test_unique_implicit_po_can_be_matched():
    result = run(invoice(po_number=None))
    assert result["decision"] == "approved"
    assert "only one open PO" in rule(result, "po_match")["message"]


def test_unknown_explicit_po_is_not_fuzzily_replaced():
    result = run(invoice(po_number="PO-11"))
    assert result["matched_po"] is None
    assert rule(result, "po_match")["blocking"] is True


def test_mismatched_currency_cannot_use_same_numeric_amount():
    result = run(invoice(currency="EUR"))
    assert rule(result, "po_currency")["blocking"] is True
    assert result["decision"] == "needs_review"


def test_closed_po_is_blocked():
    result = run(pos=[{**POS[0], "status": "closed"}])
    assert rule(result, "po_status")["blocking"] is True


def test_required_fields_and_bad_dates_block():
    result = run(invoice(invoice_number=None, invoice_date="2026-02-30"))
    assert rule(result, "required_fields")["blocking"] is True
    assert rule(result, "invoice_date")["blocking"] is True


def test_future_invoice_requires_review():
    result = run(invoice(invoice_date="2099-01-01"))
    assert rule(result, "future_date")["blocking"] is True


def test_arithmetic_disagreement_not_hidden_by_po_tolerance():
    result = run(invoice(total="995"))
    assert rule(result, "arithmetic")["blocking"] is True


def test_line_amount_and_subtotal_must_reconcile():
    result = run(invoice(line_items=[{"quantity": "2", "unit_price": "400", "amount": "850"}]))
    assert rule(result, "line_1")["blocking"] is True
    assert rule(result, "line_sum")["blocking"] is True


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity", "1,234", "1,23,456", True, {}, "9e9999"])
def test_malformed_amounts_fail_closed(amount):
    normalized = invoice(total=amount)
    result = run(normalized)
    assert normalized["total"] is None
    assert result["decision"] == "needs_review"
    assert rule(result, "normalization")["blocking"] is True


@pytest.mark.parametrize("amount, expected", [("1,234.50", "1234.50"), ("1.234,50", "1234.50"), ("1234,50", "1234.50"), ("1 234.50", "1234.50"), ("1,234,567", "1234567")])
def test_unambiguous_amount_formats(amount, expected):
    assert decimal_value(amount) == Decimal(expected)


def test_no_total_or_tax_is_invented():
    normalized = normalize_invoice({"subtotal": "100", "tax": "10"})
    assert normalized["total"] is None
    assert normalized["currency"] is None


def test_low_model_confidence_requires_review_despite_correct_amounts():
    result = run(invoice(extraction_method="openrouter", extraction_confidence=0.4))
    assert rule(result, "extraction_confidence")["blocking"] is True


def test_default_confidence_threshold_is_075():
    at_threshold = run(invoice(extraction_method="openrouter", extraction_confidence=0.75))
    below_threshold = run(invoice(extraction_method="openrouter", extraction_confidence=0.74))
    assert rule(at_threshold, "extraction_confidence")["status"] == "pass"
    assert rule(below_threshold, "extraction_confidence")["blocking"] is True
    assert "0.75" in rule(below_threshold, "extraction_confidence")["message"]


def test_model_self_reported_confidence_requires_source_evidence():
    result = run(invoice(extraction_method="openrouter", extraction_confidence=1))
    assert rule(result, "extraction_evidence")["blocking"] is True
    assert result["decision"] == "needs_review"


def test_document_with_core_evidence_can_pass_deterministic_controls():
    evidence = {key: "literal source quote" for key in ("invoice_number", "invoice_date", "currency", "total", "vendor_name")}
    result = run(invoice(extraction_method="pdf_text", extraction_confidence=0.95, field_evidence=evidence))
    assert result["decision"] == "approved"


def test_missing_confidence_not_upgraded_for_documents():
    value = invoice(extraction_method="pdf_text")
    del value["extraction_confidence"]
    result = run(value)
    assert rule(result, "extraction_confidence")["blocking"] is True


def test_bundled_total_only_invoice_is_nonblocking_review():
    result = run(invoice(subtotal=None, tax=None, line_items=[]))
    assert result["decision"] == "needs_review"
    assert rule(result, "arithmetic")["blocking"] is False


def test_conflicting_aliases_are_not_silently_ignored():
    result = run(invoice(grand_total="999"))
    assert rule(result, "normalization")["blocking"] is True


def test_invalid_policy_and_negative_po_balances_fail_closed():
    result = run(policy={"tolerance_percent": "NaN"})
    assert rule(result, "tolerance")["blocking"] is True
    result = run(pos=[{**POS[0], "committed": "-10"}])
    assert rule(result, "po_balance")["blocking"] is True


def test_currency_precision_prevents_fractional_minor_units():
    result = run(invoice(total="990.001", subtotal="900.001", tax="90", line_items=[]))
    assert rule(result, "currency_precision")["blocking"] is True
