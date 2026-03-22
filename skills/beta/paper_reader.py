"""Paper reader skill — download and extract text from arXiv PDFs."""

import logging
import re
from typing import Optional

import httpx

from ..base import Skill

log = logging.getLogger(__name__)

_ARXIV_PDF_BASE = "https://arxiv.org/pdf/"


class PaperReaderSkill(Skill):
    """Download arXiv PDFs and extract text content."""

    def __init__(self, llm_client: Optional[object] = None):
        super().__init__()
        self._llm_client = llm_client

    # ── Properties ──

    @property
    def name(self) -> str:
        return "paper_reader"

    @property
    def description(self) -> str:
        return "Download and read arXiv papers, optionally summarize with LLM"

    @property
    def capabilities(self) -> list[str]:
        return ["paper_reading", "literature_review", "paper_summary"]

    @property
    def risk_level(self) -> str:
        return "safe"

    @property
    def param_schema(self) -> dict:
        return {
            "action": {
                "type": "str",
                "required": True,
                "description": "One of: read",
            },
            "arxiv_id": {
                "type": "str",
                "required": True,
                "description": "arXiv paper ID, e.g. '2301.07041'",
            },
        }

    # ── Execution ──

    async def execute(self, params: dict) -> dict:
        action = params.get("action", "read")

        if action == "read":
            return await self._read(params)
        else:
            return {"success": False, "result": "", "error": f"Unknown action: {action}"}

    async def _read(self, params: dict) -> dict:
        arxiv_id = params.get("arxiv_id", "").strip()
        if not arxiv_id:
            return {"success": False, "result": "", "error": "'arxiv_id' is required"}

        # Clean up ID — strip URL prefix if user passed a full URL
        arxiv_id = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", arxiv_id)
        arxiv_id = arxiv_id.rstrip(".pdf").strip("/")

        pdf_url = f"{_ARXIV_PDF_BASE}{arxiv_id}.pdf"

        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=True
            ) as client:
                resp = await client.get(pdf_url)
                resp.raise_for_status()
                pdf_bytes = resp.content
        except Exception as e:
            log.warning("Failed to download PDF for %s: %s", arxiv_id, e)
            return {
                "success": False,
                "result": "",
                "error": f"Failed to download PDF: {e}",
            }

        # Extract text from PDF bytes
        text = self._extract_text_from_pdf(pdf_bytes)

        if not text:
            return {
                "success": False,
                "result": "",
                "error": "Could not extract text from PDF",
            }

        # Truncate to first 2000 characters for manageable output
        truncated = text[:2000]
        if len(text) > 2000:
            truncated += f"\n\n[... truncated, {len(text)} total characters]"

        # Optionally summarize with LLM
        if self._llm_client is not None:
            try:
                summary = await self._summarize(text[:4000])
                result = f"## Summary\n{summary}\n\n## Raw Text (first 2000 chars)\n{truncated}"
            except Exception as e:
                log.warning("LLM summarization failed: %s", e)
                result = truncated
        else:
            result = truncated

        return {"success": True, "result": result, "error": None}

    @staticmethod
    def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
        """Basic PDF text extraction using stdlib.

        Attempts to decode text from PDF stream objects.  This is a
        best-effort parser — for production use, a library like
        PyMuPDF or pdfplumber would be preferable.
        """
        try:
            raw = pdf_bytes.decode("latin-1")
        except Exception:
            return ""

        # Extract text between BT (begin text) and ET (end text) operators
        text_blocks: list[str] = []
        for match in re.finditer(r"BT\s(.*?)ET", raw, re.DOTALL):
            block = match.group(1)
            # Extract strings inside parentheses (Tj / TJ operators)
            for s in re.findall(r"\(([^)]*)\)", block):
                cleaned = s.replace("\\n", "\n").replace("\\r", "")
                if cleaned.strip():
                    text_blocks.append(cleaned)

        text = " ".join(text_blocks)
        # Clean non-printable characters
        text = re.sub(r"[^\x20-\x7E\n\t]", "", text)
        return text.strip()

    async def _summarize(self, text: str) -> str:
        """Summarize paper text using the provided LLM client.

        Expects self._llm_client to have an async ``generate(prompt)``
        method returning a string.
        """
        prompt = (
            "Summarize the following academic paper text in 3-5 bullet points, "
            "focusing on the main contribution, method, and results:\n\n"
            f"{text}"
        )
        result = await self._llm_client.generate(prompt)  # type: ignore[union-attr]
        return result
