"""Daily brief skill — aggregates news and market intelligence via SearXNG."""

import logging
from typing import Optional

import httpx

from ..base import Skill

log = logging.getLogger(__name__)

_DEFAULT_SEARXNG_URL = "http://localhost:8080/search"

_DEFAULT_TOPICS = [
    "AI startup funding",
    "large language model news",
    "tech industry trends",
]


class DailyBriefSkill(Skill):
    """Generates a daily brief by searching multiple topics via SearXNG."""

    def __init__(
        self,
        searxng_url: str = _DEFAULT_SEARXNG_URL,
        llm_client: Optional[object] = None,
    ):
        super().__init__()
        self._searxng_url = searxng_url
        self._llm_client = llm_client
        self._default_topics: list[str] = list(_DEFAULT_TOPICS)

    # ── Properties ──

    @property
    def name(self) -> str:
        return "daily_brief"

    @property
    def description(self) -> str:
        return "Generate a daily brief by searching multiple topics and aggregating results"

    @property
    def capabilities(self) -> list[str]:
        return ["daily_brief", "news_summary", "market_intelligence"]

    @property
    def risk_level(self) -> str:
        return "safe"

    @property
    def param_schema(self) -> dict:
        return {
            "action": {
                "type": "str",
                "required": True,
                "description": "One of: generate, configure",
            },
            "topics": {
                "type": "list[str]",
                "required": False,
                "description": "Topics to search (used by both generate and configure)",
            },
        }

    # ── Execution ──

    async def execute(self, params: dict) -> dict:
        action = params.get("action", "generate")

        if action == "configure":
            return self._configure(params)
        elif action == "generate":
            return await self._generate(params)
        else:
            return {"success": False, "result": "", "error": f"Unknown action: {action}"}

    def _configure(self, params: dict) -> dict:
        topics = params.get("topics")
        if not topics or not isinstance(topics, list):
            return {
                "success": False,
                "result": "",
                "error": "topics must be a non-empty list of strings",
            }
        self._default_topics = list(topics)
        return {
            "success": True,
            "result": f"Default topics updated to: {self._default_topics}",
            "error": None,
        }

    async def _generate(self, params: dict) -> dict:
        topics = params.get("topics") or self._default_topics
        if not topics:
            return {"success": False, "result": "", "error": "No topics provided"}

        sections: list[str] = []
        async with httpx.AsyncClient(timeout=20.0) as client:
            for topic in topics:
                try:
                    resp = await client.get(
                        self._searxng_url,
                        params={"q": topic, "format": "json", "language": "en"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    results = data.get("results", [])[:5]

                    if results:
                        items = "\n".join(
                            f"  - {r.get('title', 'N/A')}: "
                            f"{r.get('content', '')[:200]} "
                            f"[{r.get('url', '')}]"
                            for r in results
                        )
                        sections.append(f"## {topic}\n{items}")
                    else:
                        sections.append(f"## {topic}\n  (no results)")

                except Exception as e:
                    log.warning("SearXNG search failed for topic '%s': %s", topic, e)
                    sections.append(f"## {topic}\n  (search failed: {e})")

        brief = "\n\n".join(sections)
        return {"success": True, "result": brief, "error": None}
