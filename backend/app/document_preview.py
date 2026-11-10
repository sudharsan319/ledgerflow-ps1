"""Read-only, bounded source previews. Documents never become executable HTML.

PDFs and raster images are converted to PNG pages; textual sources are returned
as plain strings for the UI to render as text. Source files remain unchanged.
"""
from __future__ import annotations

from contextlib import contextmanager
import io
import math
from pathlib import Path
import warnings

from fastapi import HTTPException
from PIL import Image, ImageOps
import pymupdf

from .extraction import MAX_IMAGE_PIXELS, MAX_TEXT


MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_PREVIEW_PAGES = 20
MAX_PREVIEW_EDGE = 1600
MAX_PDF_DIMENSION = 100_000
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff'}
TEXT_SUFFIXES = {'.json', '.csv', '.txt', '.md', '.eml'}
RASTER_FORMATS = {'PNG', 'JPEG', 'WEBP', 'TIFF'}


def _read_source(path: Path) -> bytes:
    try:
        with path.open('rb') as source:
            content = source.read(MAX_SOURCE_BYTES + 1)
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as exc:
        raise HTTPException(404, 'The original source is unavailable.') from exc
    if len(content) > MAX_SOURCE_BYTES:
        raise HTTPException(413, 'The source exceeds the 10 MB preview limit. Download it to view locally.')
    if not content:
        raise HTTPException(422, 'The original source is empty and cannot be previewed.')
    return content


def _safe_filename(filename: str) -> str:
    return Path(str(filename).replace('\\', '/')).name or 'document'


def _page_index(page: int, page_count: int) -> None:
    if isinstance(page, bool) or not isinstance(page, int) or not 0 <= page < min(page_count, MAX_PREVIEW_PAGES):
        raise HTTPException(404, 'This preview page is unavailable.')


@contextmanager
def _pdf_document(content: bytes):
    document = None
    try:
        # Supplying a filetype avoids automatically opening an image or another
        # document format whose filename merely ends in .pdf.
        if b'%PDF-' not in content[:1024]:
            raise HTTPException(422, 'The source is not a valid PDF document.')
        document = pymupdf.open(stream=content, filetype='pdf')
        if document.needs_pass:
            raise HTTPException(422, 'This PDF is password protected. Download it to view locally.')
        if document.page_count < 1:
            raise HTTPException(422, 'The PDF has no pages to preview.')
        yield document
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, 'The PDF could not be previewed. Download the original to inspect it.') from exc
    finally:
        if document is not None:
            document.close()


def _check_pdf_page(page) -> None:
    rectangle = page.rect
    if (not all(math.isfinite(value) and abs(value) <= MAX_PDF_DIMENSION for value in rectangle)
            or rectangle.width <= 0 or rectangle.height <= 0):
        raise HTTPException(422, 'The PDF page dimensions exceed the preview limits.')
    images = page.get_images(full=True)
    if len(images) > 1000 or sum(image[2] * image[3] for image in images) > MAX_IMAGE_PIXELS:
        raise HTTPException(422, 'The PDF page contains too much image data to preview safely. Download the original to view locally.')


@contextmanager
def _raster_document(content: bytes):
    source = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            source = Image.open(io.BytesIO(content))
            if source.format not in RASTER_FORMATS:
                raise HTTPException(422, 'The source is not a supported raster image.')
            yield source
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, 'The image dimensions exceed the preview limits.') from exc
    except Exception as exc:
        raise HTTPException(422, 'The image could not be previewed. Download the original to inspect it.') from exc
    finally:
        if source is not None:
            source.close()


def _raster_pages(source) -> int:
    count = getattr(source, 'n_frames', 1)
    if count < 1:
        raise HTTPException(422, 'The image has no pages to preview.')
    pixels = 0
    for page in range(min(count, MAX_PREVIEW_PAGES)):
        source.seek(page)
        pixels += source.width * source.height
        if source.width <= 0 or source.height <= 0 or pixels > MAX_IMAGE_PIXELS:
            raise HTTPException(422, 'Image pages exceed the 25-megapixel preview limit. Download the original to view locally.')
    return count


def describe_document(path: Path, filename: str) -> dict:
    """Return preview metadata; page URLs/download URLs are added by the API.

    Raises HTTPException: 404 for missing source, 413 for excessive source bytes,
    and 422 for empty/corrupt/encrypted or unsupported-size source contents.
    """
    filename = _safe_filename(filename)
    suffix = Path(filename).suffix.lower()
    content = _read_source(path)
    result = {'filename': filename, 'kind': 'unsupported', 'page_count': 0,
              'preview_page_count': 0, 'text': None, 'truncated': False,
              'message': 'A preview is not available for this format. Download the original to view it locally.'}
    if suffix == '.pdf':
        with _pdf_document(content) as document:
            count = document.page_count
            for index in range(min(count, MAX_PREVIEW_PAGES)):
                _check_pdf_page(document[index])
        result.update(kind='pdf', page_count=count, preview_page_count=min(count, MAX_PREVIEW_PAGES),
                      truncated=count > MAX_PREVIEW_PAGES, message=None)
    elif suffix in IMAGE_SUFFIXES:
        with _raster_document(content) as source:
            count = _raster_pages(source)
        result.update(kind='image', page_count=count, preview_page_count=min(count, MAX_PREVIEW_PAGES),
                      truncated=count > MAX_PREVIEW_PAGES, message=None)
    elif suffix in TEXT_SUFFIXES:
        if content.startswith((b'\xff\xfe', b'\xfe\xff')):
            text = content.decode('utf-16', errors='replace')
        else:
            text = content.decode('utf-8-sig', errors='replace')
        if '\x00' in text:
            result['message'] = 'This source contains binary data. Download the original to view it locally.'
            return result
        result.update(kind='text', text=text[:MAX_TEXT], truncated=len(text) > MAX_TEXT, message=None)
        if '\ufffd' in text:
            result['message'] = 'Some characters could not be decoded. Download the original to check its encoding.'
    if result['truncated']:
        result['message'] = (f'Preview shows the first {MAX_TEXT:,} characters. Download the original for the complete document.'
                             if result['kind'] == 'text' else f'Preview shows the first {MAX_PREVIEW_PAGES} pages. Download the original for all pages.')
    return result


def render_document_page(path: Path, filename: str, page: int) -> bytes:
    """Render one zero-based PDF/image page as a bounded PNG, without mutations."""
    suffix = Path(_safe_filename(filename)).suffix.lower()
    if suffix not in IMAGE_SUFFIXES | {'.pdf'}:
        raise HTTPException(404, 'This document does not have image preview pages.')
    content = _read_source(path)
    if suffix == '.pdf':
        with _pdf_document(content) as document:
            _page_index(page, document.page_count)
            selected = document[page]
            _check_pdf_page(selected)
            # A one-pixel margin avoids float rounding producing a 1601px edge.
            scale = min(2.0, (MAX_PREVIEW_EDGE - 1) / max(selected.rect.width, selected.rect.height))
            pixmap = selected.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
            if max(pixmap.width, pixmap.height) > MAX_PREVIEW_EDGE:
                raise HTTPException(422, 'The PDF page could not be rendered within preview limits.')
            return pixmap.tobytes('png')
    with _raster_document(content) as source:
        count = _raster_pages(source)
        _page_index(page, count)
        source.seek(page)
        image = ImageOps.exif_transpose(source)
        image.thumbnail((MAX_PREVIEW_EDGE, MAX_PREVIEW_EDGE), Image.Resampling.LANCZOS)
        if 'A' in image.getbands() or (image.mode == 'P' and 'transparency' in image.info):
            rgba = image.convert('RGBA')
            output_image = Image.new('RGB', rgba.size, 'white')
            output_image.paste(rgba, mask=rgba.getchannel('A'))
        else:
            output_image = image.convert('RGB')
        output = io.BytesIO()
        output_image.save(output, format='PNG')
        return output.getvalue()
