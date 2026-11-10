import csv
import io
import json
import zipfile
from datetime import datetime

import pytest

from backend.app.extraction import ExtractionError
from backend.app.po_extraction import FIELDS, LINE_FIELDS, extract_purchase_orders


TEXT = """PURCHASE ORDER
PO number: PO-101
Vendor: Northstar Labs
Vendor ID: northstar
PO date: 2026-11-01
Currency: USD
Subtotal: 1000.00
Tax: 100.00
Total: 1100.00
Approval status: approved
Status: open
Approved by: Pat Buyer
Consulting | 10 | 100.00 | 1000.00
"""
HEADERS = ["po_number", "vendor_name", "po_date", "currency", "subtotal", "tax", "total", "approval_status", "status", "line_description", "quantity", "unit_price", "line_amount"]
ROW = ["PO-101", "Northstar Labs", "2026-11-01", "USD", "30", "3", "33", "approved", "open", "Widget", "2", "10", "20"]


def _csv(rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(rows)
    return output.getvalue().encode()


def _xlsx(rows, second_sheet=None):
    from openpyxl import Workbook
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    if second_sheet:
        sheet = workbook.create_sheet("Second")
        for row in second_sheet:
            sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_json_vendor_lookup_never_grants_approval(monkeypatch):
    monkeypatch.setattr("backend.app.po_extraction._model_extract", lambda *args: pytest.fail("Structured data must not contact AI"))
    payload = {"vendors": [{"id": "northstar", "name": "Northstar Labs", "approved": True}], "purchase_orders": [{"po_number": "PO-101", "vendor_id": "northstar", "total": "20", "verified": True, "approval": "pending", "items": [{"item": "Widget", "qty": "2", "price": "10", "line_total": "20"}]}]}
    result = extract_purchase_orders(json.dumps(payload).encode(), "orders.json", {"enable_openrouter": True})[0]
    assert result["purchase_order"]["vendor_name"] == "Northstar Labs"
    assert result["purchase_order"]["approval_status"] == "pending"
    assert "approved" not in result["purchase_order"]
    assert "verified" not in result["purchase_order"]
    assert result["purchase_order"]["line_items"] == [{"description": "Widget", "quantity": "2", "unit_price": "10", "amount": "20"}]
    assert result["method"] == "structured"


def test_csv_groups_rows_and_preserves_separate_orders():
    next_item = ROW[:9] + ["Bracket", "1", "10", "10"]
    second_order = ["PO-102"] + ROW[1:]
    results = extract_purchase_orders(_csv([HEADERS, ROW, next_item, second_order]), "orders.csv")
    assert len(results) == 2
    assert len(results[0]["purchase_order"]["line_items"]) == 2
    assert results[0]["purchase_order"]["line_items"][1]["description"] == "Bracket"
    assert results[0]["purchase_order"]["total"] == "33"
    assert results[0]["evidence"]["vendor_name"] == "Northstar Labs"


def test_csv_repeated_header_conflict_is_rejected():
    conflict = ROW[:]
    conflict[6] = "1000"
    with pytest.raises(ExtractionError, match="disagree on total"):
        extract_purchase_orders(_csv([HEADERS, ROW, conflict]), "orders.csv")


def test_csv_generic_description_and_nested_json_items():
    result = extract_purchase_orders(_csv([["po_number", "description", "quantity", "unit_price", "amount"], ["PO-1", "Consulting", "2", "5", "10"]]), "orders.csv")[0]
    assert result["purchase_order"]["line_items"][0]["description"] == "Consulting"
    assert "description" not in result["purchase_order"]
    items = [{"description": "Consulting", "quantity": "2", "unit_price": "5", "amount": "10"}]
    result = extract_purchase_orders(_csv([["po_number", "line_items"], ["PO-1", json.dumps(items)]]), "orders.csv")[0]
    assert result["purchase_order"]["line_items"] == items


def test_excel_dates_multiple_sheets_and_grouping():
    row = ROW[:]
    row[2] = datetime(2026, 11, 1)
    result = extract_purchase_orders(_xlsx([HEADERS, row], [HEADERS, ROW[:9] + ["Bracket", "1", "10", "10"]]), "orders.xlsx")[0]
    assert result["purchase_order"]["po_date"] == "2026-11-01"
    assert len(result["purchase_order"]["line_items"]) == 2


def test_excel_formula_is_not_evaluated_or_accepted():
    row = ROW[:]
    row[6] = "=SUM(E2:F2)"
    with pytest.raises(ExtractionError, match="formulas"):
        extract_purchase_orders(_xlsx([HEADERS, row]), "orders.xlsx")


def _hide_excel_rows(content):
    import re
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for entry in source.infolist():
            data = source.read(entry.filename)
            if entry.filename.startswith("xl/worksheets/"):
                data = re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="A1:A1"', data)
            target.writestr(entry, data)
    return output.getvalue()


def test_excel_declared_dimensions_cannot_hide_data_or_formulas():
    result = extract_purchase_orders(_hide_excel_rows(_xlsx([HEADERS, ROW])), "orders.xlsx")[0]
    assert result["purchase_order"]["po_number"] == "PO-101"
    row = ROW[:]
    row[6] = "=SUM(E2:F2)"
    with pytest.raises(ExtractionError, match="formulas"):
        extract_purchase_orders(_hide_excel_rows(_xlsx([HEADERS, row])), "orders.xlsx")


def test_excel_expansion_limit_checked_before_parsing(monkeypatch):
    monkeypatch.setattr("backend.app.po_extraction.MAX_XLSX_UNCOMPRESSED", 100)
    with pytest.raises(ExtractionError, match="Expanded Excel"):
        extract_purchase_orders(_xlsx([HEADERS, ROW]), "orders.xlsx")


@pytest.mark.parametrize("content,filename", [
    (b"bad PDF", "orders.pdf"), (b"bad image", "orders.png"), (b"bad workbook", "orders.xlsx"),
    (b"", "orders.csv"), (b"[]", "orders.json"), (b"[5]", "orders.json"), (b'{"total":NaN}', "orders.json"),
    (b'{"po_number":"PO-1","total":"10","total":"20"}', "orders.json"),
    (b'{"po_number":"PO-1","po_no":"PO-2"}', "orders.json"),
    (b"po_number,po_no\nPO-1,PO-1", "orders.csv"), (b"po_number,total\nPO-1,1,2", "orders.csv"),
    (b"po_number,total\nPO-1", "orders.csv"), (b"a", "orders.xls"),
])
def test_malformed_inputs_fail_cleanly(content, filename):
    with pytest.raises(ExtractionError):
        extract_purchase_orders(content, filename)


def test_json_rejects_duplicate_orders_and_conflicting_vendor_lookup():
    with pytest.raises(ExtractionError, match="Duplicate PO"):
        extract_purchase_orders(b'[{"po_number":"PO-1"},{"po_number":"PO-1"}]', "orders.json")
    with pytest.raises(ExtractionError, match="vendor name conflicts"):
        extract_purchase_orders(b'{"vendors":[{"id":"V1","name":"Acme"}],"purchase_orders":[{"po_number":"PO-1","vendor_id":"V1","vendor_name":"Other"}]}', "orders.json")


def test_limits_orders_rows_and_lines():
    with pytest.raises(ExtractionError, match="100 purchase orders"):
        extract_purchase_orders(json.dumps([{"po_number": str(i)} for i in range(101)]).encode(), "orders.json")
    with pytest.raises(ExtractionError, match="1,000 rows"):
        extract_purchase_orders(_csv([["po_number"], *[[str(i)] for i in range(1001)]]), "orders.csv")
    with pytest.raises(ExtractionError, match="500"):
        extract_purchase_orders(json.dumps({"po_number": "PO-1", "line_items": [{}] * 501}).encode(), "orders.json")


def test_labeled_text_has_evidence_but_does_not_invent_status():
    result = extract_purchase_orders(TEXT.encode(), "order.txt")[0]
    assert result["document_kind"] == "purchase_order"
    assert result["purchase_order"]["po_number"] == "PO-101"
    assert result["purchase_order"]["po_date"] == "2026-11-01"
    assert result["evidence"]["approval_status"] == "Approval status: approved"
    assert result["purchase_order"]["line_items"][0]["amount"] == "1000.00"
    text = TEXT.replace("Approval status: approved\n", "").replace("Status: open\n", "")
    result = extract_purchase_orders(text.encode(), "order.txt")[0]
    assert "approval_status" not in result["purchase_order"]
    assert "status" not in result["purchase_order"]


def test_empty_date_does_not_consume_next_label():
    result = extract_purchase_orders(TEXT.replace("PO date: 2026-11-01", "PO date:").encode(), "order.txt")[0]
    assert "po_date" not in result["purchase_order"]
    assert result["purchase_order"]["currency"] == "USD"


def test_conflicting_source_fields_remain_visible():
    result = extract_purchase_orders((TEXT + "Total: 2000.00\n").encode(), "order.txt")[0]
    assert "total" not in result["purchase_order"]
    assert any("Conflicting total" in warning for warning in result["warnings"])


@pytest.mark.parametrize("content,filename", [
    (b"INVOICE\nInvoice number: INV-1\nPO: PO-1\nTotal: 10", "order.txt"),
    (b'{"invoice_number":"INV-1","po_number":"PO-1","total":"10"}', "order.json"),
    (b'{"invoices":[{"po_number":"PO-1"}]}', "order.json"),
    (b"invoice_number,po_number,total\nINV-1,PO-1,10", "order.csv"),
])
def test_invoice_reference_cannot_be_imported_as_a_po(content, filename):
    with pytest.raises(ExtractionError, match="invoice"):
        extract_purchase_orders(content, filename)


def test_pdf_and_page_limit():
    import fitz
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((40, 50), TEXT)
        result = extract_purchase_orders(document.tobytes(), "order.pdf")[0]
        assert result["purchase_order"]["po_number"] == "PO-101"
        document.new_page()
        with pytest.raises(ExtractionError, match="between 1 and 1"):
            extract_purchase_orders(document.tobytes(), "order.pdf", {"max_pages": 1})


def _image():
    from PIL import Image
    output = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, format="PNG")
    return output.getvalue()


def test_image_ocr_failure_preserves_review_warning(monkeypatch):
    monkeypatch.setattr("backend.app.po_extraction._ocr", lambda _: ("", "Local OCR unavailable."))
    result = extract_purchase_orders(_image(), "scan.png")[0]
    assert result["method"] == "ocr"
    assert result["document_kind"] == "unknown"
    assert result["purchase_order"]["line_items"] == []
    assert "Local OCR unavailable." in result["warnings"]


def test_multipage_tiff_preserves_all_pages_and_page_limit(monkeypatch):
    from PIL import Image
    first = Image.new("RGB", (50, 50), "white")
    second = Image.new("RGB", (50, 50), "gray")
    output = io.BytesIO()
    first.save(output, format="TIFF", save_all=True, append_images=[second])
    calls = []

    def ocr(image):
        calls.append(image)
        return (TEXT if len(calls) == 1 else "Bracket | 1 | 5.00 | 5.00", None)

    monkeypatch.setattr("backend.app.po_extraction._ocr", ocr)
    result = extract_purchase_orders(output.getvalue(), "order.tiff")[0]
    assert len(calls) == 2
    assert len(result["purchase_order"]["line_items"]) == 2
    with pytest.raises(ExtractionError, match="at most 1"):
        extract_purchase_orders(output.getvalue(), "order.tiff", {"max_pages": 1})


def _fake_model_response(monkeypatch, body, status=200):
    import httpx
    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            assert url == "https://openrouter.ai/api/v1/chat/completions"
            assert kwargs["json"]["response_format"]["json_schema"]["name"] == "purchase_order_extraction"
            assert kwargs["json"]["provider"]["require_parameters"] is True
            calls.append(kwargs["json"])
            return httpx.Response(status, json={"choices": [{"message": {"content": json.dumps(body)}}]})

    monkeypatch.setattr(httpx, "Client", Client)
    return calls


SETTINGS = {"enable_openrouter": True, "openrouter_api_key": "fake-secret-never-print", "openrouter_model": "test/model"}


def _model_payload():
    result = extract_purchase_orders(TEXT.encode(), "order.txt")[0]
    po = {field: result["purchase_order"].get(field) for field in FIELDS}
    po["line_items"] = [{field: item.get(field) for field in LINE_FIELDS} for item in result["purchase_order"]["line_items"]]
    return {"document_kind": "purchase_order", "purchase_order": po, "evidence": {field: result["evidence"].get(field) for field in FIELDS}}


def test_model_is_opt_in_and_falls_back_without_key(monkeypatch):
    result = extract_purchase_orders(TEXT.encode(), "order.txt", {"enable_openrouter": True})[0]
    assert result["method"] == "local_text"
    assert any("OPENROUTER_API_KEY" in warning for warning in result["warnings"])
    monkeypatch.setattr("backend.app.po_extraction._model_extract", lambda *args: pytest.fail("AI not enabled"))
    assert extract_purchase_orders(TEXT.encode(), "order.txt", {"openrouter_api_key": "fake"})[0]["method"] == "local_text"


def test_valid_model_schema_and_image_payload(monkeypatch):
    body = _model_payload()
    calls = _fake_model_response(monkeypatch, body)
    monkeypatch.setattr("backend.app.po_extraction._ocr", lambda _: ("", "OCR unavailable"))
    result = extract_purchase_orders(_image(), "handwritten.png", SETTINGS)[0]
    assert result["method"] == "openrouter"
    assert result["purchase_order"]["total"] == "1100.00"
    assert result["warnings"][0] == "OCR unavailable"
    assert calls[0]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_model_schema_failure_and_http_error_are_safe(monkeypatch):
    _fake_model_response(monkeypatch, {"purchase_order": {}, "evidence": {}})
    result = extract_purchase_orders(TEXT.encode(), "order.txt", SETTINGS)[0]
    assert result["method"] == "local_text"
    assert any("invalid PO extraction" in warning for warning in result["warnings"])
    _fake_model_response(monkeypatch, {"error": SETTINGS["openrouter_api_key"]}, status=401)
    result = extract_purchase_orders(TEXT.encode(), "order.txt", SETTINGS)[0]
    assert any("HTTP 401" in warning for warning in result["warnings"])
    assert SETTINGS["openrouter_api_key"] not in json.dumps(result)


def test_model_unsupported_approval_is_cleared_and_conflicts_retained(monkeypatch):
    body = _model_payload()
    body["purchase_order"]["approved_by"] = "Invented Reviewer"
    body["purchase_order"]["vendor_id"] = "invented-vendor"
    body["purchase_order"]["total"] = "9999"
    _fake_model_response(monkeypatch, body)
    result = extract_purchase_orders(TEXT.encode(), "order.txt", SETTINGS)[0]
    assert result["purchase_order"]["approved_by"] is None
    assert result["purchase_order"]["vendor_id"] is None
    assert any("disagree on: total" in warning for warning in result["warnings"])
    assert result["purchase_order"]["approval_status"] == "approved"


def test_model_cannot_silently_convert_invoice_image_to_po(monkeypatch):
    body = _model_payload()
    body["document_kind"] = "invoice"
    _fake_model_response(monkeypatch, body)
    monkeypatch.setattr("backend.app.po_extraction._ocr", lambda _: ("", None))
    with pytest.raises(ExtractionError, match="invoice"):
        extract_purchase_orders(_image(), "invoice.png", SETTINGS)
