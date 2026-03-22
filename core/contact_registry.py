"""Contact Registry — structured contact storage in the belief graph.

Contacts are stored as FACT beliefs with tag 'contact_registry'.
Statement format is a parseable JSON-like string for structured access.

Status flow: new → ready → sent → replied | bounced | rejected
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger(__name__)


@dataclass
class Contact:
    name: str
    email: str
    institution: str = ""
    field: str = ""
    source_url: str = ""
    status: str = "new"  # new | ready | sent | replied | bounced | rejected
    sent_date: str = ""
    relevance: str = "medium"  # high | medium | low
    belief_id: str = ""

    def to_statement(self) -> str:
        """Serialize to belief statement string."""
        data = {
            "type": "contact_registry",
            "name": self.name,
            "email": self.email,
            "institution": self.institution,
            "field": self.field,
            "source_url": self.source_url,
            "status": self.status,
            "sent_date": self.sent_date,
            "relevance": self.relevance,
        }
        return "CONTACT_REGISTRY: " + json.dumps(data, ensure_ascii=False)

    @staticmethod
    def from_statement(statement: str, belief_id: str = "") -> Optional[Contact]:
        """Parse a belief statement back into a Contact."""
        if not statement.startswith("CONTACT_REGISTRY:"):
            return None
        try:
            raw = statement[len("CONTACT_REGISTRY:"):].strip()
            data = json.loads(raw)
            return Contact(
                name=data.get("name", ""),
                email=data.get("email", ""),
                institution=data.get("institution", ""),
                field=data.get("field", ""),
                source_url=data.get("source_url", ""),
                status=data.get("status", "new"),
                sent_date=data.get("sent_date", ""),
                relevance=data.get("relevance", "medium"),
                belief_id=belief_id,
            )
        except (json.JSONDecodeError, KeyError) as e:
            log.warning("Failed to parse contact registry: %s", e)
            return None


class ContactRegistry:
    """Manages structured contacts in the belief graph."""

    def __init__(self, belief_graph: Any = None) -> None:
        self.belief_graph = belief_graph

    def add_contact(self, contact: Contact) -> Optional[str]:
        """Add a contact to the registry. Returns belief_id."""
        if self.belief_graph is None:
            return None

        # Check for duplicate email
        existing = self.get_by_email(contact.email)
        if existing:
            log.info("Contact already exists: %s <%s>", contact.name, contact.email)
            return existing.belief_id

        from ..dtypes import Belief, BeliefCategory, BeliefSource
        belief = Belief(
            id="",
            statement=contact.to_statement(),
            confidence=0.9,
            source=BeliefSource.OBSERVATION,
            created_at=0, last_updated=0, last_verified=0,
            tags=["contact_registry", f"contact_{contact.status}", f"rel_{contact.relevance}"],
            category=BeliefCategory.FACT,
        )
        bid = self.belief_graph.add_belief(belief)
        contact.belief_id = bid
        log.info("Contact added to registry: %s <%s> → %s", contact.name, contact.email, bid)
        return bid

    def get_by_email(self, email: str) -> Optional[Contact]:
        """Find a contact by email."""
        for contact in self.get_all():
            if contact.email.lower() == email.lower():
                return contact
        return None

    def get_all(self) -> list[Contact]:
        """Get all contacts from the registry."""
        if self.belief_graph is None:
            return []
        contacts = []
        for b in self.belief_graph.get_beliefs_by_tag("contact_registry"):
            contact = Contact.from_statement(b.statement, b.id)
            if contact:
                contacts.append(contact)
        return contacts

    def get_by_status(self, status: str) -> list[Contact]:
        """Get contacts with a specific status."""
        return [c for c in self.get_all() if c.status == status]

    def update_status(self, email: str, new_status: str, sent_date: str = "") -> bool:
        """Update a contact's status."""
        if self.belief_graph is None:
            return False
        for b in self.belief_graph.get_beliefs_by_tag("contact_registry"):
            contact = Contact.from_statement(b.statement, b.id)
            if contact and contact.email.lower() == email.lower():
                contact.status = new_status
                if sent_date:
                    contact.sent_date = sent_date
                self.belief_graph.update_belief_statement(b.id, contact.to_statement())
                # Update tags
                b.tags = [t for t in b.tags if not t.startswith("contact_")]
                b.tags.append(f"contact_{new_status}")
                log.info("Contact %s status → %s", email, new_status)
                return True
        return False

    def get_next_to_contact(self) -> Optional[Contact]:
        """Get the next contact ready to be emailed.

        Priority: ready > new. Within same status, prefer high relevance.
        """
        ready = self.get_by_status("ready")
        if ready:
            # Prefer high relevance
            ready.sort(key=lambda c: {"high": 0, "medium": 1, "low": 2}.get(c.relevance, 1))
            return ready[0]

        new = self.get_by_status("new")
        if new:
            new.sort(key=lambda c: {"high": 0, "medium": 1, "low": 2}.get(c.relevance, 1))
            return new[0]

        return None

    def summary(self) -> dict:
        """Get registry summary stats."""
        all_contacts = self.get_all()
        by_status: dict[str, int] = {}
        for c in all_contacts:
            by_status[c.status] = by_status.get(c.status, 0) + 1
        return {
            "total": len(all_contacts),
            "by_status": by_status,
        }
