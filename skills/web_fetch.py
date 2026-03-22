"""WebFetchSkill — fetch web pages and extract text content."""

from __future__ import annotations

import logging
import re

import httpx

from .base import Skill, SkillResult

log = logging.getLogger(__name__)

_MAX_SIZE = 500 * 1024  # 500KB
_DEFAULT_TIMEOUT = 10.0


class WebFetchSkill(Skill):
    """Fetch a URL and return text content (HTML tags stripped)."""

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        super().__init__()
        self._timeout = timeout
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return "抓取网页内容并转换为纯文本"

    @property
    def capabilities(self) -> list[str]:
        return ["fetch_url", "read_webpage", "extract_content"]

    @property
    def param_schema(self) -> dict:
        return {
            "url": {"type": "str", "required": True, "description": "URL to fetch"},
            "max_length": {"type": "int", "required": False, "default": 5000, "description": "Max text chars to return"},
        }

    @property
    def risk_level(self) -> str:
        return "safe"

    async def execute(self, params: dict) -> dict:
        url = params.get("url", "")
        max_length = params.get("max_length", 5000)
        self._call_count += 1

        if not url:
            return {"success": False, "result": "", "error": "No URL provided"}

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

                # Check content length
                content_length = resp.headers.get("content-length")
                if content_length and int(content_length) > _MAX_SIZE:
                    return {
                        "success": False,
                        "result": "",
                        "error": f"Content too large: {content_length} bytes (limit {_MAX_SIZE})",
                    }

                html = resp.text
                if len(html.encode("utf-8")) > _MAX_SIZE:
                    html = html[:_MAX_SIZE // 2]  # rough truncation

            # Simple HTML to text
            text = _html_to_text(html)

            # Extract email addresses from raw HTML before stripping
            found_emails = _extract_emails(html)

            if len(text) > max_length:
                text = text[:max_length] + "... [truncated]"

            # Append found emails as structured data
            if found_emails:
                email_section = "\n\n[EMAILS FOUND ON PAGE: " + ", ".join(found_emails) + "]"
                text += email_section

            self._success_count += 1
            return {"success": True, "result": text, "error": None}

        except httpx.TimeoutException:
            return {"success": False, "result": "", "error": f"Timeout after {self._timeout}s"}
        except Exception as e:
            log.warning("WebFetchSkill failed for %s: %s", url, e)
            return {"success": False, "result": "", "error": str(e)}

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }


_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
_JUNK_EMAIL_DOMAINS = {
    "example.com", "example.org", "test.com", "sentry.io",
    "w3.org", "schema.org", "xmlns.com", "google.com",
    "gstatic.com", "googleapis.com", "cloudflare.com",
}


def _extract_emails(html: str) -> list[str]:
    """Extract real email addresses from HTML using regex.

    Filters out junk (tracking pixels, schema URIs, etc).
    """
    # Also check mailto: links
    raw = set(_EMAIL_RE.findall(html))
    # Also decode HTML entities that might hide emails
    decoded = html.replace("&#64;", "@").replace("&#x40;", "@").replace("[at]", "@").replace(" AT ", "@")
    raw.update(_EMAIL_RE.findall(decoded))

    cleaned = []
    for addr in raw:
        addr_lower = addr.lower()
        domain = addr_lower.split("@")[1] if "@" in addr_lower else ""
        # Skip junk domains
        if domain in _JUNK_EMAIL_DOMAINS:
            continue
        # Skip image/asset filenames that look like emails
        if any(addr_lower.endswith(ext) for ext in [".png", ".jpg", ".gif", ".svg", ".css", ".js"]):
            continue
        cleaned.append(addr_lower)

    return sorted(set(cleaned))[:10]  # cap at 10


def _html_to_text(html: str) -> str:
    """Minimal HTML to plain text conversion."""
    # Remove script and style blocks
    text = re.sub(r"<script[^>]*>[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[^>]*>[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&nbsp;", " ")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text
