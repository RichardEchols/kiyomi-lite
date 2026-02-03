"""
Kiyomi Lite — Proactive Engine
Periodically checks all skills for nudges/reminders and sends them via Telegram.

This is NOT a skill — it's the scheduler that queries all skills.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    from config import CONFIG_DIR, load_config
except ImportError:
    from engine.config import CONFIG_DIR, load_config

logger = logging.getLogger("kiyomi.proactive")

# Where we store the nudge dedup log
PROACTIVE_LOG = CONFIG_DIR / "proactive_log.json"

# Skills to check — add new skill module names here
SKILL_MODULES = [
    "skills.health",
    "skills.budget",
    "skills.tasks",
]

# How often to run (seconds) — 4 hours
CHECK_INTERVAL = 4 * 60 * 60

# Don't repeat the same nudge within this window
NUDGE_COOLDOWN = timedelta(hours=12)

# Max entries kept in the log
MAX_LOG_ENTRIES = 50


# ---------------------------------------------------------------------------
# Nudge log persistence
# ---------------------------------------------------------------------------

def _load_log() -> dict:
    """Load the proactive nudge log from disk."""
    if PROACTIVE_LOG.exists():
        try:
            with open(PROACTIVE_LOG) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"nudges": []}


def _save_log(log: dict) -> None:
    """Save the proactive nudge log, trimming to MAX_LOG_ENTRIES."""
    log["nudges"] = log["nudges"][-MAX_LOG_ENTRIES:]
    PROACTIVE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(PROACTIVE_LOG, "w") as f:
        json.dump(log, f, indent=2)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def should_send_nudge(nudge_text: str) -> bool:
    """Return True if we haven't sent this nudge within the cooldown window."""
    log = _load_log()
    now = datetime.now()
    for entry in reversed(log["nudges"]):
        if entry["text"] == nudge_text:
            sent_at = datetime.fromisoformat(entry["sent_at"])
            if now - sent_at < NUDGE_COOLDOWN:
                return False
            break  # older duplicate is outside window — OK to resend
    return True


def record_nudge(nudge_text: str) -> None:
    """Record that we sent a nudge so we can dedup later."""
    log = _load_log()
    log["nudges"].append({
        "text": nudge_text,
        "sent_at": datetime.now().isoformat(timespec="seconds"),
    })
    _save_log(log)


def collect_nudges() -> list[str]:
    """Collect proactive nudges from all registered skill instances.

    Skills that don't exist yet (or fail to import) are silently skipped.
    """
    nudges: list[str] = []

    # Use the skill registry to get instantiated skills
    try:
        try:
            from skills import get_all_skills
        except ImportError:
            from engine.skills import get_all_skills
        skills = get_all_skills()
    except ImportError:
        logger.debug("Skills registry not available — skipping nudges")
        return nudges
    except Exception:
        logger.exception("Unexpected error loading skills")
        return nudges

    for skill in skills:
        try:
            result = skill.get_proactive_nudges()
            if isinstance(result, list):
                nudges.extend(result)
        except Exception:
            logger.exception("Error collecting nudges from %s", skill.name)

    return nudges


def format_nudge_message(nudges: list[str]) -> str:
    """Format a list of nudge strings into a friendly Telegram message."""
    if not nudges:
        return ""
    lines = ["🌸 *Hey! A few things to keep in mind:*\n"]
    for nudge in nudges:
        lines.append(f"• {nudge}")
    lines.append("\n_I'll check in again later — you've got this!_ 💪")
    return "\n".join(lines)


def get_quiet_hours() -> tuple[int, int]:
    """Return (start_hour, end_hour) for quiet hours from config.

    Default: 23:00–07:00.  Config keys: ``quiet_start``, ``quiet_end``.
    """
    config = load_config()
    start = int(config.get("quiet_start", 23))
    end = int(config.get("quiet_end", 7))
    return start, end


def is_quiet_time(now: Optional[datetime] = None) -> bool:
    """Return True if the current time falls within quiet hours."""
    if now is None:
        now = datetime.now()
    start, end = get_quiet_hours()
    hour = now.hour
    if start > end:
        # Wraps midnight, e.g. 23-7
        return hour >= start or hour < end
    else:
        return start <= hour < end


# ---------------------------------------------------------------------------
# Core async runner
# ---------------------------------------------------------------------------

async def run_proactive_check(bot, chat_id: str) -> None:
    """Run one proactive check cycle.

    1. Bail if quiet time.
    2. Collect nudges from all skills.
    3. Filter out recently-sent nudges.
    4. Format & send via Telegram.
    5. Record sent nudges.
    """
    if is_quiet_time():
        logger.info("Quiet hours — skipping proactive check")
        return

    all_nudges = collect_nudges()
    if not all_nudges:
        logger.info("No nudges from any skill")
        return

    fresh = [n for n in all_nudges if should_send_nudge(n)]
    if not fresh:
        logger.info("All nudges already sent recently — nothing new")
        return

    message = format_nudge_message(fresh)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="Markdown",
        )
        logger.info("Sent %d proactive nudge(s)", len(fresh))
    except Exception:
        logger.exception("Failed to send proactive message")
        return

    for nudge in fresh:
        record_nudge(nudge)


# ---------------------------------------------------------------------------
# Asyncio integration for the bot event loop
# ---------------------------------------------------------------------------

async def _proactive_loop(bot, chat_id: str) -> None:
    """Infinite loop: wait CHECK_INTERVAL, then run a proactive check."""
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            await run_proactive_check(bot, chat_id)
        except Exception:
            logger.exception("Proactive check failed")


def start_proactive_loop(bot, chat_id: str) -> asyncio.Task:
    """Schedule the proactive engine on the running event loop.

    Call this after the bot has started (e.g., inside ``post_init``).
    Returns the asyncio Task so it can be cancelled on shutdown.
    """
    task = asyncio.create_task(_proactive_loop(bot, chat_id))
    logger.info(
        "Proactive engine started — checking every %d hours",
        CHECK_INTERVAL // 3600,
    )
    return task
