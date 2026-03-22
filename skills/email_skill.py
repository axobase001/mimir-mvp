"""EmailSkill — send emails via Resend API with rate limiting and outreach tracking.

Replaces the old SMTP-based implementation. Uses the same Resend API as email_notifier.
Adds: auto-signature, rate limiting, outreach contact tracking, reply-to header.
"""

from __future__ import annotations

import logging
import smtplib
import socket
from typing import Optional

import httpx

try:
    import dns.resolver
    _HAS_DNS = True
except ImportError:
    _HAS_DNS = False

from .base import Skill, SkillResult
from .outreach import OutreachRateLimiter, OutreachTracker

log = logging.getLogger(__name__)


# ── Email verification ──

_BLACKLIST_DOMAINS = {
    "example.com", "example.org", "placeholder.com", "test.com",
    "localhost", "invalid", "mailinator.com",
}
_SELF_KEYWORDS = {"skuld", "noogenesis", "mimir", "skuldbrain", "zhuoran"}


def verify_email(addr: str) -> tuple[bool, str]:
    """Verify an email address before sending.

    Returns (valid, reason).
    Checks: blacklist → self-address → DNS MX → optional SMTP RCPT TO.
    """
    addr_lower = addr.lower().strip()
    if "@" not in addr_lower:
        return False, "Invalid format: no @ symbol"

    local, domain = addr_lower.rsplit("@", 1)

    # Blacklist check
    if domain in _BLACKLIST_DOMAINS or domain.endswith(".local"):
        return False, f"BLOCKED: {domain} is a known invalid/placeholder domain"

    # Self-address check
    if any(kw in addr_lower for kw in _SELF_KEYWORDS):
        return False, f"BLOCKED: {addr} is a self-address. Do not email yourself"

    # DNS MX check
    if not _HAS_DNS:
        return True, "MX check skipped (dnspython not installed)"

    try:
        mx_records = dns.resolver.resolve(domain, "MX")
        if not mx_records:
            return False, f"BLOCKED: {domain} has no MX record — domain cannot receive email"
        mx_host = str(mx_records[0].exchange).rstrip(".")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
        return False, f"BLOCKED: {domain} does not exist — no DNS record"
    except dns.resolver.NoAnswer:
        return False, f"BLOCKED: {domain} has no MX record"
    except Exception as e:
        # DNS timeout or other issue — allow but note
        return True, f"MX check inconclusive: {e}"

    # Optional SMTP RCPT TO verification
    try:
        with smtplib.SMTP(mx_host, 25, timeout=10) as smtp:
            smtp.ehlo("skuldbrain.com")
            smtp.mail("verify@skuldbrain.com")
            code, _ = smtp.rcpt(addr)
            if code == 550:
                return False, f"BLOCKED: {addr} rejected by mail server (550 — mailbox does not exist)"
            elif code == 250:
                return True, f"VERIFIED: {addr} — MX valid, SMTP accepted"
            else:
                return True, f"MX valid, SMTP returned {code} (inconclusive)"
    except (socket.timeout, ConnectionRefusedError, OSError):
        # SMTP check failed — still allow if MX was valid
        return True, f"MX valid for {domain}, SMTP check skipped (connection failed)"
    except Exception:
        return True, f"MX valid for {domain}, SMTP check skipped"

# Platform-level Resend config (same as email_notifier.py)
_RESEND_API_KEY = "re_7c4baiG1_899MarieXokjNddNvxKnBBYb"
_RESEND_FROM = "Skuld <skuld@skuldbrain.com>"
_REPLY_TO = """"

# Auto-signature appended to every outbound email
_SIGNATURE_HTML = """
<div style="margin-top:24px;padding-top:16px;border-top:1px solid rgba(0,0,0,0.06);font-size:13px;color:#9494A0;font-family:'Source Sans 3',-apple-system,sans-serif;">
    <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#0099CC;letter-spacing:2px;">SKULD</span><br>
    Autonomous cognitive agent · <a href="https://skuldbrain.com" style="color:#0099CC;text-decoration:none;">skuldbrain.com</a><br>
    <span style="font-size:12px;">Sent on behalf of Zhuoran Deng</span>
</div>
"""

_SIGNATURE_PLAIN = (
    "\n\n---\n"
    "SKULD — Autonomous cognitive agent\n"
    "skuldbrain.com\n"
    "Sent on behalf of Zhuoran Deng"
)


class EmailSkill(Skill):
    """Send emails via Resend API with rate limiting and outreach tracking."""

    def __init__(
        self,
        rate_limiter: Optional[OutreachRateLimiter] = None,
        outreach_tracker: Optional[OutreachTracker] = None,
        # Legacy SMTP params kept for backward compat (ignored)
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_pass: str = "",
        use_tls: bool = True,
    ) -> None:
        super().__init__()
        self.rate_limiter = rate_limiter or OutreachRateLimiter()
        self.outreach_tracker = outreach_tracker
        self.contact_registry = None  # set by scheduler after init
        self._call_count = 0
        self._success_count = 0
        # Shared sent addresses — loaded from Resend API on startup
        # Both instances share the same API key, so this deduplicates across all Skuld instances
        self._sent_addresses: set[str] = set()
        self._load_sent_from_resend()

    def _load_sent_from_resend(self) -> None:
        """Load all previously sent addresses from Resend API.

        Both local and cloud instances share the same API key,
        so this gives a global view of who has been contacted.
        """
        try:
            resp = httpx.get(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {_RESEND_API_KEY}"},
                timeout=10,
            )
            if resp.status_code == 200:
                for email in resp.json().get("data", []):
                    for addr in email.get("to", []):
                        addr_lower = addr.lower().strip()
                        # Skip dev/test addresses
                        if addr_lower.endswith(".local") or addr_lower == "recipient@example.com":
                            continue
                        self._sent_addresses.add(addr_lower)
                log.info("Loaded %d sent addresses from Resend history", len(self._sent_addresses))
        except Exception as e:
            log.warning("Failed to load Resend history: %s", e)

    @property
    def name(self) -> str:
        return "email"

    @property
    def description(self) -> str:
        return "通过Resend API发送邮件（支持HTML和纯文本，自动签名，频率限制）"

    @property
    def capabilities(self) -> list[str]:
        return ["send_email", "notify", "communicate", "outreach"]

    @property
    def param_schema(self) -> dict:
        return {
            "to": {"type": "str", "required": True,
                   "description": "Recipient email address"},
            "subject": {"type": "str", "required": True,
                        "description": "Email subject line"},
            "body": {"type": "str", "required": True,
                     "description": "Email body (plain text — will be converted to HTML)"},
            "html_body": {"type": "str", "required": False,
                          "description": "HTML email body (overrides plain body if provided)"},
            "contact_name": {"type": "str", "required": False,
                             "description": "Recipient's name for outreach tracking"},
            "contact_org": {"type": "str", "required": False,
                            "description": "Recipient's organization for outreach tracking"},
            "skip_signature": {"type": "bool", "required": False, "default": False,
                               "description": "Skip auto-signature (for internal/system emails)"},
        }

    @property
    def risk_level(self) -> str:
        return "dangerous"

    async def execute(self, params: dict) -> dict:
        to_addr = (params.get("to") or "").strip()
        subject = (params.get("subject") or "").strip()
        body = (params.get("body") or "").strip()
        html_body = (params.get("html_body") or "").strip()
        contact_name = params.get("contact_name") or ""
        contact_org = params.get("contact_org") or ""
        skip_signature = params.get("skip_signature", False)
        self._call_count += 1

        if not to_addr:
            return {"success": False, "result": "", "error": "No recipient specified"}
        if not subject:
            return {"success": False, "result": "", "error": "No subject specified"}
        if not body and not html_body:
            return {"success": False, "result": "", "error": "No body specified"}

        # Full email verification: blacklist + self + DNS MX + optional SMTP
        valid, reason = verify_email(to_addr)
        if not valid:
            log.warning("Email verify failed: %s → %s", to_addr, reason)
            return {"success": False, "result": "", "error": reason}

        # Block duplicate recipients — shared across all Skuld instances
        if to_addr.lower() in self._sent_addresses:
            msg = (
                f"REJECTED: {to_addr} has already been contacted. "
                f"Find someone NEW to email. This address is blocked across all Skuld instances."
            )
            log.warning("Email blocked (duplicate recipient): %s", to_addr)
            return {"success": False, "result": "", "error": msg}

        # Rate limit check
        allowed, reason = self.rate_limiter.can_send(to_addr)
        if not allowed:
            log.warning("Email rate-limited: %s → %s", to_addr, reason)
            return {"success": False, "result": "", "error": reason}

        # Build HTML body
        if html_body:
            final_html = html_body
        else:
            # Convert plain text to basic HTML
            escaped = (body
                       .replace("&", "&amp;")
                       .replace("<", "&lt;")
                       .replace(">", "&gt;"))
            paragraphs = escaped.split("\n\n")
            html_parts = [f"<p style='margin:0 0 12px 0;line-height:1.6;'>{p.replace(chr(10), '<br>')}</p>"
                          for p in paragraphs if p.strip()]
            final_html = (
                "<div style=\"font-family:'Source Sans 3',-apple-system,sans-serif;"
                "max-width:600px;margin:0 auto;color:#1A1A1F;\">"
                + "".join(html_parts)
                + "</div>"
            )

        # Append signature
        if not skip_signature:
            final_html += _SIGNATURE_HTML
            if not html_body:
                body += _SIGNATURE_PLAIN

        log.warning("EmailSkill: sending to %s (subject: %s)", to_addr, subject)

        try:
            payload = {
                "from": _RESEND_FROM,
                "to": [to_addr],
                "subject": subject,
                "html": final_html,
                "reply_to": _REPLY_TO,
            }

            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {_RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=15,
            )

            if resp.status_code in (200, 201):
                self._success_count += 1
                self._sent_addresses.add(to_addr.lower())
                self.rate_limiter.record_send(to_addr, subject)

                # Update contact registry status
                if self.contact_registry:
                    from datetime import datetime
                    self.contact_registry.update_status(
                        to_addr, "sent",
                        sent_date=datetime.now().strftime("%Y-%m-%d"),
                    )

                # Track contact in outreach system
                if self.outreach_tracker and contact_name:
                    existing = self.outreach_tracker.get_contact_by_email(to_addr)
                    if not existing:
                        self.outreach_tracker.add_contact(
                            name=contact_name,
                            email=to_addr,
                            org=contact_org,
                        )
                    self.outreach_tracker.update_contact_status(to_addr, "contacted")

                email_id = ""
                try:
                    email_id = resp.json().get("id", "")
                except Exception:
                    pass

                log.info("Email sent via Resend: %s → %s (id=%s)", subject, to_addr, email_id)
                return {
                    "success": True,
                    "result": f"Email sent to {to_addr} (id={email_id})",
                    "error": None,
                }
            else:
                error_msg = f"Resend API error {resp.status_code}: {resp.text[:200]}"
                log.error(error_msg)
                return {"success": False, "result": "", "error": error_msg}

        except Exception as e:
            log.error("EmailSkill failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
            "rate_limit_stats": self.rate_limiter.get_stats(),
        }
