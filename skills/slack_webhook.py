"""SlackWebhookSkill — send messages to Slack via incoming webhooks."""

from __future__ import annotations

import json
import logging

import httpx

from .base import Skill, SkillResult

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0


class SlackWebhookSkill(Skill):
    """Send messages to Slack channels via incoming webhook URLs."""

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        super().__init__()
        self._timeout = timeout
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "slack_webhook"

    @property
    def description(self) -> str:
        return "通过Webhook URL发送消息到Slack频道"

    @property
    def capabilities(self) -> list[str]:
        return ["send_message", "notify_team", "slack_notification"]

    @property
    def param_schema(self) -> dict:
        return {
            "webhook_url": {"type": "str", "required": True,
                            "description": "Slack incoming webhook URL"},
            "message": {"type": "str", "required": True,
                        "description": "Message text to send"},
            "channel": {"type": "str", "required": False,
                        "description": "Override channel (if webhook supports it)"},
            "username": {"type": "str", "required": False,
                         "description": "Override bot username"},
        }

    @property
    def risk_level(self) -> str:
        return "review"

    async def execute(self, params: dict) -> dict:
        webhook_url = params.get("webhook_url", "")
        message = params.get("message", "")
        channel = params.get("channel", "")
        username = params.get("username", "")
        self._call_count += 1

        if not webhook_url:
            return {"success": False, "result": "", "error": "No webhook_url provided"}
        if not message:
            return {"success": False, "result": "", "error": "No message provided"}

        payload: dict = {"text": message}
        if channel:
            payload["channel"] = channel
        if username:
            payload["username"] = username

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()

            self._success_count += 1
            return {
                "success": True,
                "result": f"Message sent to Slack ({len(message)} chars)",
                "error": None,
            }

        except httpx.TimeoutException:
            return {"success": False, "result": "",
                    "error": f"Timeout after {self._timeout}s"}
        except httpx.HTTPStatusError as e:
            return {"success": False, "result": "",
                    "error": f"HTTP {e.response.status_code}: {e.response.text[:500]}"}
        except Exception as e:
            log.error("SlackWebhookSkill failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }
