import io
import json
from email.message import EmailMessage

import pytest

from backend.app.extraction import ExtractionError, MAX_TEXT, extract_invoices


TEXT = """INVOICE
Invoice number: INV-101
Vendor: Northstar Labs
PO: PO-1001
Invoice date: 2026-11-01
Currency: USD
Subtotal: 1000.00
Tax: 100.00
Total: 1100.00
Consulting | 10 | 100.00 | 1000.00
"""


def test_labeled_text_has_evidence_and_items():
    result = extract_invoices(TEXT.encode(), "invoice.txt", {})[0]
    assert result["invoice"]["invoice_number"] == "INV-101"
    assert result["invoice"]["total"] == "1100.00"
    assert result["invoice"]["extraction_confidence"] > 0.85
    assert result["evidence"]["total"] == "Total: 1100.00"
    assert result["invoice"]["line_items"][0]["quantity"] == "10"


def test_conflicting_totals_require_review():
    result = extract_invoices((TEXT + "Total: 1200.00\n").encode(), "invoice.txt", {})[0]
    assert "total" not in result["invoice"]
    assert result["invoice"]["extraction_confidence"] < 0.85
    assert any("Conflicting total" in warning for warning in result["warnings"])


def test_structured_batch_aliases_and_no_network(monkeypatch):
    import backend.app.extraction as extraction
    monkeypatch.setattr(extraction, "_model_extract", lambda *args: pytest.fail("Structured invoices must not contact a model"))
    payload = {"invoices": [{"invoice_no": "A-1", "supplier": "Example", "grand_total": "12.50"}, {"invoice_number": "A-2", "total": 8}]}
    result = extract_invoices(json.dumps(payload).encode(), "batch.json", {"enable_openrouter": True})
    assert len(result) == 2
    assert result[0]["invoice"]["invoice_number"] == "A-1"
    assert result[0]["invoice"]["vendor_name"] == "Example"
    assert result[0]["invoice"]["extraction_confidence"] == 1.0


def test_csv_unicode_and_quoted_amount():
    content = '\ufeffinvoice_number,vendor_name,total,currency\nINV-1,"Acme, Inc.","1,500.00",USD\n'.encode()
    result = extract_invoices(content, "invoices.csv", {})[0]
    assert result["invoice"]["vendor_name"] == "Acme, Inc."
    assert result["invoice"]["total"] == "1,500.00"


@pytest.mark.parametrize("content,filename", [(b'{"total":NaN}', "x.json"), (b"[12]", "x.json"), (b"[]", "x.json"), (b"invoice_number,invoice_number\nA,B", "x.csv"), (b"invoice_number,total\nA,2,3", "x.csv"), (b"a", "x.exe"), (b"bad PDF", "x.pdf"), (b"bad image", "x.png")])
def test_invalid_input_fails_cleanly(content, filename):
    with pytest.raises(ExtractionError):
        extract_invoices(content, filename, {})


def test_resource_limits():
    with pytest.raises(ExtractionError, match="character"):
        extract_invoices(b"a" * (MAX_TEXT + 1), "long.txt", {})
    with pytest.raises(ExtractionError, match="100"):
        extract_invoices(json.dumps([{"total": "1"}] * 101).encode(), "batch.json", {})


def test_opt_in_without_key_retains_local_and_requires_review():
    result = extract_invoices(TEXT.encode(), "invoice.txt", {"enable_openrouter": True})[0]
    assert result["method"] == "local_text"
    assert result["invoice"]["extraction_confidence"] <= 0.80
    assert any("OPENROUTER_API_KEY" in warning for warning in result["warnings"])


def test_model_disabled_does_not_make_network_request(monkeypatch):
    import backend.app.extraction as extraction
    monkeypatch.setattr(extraction, "_model_extract", lambda *args: pytest.fail("OpenRouter must remain disabled"))
    assert extract_invoices(TEXT.encode(), "invoice.txt", {"openrouter_api_key": "fake-secret"})[0]["method"] == "local_text"


def test_image_without_ocr_requires_review(monkeypatch):
    from PIL import Image
    monkeypatch.setattr("backend.app.extraction.shutil.which", lambda _: None)
    output = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="PNG")
    result = extract_invoices(output.getvalue(), "scan.png", {})[0]
    assert result["method"] == "ocr"
    assert result["invoice"]["extraction_confidence"] < 0.85
    assert any("Tesseract" in warning for warning in result["warnings"])


def test_digital_pdf_and_page_limit():
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 50), TEXT)
    result = extract_invoices(doc.tobytes(), "invoice.pdf", {})[0]
    assert result["invoice"]["invoice_number"] == "INV-101"
    doc.new_page()
    with pytest.raises(ExtractionError, match="between 1 and 1"):
        extract_invoices(doc.tobytes(), "invoice.pdf", {"max_pages": 1})
    doc.close()


def test_eml_attachment():
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message.set_content("Please process the attached invoice.")
    message.add_attachment(TEXT.encode(), maintype="application", subtype="octet-stream", filename="invoice.txt")
    result = extract_invoices(message.as_bytes(), "invoice.eml", {})
    assert result[0]["invoice"]["invoice_number"] == "INV-101"


def test_model_http_errors_do_not_expose_key(monkeypatch):
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            assert url == "https://openrouter.ai/api/v1/chat/completions"
            assert kwargs["json"]["provider"]["require_parameters"] is True
            return httpx.Response(401, json={"error": "fake-secret-do-not-leak"})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = extract_invoices(TEXT.encode(), "invoice.txt", {"enable_openrouter": True, "openrouter_api_key": "fake-secret-do-not-leak", "openrouter_model": "test/model"})[0]
    assert "HTTP 401" in " ".join(result["warnings"])
    assert "fake-secret-do-not-leak" not in json.dumps(result)


def test_model_response_must_be_valid_schema(monkeypatch):
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *args, **kwargs):
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"invoice":{},"evidence":{}}'}}]})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = extract_invoices(TEXT.encode(), "invoice.txt", {"enable_openrouter": True, "openrouter_api_key": "fake", "openrouter_model": "test/model"})[0]
    assert result["method"] == "local_text"
    assert any("invalid extraction" in warning for warning in result["warnings"])
    assert result["invoice"]["extraction_confidence"] <= 0.80


def test_conflicting_aliases_and_duplicate_json_keys_are_rejected():
    for content in [b'{"total":"10", "grand_total":"20"}', b'{"total":"10", "total":"20"}']:
        with pytest.raises(ExtractionError):
            extract_invoices(content, "invoice.json", {})


def test_model_cannot_hide_conflicting_source_fields(monkeypatch):
    import backend.app.extraction as extraction
    invoice = extraction._local_extract(TEXT)["invoice"]
    monkeypatch.setattr(extraction, "_model_extract", lambda *args: {"invoice": dict(invoice), "evidence": {}, "warnings": [], "method": "openrouter", "raw_text": TEXT})
    result = extract_invoices((TEXT + "Total: 999.00\n").encode(), "invoice.txt", {"enable_openrouter": True})[0]
    assert result["invoice"]["extraction_confidence"] <= 0.80
    assert any("Conflicting total" in warning for warning in result["warnings"])


@pytest.mark.parametrize("field,value,expected", [("total", "100", 0.80), ("invoice_number", "INV-10", 0.80), ("total", "1000", 0.90)])
def test_model_requires_complete_value_tokens_in_source_evidence(monkeypatch, field, value, expected):
    import httpx
    from backend.app.extraction import FIELDS, _local_extract, _model_extract

    source = TEXT.replace("Total: 1100.00", "Total: 1000")
    local = _local_extract(source)
    model_invoice = {name: local["invoice"].get(name) for name in FIELDS}
    model_invoice.update(line_items=local["invoice"]["line_items"], extraction_confidence=0.99)
    model_invoice[field] = value
    evidence = {name: local["evidence"].get(name) for name in FIELDS}
    response_body = {"invoice": model_invoice, "evidence": evidence}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *args, **kwargs):
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response_body)}}]})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    # Test model grounding directly: downstream local-parser disagreement checks
    # are independent and must not mask an unsafe grounding implementation.
    result = _model_extract(source, [], {"openrouter_api_key": "fake", "openrouter_model": "test/model"})
    assert result["invoice"]["extraction_confidence"] == expected
