"""Web search skill — SearXNG backend (self-hosted, no API key needed)."""

import logging

import httpx

from .base import Skill, SkillResult

log = logging.getLogger(__name__)

# Default SearXNG instance on the same server as Skuld
_DEFAULT_SEARXNG_URL = "http://localhost:8080/search"


class WebSearchSkill(Skill):
    """Search the web via a self-hosted SearXNG instance."""

    def __init__(self, searxng_url: str = _DEFAULT_SEARXNG_URL):
        super().__init__()
        self._searxng_url = searxng_url
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "搜索互联网获取最新信息（SearXNG聚合搜索）"

    @property
    def capabilities(self) -> list[str]:
        return ["web_search", "information_retrieval", "fact_check"]

    @property
    def param_schema(self) -> dict:
        return {
            "query": {"type": "str", "required": True, "description": "Search query"},
            "count": {"type": "int", "required": False, "default": 5, "description": "Number of results"},
        }

    @property
    def risk_level(self) -> str:
        return "safe"

    @staticmethod
    def _ensure_english(query: str) -> str:
        """Detect if query is mostly non-ASCII (e.g. Chinese) and add English hint."""
        non_ascii = sum(1 for c in query if ord(c) > 127)
        if non_ascii > len(query) * 0.3:
            # Query is mostly non-English — append English marker
            # so SearXNG prioritizes English results
            return query + " english"
        return query

    async def execute(self, params: dict) -> dict:
        query = params.get("query", "")
        count = params.get("count", 5)
        self._call_count += 1

        if not query:
            return {"success": False, "result": "", "error": "Empty query"}

        # Force English for outreach-related queries
        query = self._ensure_english(query)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    self._searxng_url,
                    params={"q": query, "format": "json", "language": "en"},
                )
                resp.raise_for_status()
                data = resp.json()

            results = data.get("results", [])[:count]
            if not results:
                return {"success": True, "result": "No results found.", "error": None}

            text = "\n".join(
                f"- {r.get('title', '')}: {r.get('content', '')} [URL: {r.get('url', '')}]"
                for r in results
            )
            self._success_count += 1
            return {"success": True, "result": text, "error": None}

        except Exception as e:
            log.warning("SearXNG search failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }


# Backward-compatible alias — old code imports BraveSearchSkill
BraveSearchSkill = WebSearchSkill
