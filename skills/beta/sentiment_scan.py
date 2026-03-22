"""Sentiment scan skill — SearXNG search + Fear & Greed Index from alternative.me."""

from __future__ import annotations

import logging

import httpx

from ..base import Skill

log = logging.getLogger(__name__)

_DEFAULT_SEARXNG_URL = "http://localhost:8080/search"
_FEAR_GREED_URL = "https://api.alternative.me/fng/"


class SentimentScanSkill(Skill):
    """Scan crypto sentiment via SearXNG search and the Fear & Greed Index."""

    def __init__(self, searxng_url: str = _DEFAULT_SEARXNG_URL) -> None:
        super().__init__()
        self._searxng_url = searxng_url
        self._call_count = 0
        self._success_count = 0

    # ── Metadata ──

    @property
    def name(self) -> str:
        return "sentiment_scan"

    @property
    def description(self) -> str:
        return "Scan crypto sentiment via web search and the Fear & Greed Index"

    @property
    def capabilities(self) -> list[str]:
        return ["sentiment_analysis", "crypto_sentiment", "fear_greed_index"]

    @property
    def risk_level(self) -> str:
        return "safe"

    @property
    def param_schema(self) -> dict:
        return {
            "action": {
                "type": "str",
                "required": False,
                "default": "scan",
                "description": "'scan' for sentiment search, 'fear_greed' for index only, 'both' for combined",
            },
            "coin": {
                "type": "str",
                "required": False,
                "default": "bitcoin",
                "description": "Coin name to search sentiment for (used with action=scan/both)",
            },
            "count": {
                "type": "int",
                "required": False,
                "default": 5,
                "description": "Number of search results to return",
            },
        }

    # ── Execute ──

    async def execute(self, params: dict) -> dict:
        action = params.get("action", "scan").lower()
        self._call_count += 1

        try:
            if action == "fear_greed":
                return await self._fear_greed()
            elif action == "scan":
                return await self._sentiment_search(params)
            elif action == "both":
                return await self._combined(params)
            else:
                return {
                    "success": False,
                    "result": "",
                    "error": f"Unknown action '{action}'. Use: scan, fear_greed, both.",
                }
        except Exception as e:
            log.warning("SentimentScanSkill error: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    async def _sentiment_search(self, params: dict) -> dict:
        coin = params.get("coin", "bitcoin").strip()
        count = params.get("count", 5)
        query = f"{coin} sentiment reddit crypto"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    self._searxng_url,
                    params={"q": query, "format": "json", "language": "en"},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            return {
                "success": False,
                "result": "",
                "error": f"SearXNG search failed: {e}",
            }

        results = data.get("results", [])[:count]
        if not results:
            return {
                "success": True,
                "result": f"No sentiment results found for '{coin}'.",
                "error": None,
            }

        lines = [f"Sentiment search results for {coin}:"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "?")
            content = r.get("content", "")[:150]
            url = r.get("url", "")
            lines.append(f"{i}. {title}")
            if content:
                lines.append(f"   {content}")
            if url:
                lines.append(f"   {url}")

        self._success_count += 1
        return {"success": True, "result": "\n".join(lines), "error": None}

    async def _fear_greed(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(_FEAR_GREED_URL, params={"limit": 1})
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            return {
                "success": False,
                "result": "",
                "error": f"Fear & Greed API failed: {e}",
            }

        entries = data.get("data", [])
        if not entries:
            return {"success": True, "result": "No Fear & Greed data available.", "error": None}

        entry = entries[0]
        value = entry.get("value", "?")
        classification = entry.get("value_classification", "?")
        timestamp = entry.get("timestamp", "?")

        text = (
            f"Crypto Fear & Greed Index:\n"
            f"  Value: {value}/100\n"
            f"  Classification: {classification}\n"
            f"  Updated: {timestamp}"
        )
        self._success_count += 1
        return {"success": True, "result": text, "error": None}

    async def _combined(self, params: dict) -> dict:
        # Run both in sequence (simple, avoids asyncio.gather complexity)
        fg_result = await self._fear_greed()
        search_result = await self._sentiment_search(params)

        parts: list[str] = []

        if fg_result["success"]:
            parts.append(fg_result["result"])
        else:
            parts.append(f"[Fear & Greed unavailable: {fg_result.get('error', '?')}]")

        parts.append("")  # blank line separator

        if search_result["success"]:
            parts.append(search_result["result"])
        else:
            parts.append(f"[Sentiment search unavailable: {search_result.get('error', '?')}]")

        self._success_count += 1
        return {"success": True, "result": "\n".join(parts), "error": None}

    @property
    def usage_stats(self) -> dict:
        return {"call_count": self._call_count, "success_count": self._success_count}
