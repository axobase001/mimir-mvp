"""EmailReadSkill — read emails via IMAP (imaplib, stdlib)."""

from __future__ import annotations

import email
import email.header
import imaplib
import logging
from typing import Optional

from .base import Skill

log = logging.getLogger(__name__)


class EmailReadSkill(Skill):
    """Read emails from an IMAP mailbox."""

    def __init__(
        self,
        imap_host: str = "localhost",
        imap_port: int = 993,
        imap_user: str = "",
        imap_pass: str = "",
    ) -> None:
        super().__init__()
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._imap_user = imap_user
        self._imap_pass = imap_pass
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "email_read"

    @property
    def description(self) -> str:
        return "通过IMAP读取邮件（主题/发件人/正文摘要）"

    @property
    def capabilities(self) -> list[str]:
        return ["read_email", "check_inbox", "fetch_messages"]

    @property
    def param_schema(self) -> dict:
        return {
            "folder": {"type": "str", "required": False, "default": "INBOX",
                        "description": "Mailbox folder to read from"},
            "count": {"type": "int", "required": False, "default": 5,
                       "description": "Number of recent messages to fetch"},
            "unread_only": {"type": "bool", "required": False, "default": True,
                             "description": "Only fetch unread messages"},
        }

    @property
    def risk_level(self) -> str:
        return "safe"

    async def execute(self, params: dict) -> dict:
        folder = params.get("folder", "INBOX")
        count = int(params.get("count", 5))
        unread_only = params.get("unread_only", True)
        self._call_count += 1

        if not self._imap_user:
            return {"success": False, "result": "", "error": "IMAP user not configured"}

        try:
            conn = imaplib.IMAP4_SSL(self._imap_host, self._imap_port)
            conn.login(self._imap_user, self._imap_pass)
            conn.select(folder, readonly=True)

            criteria = "(UNSEEN)" if unread_only else "ALL"
            status, msg_ids_raw = conn.search(None, criteria)
            if status != "OK" or not msg_ids_raw[0]:
                conn.close()
                conn.logout()
                self._success_count += 1
                return {"success": True, "result": "No messages found.", "error": None}

            msg_ids = msg_ids_raw[0].split()
            # Take the last N (most recent)
            selected = msg_ids[-count:]

            messages: list[dict] = []
            for mid in reversed(selected):
                status, data = conn.fetch(mid, "(RFC822)")
                if status != "OK" or not data[0]:
                    continue
                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)

                subject = _decode_header(msg.get("Subject", ""))
                from_addr = _decode_header(msg.get("From", ""))
                date_str = msg.get("Date", "")

                body = _extract_body(msg)
                # Truncate body to 500 chars
                if len(body) > 500:
                    body = body[:500] + "..."

                messages.append({
                    "subject": subject,
                    "from": from_addr,
                    "date": date_str,
                    "body_preview": body,
                })

            conn.close()
            conn.logout()

            # Format as readable text
            lines: list[str] = []
            for i, m in enumerate(messages, 1):
                lines.append(
                    f"[{i}] From: {m['from']}\n"
                    f"    Subject: {m['subject']}\n"
                    f"    Date: {m['date']}\n"
                    f"    Preview: {m['body_preview'][:200]}\n"
                )

            result_text = "\n".join(lines) if lines else "No messages found."
            self._success_count += 1
            return {"success": True, "result": result_text, "error": None}

        except Exception as e:
            log.error("EmailReadSkill failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }


def _decode_header(value: str) -> str:
    """Decode an RFC2047-encoded header value."""
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _extract_body(msg: email.message.Message) -> str:
    """Extract plain text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fallback: try text/html
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        return ""
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        return ""
