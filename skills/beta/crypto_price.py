"""Crypto price skill — CoinGecko free API, no key required."""

from __future__ import annotations

import json
import logging

import httpx

from ..base import Skill

log = logging.getLogger(__name__)

_COINGECKO_BASE = "https://api.coingecko.com/api/v3"


class CryptoPriceSkill(Skill):
    """Fetch live cryptocurrency prices and trending coins via CoinGecko."""

    def __init__(self) -> None:
        super().__init__()
        self._call_count = 0
        self._success_count = 0

    # ── Metadata ──

    @property
    def name(self) -> str:
        return "crypto_price"

    @property
    def description(self) -> str:
        return "Query live crypto prices and trending coins from CoinGecko (free, no API key)"

    @property
    def capabilities(self) -> list[str]:
        return ["crypto_price", "market_data", "trending_coins"]

    @property
    def risk_level(self) -> str:
        return "safe"

    @property
    def param_schema(self) -> dict:
        return {
            "action": {
                "type": "str",
                "required": False,
                "default": "price",
                "description": "'price' for spot prices, 'trending' for trending coins",
            },
            "coin_ids": {
                "type": "str",
                "required": False,
                "default": "bitcoin,ethereum",
                "description": "Comma-separated CoinGecko coin IDs (used with action=price)",
            },
            "vs_currencies": {
                "type": "str",
                "required": False,
                "default": "usd",
                "description": "Comma-separated fiat currencies (used with action=price)",
            },
        }

    # ── Execute ──

    async def execute(self, params: dict) -> dict:
        action = params.get("action", "price").lower()
        self._call_count += 1

        try:
            if action == "trending":
                return await self._trending()
            else:
                coin_ids = params.get("coin_ids", "bitcoin,ethereum")
                vs_currencies = params.get("vs_currencies", "usd")
                return await self._price(coin_ids, vs_currencies)
        except httpx.TimeoutException:
            return {"success": False, "result": "", "error": "CoinGecko request timed out"}
        except httpx.HTTPStatusError as e:
            return {
                "success": False,
                "result": "",
                "error": f"CoinGecko HTTP {e.response.status_code}: {e.response.text[:300]}",
            }
        except Exception as e:
            log.warning("CryptoPriceSkill error: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    async def _price(self, coin_ids: str, vs_currencies: str) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_COINGECKO_BASE}/simple/price",
                params={
                    "ids": coin_ids,
                    "vs_currencies": vs_currencies,
                    "include_24hr_change": "true",
                    "include_market_cap": "true",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if not data:
            return {"success": True, "result": "No price data returned.", "error": None}

        lines: list[str] = []
        currencies = [c.strip() for c in vs_currencies.split(",")]
        for coin, prices in data.items():
            parts = [f"{coin.upper()}:"]
            for cur in currencies:
                price = prices.get(cur)
                change = prices.get(f"{cur}_24h_change")
                mcap = prices.get(f"{cur}_market_cap")
                if price is not None:
                    entry = f"  {cur.upper()} {price:,.2f}"
                    if change is not None:
                        entry += f" ({change:+.2f}% 24h)"
                    if mcap is not None:
                        entry += f" [mcap {mcap:,.0f}]"
                    parts.append(entry)
            lines.append(" ".join(parts))

        self._success_count += 1
        return {"success": True, "result": "\n".join(lines), "error": None}

    async def _trending(self) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{_COINGECKO_BASE}/search/trending")
            resp.raise_for_status()
            data = resp.json()

        coins = data.get("coins", [])
        if not coins:
            return {"success": True, "result": "No trending coins found.", "error": None}

        lines: list[str] = []
        for i, entry in enumerate(coins[:10], 1):
            item = entry.get("item", {})
            name = item.get("name", "?")
            symbol = item.get("symbol", "?")
            rank = item.get("market_cap_rank", "N/A")
            price_btc = item.get("price_btc", 0)
            lines.append(
                f"{i}. {name} ({symbol}) — rank #{rank}, {price_btc:.8f} BTC"
            )

        self._success_count += 1
        return {"success": True, "result": "\n".join(lines), "error": None}

    @property
    def usage_stats(self) -> dict:
        return {"call_count": self._call_count, "success_count": self._success_count}
