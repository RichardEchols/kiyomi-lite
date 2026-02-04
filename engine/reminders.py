"""
Kiyomi Lite — Reminder System
"Remind me about my budget every morning" → just works.

Includes scheduler-facing helpers:
    - calculate_fire_time()  — parse "8:00 AM" → today's datetime
    - get_due_reminders()    — reminders due *right now* (60-s window)
    - mark_reminder_sent()   — update last_sent, deactivate if non-recurring
"""
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from engine.config import CONFIG_DIR

# Regex for relative-time strings produced by parse_reminder_from_message
# e.g. "30m", "2h", "10s"
_RELATIVE_RE = re.compile(r"^(\d+)(h|m|s)$")

logger = logging.getLogger(__name__)

REMINDERS_FILE = CONFIG_DIR / "reminders.json"


def load_reminders() -> list:
    """Load all reminders."""
    if REMINDERS_FILE.exists():
        with open(REMINDERS_FILE) as f:
            return json.load(f)
    return []


def save_reminders(reminders: list):
    """Save all reminders."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(REMINDERS_FILE, "w") as f:
        json.dump(reminders, f, indent=2)


def add_reminder(text: str, time_str: str, recurring: bool = False) -> dict:
    """Add a new reminder.
    
    time_str: "8am", "every morning", "in 30 minutes", "tomorrow at 3pm"
    
    The returned dict includes ``last_sent`` (initially *None*) and
    ``next_fire`` (pre-calculated ISO string when possible).
    """
    import uuid

    now = datetime.now()
    reminder: dict = {
        "id": uuid.uuid4().hex[:12],
        "text": text,
        "time": time_str,
        "recurring": recurring,
        "created": now.isoformat(),
        "active": True,
        "last_sent": None,
        "next_fire": None,
    }

    # Pre-calculate next_fire so the scheduler can use it immediately
    fire = _resolve_fire_time(reminder, now)
    if fire is not None:
        reminder["next_fire"] = fire.isoformat(timespec="seconds")

    reminders = load_reminders()
    reminders.append(reminder)
    save_reminders(reminders)
    
    return reminder


def remove_reminder(reminder_id) -> bool:
    """Remove a reminder by ID (str or int)."""
    reminders = load_reminders()
    reminders = [r for r in reminders if str(r["id"]) != str(reminder_id)]
    save_reminders(reminders)
    return True


def list_active_reminders() -> list:
    """Get all active reminders."""
    return [r for r in load_reminders() if r.get("active", True)]


def parse_reminder_from_message(message: str) -> Optional[dict]:
    """Try to extract a reminder from a natural message.
    
    "Remind me to check my budget every morning"
    "Remind me about the meeting at 3pm"
    "Don't let me forget to call mom tomorrow"
    
    Returns dict with 'text' and 'time' or None.
    """
    msg_lower = message.lower()
    
    # Check if this is a reminder request
    reminder_triggers = ['remind me', "don't let me forget", 'dont let me forget', "don't forget", 'dont forget', 'remember to', 'alert me']
    if not any(trigger in msg_lower for trigger in reminder_triggers):
        return None
    
    # Extract the reminder text (everything after the trigger)
    text = message
    for trigger in reminder_triggers:
        if trigger in msg_lower:
            idx = msg_lower.index(trigger) + len(trigger)
            text = message[idx:].strip()
            # Remove leading "to", "about", "that"
            for prefix in ['to ', 'about ', 'that ']:
                if text.lower().startswith(prefix):
                    text = text[len(prefix):]
            break
    
    # Try to find time
    time_str = "9:00 AM"  # default
    recurring = False
    found_explicit_time = False
    found_recurring = False
    
    # 1. ALWAYS check explicit time first: "at 10:30pm", "at 3pm", "at 8am"
    time_match = re.search(
        r'(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|AM|PM)',
        message
    )
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        ampm = time_match.group(3).upper()
        time_str = f"{hour}:{minute:02d} {ampm}"
        text = re.sub(
            r'(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM)',
            '', text
        ).strip()
        found_explicit_time = True
    
    # 2. Check bare "at 3", "at 7" (no AM/PM) — infer from context
    if not found_explicit_time:
        bare_time = re.search(r'at\s+(\d{1,2})(?::(\d{2}))?\b(?!\s*(?:am|pm))', msg_lower)
        if bare_time:
            hour = int(bare_time.group(1))
            minute = int(bare_time.group(2) or 0)
            # Infer AM/PM: "tonight"/"evening"/"night" → PM, else daytime logic
            if any(w in msg_lower for w in ['tonight', 'evening', 'night', 'pm']):
                ampm = "PM"
            elif hour <= 6:
                ampm = "PM"  # "at 3" more likely 3 PM than 3 AM
            else:
                ampm = "AM" if hour < 12 else "PM"
            time_str = f"{hour}:{minute:02d} {ampm}"
            text = re.sub(r'at\s+\d{1,2}(?::\d{2})?\b', '', text).strip()
            found_explicit_time = True
    
    # 3. Check recurring patterns (don't override explicit time, just set recurring flag)
    recurring_patterns = {
        'every morning': ('8:00 AM', True),
        'every evening': ('6:00 PM', True),
        'every night': ('9:00 PM', True),
        'every day': ('9:00 AM', True),
        'daily': ('9:00 AM', True),
    }
    
    for pattern, (default_time, is_recurring) in recurring_patterns.items():
        if pattern in msg_lower:
            recurring = is_recurring
            if not found_explicit_time:
                time_str = default_time
            # Remove the pattern from text
            text = re.sub(re.escape(pattern), '', text, flags=re.IGNORECASE).strip()
            for prefix in ['to ', 'about ', 'that ']:
                if text.lower().startswith(prefix):
                    text = text[len(prefix):]
            found_recurring = True
            break
    
    # 4. Check relative time patterns
    if not found_explicit_time and not found_recurring:
        relative_patterns = [
            (r'in\s+(\d+)\s*(?:hour|hr)s?', lambda m: (f"{int(m.group(1))}h", False)),
            (r'in\s+(\d+)\s*(?:minute|min)s?', lambda m: (f"{int(m.group(1))}m", False)),
            (r'in\s+(\d+)\s*(?:second|sec)s?', lambda m: (f"{int(m.group(1))}s", False)),
        ]
        for pat, handler in relative_patterns:
            match = re.search(pat, msg_lower)
            if match:
                time_str, recurring = handler(match)
                text = re.sub(pat, '', text, flags=re.IGNORECASE).strip()
                found_explicit_time = True
                break
    
    # 5. Check date-relative patterns
    if not found_explicit_time and not found_recurring:
        simple_patterns = {
            'tonight': ('9:00 PM', False),
            'tomorrow morning': ('8:00 AM', False),
            'tomorrow evening': ('6:00 PM', False),
            'tomorrow night': ('9:00 PM', False),
            'tomorrow': ('9:00 AM', False),
        }
        for pattern, (time_val, is_recurring) in simple_patterns.items():
            if pattern in msg_lower:
                time_str = time_val
                recurring = is_recurring
                text = text.replace(pattern, '').strip()
                break
    
    # Clean up "tonight"/"tomorrow" from text if still present
    for cleanup in ['tonight', 'tomorrow']:
        text = text.replace(cleanup, '').strip()
    
    if not text:
        text = message
    
    return {
        "text": text.strip(' .,!'),
        "time": time_str,
        "recurring": recurring,
    }


# ---------------------------------------------------------------------------
# Scheduler-facing helpers
# ---------------------------------------------------------------------------

def calculate_fire_time(time_str: str, now: datetime) -> Optional[datetime]:
    """Parse an absolute time string into today's :class:`datetime`.

    Handles formats like ``"8:00 AM"``, ``"3:30 PM"``, ``"12 PM"``.
    Returns *None* if the string cannot be parsed.

    >>> from datetime import datetime
    >>> calculate_fire_time("8:00 AM", datetime(2026, 2, 3, 7, 0))
    datetime.datetime(2026, 2, 3, 8, 0)
    """
    clean = time_str.strip()
    for fmt in ("%I:%M %p", "%I %p", "%I:%M%p", "%I%p"):
        try:
            parsed = datetime.strptime(clean, fmt)
            return now.replace(
                hour=parsed.hour,
                minute=parsed.minute,
                second=0,
                microsecond=0,
            )
        except ValueError:
            continue
    return None


def _resolve_fire_time(reminder: dict, now: datetime) -> Optional[datetime]:
    """Resolve the next fire time for *any* reminder (absolute or relative).

    Resolution order:
        1. Pre-calculated ``next_fire`` field (if present & valid).
        2. Relative time string (``"30m"``, ``"2h"``, ``"10s"``) →
           ``created + delta``.
        3. Absolute time string (``"8:00 AM"``) → today at that time.
    """
    # 1. Pre-calculated next_fire
    nf = reminder.get("next_fire")
    if nf:
        try:
            return datetime.fromisoformat(nf)
        except (ValueError, TypeError):
            pass

    time_str: str = reminder.get("time", "")

    # 2. Relative time (e.g. "30m", "2h", "10s")
    match = _RELATIVE_RE.match(time_str.strip())
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        created_raw = reminder.get("created")
        if not created_raw:
            return None
        try:
            base = datetime.fromisoformat(created_raw)
        except (ValueError, TypeError):
            return None
        delta_map = {
            "h": timedelta(hours=amount),
            "m": timedelta(minutes=amount),
            "s": timedelta(seconds=amount),
        }
        return base + delta_map[unit]

    # 3. Absolute time
    return calculate_fire_time(time_str, now)


def get_due_reminders(now: datetime) -> list[dict]:
    """Return active reminders whose fire time is within 60 seconds of *now*.

    Each returned dict is the full reminder object (including ``id``).

    Rules:
        • Recurring reminders are skipped if ``last_sent`` is already today.
        • Non-recurring reminders are skipped if ``last_sent`` is set at all
          (safety net — they should already be inactive).
    """
    reminders = load_reminders()
    due: list[dict] = []
    today_str = now.strftime("%Y-%m-%d")

    for r in reminders:
        if not r.get("active", True):
            continue

        fire_time = _resolve_fire_time(r, now)
        if fire_time is None:
            continue

        # Within the 60-second window?
        delta_seconds = abs((now - fire_time).total_seconds())
        if delta_seconds > 60:
            continue

        # Dedup: avoid double-firing
        last_sent = r.get("last_sent")
        if last_sent:
            if r.get("recurring"):
                # Recurring — skip only if already sent *today*
                try:
                    last_dt = datetime.fromisoformat(last_sent)
                    if last_dt.strftime("%Y-%m-%d") == today_str:
                        continue
                except (ValueError, TypeError):
                    pass
            else:
                # Non-recurring — should have been deactivated; skip
                continue

        due.append(r)

    return due


def mark_reminder_sent(reminder_id: str, now: datetime) -> None:
    """Record that a reminder was fired.

    Sets ``last_sent`` to *now*.  If the reminder is **not** recurring,
    it is also deactivated (``active = False``).

    For recurring reminders the ``next_fire`` field is recalculated for
    the next day at the same time.
    """
    reminders = load_reminders()
    changed = False

    for r in reminders:
        if r.get("id") != reminder_id:
            continue

        r["last_sent"] = now.isoformat(timespec="seconds")

        if r.get("recurring"):
            # Recalculate next_fire for tomorrow (absolute times)
            fire = calculate_fire_time(r.get("time", ""), now)
            if fire is not None:
                next_day = fire + timedelta(days=1)
                r["next_fire"] = next_day.isoformat(timespec="seconds")
        else:
            r["active"] = False

        changed = True
        break

    if changed:
        save_reminders(reminders)
