"""
Skuld — Brain-first AI cognitive system (multi-user).

Usage:
    python -m mimir.main --config config.json
    python -m mimir.main --config config.json --port 8000
"""

import argparse
import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn

from .config import MimirConfig
from .storage.user_db import UserDB
from .storage.brain_store import BrainStore
from .core.scheduler import BrainScheduler
from .server.app import app
from .server.routes.ws import manager as ws_manager
from .server.auth.jwt import configure as configure_jwt

log = logging.getLogger("mimir")


def build_config(raw: dict) -> MimirConfig:
    valid_fields = {f.name for f in MimirConfig.__dataclass_fields__.values()}
    filtered = {k: v for k, v in raw.items() if k in valid_fields}
    return MimirConfig(**filtered)


_should_stop = False


async def main_async(args: argparse.Namespace) -> None:
    global _should_stop

    config_data = json.loads(Path(args.config).read_text(encoding="utf-8"))
    config = build_config(config_data)

    # Override config from environment variables
    jwt_secret = os.environ.get("JWT_SECRET", config.jwt_secret or "skuld-dev-secret-change-me")
    config.jwt_secret = jwt_secret
    config.default_llm_key = os.environ.get("LLM_API_KEY", config.default_llm_key or config.llm_api_key)
    config.default_brave_key = os.environ.get("BRAVE_API_KEY", config.default_brave_key or config.brave_api_key)

    # Configure JWT
    configure_jwt(config.jwt_secret, config.jwt_expire_hours)

    # Initialize storage
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    user_db = UserDB(
        db_path=str(data_dir / "users.db"),
        jwt_secret=config.jwt_secret,
    )
    brain_store = BrainStore(base_dir=str(data_dir / "brains"))

    # Initialize scheduler
    scheduler = BrainScheduler(
        config=config,
        user_db=user_db,
        brain_store=brain_store,
        ws_manager=ws_manager,
    )

    # Attach to app.state
    app.state.user_db = user_db
    app.state.brain_store = brain_store
    app.state.scheduler = scheduler
    app.state.config = config
    app.state.max_users = config.max_users

    # ── DEV MODE: auto-create user + brain, bypass auth ──
    seed_beliefs = config_data.get("seed_beliefs", [])
    try:
        dev_user = user_db.get_user_by_email("dev@mimir.local") or user_db.get_user_by_email("dev@skuld.local")
        if dev_user is None:
            dev_user = user_db.create_user("dev@skuld.local", "devdev123", "Dev")
        dev_uid = dev_user["id"]
        app.state._dev_user_id = dev_uid
        scheduler._dev_user_id = dev_uid
        if not brain_store.brain_exists(dev_uid) and seed_beliefs:
            await scheduler.start_brain(dev_uid, seed_beliefs)
            log.info("DEV brain initialized with %d seeds", len(seed_beliefs))
        elif brain_store.brain_exists(dev_uid):
            await scheduler.start_brain(dev_uid)
            log.info("DEV brain restored")
    except Exception as e:
        log.warning("DEV mode setup failed: %s", e)

    # Shutdown handler
    def on_signal(*_: Any) -> None:
        global _should_stop
        _should_stop = True
        scheduler.stop()
        log.info("Shutdown signal received.")

    signal.signal(signal.SIGINT, on_signal)

    # Run server + scheduler concurrently
    port = args.port
    uv_config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(uv_config)

    log.info("Starting Skuld (multi-user) on http://localhost:%d", port)

    try:
        await asyncio.gather(
            server.serve(),
            scheduler.run_scheduler_loop(),
        )
    finally:
        scheduler.stop()
        log.info("Skuld shut down.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Skuld")
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
