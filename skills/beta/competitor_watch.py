"""Competitor watch skill — monitor competitor URLs for changes."""

import hashlib
import logging
import time
from typing import Any

import httpx

from ..base import Skill

log = logging.getLogger(__name__)


class CompetitorWatchSkill(Skill):
    """Monitor competitor web pages for content changes."""

    def __init__(self):
        super().__init__()
        self._competitors: dict[str, dict[str, Any]] = {}

    # ── Properties ──

    @property
    def name(self) -> str:
        return "competitor_watch"

    @property
    def description(self) -> str:
        return "Monitor competitor URLs and detect content changes"

    @property
    def capabilities(self) -> list[str]:
        return ["competitor_watch", "market_intelligence", "change_detection"]

    @property
    def risk_level(self) -> str:
        return "safe"

    @property
    def param_schema(self) -> dict:
        return {
            "action": {
                "type": "str",
                "required": True,
                "description": "One of: add, check, list, remove",
            },
            "name": {
                "type": "str",
                "required": False,
                "description": "Competitor name (for add/remove)",
            },
            "url": {
                "type": "str",
                "required": False,
                "description": "Competitor URL (for add)",
            },
        }

    # ── Execution ──

    async def execute(self, params: dict) -> dict:
        action = params.get("action", "list")

        if action == "add":
            return self._add(params)
        elif action == "remove":
            return self._remove(params)
        elif action == "list":
            return self._list()
        elif action == "check":
            return await self._check()
        else:
            return {"success": False, "result": "", "error": f"Unknown action: {action}"}

    def _add(self, params: dict) -> dict:
        name = params.get("name", "").strip()
        url = params.get("url", "").strip()
        if not name or not url:
            return {
                "success": False,
                "result": "",
                "error": "Both 'name' and 'url' are required",
            }
        self._competitors[name] = {
            "url": url,
            "last_snapshot": None,
            "last_checked": None,
        }
        return {
            "success": True,
            "result": f"Added competitor '{name}' -> {url}",
            "error": None,
        }

    def _remove(self, params: dict) -> dict:
        name = params.get("name", "").strip()
        if not name:
            return {"success": False, "result": "", "error": "'name' is required"}
        if name not in self._competitors:
            return {
                "success": False,
                "result": "",
                "error": f"Competitor '{name}' not found",
            }
        del self._competitors[name]
        return {
            "success": True,
            "result": f"Removed competitor '{name}'",
            "error": None,
        }

    def _list(self) -> dict:
        if not self._competitors:
            return {
                "success": True,
                "result": "No competitors being tracked.",
                "error": None,
            }
        lines = []
        for name, info in self._competitors.items():
            checked = info["last_checked"] or "never"
            lines.append(f"- {name}: {info['url']} (last checked: {checked})")
        return {"success": True, "result": "\n".join(lines), "error": None}

    async def _check(self) -> dict:
        if not self._competitors:
            return {
                "success": True,
                "result": "No competitors to check.",
                "error": None,
            }

        report: list[str] = []
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            for name, info in self._competitors.items():
                try:
                    resp = await client.get(info["url"])
                    resp.raise_for_status()
                    body = resp.text
                    current_hash = hashlib.sha256(body.encode()).hexdigest()
                    current_length = len(body)

                    prev = info["last_snapshot"]
                    info["last_checked"] = time.strftime("%Y-%m-%d %H:%M:%S")

                    if prev is None:
                        report.append(
                            f"- {name}: initial snapshot taken "
                            f"(length={current_length}, hash={current_hash[:12]})"
                        )
                    elif prev["hash"] != current_hash:
                        delta = current_length - prev["length"]
                        sign = "+" if delta >= 0 else ""
                        report.append(
                            f"- {name}: CHANGED "
                            f"(length {prev['length']} -> {current_length}, "
                            f"delta={sign}{delta})"
                        )
                    else:
                        report.append(f"- {name}: no change")

                    info["last_snapshot"] = {
                        "hash": current_hash,
                        "length": current_length,
                    }

                except Exception as e:
                    log.warning("Failed to check competitor '%s': %s", name, e)
                    report.append(f"- {name}: ERROR ({e})")

        return {"success": True, "result": "\n".join(report), "error": None}
