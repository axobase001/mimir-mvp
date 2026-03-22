"""Outreach system — rate limiting, contact tracking, and follow-up automation.

Tracks all outbound emails, enforces rate limits, and manages follow-up schedules.
Contact state lives in the belief graph as FACT beliefs with tag 'outreach_contact'.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

# ── Rate limits ──

DEFAULT_LIMITS = {
    "per_domain_per_day": 2,
}


@dataclass
class SendRecord:
    to: str
    domain: str
    timestamp: float
    subject: str


class OutreachRateLimiter:
    """Enforce per-cycle (max 1), per-day, and per-domain-per-day send limits."""

    def __init__(
        self,
        per_cycle: int = 1,
        per_domain_per_day: int = DEFAULT_LIMITS["per_domain_per_day"],
    ) -> None:
        self.per_cycle = per_cycle
        self.per_domain_per_day = per_domain_per_day
        self._history: list[SendRecord] = []
        self._cycle_sends: int = 0  # reset each cycle

    def reset_cycle(self) -> None:
        """Call at the start of each cycle to reset per-cycle counter."""
        self._cycle_sends = 0

    def _prune(self) -> None:
        """Remove records older than 24h."""
        cutoff = time.time() - 86400
        self._history = [r for r in self._history if r.timestamp > cutoff]

    def can_send(self, to_addr: str) -> tuple[bool, str]:
        """Check if sending to this address is allowed. Returns (allowed, reason)."""
        self._prune()
        domain = to_addr.split("@")[-1].lower() if "@" in to_addr else ""

        # Per-cycle check
        if self._cycle_sends >= self.per_cycle:
            return False, f"Rate limit: {self._cycle_sends}/{self.per_cycle} emails this cycle"

        # Per-domain-per-day check
        if domain:
            domain_count = sum(1 for r in self._history if r.domain == domain)
            if domain_count >= self.per_domain_per_day:
                return False, f"Rate limit: {domain_count}/{self.per_domain_per_day} emails to {domain} today"

        return True, "ok"

    def record_send(self, to_addr: str, subject: str) -> None:
        """Record a successful send."""
        self._cycle_sends += 1
        domain = to_addr.split("@")[-1].lower() if "@" in to_addr else ""
        self._history.append(SendRecord(
            to=to_addr,
            domain=domain,
            timestamp=time.time(),
            subject=subject,
        ))

    def get_stats(self) -> dict:
        self._prune()
        return {
            "this_cycle": self._cycle_sends,
            "last_24h": len(self._history),
            "limit_cycle": self.per_cycle,
            "limit_domain_day": self.per_domain_per_day,
        }


# ── Contact Tracker (belief graph integration) ──

@dataclass
class OutreachContact:
    """In-memory representation of an outreach contact."""
    name: str
    email: str
    org: str = ""
    status: str = "identified"  # identified → contacted → replied → converted | rejected
    last_contacted: float = 0.0
    follow_up_count: int = 0
    max_follow_ups: int = 1
    notes: str = ""
    belief_id: str = ""  # ID in belief graph

    def to_belief_statement(self) -> str:
        parts = [f"Outreach contact: {self.name} <{self.email}>"]
        if self.org:
            parts.append(f"org={self.org}")
        parts.append(f"status={self.status}")
        if self.last_contacted:
            parts.append(f"last_contacted={int(self.last_contacted)}")
        parts.append(f"follow_ups={self.follow_up_count}/{self.max_follow_ups}")
        if self.notes:
            parts.append(f"notes={self.notes}")
        return " | ".join(parts)

    @staticmethod
    def from_belief_statement(statement: str, belief_id: str = "") -> Optional[OutreachContact]:
        """Parse a belief statement back into an OutreachContact."""
        if not statement.startswith("Outreach contact:"):
            return None
        try:
            parts = [p.strip() for p in statement.split("|")]
            # Parse name and email from first part
            header = parts[0].replace("Outreach contact:", "").strip()
            name = header.split("<")[0].strip()
            email = header.split("<")[1].rstrip(">").strip() if "<" in header else ""

            contact = OutreachContact(name=name, email=email, belief_id=belief_id)
            for part in parts[1:]:
                if part.startswith("org="):
                    contact.org = part[4:]
                elif part.startswith("status="):
                    contact.status = part[7:]
                elif part.startswith("last_contacted="):
                    contact.last_contacted = float(part[15:])
                elif part.startswith("follow_ups="):
                    nums = part[11:].split("/")
                    contact.follow_up_count = int(nums[0])
                    contact.max_follow_ups = int(nums[1]) if len(nums) > 1 else 1
                elif part.startswith("notes="):
                    contact.notes = part[6:]
            return contact
        except Exception as e:
            log.warning("Failed to parse outreach contact: %s", e)
            return None


class OutreachTracker:
    """Track outreach contacts via belief graph.

    Each contact is stored as a FACT belief with tag 'outreach_contact'.
    Status transitions: identified → contacted → replied → converted | rejected
    """

    def __init__(self, belief_graph: Any = None) -> None:
        self.belief_graph = belief_graph

    def add_contact(
        self,
        name: str,
        email: str,
        org: str = "",
        notes: str = "",
    ) -> Optional[OutreachContact]:
        """Add a new outreach contact to the belief graph."""
        if self.belief_graph is None:
            log.warning("No belief graph available for outreach tracking")
            return None

        # Check for duplicate
        existing = self.get_contact_by_email(email)
        if existing:
            log.info("Contact already exists: %s <%s>", name, email)
            return existing

        contact = OutreachContact(
            name=name, email=email, org=org, notes=notes,
        )

        from ..dtypes import Belief, BeliefCategory, BeliefSource
        belief = Belief(
            id="",
            statement=contact.to_belief_statement(),
            confidence=0.8,
            source=BeliefSource.OBSERVATION,
            created_at=0, last_updated=0, last_verified=0,
            tags=["outreach_contact", f"outreach_{email.split('@')[0]}"],
            category=BeliefCategory.FACT,
        )
        bid = self.belief_graph.add_belief(belief)
        contact.belief_id = bid
        log.info("Added outreach contact: %s <%s> → %s", name, email, bid)
        return contact

    def get_contact_by_email(self, email: str) -> Optional[OutreachContact]:
        """Find a contact by email address."""
        if self.belief_graph is None:
            return None
        for b in self.belief_graph.get_beliefs_by_tag("outreach_contact"):
            contact = OutreachContact.from_belief_statement(
                b.statement, belief_id=b.id,
            )
            if contact and contact.email.lower() == email.lower():
                return contact
        return None

    def update_contact_status(
        self,
        email: str,
        status: str,
        notes: str = "",
    ) -> bool:
        """Update the status of a contact. Returns True if found and updated."""
        if self.belief_graph is None:
            return False

        for b in self.belief_graph.get_beliefs_by_tag("outreach_contact"):
            contact = OutreachContact.from_belief_statement(b.statement, b.id)
            if contact and contact.email.lower() == email.lower():
                contact.status = status
                if notes:
                    contact.notes = notes
                if status == "contacted":
                    contact.last_contacted = time.time()
                    contact.follow_up_count += 1

                # Update belief statement
                new_statement = contact.to_belief_statement()
                self.belief_graph.update_belief_statement(b.id, new_statement)
                log.info("Updated contact %s status to %s", email, status)
                return True
        return False

    def get_all_contacts(self) -> list[OutreachContact]:
        """Get all outreach contacts."""
        if self.belief_graph is None:
            return []
        contacts = []
        for b in self.belief_graph.get_beliefs_by_tag("outreach_contact"):
            contact = OutreachContact.from_belief_statement(b.statement, b.id)
            if contact:
                contacts.append(contact)
        return contacts

    def get_contacts_needing_followup(
        self,
        hours_since_last: float = 72.0,
    ) -> list[OutreachContact]:
        """Get contacts that need follow-up (contacted but no reply, past threshold)."""
        now = time.time()
        threshold = hours_since_last * 3600
        results = []
        for contact in self.get_all_contacts():
            if (
                contact.status == "contacted"
                and contact.last_contacted > 0
                and (now - contact.last_contacted) > threshold
                and contact.follow_up_count < contact.max_follow_ups
            ):
                results.append(contact)
        return results


# ── Follow-up Manager ──

class FollowUpManager:
    """Automates follow-up emails based on outreach tracker state."""

    def __init__(
        self,
        tracker: OutreachTracker,
        rate_limiter: OutreachRateLimiter,
        hours_before_followup: float = 72.0,
    ) -> None:
        self.tracker = tracker
        self.rate_limiter = rate_limiter
        self.hours_before_followup = hours_before_followup

    def get_pending_followups(self) -> list[OutreachContact]:
        """Get contacts due for follow-up."""
        return self.tracker.get_contacts_needing_followup(
            hours_since_last=self.hours_before_followup,
        )

    def mark_followed_up(self, email: str) -> None:
        """Mark a contact as having been followed up."""
        self.tracker.update_contact_status(email, "contacted")

    def mark_replied(self, email: str) -> None:
        """Mark a contact as having replied (no more follow-ups)."""
        self.tracker.update_contact_status(email, "replied")
