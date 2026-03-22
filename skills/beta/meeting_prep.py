"""Meeting prep skill — research attendees and topics via SearXNG."""

import logging

import httpx

from ..base import Skill

log = logging.getLogger(__name__)

_DEFAULT_SEARXNG_URL = "http://localhost:8080/search"


class MeetingPrepSkill(Skill):
    """Prepare for meetings by researching attendees and topics."""

    def __init__(self, searxng_url: str = _DEFAULT_SEARXNG_URL):
        super().__init__()
        self._searxng_url = searxng_url

    # ── Properties ──

    @property
    def name(self) -> str:
        return "meeting_prep"

    @property
    def description(self) -> str:
        return "Research meeting attendees and topics for preparation"

    @property
    def capabilities(self) -> list[str]:
        return ["meeting_prep", "research", "background_check"]

    @property
    def risk_level(self) -> str:
        return "safe"

    @property
    def param_schema(self) -> dict:
        return {
            "action": {
                "type": "str",
                "required": True,
                "description": "One of: prep, brief",
            },
            "attendee_names": {
                "type": "list[str]",
                "required": False,
                "description": "List of attendee names to research (for prep action)",
            },
            "topic": {
                "type": "str",
                "required": False,
                "description": "Topic to research (for brief action)",
            },
        }

    # ── Execution ──

    async def execute(self, params: dict) -> dict:
        action = params.get("action", "prep")

        if action == "prep":
            return await self._prep(params)
        elif action == "brief":
            return await self._brief(params)
        else:
            return {"success": False, "result": "", "error": f"Unknown action: {action}"}

    async def _search(self, client: httpx.AsyncClient, query: str, count: int = 5) -> list[dict]:
        """Run a single SearXNG query and return results."""
        try:
            resp = await client.get(
                self._searxng_url,
                params={"q": query, "format": "json", "language": "en"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", [])[:count]
        except Exception as e:
            log.warning("SearXNG search failed for '%s': %s", query, e)
            return []

    async def _prep(self, params: dict) -> dict:
        names = params.get("attendee_names")
        if not names or not isinstance(names, list):
            return {
                "success": False,
                "result": "",
                "error": "attendee_names must be a non-empty list of strings",
            }

        sections: list[str] = []
        async with httpx.AsyncClient(timeout=20.0) as client:
            for name in names:
                results = await self._search(client, name)
                if results:
                    items = "\n".join(
                        f"  - {r.get('title', 'N/A')}: "
                        f"{r.get('content', '')[:200]} "
                        f"[{r.get('url', '')}]"
                        for r in results
                    )
                    sections.append(f"## {name}\n{items}")
                else:
                    sections.append(f"## {name}\n  (no results found)")

        return {
            "success": True,
            "result": "\n\n".join(sections),
            "error": None,
        }

    async def _brief(self, params: dict) -> dict:
        topic = params.get("topic", "").strip()
        if not topic:
            return {"success": False, "result": "", "error": "'topic' is required"}

        async with httpx.AsyncClient(timeout=20.0) as client:
            results = await self._search(client, topic, count=8)

        if not results:
            return {
                "success": True,
                "result": f"No information found for topic: {topic}",
                "error": None,
            }

        items = "\n".join(
            f"- {r.get('title', 'N/A')}: "
            f"{r.get('content', '')[:300]} "
            f"[{r.get('url', '')}]"
            for r in results
        )
        brief = f"## Topic Brief: {topic}\n\n{items}"
        return {"success": True, "result": brief, "error": None}
