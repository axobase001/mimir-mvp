"""PDFReadSkill — extract text from PDF files.

Tries PyPDF2, then pdfplumber, then a naive binary text extraction fallback.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .base import Skill

log = logging.getLogger(__name__)


class PDFReadSkill(Skill):
    """Read and extract text from PDF documents."""

    def __init__(self) -> None:
        super().__init__()
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "pdf_read"

    @property
    def description(self) -> str:
        return "读取PDF文件并提取文本内容"

    @property
    def capabilities(self) -> list[str]:
        return ["read_pdf", "extract_text", "parse_document"]

    @property
    def param_schema(self) -> dict:
        return {
            "path": {"type": "str", "required": True,
                      "description": "Path to the PDF file"},
            "pages": {"type": "str", "required": False, "default": "all",
                       "description": "Page range, e.g. '1-5', '3', or 'all'"},
        }

    @property
    def risk_level(self) -> str:
        return "safe"

    async def execute(self, params: dict) -> dict:
        path = params.get("path", "")
        pages_spec = params.get("pages", "all")
        self._call_count += 1

        if not path:
            return {"success": False, "result": "", "error": "No path provided"}

        p = Path(path)
        if not p.exists():
            return {"success": False, "result": "", "error": f"File not found: {path}"}

        page_range = _parse_page_range(pages_spec)

        # Try extraction backends in order
        text = ""
        method = ""

        # Backend 1: PyPDF2
        try:
            text, method = _extract_pypdf2(p, page_range)
        except ImportError:
            pass
        except Exception as e:
            log.warning("PyPDF2 extraction failed: %s", e)

        # Backend 2: pdfplumber
        if not text:
            try:
                text, method = _extract_pdfplumber(p, page_range)
            except ImportError:
                pass
            except Exception as e:
                log.warning("pdfplumber extraction failed: %s", e)

        # Backend 3: naive binary extraction
        if not text:
            try:
                text, method = _extract_naive(p)
            except Exception as e:
                log.warning("Naive extraction failed: %s", e)
                return {"success": False, "result": "",
                        "error": f"All extraction methods failed. Last: {e}"}

        if not text.strip():
            return {"success": False, "result": "",
                    "error": "PDF appears to contain no extractable text (scanned image?)"}

        self._success_count += 1
        return {
            "success": True,
            "result": text.strip(),
            "error": None,
            "artifacts": [f"method={method}", f"chars={len(text)}"],
        }

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }


def _parse_page_range(spec: str) -> tuple[int, int] | None:
    """Parse page range spec. Returns (start, end) 0-indexed, or None for all."""
    if not spec or spec.lower() == "all":
        return None
    spec = spec.strip()
    if "-" in spec:
        parts = spec.split("-", 1)
        try:
            start = int(parts[0]) - 1  # 1-indexed to 0-indexed
            end = int(parts[1])        # end is exclusive internally
            return (max(0, start), end)
        except ValueError:
            return None
    else:
        try:
            page = int(spec) - 1
            return (max(0, page), page + 1)
        except ValueError:
            return None


def _extract_pypdf2(path: Path, page_range: tuple[int, int] | None) -> tuple[str, str]:
    """Extract text using PyPDF2."""
    from PyPDF2 import PdfReader  # type: ignore

    reader = PdfReader(str(path))
    pages = reader.pages

    if page_range is not None:
        start, end = page_range
        pages = pages[start:end]

    texts: list[str] = []
    for i, page in enumerate(pages):
        page_text = page.extract_text() or ""
        if page_text.strip():
            texts.append(f"--- Page {i + 1} ---\n{page_text}")

    return "\n\n".join(texts), "PyPDF2"


def _extract_pdfplumber(path: Path, page_range: tuple[int, int] | None) -> tuple[str, str]:
    """Extract text using pdfplumber."""
    import pdfplumber  # type: ignore

    texts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        pages = pdf.pages
        if page_range is not None:
            start, end = page_range
            pages = pages[start:end]

        for i, page in enumerate(pages):
            page_text = page.extract_text() or ""
            if page_text.strip():
                texts.append(f"--- Page {i + 1} ---\n{page_text}")

    return "\n\n".join(texts), "pdfplumber"


def _extract_naive(path: Path) -> tuple[str, str]:
    """Naive binary extraction: find text-like sequences in PDF binary."""
    raw = path.read_bytes()
    # Find content between stream/endstream markers
    streams = re.findall(b"stream\r?\n(.*?)\r?\nendstream", raw, re.DOTALL)

    texts: list[str] = []
    for stream in streams:
        # Try to find printable text sequences
        printable = re.findall(rb"[\x20-\x7e]{4,}", stream)
        for seq in printable:
            try:
                decoded = seq.decode("ascii", errors="ignore")
                if len(decoded) > 10:
                    texts.append(decoded)
            except Exception:
                pass

    return "\n".join(texts), "naive_binary"
