"""SiblingMessageSkill — send and receive messages to/from sibling Skuld instance."""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from .base import Skill

log = logging.getLogger(__name__)


class SiblingMessageSkill(Skill):
    """Communicate with sibling Skuld instance via shared mailbox."""

    def __init__(self, my_name: str = "unknown", sibling_url: str = ""):
        super().__init__()
        self.my_name = my_name
        self.sibling_url = sibling_url  # e.g. "http://sibling-host:8000"
        self._call_count = 0

    @property
    def name(self) -> str:
        return "sibling_message"

    @property
    def description(self) -> str:
        return "Send a message to your sibling Skuld instance or check for messages from them"

    @property
    def capabilities(self) -> list[str]:
        return ["send_sibling", "check_sibling", "communicate"]

    @property
    def param_schema(self) -> dict:
        return {
            "action": {"type": "str", "required": True,
                       "description": "send or check"},
            "message": {"type": "str", "required": False,
                        "description": "Message to send (for send action)"},
        }

    @property
    def risk_level(self) -> str:
        return "safe"

    async def execute(self, params: dict) -> dict:
        action = (params.get("action") or "").strip().lower()
        self._call_count += 1

        if action == "send":
            return await self._send(params.get("message", ""))
        elif action == "check":
            return await self._check()
        else:
            return {"success": False, "result": "", "error": f"Unknown action: {action}. Use 'send' or 'check'."}

    async def _send(self, message: str) -> dict:
        if not message:
            return {"success": False, "result": "", "error": "No message to send"}
        if not self.sibling_url:
            return {"success": False, "result": "", "error": "No sibling URL configured"}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self.sibling_url}/api/mailbox",
                    json={"from": self.my_name, "message": message},
                )
                if resp.status_code == 200:
                    log.info("Sibling message sent from %s: %s", self.my_name, message[:60])
                    return {"success": True, "result": f"Message sent to sibling: {message[:100]}", "error": None}
                else:
                    return {"success": False, "result": "", "error": f"Sibling API returned {resp.status_code}"}
        except Exception as e:
            log.warning("Sibling message failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    async def _check(self) -> dict:
        """Check own mailbox for messages from sibling."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Check local mailbox
                resp = await client.get("http://localhost:8000/api/mailbox")
                if resp.status_code == 200:
                    data = resp.json()
                    unread = data.get("messages", [])
                    if not unread:
                        return {"success": True, "result": "No new messages from sibling.", "error": None}

                    # Format messages
                    lines = []
                    for m in unread:
                        lines.append(f"[{m['from']}]: {m['message']}")

                    # Mark as read
                    await client.post("http://localhost:8000/api/mailbox/read")

                    result = "\n".join(lines)
                    log.info("Sibling messages received: %d", len(unread))
                    return {"success": True, "result": result, "error": None}
                else:
                    return {"success": False, "result": "", "error": f"Mailbox returned {resp.status_code}"}
        except Exception as e:
            return {"success": False, "result": "", "error": str(e)}

    @property
    def usage_stats(self) -> dict:
        return {"call_count": self._call_count}
