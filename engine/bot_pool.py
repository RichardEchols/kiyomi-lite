"""
Kiyomi Bot Pool — Manages pre-created Telegram bot tokens.

Bots are created ahead of time via scripts/create_bots.py and stored in
data/bot_pool.json. During onboarding, a user claims an unclaimed bot
and gets a direct link to start chatting.
"""
import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

POOL_FILE = Path(__file__).parent.parent / "data" / "bot_pool.json"


def _load_pool() -> dict:
    """Load the bot pool from disk."""
    if not POOL_FILE.exists():
        return {"bots": []}
    with open(POOL_FILE) as f:
        return json.load(f)


def _save_pool(pool: dict):
    """Save the bot pool to disk."""
    POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(POOL_FILE, "w") as f:
        json.dump(pool, f, indent=2)


def claim_bot(claimed_by: str = "") -> dict | None:
    """Claim the next available bot from the pool.

    Args:
        claimed_by: Optional identifier (user name, email, etc.)

    Returns:
        Dict with {token, username, display_name, deep_link} or None if
        no bots are available.
    """
    pool = _load_pool()

    for bot in pool["bots"]:
        if not bot.get("claimed"):
            bot["claimed"] = True
            bot["claimed_by"] = claimed_by or "onboarding"
            bot["claimed_at"] = datetime.now().isoformat()
            _save_pool(pool)

            username = bot["username"].lstrip("@")
            logger.info(f"Bot claimed: @{username} by {claimed_by}")
            return {
                "token": bot["token"],
                "username": username,
                "display_name": bot.get("display_name", "Kiyomi"),
                "deep_link": f"https://t.me/{username}",
            }

    logger.warning("No unclaimed bots available in pool")
    return None


def release_bot(token: str) -> bool:
    """Release a claimed bot back to the pool.

    Args:
        token: The bot token to release.

    Returns:
        True if released, False if not found.
    """
    pool = _load_pool()

    for bot in pool["bots"]:
        if bot["token"] == token:
            bot["claimed"] = False
            bot["claimed_by"] = None
            bot.pop("claimed_at", None)
            _save_pool(pool)
            logger.info(f"Bot released: @{bot['username']}")
            return True

    return False


def get_pool_status() -> dict:
    """Get pool statistics.

    Returns:
        Dict with {total, available, claimed, bots: [...]}
    """
    pool = _load_pool()
    bots = pool.get("bots", [])
    claimed = sum(1 for b in bots if b.get("claimed"))

    return {
        "total": len(bots),
        "available": len(bots) - claimed,
        "claimed": claimed,
    }


def has_available_bots() -> bool:
    """Check if there are any unclaimed bots in the pool."""
    pool = _load_pool()
    return any(not b.get("claimed") for b in pool.get("bots", []))
