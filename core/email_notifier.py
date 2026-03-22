"""Email notification system for Skuld.

Uses Resend API — user only needs to provide their email address.
No SMTP configuration needed.

Two trigger types:
1. Scheduled digests (daily/weekly) — sent in the morning
2. Real-time alerts — when Brain discovers something noteworthy
"""

import asyncio
import logging
import json
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, time as dt_time

import httpx

log = logging.getLogger(__name__)

# Resend API key (platform-level, not per-user)
_RESEND_API_KEY = "re_7c4baiG1_899MarieXokjNddNvxKnBBYb"
_RESEND_FROM = "Skuld <skuld@skuldbrain.com>"


@dataclass
class EmailConfig:
    to_addr: str = ""  # user's email — only thing they need to fill
    enabled: bool = False
    daily_digest: bool = True
    weekly_digest: bool = True
    realtime_alerts: bool = True
    digest_hour: int = 8
    # Legacy SMTP fields (kept for backward compat, not used with Resend)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    from_addr: str = ""


class EmailNotifier:
    def __init__(self, config: EmailConfig):
        self.config = config
        self._last_daily: Optional[datetime] = None
        self._last_weekly: Optional[datetime] = None
        self._pending_alerts: list[dict] = []

    def send_email(self, subject: str, html_body: str) -> bool:
        """Send an HTML email via Resend API. Returns True on success."""
        if not self.config.to_addr:
            log.info("No recipient email configured, skipping: %s", subject)
            return False
        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {_RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": _RESEND_FROM,
                    "to": [self.config.to_addr],
                    "subject": subject,
                    "html": html_body,
                    "reply_to": """",
                },
                timeout=15,
            )
            if resp.status_code in (200, 201):
                log.info("Email sent via Resend: %s -> %s", subject, self.config.to_addr)
                return True
            else:
                log.error("Resend API error %d: %s", resp.status_code, resp.text[:200])
                return False
        except Exception as e:
            log.error("Email send failed: %s", e)
            return False

    async def send_email_async(self, subject: str, html_body: str) -> bool:
        """Async wrapper — runs send_email in executor to avoid blocking."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.send_email, subject, html_body)

    def build_daily_digest(self, brain_state: dict) -> tuple[str, str]:
        """Build daily digest email. Returns (subject, html_body)."""
        beliefs = brain_state.get('belief_count', 0)
        cycle = brain_state.get('cycle', 0)
        cost = brain_state.get('cost', 0)
        new_beliefs = brain_state.get('new_beliefs_24h', 0)
        pruned = brain_state.get('pruned_24h', 0)
        sec_top = brain_state.get('sec_top', [])
        goals_active = brain_state.get('active_goals', [])
        discoveries = brain_state.get('discoveries', [])

        subject = f"[Skuld] 每日总结 — {beliefs}条信念 · {new_beliefs}条新增"

        # Build SEC section
        sec_html = ""
        if sec_top:
            sec_items = []
            for s in sec_top[:5]:
                name = s[0] if isinstance(s, (list, tuple)) else s.get('name', '')
                c_val = s[1] if isinstance(s, (list, tuple)) else s.get('c_value', 0)
                sec_items.append(
                    f"<span style='font-family:monospace;color:#0099CC;'>{name}</span> C={c_val:.3f}"
                )
            sec_html = (
                "<div style='padding:16px;background:#FAFAF8;border-radius:8px;margin-bottom:16px;'>"
                "<strong>SEC关注方向</strong><br>"
                + "<br>".join(sec_items)
                + "</div>"
            )

        # Build goals section
        goals_html = ""
        if goals_active:
            goal_items = [f"· {g}" for g in goals_active[:3]]
            goals_html = (
                "<div style='padding:16px;background:#FAFAF8;border-radius:8px;margin-bottom:16px;'>"
                "<strong>活跃目标</strong><br>"
                + "<br>".join(goal_items)
                + "</div>"
            )

        # Build discoveries section
        disc_html = ""
        if discoveries:
            disc_items = [f"<span style='color:#5DCAA5;'>+</span> {d}" for d in discoveries[:5]]
            disc_html = (
                "<div style='padding:16px;background:#FAFAF8;border-radius:8px;margin-bottom:16px;'>"
                "<strong>今日发现</strong><br>"
                + "<br>".join(disc_items)
                + "</div>"
            )

        html = f"""
        <div style="font-family:'Source Sans 3',-apple-system,sans-serif;max-width:600px;margin:0 auto;color:#1A1A1F;line-height:1.6;">
            <div style="padding:20px 0;border-bottom:1px solid rgba(0,0,0,0.06);">
                <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#0099CC;letter-spacing:3px;text-transform:uppercase;">SKULD 每日总结</span>
            </div>

            <div style="padding:20px 0;">
                <table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:20px;">
                    <tr>
                        <td style="padding-right:24px;text-align:center;">
                            <span style="font-family:'JetBrains Mono',monospace;font-size:24px;font-weight:500;">{beliefs}</span><br>
                            <span style="font-size:12px;color:#9494A0;">信念</span>
                        </td>
                        <td style="padding-right:24px;text-align:center;">
                            <span style="font-family:'JetBrains Mono',monospace;font-size:24px;font-weight:500;color:#5DCAA5;">+{new_beliefs}</span><br>
                            <span style="font-size:12px;color:#9494A0;">新增</span>
                        </td>
                        <td style="padding-right:24px;text-align:center;">
                            <span style="font-family:'JetBrains Mono',monospace;font-size:24px;font-weight:500;color:#F0997B;">-{pruned}</span><br>
                            <span style="font-size:12px;color:#9494A0;">修剪</span>
                        </td>
                        <td style="text-align:center;">
                            <span style="font-family:'JetBrains Mono',monospace;font-size:24px;font-weight:500;">${cost:.4f}</span><br>
                            <span style="font-size:12px;color:#9494A0;">成本</span>
                        </td>
                    </tr>
                </table>
            </div>

            {sec_html}
            {goals_html}
            {disc_html}

            <div style="padding:16px 0;font-size:12px;color:#9494A0;border-top:1px solid rgba(0,0,0,0.06);">
                Cycle {cycle} · <a href="http://localhost:8000" style="color:#0099CC;text-decoration:none;">打开Dashboard</a>
            </div>
        </div>
        """
        return subject, html

    def build_weekly_digest(self, brain_state: dict) -> tuple[str, str]:
        """Build weekly digest. Similar to daily but covers 7 days."""
        beliefs = brain_state.get('belief_count', 0)
        subject = f"[Skuld] 周报 — {beliefs}条信念 · 本周概要"
        # Reuse daily template with weekly header
        _, html = self.build_daily_digest(brain_state)
        html = html.replace('每日总结', '本周总结')
        return subject, html

    def build_realtime_alert(self, alert: dict) -> tuple[str, str]:
        """Build real-time alert email for a discovery."""
        title = alert.get('title', 'Skuld发现了新信息')
        body = alert.get('body', '')
        belief = alert.get('belief', '')
        confidence = alert.get('confidence', 0)

        subject = f"[Skuld] {title}"

        belief_html = ""
        if belief:
            belief_html = (
                f"<div style='padding:12px 16px;background:#FAFAF8;border-radius:8px;"
                f"border-left:3px solid #5DCAA5;margin-top:12px;'>"
                f"<strong>新信念</strong>（置信度 {confidence:.2f}）<br>{belief}</div>"
            )

        html = f"""
        <div style="font-family:'Source Sans 3',-apple-system,sans-serif;max-width:600px;margin:0 auto;color:#1A1A1F;line-height:1.6;">
            <div style="padding:20px 0;border-bottom:1px solid rgba(0,0,0,0.06);">
                <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#F0997B;letter-spacing:3px;text-transform:uppercase;">SKULD 实时发现</span>
            </div>
            <div style="padding:20px 0;">
                <h2 style="font-size:18px;font-weight:400;margin:0 0 12px 0;">{title}</h2>
                <p style="margin:0;">{body}</p>
                {belief_html}
            </div>
            <div style="padding:16px 0;font-size:12px;color:#9494A0;border-top:1px solid rgba(0,0,0,0.06);">
                <a href="http://localhost:8000" style="color:#0099CC;text-decoration:none;">打开Dashboard</a>
            </div>
        </div>
        """
        return subject, html

    def queue_alert(self, alert: dict) -> None:
        """Queue a real-time alert for sending."""
        self._pending_alerts.append(alert)

    async def flush_alerts(self) -> int:
        """Send all pending alerts. Returns number sent."""
        sent = 0
        while self._pending_alerts:
            alert = self._pending_alerts.pop(0)
            subject, html = self.build_realtime_alert(alert)
            if await self.send_email_async(subject, html):
                sent += 1
        return sent

    def should_send_daily(self) -> bool:
        """Check if daily digest should be sent now."""
        if not self.config.daily_digest:
            return False
        now = datetime.now()
        if now.hour != self.config.digest_hour:
            return False
        if self._last_daily and self._last_daily.date() == now.date():
            return False
        return True

    def should_send_weekly(self) -> bool:
        """Check if weekly digest should be sent now (Monday morning)."""
        if not self.config.weekly_digest:
            return False
        now = datetime.now()
        if now.weekday() != 0 or now.hour != self.config.digest_hour:
            return False
        if self._last_weekly and (now - self._last_weekly).days < 6:
            return False
        return True

    def mark_daily_sent(self):
        self._last_daily = datetime.now()

    def mark_weekly_sent(self):
        self._last_weekly = datetime.now()
