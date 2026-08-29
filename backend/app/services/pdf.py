"""PDF text extraction built on pypdf."""

from __future__ import annotations

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.services.chunking import PageText

logger = logging.getLogger(__name__)


class PdfExtractionError(Exception):
    """Raised when a upload cannot be read as a text-bearing PDF."""


def looks_like_pdf(data: bytes) -> bool:
    """Cheap magic-number check so we reject junk before parsing it."""
    return data[:5] == b"%PDF-"


def extract_pages(data: bytes) -> list[PageText]:
    """Extract per-page text from an in-memory PDF.

    Raises:
        PdfExtractionError: the file is not a PDF, is encrypted, is corrupt, or
            contains no extractable text (e.g. a pure scan with no OCR layer).
    """
    if not data:
        raise PdfExtractionError("The uploaded file is empty.")
    if not looks_like_pdf(data):
        raise PdfExtractionError("The uploaded file is not a valid PDF.")

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Try the empty password: many PDFs are "encrypted" with no password.
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001 - surfaced to the user as 400
                raise PdfExtractionError(
                    "This PDF is password-protected and cannot be read."
                ) from exc
        pages: list[PageText] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001 - one bad page shouldn't fail the file
                logger.warning("Failed to extract text from page %s", index)
                text = ""
            pages.append(PageText(page_number=index, text=text))
    except PdfExtractionError:
        raise
    except (PdfReadError, ValueError, OSError) as exc:
        raise PdfExtractionError(f"Could not read the PDF: {exc}") from exc

    if not any(page.text.strip() for page in pages):
        raise PdfExtractionError(
            "No selectable text found in this PDF. Scanned documents need OCR "
            "before they can be indexed."
        )
    return pages
