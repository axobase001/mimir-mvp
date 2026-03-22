"""Portfolio tracking skill — pure local state with CoinGecko price lookups."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from ..base import Skill

log = logging.getLogger(__name__)

_COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"


class PortfolioTrackSkill(Skill):
    """Track crypto positions and calculate P&L against live CoinGecko prices."""

    def __init__(self) -> None:
        super().__init__()
        # {coin_id: {"amount": float, "buy_price": float}}
        self._positions: dict[str, dict[str, float]] = {}
        self._call_count = 0
        self._success_count = 0

    # ── Metadata ──

    @property
    def name(self) -> str:
        return "portfolio_track"

    @property
    def description(self) -> str:
        return "Track crypto portfolio positions and calculate profit/loss with live prices"

    @property
    def capabilities(self) -> list[str]:
        return ["portfolio_tracking", "pnl_calculation", "position_management"]

    @property
    def risk_level(self) -> str:
        return "safe"

    @property
    def param_schema(self) -> dict:
        return {
            "action": {
                "type": "str",
                "required": True,
                "description": "'add', 'remove', 'view', or 'pnl'",
            },
            "coin_id": {
                "type": "str",
                "required": False,
                "description": "CoinGecko coin ID (required for add/remove)",
            },
            "amount": {
                "type": "float",
                "required": False,
                "description": "Quantity of coins (required for add)",
            },
            "buy_price": {
                "type": "float",
                "required": False,
                "description": "Purchase price per coin in USD (required for add)",
            },
        }

    # ── Execute ──

    async def execute(self, params: dict) -> dict:
        action = params.get("action", "").lower()
        self._call_count += 1

        if action == "add":
            return self._add_position(params)
        elif action == "remove":
            return self._remove_position(params)
        elif action == "view":
            return await self._view_portfolio()
        elif action == "pnl":
            return await self._calculate_pnl()
        else:
            return {
                "success": False,
                "result": "",
                "error": f"Unknown action '{action}'. Use: add, remove, view, pnl.",
            }

    def _add_position(self, params: dict) -> dict:
        coin_id = params.get("coin_id", "").lower().strip()
        amount = params.get("amount")
        buy_price = params.get("buy_price")

        if not coin_id:
            return {"success": False, "result": "", "error": "coin_id is required for 'add'"}
        if amount is None:
            return {"success": False, "result": "", "error": "amount is required for 'add'"}
        if buy_price is None:
            return {"success": False, "result": "", "error": "buy_price is required for 'add'"}

        try:
            amount = float(amount)
            buy_price = float(buy_price)
        except (ValueError, TypeError):
            return {"success": False, "result": "", "error": "amount and buy_price must be numeric"}

        if amount <= 0:
            return {"success": False, "result": "", "error": "amount must be positive"}
        if buy_price < 0:
            return {"success": False, "result": "", "error": "buy_price cannot be negative"}

        if coin_id in self._positions:
            # Average into existing position
            existing = self._positions[coin_id]
            old_amount = existing["amount"]
            old_price = existing["buy_price"]
            total_amount = old_amount + amount
            avg_price = (old_amount * old_price + amount * buy_price) / total_amount
            self._positions[coin_id] = {"amount": total_amount, "buy_price": avg_price}
            self._success_count += 1
            return {
                "success": True,
                "result": (
                    f"Added {amount} {coin_id} @ ${buy_price:,.2f} to existing position.\n"
                    f"New total: {total_amount} {coin_id} @ avg ${avg_price:,.2f}"
                ),
                "error": None,
            }
        else:
            self._positions[coin_id] = {"amount": amount, "buy_price": buy_price}
            self._success_count += 1
            cost = amount * buy_price
            return {
                "success": True,
                "result": f"Position added: {amount} {coin_id} @ ${buy_price:,.2f} (cost ${cost:,.2f})",
                "error": None,
            }

    def _remove_position(self, params: dict) -> dict:
        coin_id = params.get("coin_id", "").lower().strip()
        if not coin_id:
            return {"success": False, "result": "", "error": "coin_id is required for 'remove'"}
        if coin_id not in self._positions:
            return {"success": False, "result": "", "error": f"No position found for '{coin_id}'"}

        removed = self._positions.pop(coin_id)
        self._success_count += 1
        return {
            "success": True,
            "result": f"Removed position: {removed['amount']} {coin_id} @ ${removed['buy_price']:,.2f}",
            "error": None,
        }

    async def _fetch_prices(self, coin_ids: list[str]) -> dict[str, float | None]:
        """Fetch current USD prices for a list of coins. Returns {coin_id: price_or_None}."""
        if not coin_ids:
            return {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    _COINGECKO_PRICE_URL,
                    params={"ids": ",".join(coin_ids), "vs_currencies": "usd"},
                )
                resp.raise_for_status()
                data = resp.json()
            return {cid: data.get(cid, {}).get("usd") for cid in coin_ids}
        except Exception as e:
            log.warning("Price fetch failed: %s", e)
            return {cid: None for cid in coin_ids}

    async def _view_portfolio(self) -> dict:
        if not self._positions:
            return {"success": True, "result": "Portfolio is empty.", "error": None}

        coin_ids = list(self._positions.keys())
        prices = await self._fetch_prices(coin_ids)

        lines = ["=== Portfolio ==="]
        total_cost = 0.0
        total_value = 0.0

        for coin_id, pos in self._positions.items():
            amount = pos["amount"]
            buy_price = pos["buy_price"]
            cost = amount * buy_price
            total_cost += cost

            current_price = prices.get(coin_id)
            if current_price is not None:
                value = amount * current_price
                total_value += value
                pnl = value - cost
                pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0
                lines.append(
                    f"  {coin_id}: {amount} @ ${buy_price:,.2f} (cost ${cost:,.2f})"
                    f" | now ${current_price:,.2f} = ${value:,.2f} | P&L {pnl:+,.2f} ({pnl_pct:+.1f}%)"
                )
            else:
                lines.append(
                    f"  {coin_id}: {amount} @ ${buy_price:,.2f} (cost ${cost:,.2f})"
                    f" | price unavailable"
                )

        lines.append(f"\nTotal cost: ${total_cost:,.2f}")
        if total_value > 0:
            total_pnl = total_value - total_cost
            total_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
            lines.append(f"Total value: ${total_value:,.2f}")
            lines.append(f"Total P&L: {total_pnl:+,.2f} ({total_pct:+.1f}%)")

        self._success_count += 1
        return {"success": True, "result": "\n".join(lines), "error": None}

    async def _calculate_pnl(self) -> dict:
        if not self._positions:
            return {"success": True, "result": "No positions to calculate P&L for.", "error": None}

        coin_ids = list(self._positions.keys())
        prices = await self._fetch_prices(coin_ids)

        lines = ["=== Profit & Loss ==="]
        total_cost = 0.0
        total_value = 0.0
        unavailable: list[str] = []

        for coin_id, pos in self._positions.items():
            amount = pos["amount"]
            buy_price = pos["buy_price"]
            cost = amount * buy_price
            total_cost += cost

            current_price = prices.get(coin_id)
            if current_price is not None:
                value = amount * current_price
                total_value += value
                pnl = value - cost
                pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0
                marker = "+" if pnl >= 0 else ""
                lines.append(
                    f"  {coin_id}: {marker}${abs(pnl):,.2f} ({pnl_pct:+.1f}%)"
                    f"  [bought ${buy_price:,.2f} -> now ${current_price:,.2f}]"
                )
            else:
                unavailable.append(coin_id)

        if unavailable:
            lines.append(f"\n  Price unavailable for: {', '.join(unavailable)}")

        total_pnl = total_value - total_cost
        total_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
        lines.append(f"\nOverall: {total_pnl:+,.2f} ({total_pct:+.1f}%)")
        lines.append(f"  Cost basis: ${total_cost:,.2f} | Current value: ${total_value:,.2f}")

        self._success_count += 1
        return {"success": True, "result": "\n".join(lines), "error": None}

    @property
    def usage_stats(self) -> dict:
        return {"call_count": self._call_count, "success_count": self._success_count}
