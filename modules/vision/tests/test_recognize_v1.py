"""V1 contract tests for modules.vision.recognize — PDF resource limits.

Covers:
- Limit constants exist and are configurable via env
- File size limit enforced before opening PDF
- Page count limit enforced (via mocked fitz)
- Per-page pixel limit enforced (via mocked fitz)
- Total pixel limit enforced (via mocked fitz)
- Batch processing splits large request (via mocked API)
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.vision import recognize


# ---------- V1: Limit constants exist and are configurable ----------

def test_v1_limit_constants_exist():
    """V1: All configurable limit constants must exist."""
    assert hasattr(recognize, 'MAX_PDF_FILE_BYTES')
    assert hasattr(recognize, 'MAX_PDF_PAGES')
    assert hasattr(recognize, 'MAX_PAGE_PIXELS')
    assert hasattr(recognize, 'MAX_TOTAL_PIXELS')
    assert hasattr(recognize, 'MAX_REQUEST_BYTES')
    assert hasattr(recognize, 'PDF_BATCH_SIZE')
    assert hasattr(recognize, 'RENDER_DPI')


def test_v1_default_limits_are_sane():
    """V1: Default limit values are reasonable."""
    assert recognize.MAX_PDF_FILE_BYTES >= 10 * 1024 * 1024  # >= 10 MB
    assert recognize.MAX_PDF_PAGES >= 5
    assert recognize.MAX_PAGE_PIXELS >= 1 * 1024 * 1024  # >= 1 MP
    assert recognize.MAX_TOTAL_PIXELS >= recognize.MAX_PAGE_PIXELS
    assert recognize.MAX_REQUEST_BYTES >= 5 * 1024 * 1024  # >= 5 MB
    assert recognize.PDF_BATCH_SIZE >= 1
    assert recognize.RENDER_DPI >= 72  # At least screen DPI


# ---------- V1: File size limit enforced before opening ----------

def test_v1_pdf_file_size_limit_rejected():
    """V1: A PDF exceeding MAX_PDF_FILE_BYTES is rejected before opening."""
    # Create a fake PDF file that exceeds the limit
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
        # Write enough bytes to exceed the limit
        limit = recognize.MAX_PDF_FILE_BYTES
        f.write(b'%PDF-1.4\n')  # Minimal PDF header
        # Pad to exceed the limit
        f.write(b'\x00' * (limit + 1))
        large_pdf_path = f.name

    try:
        with pytest.raises(RuntimeError, match='PDF file too large'):
            recognize._convert_pdf_to_pngs(large_pdf_path)
    finally:
        os.unlink(large_pdf_path)


def test_v1_pdf_file_size_under_limit_accepted():
    """V1: A PDF under MAX_PDF_FILE_BYTES passes the size check.

    We mock fitz to avoid actually rendering a PDF. The size check
    must pass, and the mocked fitz handles the rest.
    """
    # Create a small fake PDF
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
        f.write(b'%PDF-1.4\n%%EOF\n')
        small_pdf_path = f.name

    # Mock fitz module
    mock_fitz = MagicMock()
    mock_doc = MagicMock()
    mock_page = MagicMock()
    mock_rect = MagicMock()
    mock_rect.width = 612.0  # 8.5 inches at 72 DPI
    mock_rect.height = 792.0  # 11 inches at 72 DPI
    mock_page.rect = mock_rect
    mock_pixmap = MagicMock()
    mock_page.get_pixmap.return_value = mock_pixmap
    mock_doc.__len__ = MagicMock(return_value=1)
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    mock_doc.close = MagicMock()
    mock_fitz.open.return_value = mock_doc

    try:
        with patch.dict('sys.modules', {'fitz': mock_fitz}):
            # R2-6: _convert_pdf_to_pngs returns (png_paths, info_dict)
            result, info = recognize._convert_pdf_to_pngs(small_pdf_path)
            # Should return a list of PNG paths (one per page)
            assert isinstance(result, list)
            assert len(result) == 1
            # R2-6: info dict must contain structured fields
            assert isinstance(info, dict)
            assert info['processed_pages'] == 1
            assert info['skipped_pages'] == 0
            assert info['total_pages'] == 1
            assert info['truncated'] is False
            # Clean up generated temp files
            for p in result:
                if os.path.exists(p):
                    os.unlink(p)
    finally:
        os.unlink(small_pdf_path)


# ---------- V1: Page count limit ----------

def test_v1_pdf_page_count_limit_enforced():
    """V1: A PDF with more pages than MAX_PDF_PAGES is truncated."""
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
        f.write(b'%PDF-1.4\n%%EOF\n')
        pdf_path = f.name

    mock_fitz = MagicMock()
    mock_doc = MagicMock()
    mock_page = MagicMock()
    mock_rect = MagicMock()
    mock_rect.width = 612.0
    mock_rect.height = 792.0
    mock_page.rect = mock_rect
    mock_pixmap = MagicMock()
    mock_page.get_pixmap.return_value = mock_pixmap
    # More pages than the limit
    page_count = recognize.MAX_PDF_PAGES + 5
    mock_doc.__len__ = MagicMock(return_value=page_count)
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    mock_doc.close = MagicMock()
    mock_fitz.open.return_value = mock_doc

    try:
        with patch.dict('sys.modules', {'fitz': mock_fitz}):
            import warnings
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                # R2-6: returns (png_paths, info_dict)
                result, info = recognize._convert_pdf_to_pngs(pdf_path)
                # Should only render MAX_PDF_PAGES pages
                assert len(result) == recognize.MAX_PDF_PAGES
                # R2-6: info must reflect truncation
                assert info['truncated'] is True
                assert info['total_pages'] == page_count
                assert info['processed_pages'] == recognize.MAX_PDF_PAGES
                # Should warn about truncation
                truncation_warnings = [w for w in caught if 'truncated' in str(w.message).lower()
                                       or 'limited' in str(w.message).lower()]
                assert len(truncation_warnings) >= 1
            for p in result:
                if os.path.exists(p):
                    os.unlink(p)
    finally:
        os.unlink(pdf_path)


# ---------- V1: Per-page pixel limit ----------

def test_v1_pdf_per_page_pixel_limit_enforced():
    """V1: Pages exceeding MAX_PAGE_PIXELS are skipped."""
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
        f.write(b'%PDF-1.4\n%%EOF\n')
        pdf_path = f.name

    mock_fitz = MagicMock()
    mock_doc = MagicMock()

    # Page with very large dimensions (exceeds MAX_PAGE_PIXELS at RENDER_DPI)
    large_page = MagicMock()
    large_rect = MagicMock()
    # Make dimensions huge: 10000 x 10000 points → at 150 DPI = ~20833 x 20833 px
    large_rect.width = 10000.0
    large_rect.height = 10000.0
    large_page.rect = large_rect

    # Normal page (within limits)
    normal_page = MagicMock()
    normal_rect = MagicMock()
    normal_rect.width = 612.0
    normal_rect.height = 792.0
    normal_page.rect = normal_rect
    normal_pixmap = MagicMock()
    normal_page.get_pixmap.return_value = normal_pixmap

    mock_doc.__len__ = MagicMock(return_value=2)
    mock_doc.__getitem__ = MagicMock(side_effect=lambda i: [large_page, normal_page][i])
    mock_doc.close = MagicMock()
    mock_fitz.open.return_value = mock_doc

    try:
        with patch.dict('sys.modules', {'fitz': mock_fitz}):
            import warnings
            with warnings.catch_warnings(record=True):
                warnings.simplefilter('always')
                # R2-6: returns (png_paths, info_dict)
                result, info = recognize._convert_pdf_to_pngs(pdf_path)
                # Only the normal page should be rendered
                assert len(result) == 1
                # R2-6: info must reflect skipped page
                assert info['skipped_pages'] == 1
                assert info['processed_pages'] == 1
                assert len(info['warnings']) >= 1
            for p in result:
                if os.path.exists(p):
                    os.unlink(p)
    finally:
        os.unlink(pdf_path)


# ---------- V1: Batch processing splits large requests ----------

def test_v1_batch_processing_splits_large_request():
    """V1: When encoded images exceed MAX_REQUEST_BYTES, batch processing is used."""
    # Create temporary image files
    tmp_files = []
    for i in range(6):
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            # Write enough bytes to make each image ~2 MB when base64-encoded
            f.write(b'\x89PNG\r\n\x1a\n' + b'\x00' * (1024 * 1024))
            tmp_files.append(f.name)

    try:
        # Mock the API call to track batch invocations
        batch_calls = []

        def mock_batch(images, prompt, api_key):
            batch_calls.append(len(images))
            return f"batch-{len(batch_calls)}", {'prompt_tokens': 10, 'completion_tokens': 5}

        with patch.object(recognize, '_call_vision_api_batch', side_effect=mock_batch):
            # Force a low MAX_REQUEST_BYTES to trigger batching
            with patch.object(recognize, 'MAX_REQUEST_BYTES', 3 * 1024 * 1024):  # 3 MB
                with patch.object(recognize, 'PDF_BATCH_SIZE', 2):
                    with patch.dict('os.environ', {'SILICONFLOW_API_KEY': 'test-key'}):
                        text, usage = recognize._call_vision_api(tmp_files, "test prompt")

        # Should have made multiple batch calls
        assert len(batch_calls) >= 2
        # Each batch should have at most PDF_BATCH_SIZE images
        assert all(n <= 2 for n in batch_calls)
        # Total images processed should equal input
        assert sum(batch_calls) == len(tmp_files)
    finally:
        for p in tmp_files:
            if os.path.exists(p):
                os.unlink(p)


def test_v1_single_request_when_under_limit():
    """V1: When images fit within MAX_REQUEST_BYTES, a single request is made."""
    tmp_files = []
    for i in range(2):
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            f.write(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100)  # Small image
            tmp_files.append(f.name)

    try:
        batch_calls = []

        def mock_batch(images, prompt, api_key):
            batch_calls.append(len(images))
            return "single result", {'prompt_tokens': 5, 'completion_tokens': 3}

        with patch.object(recognize, '_call_vision_api_batch', side_effect=mock_batch):
            with patch.dict('os.environ', {'SILICONFLOW_API_KEY': 'test-key'}):
                text, usage = recognize._call_vision_api(tmp_files, "test")

        assert len(batch_calls) == 1
        assert batch_calls[0] == 2
    finally:
        for p in tmp_files:
            if os.path.exists(p):
                os.unlink(p)
