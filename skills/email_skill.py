"""EmailSkill — send emails via SMTP (aiosmtplib)."""

from __future__ import annotations

import logging
from email.message import EmailMessage

import aiosmtplib

from .base import Skill, SkillResult

log = logging.getLogger(__name__)


class EmailSkill(Skill):
    """Send emails via SMTP using aiosmtplib."""

    def __init__(
        self,
        smtp_host: str = "localhost",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_pass: str = "",
        use_tls: bool = True,
    ) -> None:
        super().__init__()
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user
        self._smtp_pass = smtp_pass
        self._use_tls = use_tls
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "email"

    @property
    def description(self) -> str:
        return "通过SMTP发送邮件"

    @property
    def capabilities(self) -> list[str]:
        return ["send_email", "notify", "communicate"]

    @property
    def param_schema(self) -> dict:
        return {
            "to": {"type": "str", "required": True, "description": "Recipient email address"},
            "subject": {"type": "str", "required": True, "description": "Email subject"},
            "body": {"type": "str", "required": True, "description": "Email body text"},
            "from_addr": {"type": "str", "required": False, "description": "Sender address (defaults to smtp_user)"},
        }

    @property
    def risk_level(self) -> str:
        return "dangerous"

    async def execute(self, params: dict) -> dict:
        to_addr = params.get("to", "")
        subject = params.get("subject", "")
        body = params.get("body", "")
        from_addr = params.get("from_addr", self._smtp_user)
        self._call_count += 1

        if not to_addr:
            return {"success": False, "result": "", "error": "No recipient specified"}
        if not subject:
            return {"success": False, "result": "", "error": "No subject specified"}

        log.warning("EmailSkill: sending email to %s (subject: %s)", to_addr, subject)

        try:
            msg = EmailMessage()
            msg["From"] = from_addr
            msg["To"] = to_addr
            msg["Subject"] = subject
            msg.set_content(body)

            await aiosmtplib.send(
                msg,
                hostname=self._smtp_host,
                port=self._smtp_port,
                username=self._smtp_user or None,
                password=self._smtp_pass or None,
                start_tls=self._use_tls,
            )

            self._success_count += 1
            return {
                "success": True,
                "result": f"Email sent to {to_addr}",
                "error": None,
            }

        except Exception as e:
            log.error("EmailSkill failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }
