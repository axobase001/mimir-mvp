"""Sibling mailbox — async messaging between Skuld instances."""

import json
import time
import logging
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException

log = logging.getLogger(__name__)
router = APIRouter()

MAILBOX_FILE = Path("data/sibling_mailbox.json")


def _load_mailbox() -> list:
    if MAILBOX_FILE.exists():
        try:
            return json.loads(MAILBOX_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_mailbox(messages: list):
    MAILBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
    MAILBOX_FILE.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")


@router.post("/api/mailbox")
async def post_message(request: Request):
    """Post a message to the sibling mailbox."""
    data = await request.json()
    sender = data.get("from", "unknown")
    message = data.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="message required")

    messages = _load_mailbox()
    entry = {
        "from": sender,
        "message": message,
        "timestamp": time.time(),
        "read": False,
    }
    messages.append(entry)
    _save_mailbox(messages)
    log.info("Mailbox: message from %s (%d chars)", sender, len(message))
    return {"action": "posted", "total": len(messages)}


@router.get("/api/mailbox")
async def get_messages(request: Request):
    """Get unread messages from the sibling mailbox."""
    messages = _load_mailbox()
    unread = [m for m in messages if not m.get("read")]
    return {"unread": len(unread), "messages": unread}


@router.post("/api/mailbox/read")
async def mark_read(request: Request):
    """Mark all messages as read."""
    messages = _load_mailbox()
    for m in messages:
        m["read"] = True
    _save_mailbox(messages)
    return {"action": "marked_read", "count": len(messages)}


@router.get("/api/mailbox/all")
async def get_all_messages(request: Request):
    """Get all messages (read and unread)."""
    messages = _load_mailbox()
    return {"total": len(messages), "messages": messages}
