"""
Kiyomi Lite — Simple Configuration
All config lives in ~/.kiyomi/config.json
"""
import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".kiyomi"
CONFIG_FILE = CONFIG_DIR / "config.json"
MEMORY_DIR = CONFIG_DIR / "memory"
SKILLS_DIR = CONFIG_DIR / "skills"
LOGS_DIR = CONFIG_DIR / "logs"

# Defaults
DEFAULT_CONFIG = {
    "name": "",
    "provider": "gemini",
    "gemini_key": "",
    "anthropic_key": "",
    "openai_key": "",
    "telegram_token": "",
    "telegram_user_id": "",
    "timezone": "UTC",
    "model": "gemini-2.0-flash",
    "imported_chats": False,
    "setup_complete": False,
}


def ensure_dirs():
    """Create all required directories."""
    for d in [CONFIG_DIR, MEMORY_DIR, SKILLS_DIR, LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load config from ~/.kiyomi/config.json."""
    ensure_dirs()
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            stored = json.load(f)
            # Merge with defaults (adds any new keys)
            config = {**DEFAULT_CONFIG, **stored}
            return config
    return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    """Save config to ~/.kiyomi/config.json."""
    ensure_dirs()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_api_key(config: dict) -> str:
    """Get the active API key based on provider."""
    provider = config.get("provider", "gemini")
    key_map = {
        "gemini": "gemini_key",
        "anthropic": "anthropic_key",
        "openai": "openai_key",
    }
    return config.get(key_map.get(provider, "gemini_key"), "")


def get_model(config: dict) -> str:
    """Get the model name for the active provider."""
    provider = config.get("provider", "gemini")
    model = config.get("model", "")
    if model:
        return model
    # Sensible defaults
    defaults = {
        "gemini": "gemini-2.0-flash",
        "anthropic": "claude-sonnet-4-20250514",
        "openai": "gpt-4o-mini",
    }
    return defaults.get(provider, "gemini-2.0-flash")
