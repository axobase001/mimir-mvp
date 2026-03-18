"""SummarizeURLSkill — fetch a URL and generate an LLM summary."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import httpx

from .base import Skill, SkillResult

log = logging.getLogger(__name__)

_MAX_SIZE = 500 * 1024  # 500KB
_DEFAULT_TIMEOUT = 15.0
_DEFAULT_MAX_WORDS = 200


def _html_to_text(html: str) -> str:
    """Minimal HTML to plain text conversion."""
    text = re.sub(r"<script[^>]*>[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[^>]*>[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


class SummarizeURLSkill(Skill):
    """Fetch a URL and generate a concise summary using the LLM."""

    def __init__(
        self,
        llm_client: Optional[Any] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__()
        self._llm_client = llm_client
        self._timeout = timeout
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "summarize_url"

    @property
    def description(self) -> str:
        return "抓取URL网页内容并用LLM生成摘要"

    @property
    def capabilities(self) -> list[str]:
        return ["summarize_webpage", "read_article", "extract_key_points"]

    @property
    def param_schema(self) -> dict:
        return {
            "url": {"type": "str", "required": True,
                    "description": "URL to fetch and summarize"},
            "max_words": {"type": "int", "required": False,
                          "default": _DEFAULT_MAX_WORDS,
                          "description": "Maximum words in the summary"},
            "language": {"type": "str", "required": False,
                         "default": "same as source",
                         "description": "Language for the summary output"},
        }

    @property
    def risk_level(self) -> str:
        return "safe"

    async def execute(self, params: dict) -> dict:
        url = params.get("url", "")
        max_words = params.get("max_words", _DEFAULT_MAX_WORDS)
        language = params.get("language", "same as source")
        self._call_count += 1

        if not url:
            return {"success": False, "result": "", "error": "No URL provided"}

        if self._llm_client is None:
            return {"success": False, "result": "",
                    "error": "LLM client not configured. SummarizeURLSkill requires an llm_client."}

        # Step 1: Fetch the URL
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                max_redirects=5,
            ) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Skuld/1.0 (cognitive agent)",
                })
                resp.raise_for_status()

                content_length = resp.headers.get("content-length")
                if content_length and int(content_length) > _MAX_SIZE:
                    return {
                        "success": False, "result": "",
                        "error": f"Content too large: {content_length} bytes",
                    }

                html = resp.text
                if len(html.encode("utf-8")) > _MAX_SIZE:
                    html = html[:_MAX_SIZE // 2]

        except httpx.TimeoutException:
            return {"success": False, "result": "",
                    "error": f"Timeout fetching URL after {self._timeout}s"}
        except Exception as e:
            return {"success": False, "result": "",
                    "error": f"Failed to fetch URL: {e}"}

        # Step 2: Convert to text
        text = _html_to_text(html)
        if not text.strip():
            return {"success": False, "result": "",
                    "error": "No readable text content found on page"}

        # Truncate to reasonable size for LLM
        if len(text) > 10000:
            text = text[:10000] + "... [truncated]"

        # Step 3: Summarize with LLM
        lang_instruction = ""
        if language and language != "same as source":
            lang_instruction = f" Write the summary in {language}."

        prompt = (
            f"Summarize the following webpage content in at most {max_words} words. "
            f"Focus on the key points and main ideas.{lang_instruction}\n\n"
            f"URL: {url}\n\n"
            f"Content:\n{text}"
        )

        try:
            response = await self._llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
            )
            summary = response.get("content", "").strip()

            if not summary:
                return {"success": False, "result": "",
                        "error": "LLM returned empty summary"}

            self._success_count += 1
            return {
                "success": True,
                "result": summary,
                "error": None,
            }

        except Exception as e:
            log.error("SummarizeURLSkill LLM call failed: %s", e)
            return {"success": False, "result": "",
                    "error": f"LLM summarization failed: {e}"}

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }
