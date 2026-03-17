"""Per-user Brain state file management."""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class BrainStore:
    """Manages per-user Brain state files on disk.

    Layout: base_dir/{user_id}/state.json
    """

    def __init__(self, base_dir: str = "data/brains"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_brain_path(self, user_id: str) -> Path:
        return self.base_dir / user_id / "state.json"

    def brain_exists(self, user_id: str) -> bool:
        return self.get_brain_path(user_id).is_file()

    def save_brain(self, user_id: str, state_data: dict) -> None:
        """Atomic write: write to tmp file, then rename."""
        user_dir = self.base_dir / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        target = user_dir / "state.json"

        # Write to temp file in same directory, then rename for atomicity
        fd, tmp_path = tempfile.mkstemp(
            dir=str(user_dir), suffix=".tmp", prefix="state_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state_data, f, ensure_ascii=False, indent=2)
            # On Windows, need to remove target first if it exists
            if target.exists():
                target.unlink()
            Path(tmp_path).rename(target)
            log.debug("Brain state saved for user %s", user_id)
        except Exception:
            # Clean up temp file on failure
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def load_brain(self, user_id: str) -> Optional[dict]:
        """Load brain state from disk. Returns None if not found."""
        path = self.get_brain_path(user_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.error("Failed to load brain for user %s: %s", user_id, e)
            return None

    def delete_brain(self, user_id: str) -> bool:
        """Delete a user's brain state. Returns True if deleted."""
        import shutil
        user_dir = self.base_dir / user_id
        if user_dir.exists():
            shutil.rmtree(str(user_dir), ignore_errors=True)
            log.info("Brain deleted for user %s", user_id)
            return True
        return False

    def list_active_brains(self) -> list[str]:
        """Return list of user_ids that have brain state files."""
        if not self.base_dir.exists():
            return []
        result = []
        for entry in self.base_dir.iterdir():
            if entry.is_dir() and (entry / "state.json").is_file():
                result.append(entry.name)
        return result

    def get_total_storage(self) -> int:
        """Return total bytes used by all brain state files."""
        total = 0
        if not self.base_dir.exists():
            return 0
        for path in self.base_dir.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
        return total
