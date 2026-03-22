"""RSS/Atom feed monitor skill — track feeds for new entries."""

import logging
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from ..base import Skill

log = logging.getLogger(__name__)


class RssMonitorSkill(Skill):
    """Parse and monitor RSS/Atom feeds for new entries."""

    def __init__(self):
        super().__init__()
        self._feeds: dict[str, dict[str, Any]] = {}

    # ── Properties ──

    @property
    def name(self) -> str:
        return "rss_monitor"

    @property
    def description(self) -> str:
        return "Monitor RSS/Atom feeds and report new entries"

    @property
    def capabilities(self) -> list[str]:
        return ["rss_monitoring", "news_feed", "content_tracking"]

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
                "description": "Feed name (for add/remove)",
            },
            "url": {
                "type": "str",
                "required": False,
                "description": "Feed URL (for add)",
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
        self._feeds[name] = {"url": url, "last_seen_ids": set()}
        return {
            "success": True,
            "result": f"Added feed '{name}' -> {url}",
            "error": None,
        }

    def _remove(self, params: dict) -> dict:
        name = params.get("name", "").strip()
        if not name:
            return {"success": False, "result": "", "error": "'name' is required"}
        if name not in self._feeds:
            return {
                "success": False,
                "result": "",
                "error": f"Feed '{name}' not found",
            }
        del self._feeds[name]
        return {"success": True, "result": f"Removed feed '{name}'", "error": None}

    def _list(self) -> dict:
        if not self._feeds:
            return {
                "success": True,
                "result": "No feeds being tracked.",
                "error": None,
            }
        lines = [
            f"- {name}: {info['url']} ({len(info['last_seen_ids'])} seen entries)"
            for name, info in self._feeds.items()
        ]
        return {"success": True, "result": "\n".join(lines), "error": None}

    async def _check(self) -> dict:
        if not self._feeds:
            return {"success": True, "result": "No feeds to check.", "error": None}

        report: list[str] = []
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            for feed_name, info in self._feeds.items():
                try:
                    resp = await client.get(info["url"])
                    resp.raise_for_status()
                    entries = self._parse_feed(resp.text)

                    new_entries = [
                        e for e in entries if e["id"] not in info["last_seen_ids"]
                    ]

                    # Update seen IDs
                    info["last_seen_ids"].update(e["id"] for e in entries)

                    if new_entries:
                        items = "\n".join(
                            f"    - {e['title']} [{e.get('link', '')}]"
                            for e in new_entries[:10]
                        )
                        report.append(
                            f"- {feed_name}: {len(new_entries)} new entries\n{items}"
                        )
                    else:
                        report.append(f"- {feed_name}: no new entries")

                except Exception as e:
                    log.warning("Failed to check feed '%s': %s", feed_name, e)
                    report.append(f"- {feed_name}: ERROR ({e})")

        return {"success": True, "result": "\n".join(report), "error": None}

    @staticmethod
    def _parse_feed(xml_text: str) -> list[dict]:
        """Parse RSS 2.0 or Atom feed XML into a list of entry dicts."""
        entries: list[dict] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return entries

        # Atom namespace
        atom_ns = "{http://www.w3.org/2005/Atom}"

        # Try Atom format first
        for entry_el in root.findall(f"{atom_ns}entry"):
            entry_id = ""
            title = ""
            link = ""

            id_el = entry_el.find(f"{atom_ns}id")
            if id_el is not None and id_el.text:
                entry_id = id_el.text.strip()

            title_el = entry_el.find(f"{atom_ns}title")
            if title_el is not None and title_el.text:
                title = title_el.text.strip()

            link_el = entry_el.find(f"{atom_ns}link")
            if link_el is not None:
                link = link_el.get("href", "")

            if not entry_id:
                entry_id = link or title
            entries.append({"id": entry_id, "title": title, "link": link})

        if entries:
            return entries

        # Try RSS 2.0 format
        for item_el in root.iter("item"):
            entry_id = ""
            title = ""
            link = ""

            guid_el = item_el.find("guid")
            if guid_el is not None and guid_el.text:
                entry_id = guid_el.text.strip()

            title_el = item_el.find("title")
            if title_el is not None and title_el.text:
                title = title_el.text.strip()

            link_el = item_el.find("link")
            if link_el is not None and link_el.text:
                link = link_el.text.strip()

            if not entry_id:
                entry_id = link or title
            entries.append({"id": entry_id, "title": title, "link": link})

        return entries
