"""Price alert skill — set above/below thresholds, check against CoinGecko live prices."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from ..base import Skill

log = logging.getLogger(__name__)

_COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"


class PriceAlertSkill(Skill):
    """Manage crypto price alerts with above/below thresholds (no API key needed)."""

    def __init__(self) -> None:
        super().__init__()
        # {alert_id: {"coin_id": str, "above": float|None, "below": float|None}}
        self._alerts: dict[str, dict[str, Any]] = {}
        self._next_id = 1
        self._call_count = 0
        self._success_count = 0

    # ── Metadata ──

    @property
    def name(self) -> str:
        return "price_alert"

    @property
    def description(self) -> str:
        return "Set, check, list, and remove crypto price alerts (above/below thresholds)"

    @property
    def capabilities(self) -> list[str]:
        return ["price_alert", "threshold_monitor", "crypto_watch"]

    @property
    def risk_level(self) -> str:
        return "safe"

    @property
    def param_schema(self) -> dict:
        return {
            "action": {
                "type": "str",
                "required": True,
                "description": "'set', 'check', 'list', or 'remove'",
            },
            "coin_id": {
                "type": "str",
                "required": False,
                "description": "CoinGecko coin ID (required for 'set')",
            },
            "above": {
                "type": "float",
                "required": False,
                "description": "Trigger when price rises above this (USD)",
            },
            "below": {
                "type": "float",
                "required": False,
                "description": "Trigger when price falls below this (USD)",
            },
            "alert_id": {
                "type": "str",
                "required": False,
                "description": "Alert ID to remove (required for 'remove')",
            },
        }

    # ── Execute ──

    async def execute(self, params: dict) -> dict:
        action = params.get("action", "").lower()
        self._call_count += 1

        if action == "set":
            return self._set_alert(params)
        elif action == "check":
            return await self._check_alerts()
        elif action == "list":
            return self._list_alerts()
        elif action == "remove":
            return self._remove_alert(params)
        else:
            return {
                "success": False,
                "result": "",
                "error": f"Unknown action '{action}'. Use: set, check, list, remove.",
            }

    def _set_alert(self, params: dict) -> dict:
        coin_id = params.get("coin_id", "").lower().strip()
        above = params.get("above")
        below = params.get("below")

        if not coin_id:
            return {"success": False, "result": "", "error": "coin_id is required for 'set'"}
        if above is None and below is None:
            return {"success": False, "result": "", "error": "Provide at least one of 'above' or 'below'"}

        try:
            above = float(above) if above is not None else None
            below = float(below) if below is not None else None
        except (ValueError, TypeError):
            return {"success": False, "result": "", "error": "above/below must be numeric"}

        alert_id = f"alert_{self._next_id}"
        self._next_id += 1
        self._alerts[alert_id] = {
            "coin_id": coin_id,
            "above": above,
            "below": below,
        }
        self._success_count += 1

        thresholds = []
        if above is not None:
            thresholds.append(f"above ${above:,.2f}")
        if below is not None:
            thresholds.append(f"below ${below:,.2f}")

        return {
            "success": True,
            "result": f"Alert {alert_id} created for {coin_id}: {', '.join(thresholds)}",
            "error": None,
        }

    async def _check_alerts(self) -> dict:
        if not self._alerts:
            return {"success": True, "result": "No alerts configured.", "error": None}

        # Collect unique coin IDs
        coin_ids = list({a["coin_id"] for a in self._alerts.values()})

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    _COINGECKO_PRICE_URL,
                    params={"ids": ",".join(coin_ids), "vs_currencies": "usd"},
                )
                resp.raise_for_status()
                prices = resp.json()
        except Exception as e:
            return {"success": False, "result": "", "error": f"Price fetch failed: {e}"}

        triggered: list[str] = []
        not_triggered: list[str] = []

        for alert_id, alert in self._alerts.items():
            coin = alert["coin_id"]
            price = prices.get(coin, {}).get("usd")
            if price is None:
                not_triggered.append(f"{alert_id}: {coin} — price unavailable")
                continue

            above = alert.get("above")
            below = alert.get("below")
            reasons = []
            if above is not None and price >= above:
                reasons.append(f"ABOVE ${above:,.2f}")
            if below is not None and price <= below:
                reasons.append(f"BELOW ${below:,.2f}")

            if reasons:
                triggered.append(
                    f"TRIGGERED {alert_id}: {coin} @ ${price:,.2f} — {', '.join(reasons)}"
                )
            else:
                status_parts = []
                if above is not None:
                    status_parts.append(f"above ${above:,.2f}")
                if below is not None:
                    status_parts.append(f"below ${below:,.2f}")
                not_triggered.append(
                    f"{alert_id}: {coin} @ ${price:,.2f} (waiting for {', '.join(status_parts)})"
                )

        lines = []
        if triggered:
            lines.append("=== TRIGGERED ALERTS ===")
            lines.extend(triggered)
        if not_triggered:
            lines.append("=== PENDING ALERTS ===")
            lines.extend(not_triggered)

        self._success_count += 1
        return {"success": True, "result": "\n".join(lines), "error": None}

    def _list_alerts(self) -> dict:
        if not self._alerts:
            return {"success": True, "result": "No alerts configured.", "error": None}

        lines: list[str] = []
        for alert_id, alert in self._alerts.items():
            parts = [f"{alert_id}: {alert['coin_id']}"]
            if alert.get("above") is not None:
                parts.append(f"above ${alert['above']:,.2f}")
            if alert.get("below") is not None:
                parts.append(f"below ${alert['below']:,.2f}")
            lines.append(" — ".join(parts))

        self._success_count += 1
        return {"success": True, "result": "\n".join(lines), "error": None}

    def _remove_alert(self, params: dict) -> dict:
        alert_id = params.get("alert_id", "").strip()
        if not alert_id:
            return {"success": False, "result": "", "error": "alert_id is required for 'remove'"}
        if alert_id not in self._alerts:
            return {"success": False, "result": "", "error": f"Alert '{alert_id}' not found"}

        removed = self._alerts.pop(alert_id)
        self._success_count += 1
        return {
            "success": True,
            "result": f"Removed {alert_id} ({removed['coin_id']})",
            "error": None,
        }

    @property
    def usage_stats(self) -> dict:
        return {"call_count": self._call_count, "success_count": self._success_count}
