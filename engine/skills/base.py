"""
Kiyomi Lite — Base Skill Class
All skills inherit from this. Keep it dead simple.
"""
import json
import logging
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

try:
    from config import SKILLS_DIR
except ImportError:
    from engine.config import SKILLS_DIR

log = logging.getLogger(__name__)

# Per-file locks to prevent concurrent read-modify-write corruption
_file_locks: dict[str, threading.Lock] = {}
_file_locks_lock = threading.Lock()


def _get_file_lock(path: str) -> threading.Lock:
    """Get or create a lock for a specific file path."""
    with _file_locks_lock:
        if path not in _file_locks:
            _file_locks[path] = threading.Lock()
        return _file_locks[path]


class Skill(ABC):
    """Base class for all Kiyomi skills."""

    name: str = ""
    description: str = ""

    def __init__(self):
        if not self.name:
            raise ValueError("Skill must have a name")
        self.data_dir = SKILLS_DIR / self.name
        self.data_file = self.data_dir / "data.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ── Core interface ──────────────────────────────────────

    @abstractmethod
    def detect(self, message: str) -> bool:
        """Does this message relate to this skill?"""
        ...

    @abstractmethod
    def extract(self, message: str, response: str) -> dict | None:
        """Pull structured data from the conversation.
        Returns a dict like {"category": "vitals", "entry": {...}} or None.
        """
        ...

    @abstractmethod
    def get_prompt_context(self) -> str:
        """Return context string to inject into the AI system prompt."""
        ...

    @abstractmethod
    def get_proactive_nudges(self) -> list[str]:
        """Return list of reminder/nudge strings."""
        ...

    def get_morning_brief(self) -> str:
        """Return a morning brief section for this skill.
        Override in subclasses. Default: empty string (nothing to report).
        """
        return ""

    # ── Storage helpers ─────────────────────────────────────

    def load_data(self) -> dict:
        """Load this skill's data.json. Returns empty dict on missing/corrupt."""
        if not self.data_file.exists():
            return {}
        try:
            with open(self.data_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.warning("Failed to load %s: %s", self.data_file, e)
            # Try to recover from backup
            backup = self.data_file.with_suffix(".json.bak")
            if backup.exists():
                try:
                    with open(backup) as f:
                        data = json.load(f)
                    log.info("Recovered from backup: %s", backup)
                    # Restore the main file from backup
                    self.save_data(data)
                    return data
                except Exception:
                    pass
            return {}

    def save_data(self, data: dict):
        """Write data dict to data.json atomically (write to temp, then rename)."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to temp file in same directory, then rename
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.data_dir), suffix=".tmp", prefix=".data_"
            )
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, default=str)
            # Rename is atomic on POSIX
            os.replace(tmp_path, str(self.data_file))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise

    def store(self, category: str, entry: dict, max_per_category: int = 100):
        """Append an entry to a category list, keeping last N entries.
        Thread-safe: uses per-file locking to prevent concurrent corruption.
        """
        lock = _get_file_lock(str(self.data_file))
        with lock:
            data = self.load_data()
            if category not in data:
                data[category] = []
            data[category].append(entry)
            # Trim to max
            data[category] = data[category][-max_per_category:]
            self.save_data(data)
        log.info("Stored %s entry in %s", category, self.name)

    def get_recent(self, category: str, n: int = 3) -> list[dict]:
        """Get last N entries for a category."""
        data = self.load_data()
        return data.get(category, [])[-n:]

    # ── Timestamp helper ────────────────────────────────────

    @staticmethod
    def now() -> str:
        """ISO-ish timestamp string."""
        return datetime.now().strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def today() -> str:
        """Today's date string."""
        return datetime.now().strftime("%Y-%m-%d")
