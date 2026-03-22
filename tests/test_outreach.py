"""Tests for outreach system — rate limiting, contact tracking, follow-up."""

import asyncio
import time
from unittest.mock import MagicMock, patch

from mimir.skills.outreach import (
    OutreachRateLimiter,
    OutreachTracker,
    OutreachContact,
    FollowUpManager,
)
from mimir.skills.email_skill import EmailSkill


# ── Rate Limiter ──

def test_rate_limiter_allows_initial():
    limiter = OutreachRateLimiter(per_cycle=1, per_domain_per_day=2)
    allowed, reason = limiter.can_send("test@example.com")
    assert allowed is True
    assert reason == "ok"


def test_rate_limiter_cycle_limit():
    limiter = OutreachRateLimiter(per_cycle=1, per_domain_per_day=5)
    limiter.record_send("a@x.com", "sub1")
    allowed, reason = limiter.can_send("b@y.com")
    assert allowed is False
    assert "cycle" in reason


def test_rate_limiter_cycle_reset():
    limiter = OutreachRateLimiter(per_cycle=1, per_domain_per_day=5)
    limiter.record_send("a@x.com", "sub1")
    assert limiter.can_send("b@y.com")[0] is False
    limiter.reset_cycle()
    assert limiter.can_send("b@y.com")[0] is True


def test_rate_limiter_domain_limit():
    limiter = OutreachRateLimiter(per_cycle=100, per_domain_per_day=2)
    limiter.record_send("a@example.com", "s1")
    limiter.reset_cycle()
    limiter.record_send("b@example.com", "s2")
    limiter.reset_cycle()
    allowed, reason = limiter.can_send("c@example.com")
    assert allowed is False
    assert "example.com" in reason
    # Different domain should still be allowed
    allowed2, _ = limiter.can_send("d@other.com")
    assert allowed2 is True


def test_rate_limiter_stats():
    limiter = OutreachRateLimiter(per_cycle=1, per_domain_per_day=2)
    limiter.record_send("a@x.com", "s1")
    stats = limiter.get_stats()
    assert stats["this_cycle"] == 1
    assert stats["last_24h"] == 1
    assert stats["limit_cycle"] == 1


# ── Contact Tracking ──

def test_contact_to_belief_statement():
    contact = OutreachContact(
        name="John", email="john@co.com", org="Co Inc",
        status="contacted", follow_up_count=1,
    )
    stmt = contact.to_belief_statement()
    assert "john@co.com" in stmt
    assert "status=contacted" in stmt
    assert "follow_ups=1/1" in stmt


def test_contact_roundtrip():
    original = OutreachContact(
        name="Alice", email="alice@corp.com", org="Corp",
        status="replied", follow_up_count=0, notes="Interested",
    )
    stmt = original.to_belief_statement()
    parsed = OutreachContact.from_belief_statement(stmt, belief_id="b_123")
    assert parsed is not None
    assert parsed.name == "Alice"
    assert parsed.email == "alice@corp.com"
    assert parsed.org == "Corp"
    assert parsed.status == "replied"
    assert parsed.notes == "Interested"
    assert parsed.belief_id == "b_123"


def test_contact_from_invalid_statement():
    result = OutreachContact.from_belief_statement("Some random belief")
    assert result is None


def test_tracker_no_graph():
    tracker = OutreachTracker(belief_graph=None)
    assert tracker.get_all_contacts() == []
    assert tracker.add_contact("X", "x@x.com") is None


def test_tracker_with_mock_graph():
    """Test tracker with a mock belief graph that supports required methods."""
    mock_bg = MagicMock()
    mock_bg.get_beliefs_by_tag.return_value = []
    mock_bg.add_belief.return_value = "b_001"

    tracker = OutreachTracker(belief_graph=mock_bg)

    # Add contact
    contact = tracker.add_contact("Test User", "test@corp.com", org="Corp")
    assert contact is not None
    assert contact.name == "Test User"
    assert contact.email == "test@corp.com"
    assert contact.belief_id == "b_001"
    mock_bg.add_belief.assert_called_once()


# ── Follow-up Manager ──

def test_followup_no_pending():
    tracker = OutreachTracker(belief_graph=None)
    limiter = OutreachRateLimiter()
    mgr = FollowUpManager(tracker, limiter)
    assert mgr.get_pending_followups() == []


# ── EmailSkill with rate limiter ──

def test_email_skill_rate_limited():
    limiter = OutreachRateLimiter(per_cycle=1, per_domain_per_day=5)
    skill = EmailSkill(rate_limiter=limiter)

    # First send should work
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "test_1"}

    with patch("mimir.skills.email_skill.verify_email", return_value=(True, "test")), \
         patch("mimir.skills.email_skill.httpx.post", return_value=mock_resp):
        result = asyncio.run(skill.execute({
            "to": "a@realuniversity.edu",
            "subject": "First",
            "body": "Hi",
        }))
    assert result["success"] is True

    # Second send should be rate limited
    with patch("mimir.skills.email_skill.verify_email", return_value=(True, "test")):
        result2 = asyncio.run(skill.execute({
            "to": "b@otheruniversity.edu",
            "subject": "Second",
            "body": "Hi again",
        }))
    assert result2["success"] is False
    assert "Rate limit" in result2["error"]


def test_email_skill_no_recipient():
    skill = EmailSkill()
    result = asyncio.run(skill.execute({"to": "", "subject": "X", "body": "Y"}))
    assert result["success"] is False


def test_email_skill_no_subject():
    skill = EmailSkill()
    result = asyncio.run(skill.execute({"to": "a@b.com", "subject": "", "body": "Y"}))
    assert result["success"] is False


def test_email_skill_capabilities():
    skill = EmailSkill()
    assert "send_email" in skill.capabilities
    assert "outreach" in skill.capabilities
    assert skill.risk_level == "dangerous"
