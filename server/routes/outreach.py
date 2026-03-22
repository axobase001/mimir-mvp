"""API routes for outreach management — contacts, rate limits, follow-ups."""

from fastapi import APIRouter, Request, HTTPException

router = APIRouter()


def _get_outreach(request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    scheduler = request.app.state.scheduler
    state = scheduler.get_brain_state(user_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Brain not initialized")

    tracker = state.get("outreach_tracker")
    limiter = state.get("outreach_limiter")
    followup = state.get("follow_up_mgr")
    return user_id, tracker, limiter, followup


@router.get("/api/outreach/contacts")
async def list_contacts(request: Request):
    """List all outreach contacts."""
    user_id, tracker, limiter, followup = _get_outreach(request)
    if tracker is None:
        return {"contacts": []}

    contacts = tracker.get_all_contacts()
    return {
        "contacts": [
            {
                "name": c.name,
                "email": c.email,
                "org": c.org,
                "status": c.status,
                "last_contacted": c.last_contacted,
                "follow_up_count": c.follow_up_count,
                "max_follow_ups": c.max_follow_ups,
                "belief_id": c.belief_id,
            }
            for c in contacts
        ]
    }


@router.post("/api/outreach/contacts")
async def add_contact(request: Request):
    """Add a new outreach contact."""
    user_id, tracker, limiter, followup = _get_outreach(request)
    if tracker is None:
        raise HTTPException(status_code=500, detail="Outreach tracker not available")

    data = await request.json()
    name = data.get("name", "")
    email = data.get("email", "")
    org = data.get("org", "")
    notes = data.get("notes", "")

    if not name or not email:
        raise HTTPException(status_code=400, detail="name and email required")

    contact = tracker.add_contact(name=name, email=email, org=org, notes=notes)
    if contact is None:
        raise HTTPException(status_code=500, detail="Failed to add contact")

    return {
        "action": "added",
        "contact": {
            "name": contact.name,
            "email": contact.email,
            "org": contact.org,
            "status": contact.status,
            "belief_id": contact.belief_id,
        },
    }


@router.patch("/api/outreach/contacts/{email}")
async def update_contact(email: str, request: Request):
    """Update a contact's status."""
    user_id, tracker, limiter, followup = _get_outreach(request)
    if tracker is None:
        raise HTTPException(status_code=500, detail="Outreach tracker not available")

    data = await request.json()
    status = data.get("status", "")
    notes = data.get("notes", "")

    if not status:
        raise HTTPException(status_code=400, detail="status required")

    ok = tracker.update_contact_status(email, status, notes)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Contact {email} not found")

    return {"action": "updated", "email": email, "status": status}


@router.get("/api/outreach/rate-limits")
async def get_rate_limits(request: Request):
    """Get current rate limit stats."""
    user_id, tracker, limiter, followup = _get_outreach(request)
    if limiter is None:
        return {"stats": {}}
    return {"stats": limiter.get_stats()}


@router.get("/api/outreach/followups")
async def get_pending_followups(request: Request):
    """Get contacts pending follow-up."""
    user_id, tracker, limiter, followup = _get_outreach(request)
    if followup is None:
        return {"pending": []}

    pending = followup.get_pending_followups()
    return {
        "pending": [
            {
                "name": c.name,
                "email": c.email,
                "org": c.org,
                "last_contacted": c.last_contacted,
                "follow_up_count": c.follow_up_count,
            }
            for c in pending
        ]
    }


@router.get("/api/outreach/registry")
async def get_registry(request: Request):
    """Get contact registry."""
    user_id, tracker, limiter, followup = _get_outreach(request)

    scheduler = request.app.state.scheduler
    state = scheduler.get_brain_state(user_id)
    if state is None:
        return {"registry": [], "summary": {}}

    registry = state.get("contact_registry")
    if registry is None:
        return {"registry": [], "summary": {}}

    contacts = registry.get_all()
    return {
        "summary": registry.summary(),
        "registry": [
            {
                "name": c.name, "email": c.email,
                "institution": c.institution, "field": c.field,
                "status": c.status, "relevance": c.relevance,
                "sent_date": c.sent_date, "belief_id": c.belief_id,
            }
            for c in contacts
        ],
    }
