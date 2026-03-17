"""SQLite user database for Mimir multi-user support."""

import sqlite3
import uuid
import base64
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)


def _derive_fernet_key(secret: str) -> bytes:
    """Derive a Fernet key from a JWT secret string."""
    import hashlib
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class UserDB:
    PLAN_FREE = "free"
    PLAN_PRO = "pro"

    DEFAULT_LIMITS = {
        "free": {"cycles": 3, "beliefs": 500},
        "pro": {"cycles": 20, "beliefs": -1},
    }

    def __init__(self, db_path: str = "data/users.db", jwt_secret: str = ""):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._jwt_secret = jwt_secret
        if jwt_secret:
            self._fernet = Fernet(_derive_fernet_key(jwt_secret))
        else:
            self._fernet = None
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    plan TEXT NOT NULL DEFAULT 'free',
                    created_at TEXT NOT NULL,
                    last_login TEXT,
                    cycles_today INTEGER NOT NULL DEFAULT 0,
                    cycles_limit INTEGER NOT NULL DEFAULT 3,
                    beliefs_count INTEGER NOT NULL DEFAULT 0,
                    beliefs_limit INTEGER NOT NULL DEFAULT 500,
                    llm_api_key TEXT DEFAULT '',
                    brave_api_key TEXT DEFAULT ''
                )
            """)
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _encrypt(self, plaintext: str) -> str:
        if not plaintext or not self._fernet:
            return plaintext
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def _decrypt(self, ciphertext: str) -> str:
        if not ciphertext or not self._fernet:
            return ciphertext
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except (InvalidToken, Exception):
            return ciphertext

    def create_user(
        self,
        email: str,
        password: str,
        display_name: str = "",
        plan: str = "free",
    ) -> dict:
        """Create a new user. Returns user dict or raises ValueError."""
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")

        email = email.strip().lower()
        if not email or "@" not in email:
            raise ValueError("Invalid email address")

        user_id = str(uuid.uuid4())
        password_hash = bcrypt.hashpw(
            password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

        limits = self.DEFAULT_LIMITS.get(plan, self.DEFAULT_LIMITS["free"])
        now = datetime.now(timezone.utc).isoformat()

        try:
            with self._conn() as conn:
                conn.execute(
                    """INSERT INTO users
                       (id, email, password_hash, display_name, plan,
                        created_at, cycles_limit, beliefs_limit)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id, email, password_hash,
                        display_name or email.split("@")[0],
                        plan, now, limits["cycles"], limits["beliefs"],
                    ),
                )
                conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError("Email already registered")

        return self.get_user(user_id)

    def authenticate(self, email: str, password: str) -> Optional[dict]:
        """Authenticate user by email + password. Returns user dict or None."""
        email = email.strip().lower()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()

        if row is None:
            return None

        stored_hash = row["password_hash"]
        if not bcrypt.checkpw(
            password.encode("utf-8"), stored_hash.encode("utf-8")
        ):
            return None

        # Update last_login
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET last_login = ? WHERE id = ?",
                (now, row["id"]),
            )
            conn.commit()

        return self._row_to_dict(row)

    def get_user(self, user_id: str) -> Optional[dict]:
        """Get user by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def update_usage(self, user_id: str, cycles_delta: int = 0, beliefs_count: int = -1) -> None:
        """Update usage counters for a user."""
        with self._conn() as conn:
            if cycles_delta != 0:
                conn.execute(
                    "UPDATE users SET cycles_today = cycles_today + ? WHERE id = ?",
                    (cycles_delta, user_id),
                )
            if beliefs_count >= 0:
                conn.execute(
                    "UPDATE users SET beliefs_count = ? WHERE id = ?",
                    (beliefs_count, user_id),
                )
            conn.commit()

    def reset_daily_cycles(self) -> int:
        """Reset all users' daily cycle count. Returns number of users reset."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE users SET cycles_today = 0 WHERE cycles_today > 0"
            )
            conn.commit()
            return cursor.rowcount

    def check_limit(self, user_id: str, limit_type: str = "cycles") -> bool:
        """Check if user is within their usage limit. Returns True if allowed."""
        user = self.get_user(user_id)
        if user is None:
            return False

        if limit_type == "cycles":
            limit = user["cycles_limit"]
            if limit < 0:  # unlimited
                return True
            return user["cycles_today"] < limit
        elif limit_type == "beliefs":
            limit = user["beliefs_limit"]
            if limit < 0:  # unlimited
                return True
            return user["beliefs_count"] < limit
        return False

    def update_api_keys(
        self, user_id: str, llm_api_key: str = None, brave_api_key: str = None
    ) -> None:
        """Update encrypted API keys for a user."""
        with self._conn() as conn:
            if llm_api_key is not None:
                conn.execute(
                    "UPDATE users SET llm_api_key = ? WHERE id = ?",
                    (self._encrypt(llm_api_key), user_id),
                )
            if brave_api_key is not None:
                conn.execute(
                    "UPDATE users SET brave_api_key = ? WHERE id = ?",
                    (self._encrypt(brave_api_key), user_id),
                )
            conn.commit()

    def get_decrypted_keys(self, user_id: str) -> dict:
        """Get decrypted API keys for a user."""
        user = self.get_user(user_id)
        if user is None:
            return {"llm_api_key": "", "brave_api_key": ""}
        return {
            "llm_api_key": self._decrypt(user.get("llm_api_key", "") or ""),
            "brave_api_key": self._decrypt(user.get("brave_api_key", "") or ""),
        }

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        return dict(row)

    def get_user_count(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
            return row["cnt"]
