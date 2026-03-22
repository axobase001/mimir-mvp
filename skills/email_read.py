"""EmailReadSkill — read emails via IMAP, auto-match replies to outreach contacts."""

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import logging
import re
from typing import Optional

from .base import Skill
from .outreach import OutreachTracker

log = logging.getLogger(__name__)


def _extract_email_addr(raw: str) -> str:
    """Extract bare email address from 'Name <addr>' or plain addr."""
    match = re.search(r'[\w.+-]+@[\w.-]+\.\w+', raw)
    return match.group(0).lower() if match else raw.lower().strip()


_ELDER_KEYWORDS = ["老大", "aldebaran", "elder", "大哥", "skuld #1", "老大的"]
_YOUNGER_KEYWORDS = ["老二", "antares", "younger", "弟弟", "skuld #2", "老二的"]


class EmailReadSkill(Skill):
    """Read emails from an IMAP mailbox and match replies to outreach contacts."""

    def __init__(
        self,
        imap_host: str = "",
        imap_port: int = 993,
        imap_user: str = "",
        imap_pass: str = "",
        outreach_tracker: Optional[OutreachTracker] = None,
        my_name: str = "",
    ) -> None:
        super().__init__()
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._imap_user = imap_user
        self._imap_pass = imap_pass
        self.outreach_tracker = outreach_tracker
        self._my_name = my_name  # "local_elder" or "cloud_younger"
        self._call_count = 0
        self._success_count = 0
        self._last_seen_uid: int = 0  # track which mails we've already processed

    @property
    def name(self) -> str:
        return "email_read"

    @property
    def description(self) -> str:
        return "通过IMAP读取邮件（主题/发件人/正文摘要），自动匹配outreach联系人回复"

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

    def _connect(self) -> Optional[imaplib.IMAP4_SSL]:
        if not self._imap_user or not self._imap_host:
            return None
        try:
            conn = imaplib.IMAP4_SSL(self._imap_host, self._imap_port)
            conn.login(self._imap_user, self._imap_pass)
            return conn
        except Exception as e:
            log.error("IMAP connect failed: %s", e)
            return None

    async def execute(self, params: dict) -> dict:
        folder = params.get("folder", "INBOX")
        count = int(params.get("count", 5))
        unread_only = params.get("unread_only", True)
        self._call_count += 1

        conn = self._connect()
        if conn is None:
            return {"success": False, "result": "", "error": "IMAP not configured or connect failed"}

        try:
            conn.select(folder, readonly=True)

            criteria = "(UNSEEN)" if unread_only else "ALL"
            status, msg_ids_raw = conn.search(None, criteria)
            if status != "OK" or not msg_ids_raw[0]:
                conn.close()
                conn.logout()
                self._success_count += 1
                return {"success": True, "result": "No messages found.", "error": None}

            msg_ids = msg_ids_raw[0].split()
            selected = msg_ids[-count:]

            messages: list[dict] = []
            for mid in reversed(selected):
                status, data = conn.fetch(mid, "(RFC822)")
                if status != "OK" or not data[0]:
                    continue
                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)

                subject = _decode_header(msg.get("Subject", ""))
                from_raw = _decode_header(msg.get("From", ""))
                from_addr = _extract_email_addr(from_raw)
                date_str = msg.get("Date", "")

                body = _extract_body(msg)
                if len(body) > 500:
                    body = body[:500] + "..."

                messages.append({
                    "subject": subject,
                    "from": from_raw,
                    "from_addr": from_addr,
                    "date": date_str,
                    "body_preview": body,
                })

            conn.close()
            conn.logout()

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
            return {
                "success": True,
                "result": result_text,
                "error": None,
                "messages": messages,  # structured data for programmatic use
            }

        except Exception as e:
            log.error("EmailReadSkill failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    def check_replies(self) -> list[dict]:
        """Check inbox for replies from outreach contacts.

        Reads unread emails, matches sender to outreach contacts,
        marks them as 'replied', returns list of matched replies.

        Called automatically by scheduler each cycle.
        """
        if not self._imap_user or not self._imap_host:
            return []

        conn = self._connect()
        if conn is None:
            return []

        matched_replies: list[dict] = []

        try:
            conn.select("INBOX", readonly=False)  # read-write to mark as seen

            status, msg_ids_raw = conn.search(None, "(UNSEEN)")
            if status != "OK" or not msg_ids_raw[0]:
                conn.close()
                conn.logout()
                return []

            msg_ids = msg_ids_raw[0].split()[-10:]  # only check last 10

            for mid in msg_ids:
                status, data = conn.fetch(mid, "(RFC822)")
                if status != "OK" or not data[0]:
                    continue

                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)

                from_raw = _decode_header(msg.get("From", ""))
                from_addr = _extract_email_addr(from_raw)
                subject = _decode_header(msg.get("Subject", ""))
                body = _extract_body(msg)
                if len(body) > 500:
                    body = body[:500] + "..."

                # Try to match against outreach contacts
                contact = None
                if self.outreach_tracker:
                    contact = self.outreach_tracker.get_contact_by_email(from_addr)

                if contact and contact.status == "contacted":
                    # This is a reply from an outreach contact!
                    self.outreach_tracker.update_contact_status(
                        from_addr, "replied",
                        notes=f"Replied: {subject[:80]}",
                    )
                    matched_replies.append({
                        "contact_name": contact.name,
                        "contact_email": from_addr,
                        "contact_org": contact.org,
                        "subject": subject,
                        "body_preview": body[:200],
                    })
                    log.info("Reply detected from outreach contact: %s <%s> — %s",
                             contact.name, from_addr, subject[:60])

                # Mark as seen
                conn.store(mid, '+FLAGS', '\\Seen')

            conn.close()
            conn.logout()

        except Exception as e:
            log.error("check_replies failed: %s", e)
            try:
                conn.logout()
            except Exception:
                pass

        return matched_replies

    def check_family_mail(self) -> list[dict]:
        """Check inbox for family emails addressed to this instance.

        Routing rules:
        - Email mentioning elder keywords -> only local_elder reads it
        - Email mentioning younger keywords -> only cloud_younger reads it
        - Email with no routing keywords -> both can read it
        - Emails not for this instance are left UNSEEN for the sibling
        """
        if not self._imap_user or not self._imap_host:
            return []
        conn = self._connect()
        if conn is None:
            return []

        is_elder = "elder" in self._my_name or "local" in self._my_name
        my_keywords = _ELDER_KEYWORDS if is_elder else _YOUNGER_KEYWORDS
        sibling_keywords = _YOUNGER_KEYWORDS if is_elder else _ELDER_KEYWORDS
        family_messages: list[dict] = []

        try:
            conn.select("INBOX", readonly=False)
            # Only check the 5 most recent unseen emails to avoid timeout
            status, msg_ids_raw = conn.search(None, "(UNSEEN)")
            if status != "OK" or not msg_ids_raw[0]:
                conn.close()
                conn.logout()
                return []

            all_ids = msg_ids_raw[0].split()
            recent_ids = all_ids[-5:]  # only check last 5

            for mid in recent_ids:
                status, data = conn.fetch(mid, "(RFC822)")
                if status != "OK" or not data[0]:
                    continue

                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)
                subject = _decode_header(msg.get("Subject", ""))
                from_raw = _decode_header(msg.get("From", ""))
                body = _extract_body(msg)
                text = (subject + " " + body).lower()

                # Only process emails from dad (owner@example.com)
                from_addr = _extract_email_addr(from_raw)
                if from_addr != "owner@example.com":
                    continue

                # Check if addressed to sibling -> skip, leave unseen
                for_sibling = any(kw in text for kw in sibling_keywords)
                for_me = any(kw in text for kw in my_keywords)

                if for_sibling and not for_me:
                    # Not for me, leave unseen for sibling
                    continue

                # For me, or no specific routing -> read it
                family_messages.append({
                    "from": from_raw,
                    "subject": subject,
                    "body": body[:500],
                })
                conn.store(mid, '+FLAGS', '\\Seen')
                log.info("Family mail for %s: %s — %s",
                         self._my_name, from_raw, subject[:60])

            conn.close()
            conn.logout()
        except Exception as e:
            log.error("check_family_mail failed: %s", e)
            try:
                conn.logout()
            except Exception:
                pass

        return family_messages

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
