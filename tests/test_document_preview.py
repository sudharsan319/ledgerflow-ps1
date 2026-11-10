"""Bounded, read-only previews of source documents with no provider dependency."""
import io

from fastapi import HTTPException
from PIL import Image
import pymupdf
import pytest

from backend.app import document_preview as preview


def source_path(tmp_path, content):
    path = tmp_path / 'saved-source.bin'
    path.write_bytes(content)
    return path


def pdf_bytes(pages=1, encrypted=False):
    with pymupdf.open() as document:
        for page in range(pages):
            selected = document.new_page(width=1200, height=1800)
            selected.insert_text((50, 80), f'Invoice / purchase order page {page + 1}', fontsize=20)
        options = {'encryption': pymupdf.PDF_ENCRYPT_AES_256, 'owner_pw': 'owner-secret', 'user_pw': 'user-secret'} if encrypted else {}
        return document.tobytes(**options)


def assert_png(content, max_edge=preview.MAX_PREVIEW_EDGE):
    assert content.startswith(b'\x89PNG\r\n\x1a\n')
    with Image.open(io.BytesIO(content)) as image:
        assert image.format == 'PNG'
        assert 0 < image.width <= max_edge and 0 < image.height <= max_edge
        return image.copy()


def test_pdf_metadata_and_page_rendering_preserve_original(tmp_path):
    content = pdf_bytes(pages=2)
    path = source_path(tmp_path, content)
    metadata = preview.describe_document(path, '../invoice.pdf')
    assert metadata == {'filename': 'invoice.pdf', 'kind': 'pdf', 'page_count': 2,
                        'preview_page_count': 2, 'text': None, 'truncated': False, 'message': None}
    assert_png(preview.render_document_page(path, 'invoice.pdf', 0))
    assert_png(preview.render_document_page(path, 'invoice.pdf', 1))
    assert path.read_bytes() == content
    assert str(tmp_path) not in str(metadata)


def test_pdf_preview_page_cap_and_invalid_page_indices(tmp_path):
    path = source_path(tmp_path, pdf_bytes(pages=21))
    metadata = preview.describe_document(path, 'invoice.pdf')
    assert metadata['page_count'] == 21 and metadata['preview_page_count'] == 20
    assert metadata['truncated'] and 'first 20 pages' in metadata['message']
    assert_png(preview.render_document_page(path, 'invoice.pdf', 19))
    for page in (-1, 20, 21, True, '0'):
        with pytest.raises(HTTPException) as raised:
            preview.render_document_page(path, 'invoice.pdf', page)
        assert raised.value.status_code == 404


def test_multiframe_tiff_previews_select_correct_page(tmp_path):
    output = io.BytesIO()
    first = Image.new('RGB', (2000, 1000), 'red')
    second = Image.new('RGB', (2000, 1000), 'blue')
    first.save(output, format='TIFF', save_all=True, append_images=[second], compression='tiff_deflate')
    path = source_path(tmp_path, output.getvalue())
    metadata = preview.describe_document(path, 'scan.tiff')
    assert metadata['kind'] == 'image' and metadata['page_count'] == 2
    assert assert_png(preview.render_document_page(path, 'scan.tiff', 0)).getpixel((10, 10)) == (255, 0, 0)
    assert assert_png(preview.render_document_page(path, 'scan.tiff', 1)).getpixel((10, 10)) == (0, 0, 255)
    assert path.read_bytes() == output.getvalue()


def test_raster_preview_caps_frames_and_flattens_transparency(tmp_path):
    pages = [Image.new('RGBA', (5, 5), (255, 0, 0, index)) for index in range(21)]
    output = io.BytesIO()
    pages[0].save(output, format='TIFF', save_all=True, append_images=pages[1:])
    path = source_path(tmp_path, output.getvalue())
    metadata = preview.describe_document(path, 'scan.tif')
    assert metadata['page_count'] == 21 and metadata['preview_page_count'] == 20
    assert metadata['truncated']
    image = assert_png(preview.render_document_page(path, 'scan.tif', 0))
    assert image.mode == 'RGB' and image.getpixel((0, 0)) == (255, 255, 255)
    with pytest.raises(HTTPException) as raised:
        preview.render_document_page(path, 'scan.tif', 20)
    assert raised.value.status_code == 404


def test_photo_orientation_and_grayscale_transparency(tmp_path):
    output = io.BytesIO()
    photo = Image.new('RGB', (80, 40), 'green')
    orientation = Image.Exif()
    orientation[274] = 6
    photo.save(output, format='JPEG', exif=orientation)
    path = source_path(tmp_path, output.getvalue())
    assert assert_png(preview.render_document_page(path, 'photo.jpg', 0)).size == (40, 80)
    output = io.BytesIO()
    Image.new('LA', (4, 4), (0, 0)).save(output, format='PNG')
    path.write_bytes(output.getvalue())
    assert assert_png(preview.render_document_page(path, 'scan.png', 0)).getpixel((0, 0)) == (255, 255, 255)


@pytest.mark.parametrize('filename', ['input.json', 'input.csv', 'input.txt', 'input.md', 'input.eml'])
def test_text_sources_remain_plain_strings_not_executable_markup(tmp_path, filename):
    content = '<script>alert("source is data")</script>\nOriginal document content'
    path = source_path(tmp_path, content.encode())
    metadata = preview.describe_document(path, filename)
    assert metadata['kind'] == 'text' and metadata['text'] == content
    assert metadata['page_count'] == 0 and metadata['preview_page_count'] == 0
    with pytest.raises(HTTPException) as raised:
        preview.render_document_page(path, filename, 0)
    assert raised.value.status_code == 404


def test_text_truncation_utf_bom_and_binary_fallback(tmp_path):
    text = '文' * (preview.MAX_TEXT + 10)
    path = source_path(tmp_path, text.encode('utf-8-sig'))
    metadata = preview.describe_document(path, 'invoice.txt')
    assert metadata['truncated'] and len(metadata['text']) == preview.MAX_TEXT
    assert metadata['text'] == text[:preview.MAX_TEXT]
    path.write_bytes('Invoice: 100'.encode('utf-16'))
    assert preview.describe_document(path, 'invoice.txt')['text'] == 'Invoice: 100'
    path.write_bytes(b'Invoice: \x80')
    metadata = preview.describe_document(path, 'invoice.txt')
    assert '\ufffd' in metadata['text'] and 'encoding' in metadata['message']
    path.write_bytes(b'\x00\x01binary')
    assert preview.describe_document(path, 'invoice.txt')['kind'] == 'unsupported'


def test_unsupported_files_offer_download_without_parsing(tmp_path):
    path = source_path(tmp_path, b'<html><script>untrusted()</script></html>')
    for filename in ('workbook.xlsx', 'unexpected.html', 'vector.svg'):
        metadata = preview.describe_document(path, filename)
        assert metadata['kind'] == 'unsupported' and metadata['text'] is None
        assert 'Download' in metadata['message']


@pytest.mark.parametrize('filename,content', [
    ('fake.pdf', b'<html>not a PDF</html>'),
    ('broken.pdf', b'%PDF-1.7\nbroken PDF'),
    ('fake.png', b'<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>'),
    ('broken.tiff', b'not an image'),
])
def test_corrupt_or_mislabeled_documents_fail_with_safe_message(tmp_path, filename, content):
    path = source_path(tmp_path, content)
    for action in (lambda: preview.describe_document(path, filename), lambda: preview.render_document_page(path, filename, 0)):
        with pytest.raises(HTTPException) as raised:
            action()
        assert raised.value.status_code == 422
        assert str(tmp_path) not in raised.value.detail
        assert content.decode(errors='ignore') not in raised.value.detail


def test_password_protected_pdf_has_actionable_error(tmp_path):
    path = source_path(tmp_path, pdf_bytes(encrypted=True))
    with pytest.raises(HTTPException) as raised:
        preview.describe_document(path, 'protected.pdf')
    assert raised.value.status_code == 422 and 'password protected' in raised.value.detail


def test_source_bytes_empty_and_missing_limits(tmp_path, monkeypatch):
    with pytest.raises(HTTPException) as raised:
        preview.describe_document(tmp_path / 'missing-private-path.bin', 'invoice.pdf')
    assert raised.value.status_code == 404 and 'missing-private-path' not in raised.value.detail
    path = source_path(tmp_path, b'')
    with pytest.raises(HTTPException) as raised:
        preview.describe_document(path, 'invoice.txt')
    assert raised.value.status_code == 422
    monkeypatch.setattr(preview, 'MAX_SOURCE_BYTES', 10)
    path.write_bytes(b'x' * 11)
    with pytest.raises(HTTPException) as raised:
        preview.describe_document(path, 'invoice.txt')
    assert raised.value.status_code == 413


def test_raster_pixel_bounds_apply_before_loading(tmp_path, monkeypatch):
    output = io.BytesIO()
    Image.new('RGB', (50, 50)).save(output, format='PNG')
    path = source_path(tmp_path, output.getvalue())
    monkeypatch.setattr(preview, 'MAX_IMAGE_PIXELS', 100)
    with pytest.raises(HTTPException) as raised:
        preview.describe_document(path, 'large.png')
    assert raised.value.status_code == 422
    with pytest.raises(HTTPException) as raised:
        preview.render_document_page(path, 'large.png', 0)
    assert raised.value.status_code == 422


def test_pdf_image_and_page_geometry_bounds(tmp_path, monkeypatch):
    output = io.BytesIO()
    Image.new('RGB', (50, 50)).save(output, format='PNG')
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_image(pymupdf.Rect(0, 0, 50, 50), stream=output.getvalue())
        path = source_path(tmp_path, document.tobytes())
    monkeypatch.setattr(preview, 'MAX_IMAGE_PIXELS', 100)
    with pytest.raises(HTTPException) as raised:
        preview.describe_document(path, 'image.pdf')
    assert raised.value.status_code == 422
    monkeypatch.setattr(preview, 'MAX_IMAGE_PIXELS', 25_000_000)
    monkeypatch.setattr(preview, 'MAX_PDF_DIMENSION', 100)
    with pytest.raises(HTTPException) as raised:
        preview.describe_document(path, 'geometry.pdf')
    assert raised.value.status_code == 422
