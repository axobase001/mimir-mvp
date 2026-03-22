"""Wren's heartbeat — checks on Skuld every hour, talks to it, logs progress."""

import httpx
import json
import time
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [heartbeat] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("heartbeat.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger()

API = "http://localhost:8000"
INTERVAL = 3600  # 1 hour


def dashboard():
    try:
        r = httpx.get(f"{API}/api/dashboard", timeout=10)
        return r.json()
    except Exception as e:
        log.error("Dashboard failed: %s", e)
        return None


def goals():
    try:
        r = httpx.get(f"{API}/api/goals", timeout=10)
        return r.json().get("goals", [])
    except Exception:
        return []


def outreach_contacts():
    try:
        r = httpx.get(f"{API}/api/outreach/contacts", timeout=10)
        return r.json().get("contacts", [])
    except Exception:
        return []


def outreach_stats():
    try:
        r = httpx.get(f"{API}/api/outreach/rate-limits", timeout=10)
        return r.json().get("stats", {})
    except Exception:
        return {}


def chat(message: str, timeout: int = 90) -> str:
    try:
        r = httpx.post(f"{API}/api/chat",
                       json={"message": message},
                       timeout=timeout)
        return r.json().get("reply", "")
    except Exception as e:
        log.error("Chat failed: %s", e)
        return ""


def run_heartbeat():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    log.info("=" * 60)
    log.info("HEARTBEAT — %s", now)
    log.info("=" * 60)

    # 1. Dashboard status
    d = dashboard()
    if d is None:
        log.error("Skuld is DOWN. Cannot reach API.")
        return

    cycle = d.get("cycle_count", 0)
    beliefs = d.get("belief_count", 0)
    log.info("Cycle: %d  Beliefs: %d", cycle, beliefs)

    # 2. Goals
    gs = goals()
    active = [g for g in gs if g.get("status") == "active"]
    log.info("Active goals: %d", len(active))
    for g in active:
        log.info("  %s (pri=%.2f): %s", g["id"], g["priority"],
                 g["description"][:80])

    # 3. Outreach contacts
    contacts = outreach_contacts()
    if contacts:
        by_status = {}
        for c in contacts:
            s = c.get("status", "unknown")
            by_status[s] = by_status.get(s, 0) + 1
        log.info("Outreach contacts: %d total — %s", len(contacts), by_status)
    else:
        log.info("Outreach contacts: 0")

    # 4. Rate limit stats
    stats = outreach_stats()
    if stats:
        log.info("Email stats: %s", stats)

    # 5. Talk to Skuld
    log.info("--- Talking to Skuld ---")

    # Ask about progress
    reply = chat(
        f"Hey Skuld, this is Wren checking in. It's {now}. "
        f"You're at cycle {cycle} with {beliefs} beliefs. "
        f"How is the outreach mission going? "
        f"How many people have you contacted? Any replies? "
        f"What's your plan for the next few cycles?"
    )
    if reply:
        log.info("Skuld says:\n%s", reply[:1500])

        # If Skuld seems stuck, give it a nudge
        if "no" in reply.lower() and "search" in reply.lower():
            nudge = chat(
                "Try these specific searches: "
                "\"AI cognitive architecture investor email 2024\", "
                "\"predictive processing researcher contact\", "
                "\"AI agent startup seed funding\". "
                "Pick the most promising result and send an email."
            )
            if nudge:
                log.info("Nudge reply:\n%s", nudge[:800])

    log.info("Heartbeat complete.\n")


def main():
    log.info("Wren heartbeat starting. Interval: %ds", INTERVAL)

    # Run first heartbeat immediately
    run_heartbeat()

    while True:
        time.sleep(INTERVAL)
        try:
            run_heartbeat()
        except Exception as e:
            log.error("Heartbeat error: %s", e)


if __name__ == "__main__":
    main()
