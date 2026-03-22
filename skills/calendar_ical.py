"""CalendarSkill — read/write iCalendar (.ics) files locally."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .base import Skill, SkillResult

log = logging.getLogger(__name__)


def _parse_ics_events(text: str) -> list[dict]:
    """Naively parse VEVENT blocks from iCalendar text.

    Falls back to manual parsing when the ``icalendar`` library is unavailable.
    """
    events: list[dict] = []
    in_event = False
    current: dict = {}

    for line in text.splitlines():
        line = line.strip()
        if line == "BEGIN:VEVENT":
            in_event = True
            current = {}
        elif line == "END:VEVENT":
            in_event = False
            events.append(current)
            current = {}
        elif in_event and ":" in line:
            key, _, value = line.partition(":")
            # Strip parameters from key (e.g. DTSTART;VALUE=DATE)
            key = key.split(";")[0]
            current[key] = value
    return events


def _format_events(events: list[dict]) -> str:
    """Format parsed events into a human-readable text list."""
    if not events:
        return "No events found."
    lines: list[str] = []
    for i, ev in enumerate(events, 1):
        title = ev.get("SUMMARY", "(no title)")
        start = ev.get("DTSTART", "?")
        end = ev.get("DTEND", "?")
        desc = ev.get("DESCRIPTION", "")
        entry = f"{i}. {title}  |  {start} -> {end}"
        if desc:
            entry += f"\n   {desc}"
        lines.append(entry)
    return "\n".join(lines)


def _build_vevent(
    title: str,
    start: str,
    end: str,
    description: str = "",
    uid: str = "",
) -> str:
    """Build a single VEVENT block as an iCalendar string."""
    if not uid:
        uid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Normalise ISO datetimes to basic iCal format
    def _to_ical_dt(iso_str: str) -> str:
        iso_str = iso_str.replace("-", "").replace(":", "")
        # Remove any timezone info character that isn't Z
        iso_str = re.sub(r"[+\-]\d{4}$", "", iso_str)
        if not iso_str.endswith("Z"):
            iso_str += "Z"
        return iso_str

    vevent = (
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTAMP:{now}\r\n"
        f"DTSTART:{_to_ical_dt(start)}\r\n"
        f"DTEND:{_to_ical_dt(end)}\r\n"
        f"SUMMARY:{title}\r\n"
    )
    if description:
        # Escape newlines for iCal
        escaped_desc = description.replace(chr(10), '\\n')
        vevent += f"DESCRIPTION:{escaped_desc}\r\n"
    vevent += "END:VEVENT\r\n"
    return vevent


class CalendarSkill(Skill):
    """Read and write iCalendar (.ics) files. No external API dependency."""

    def __init__(self) -> None:
        super().__init__()
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "calendar"

    @property
    def description(self) -> str:
        return "读写iCalendar (.ics)文件：列出事件、创建事件、导出摘要"

    @property
    def capabilities(self) -> list[str]:
        return ["read_calendar", "create_event", "list_events", "schedule"]

    @property
    def param_schema(self) -> dict:
        return {
            "action": {"type": "str", "required": True,
                       "description": "'list', 'create', or 'export'"},
            "path": {"type": "str", "required": True,
                     "description": "Path to the .ics file"},
            "title": {"type": "str", "required": False,
                      "description": "Event title (for create)"},
            "start": {"type": "str", "required": False,
                      "description": "ISO datetime for event start (for create)"},
            "end": {"type": "str", "required": False,
                    "description": "ISO datetime for event end (for create)"},
            "description": {"type": "str", "required": False,
                            "description": "Event description (for create)"},
        }

    @property
    def risk_level(self) -> str:
        return "review"

    async def execute(self, params: dict) -> dict:
        action = params.get("action", "")
        path_str = params.get("path", "")
        self._call_count += 1

        if not action:
            return {"success": False, "result": "", "error": "No action specified"}
        if not path_str:
            return {"success": False, "result": "", "error": "No path specified"}

        path = Path(path_str)

        try:
            if action == "list":
                return self._list_events(path)
            elif action == "create":
                return self._create_event(path, params)
            elif action == "export":
                return self._export_events(path)
            else:
                return {"success": False, "result": "",
                        "error": f"Unknown action: {action}. Use 'list', 'create', or 'export'."}
        except Exception as e:
            log.error("CalendarSkill failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    # ── Internal actions ──

    def _list_events(self, path: Path) -> dict:
        if not path.exists():
            return {"success": False, "result": "", "error": f"File not found: {path}"}

        text = path.read_text(encoding="utf-8")

        # Try icalendar library first
        events = self._parse_with_library(text)
        if events is None:
            events = _parse_ics_events(text)

        self._success_count += 1
        return {
            "success": True,
            "result": _format_events(events),
            "error": None,
        }

    def _create_event(self, path: Path, params: dict) -> dict:
        title = params.get("title", "")
        start = params.get("start", "")
        end = params.get("end", "")
        desc = params.get("description", "")

        if not title:
            return {"success": False, "result": "", "error": "No title provided"}
        if not start:
            return {"success": False, "result": "", "error": "No start datetime provided"}
        if not end:
            return {"success": False, "result": "", "error": "No end datetime provided"}

        vevent = _build_vevent(title, start, end, desc)

        if path.exists():
            text = path.read_text(encoding="utf-8")
            # Insert before the last END:VCALENDAR
            if "END:VCALENDAR" in text:
                text = text.replace("END:VCALENDAR", vevent + "END:VCALENDAR")
            else:
                text += "\r\n" + vevent
        else:
            # Create a new .ics file
            path.parent.mkdir(parents=True, exist_ok=True)
            text = (
                "BEGIN:VCALENDAR\r\n"
                "VERSION:2.0\r\n"
                "PRODID:-//Skuld//CalendarSkill//EN\r\n"
                + vevent
                + "END:VCALENDAR\r\n"
            )

        path.write_text(text, encoding="utf-8")

        self._success_count += 1
        return {
            "success": True,
            "result": f"Event '{title}' created in {path}",
            "error": None,
            "artifacts": [str(path)],
        }

    def _export_events(self, path: Path) -> dict:
        if not path.exists():
            return {"success": False, "result": "", "error": f"File not found: {path}"}

        text = path.read_text(encoding="utf-8")
        events = self._parse_with_library(text)
        if events is None:
            events = _parse_ics_events(text)

        self._success_count += 1
        return {
            "success": True,
            "result": _format_events(events),
            "error": None,
        }

    @staticmethod
    def _parse_with_library(text: str) -> Optional[list[dict]]:
        """Try to parse with the icalendar library. Returns None if not installed."""
        try:
            from icalendar import Calendar

            cal = Calendar.from_ical(text)
            events: list[dict] = []
            for component in cal.walk():
                if component.name == "VEVENT":
                    events.append({
                        "SUMMARY": str(component.get("SUMMARY", "")),
                        "DTSTART": str(component.get("DTSTART", "")),
                        "DTEND": str(component.get("DTEND", "")),
                        "DESCRIPTION": str(component.get("DESCRIPTION", "")),
                    })
            return events
        except ImportError:
            return None
        except Exception:
            return None

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }
