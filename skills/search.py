import logging

import httpx

from .base import Skill, SkillResult

log = logging.getLogger(__name__)


class BraveSearchSkill(Skill):
    def __init__(self, api_key: str):
        super().__init__()
        self._api_key = api_key
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "brave_search"

    @property
    def description(self) -> str:
        return "搜索互联网获取最新信息"

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

    async def execute(self, params: dict) -> dict:
        query = params.get("query", "")
        count = params.get("count", 5)
        self._call_count += 1

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": count},
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "X-Subscription-Token": self._api_key,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            results = data.get("web", {}).get("results", [])
            if not results:
                return {"success": True, "result": "No results found.", "error": None}

            text = "\n".join(
                f"- {r.get('title', '')}: {r.get('description', '')}"
                for r in results
            )
            self._success_count += 1
            return {"success": True, "result": text, "error": None}

        except Exception as e:
            log.warning("Brave search failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }
