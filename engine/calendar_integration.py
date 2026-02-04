"""
Kiyomi Lite — Google Calendar Integration

Provides read/write access to Google Calendar via OAuth2.
Features:
  - get_todays_events()        → Today's events, formatted with emojis
  - get_upcoming_events(days)  → Next N days of events
  - create_event(...)          → Create a new calendar event
  - find_free_time(date)       → Find open slots on a given day
  - morning_briefing()         → Concise daily summary for morning brief
  - setup_calendar()           → Interactive OAuth2 setup flow
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from engine.config import CONFIG_DIR, load_config

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CREDENTIALS_FILE = CONFIG_DIR / "google_credentials.json"
TOKEN_FILE = CONFIG_DIR / "google_token.json"

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# ---------------------------------------------------------------------------
# Emoji mapping for event types
# ---------------------------------------------------------------------------
_EVENT_EMOJIS: dict[str, str] = {
    "meeting": "🤝",
    "call": "📞",
    "lunch": "🍽️",
    "dinner": "🍽️",
    "breakfast": "🥐",
    "coffee": "☕",
    "gym": "💪",
    "workout": "💪",
    "doctor": "🏥",
    "dentist": "🦷",
    "flight": "✈️",
    "travel": "🧳",
    "birthday": "🎂",
    "interview": "💼",
    "deadline": "⏰",
    "review": "📋",
    "standup": "🧍",
    "1:1": "👥",
    "one on one": "👥",
    "presentation": "📊",
    "demo": "📊",
    "date": "❤️",
    "class": "📚",
    "study": "📚",
    "appointment": "📌",
    "focus": "🎯",
    "therapy": "🧠",
    "haircut": "💇",
    "errand": "🏃",
}

_DEFAULT_EMOJI = "📅"


def _emoji_for(title: str) -> str:
    """Pick an emoji based on keywords in the event title."""
    lower = title.lower()
    for keyword, emoji in _EVENT_EMOJIS.items():
        if keyword in lower:
            return emoji
    return _DEFAULT_EMOJI


# ---------------------------------------------------------------------------
# Timezone helpers
# ---------------------------------------------------------------------------

def _get_user_tz() -> ZoneInfo:
    """Return the user's configured timezone, falling back to local system tz."""
    config = load_config()
    tz_name = config.get("timezone", "")

    if tz_name and tz_name != "UTC":
        try:
            return ZoneInfo(tz_name)
        except (KeyError, Exception):
            pass

    # Try system timezone
    try:
        import time as _time
        local_name = _time.tzname[0]
        # Common abbreviation → IANA mapping
        abbrev_map = {
            "EST": "America/New_York",
            "EDT": "America/New_York",
            "CST": "America/Chicago",
            "CDT": "America/Chicago",
            "MST": "America/Denver",
            "MDT": "America/Denver",
            "PST": "America/Los_Angeles",
            "PDT": "America/Los_Angeles",
        }
        if local_name in abbrev_map:
            return ZoneInfo(abbrev_map[local_name])
    except Exception:
        pass

    return ZoneInfo("UTC")


def _now() -> datetime:
    """Current datetime in user's timezone."""
    return datetime.now(tz=_get_user_tz())


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def is_calendar_configured() -> bool:
    """Check if Google Calendar OAuth credentials exist."""
    return CREDENTIALS_FILE.exists()


def _get_credentials():
    """Load or refresh Google OAuth2 credentials.

    Returns a google.oauth2.credentials.Credentials object or None.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise ImportError(
            "Google Calendar dependencies missing. Install with:\n"
            "  pip install google-auth google-auth-oauthlib google-api-python-client"
        )

    creds = None

    # Load existing token
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            creds = None

    # Refresh if expired
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
        except Exception:
            creds = None

    # Need new authorization
    if not creds or not creds.valid:
        if not CREDENTIALS_FILE.exists():
            return None
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
        _save_token(creds)

    return creds


def _save_token(creds) -> None:
    """Persist OAuth token to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes and list(creds.scopes),
    }
    TOKEN_FILE.write_text(json.dumps(token_data, indent=2), encoding="utf-8")


def _get_service():
    """Return an authorized Google Calendar API service."""
    from googleapiclient.discovery import build

    creds = _get_credentials()
    if creds is None:
        raise RuntimeError(
            "Google Calendar not set up. Place your OAuth credentials at "
            f"{CREDENTIALS_FILE} and run setup_calendar()."
        )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Event formatting
# ---------------------------------------------------------------------------

def _parse_event_time(event: dict, key: str) -> datetime | None:
    """Parse start or end time from a Google Calendar event dict."""
    time_info = event.get(key, {})
    tz = _get_user_tz()

    if "dateTime" in time_info:
        dt = datetime.fromisoformat(time_info["dateTime"])
        return dt.astimezone(tz)

    if "date" in time_info:
        d = datetime.strptime(time_info["date"], "%Y-%m-%d")
        return d.replace(tzinfo=tz)

    return None


def _is_all_day(event: dict) -> bool:
    """Check whether an event is all-day."""
    return "date" in event.get("start", {})


def _format_time(dt: datetime) -> str:
    """Format a datetime as a human-friendly time string like '2:30 PM'."""
    return dt.strftime("%-I:%M %p").replace(":00 ", " ").lstrip("0")


def _format_event(event: dict, include_date: bool = False) -> str:
    """Format a single event into a human-readable line with emoji."""
    title = event.get("summary", "Untitled Event")
    emoji = _emoji_for(title)

    if _is_all_day(event):
        time_str = "All day"
    else:
        start = _parse_event_time(event, "start")
        end = _parse_event_time(event, "end")
        if start and end:
            time_str = f"{_format_time(start)} – {_format_time(end)}"
        elif start:
            time_str = _format_time(start)
        else:
            time_str = "Time TBD"

    location = event.get("location", "")
    location_str = f" 📍 {location}" if location else ""

    if include_date:
        start = _parse_event_time(event, "start")
        if start:
            date_str = start.strftime("%a %b %-d")
            return f"{emoji} {date_str} · {time_str} — {title}{location_str}"

    return f"{emoji} {time_str} — {title}{location_str}"


def _format_event_short(event: dict) -> str:
    """Ultra-short format for morning briefing: '10 AM Dentist'."""
    title = event.get("summary", "Untitled")
    if _is_all_day(event):
        return f"All-day: {title}"
    start = _parse_event_time(event, "start")
    if start:
        t = start.strftime("%-I %p").lstrip("0")
        if start.minute:
            t = start.strftime("%-I:%M %p").lstrip("0")
        return f"{t} {title}"
    return title


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_calendar() -> str:
    """Walk the user through Google Calendar OAuth setup.

    Returns a status message describing the result.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if not CREDENTIALS_FILE.exists():
        return (
            "⚙️ **Google Calendar Setup**\n\n"
            "To connect your Google Calendar:\n\n"
            "1. Go to https://console.cloud.google.com/\n"
            "2. Create a project (or use an existing one)\n"
            "3. Enable the **Google Calendar API**\n"
            "4. Go to **Credentials** → **Create Credentials** → **OAuth client ID**\n"
            "5. Choose **Desktop app** as application type\n"
            "6. Download the JSON file\n"
            "7. Save it as:\n"
            f"   `{CREDENTIALS_FILE}`\n\n"
            "Then run this setup again and I'll complete the authorization! 🚀"
        )

    try:
        creds = _get_credentials()
        if creds and creds.valid:
            # Verify by fetching calendar list
            from googleapiclient.discovery import build
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            calendar_list = service.calendarList().list(maxResults=5).execute()
            calendars = calendar_list.get("items", [])
            cal_names = [c.get("summary", "Unnamed") for c in calendars[:5]]
            cal_list_str = "\n".join(f"  • {name}" for name in cal_names)
            return (
                "✅ **Google Calendar connected!**\n\n"
                f"Found {len(calendars)} calendar(s):\n{cal_list_str}\n\n"
                "I can now check your schedule, create events, and give you daily briefings! 🗓️"
            )
        else:
            return "❌ Authorization failed. Please try again."
    except ImportError as e:
        return f"❌ Missing dependencies: {e}"
    except Exception as e:
        return f"❌ Setup failed: {type(e).__name__}: {str(e)[:300]}"


def get_todays_events() -> str:
    """Fetch and format today's calendar events."""
    try:
        service = _get_service()
    except RuntimeError as e:
        return str(e)
    except ImportError as e:
        return f"❌ Missing dependencies: {e}"

    tz = _get_user_tz()
    now = _now()
    start_of_day = datetime.combine(now.date(), dtime.min, tzinfo=tz)
    end_of_day = datetime.combine(now.date(), dtime.max, tzinfo=tz)

    try:
        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start_of_day.isoformat(),
                timeMax=end_of_day.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except Exception as e:
        return f"❌ Failed to fetch events: {type(e).__name__}: {str(e)[:200]}"

    events = events_result.get("items", [])

    if not events:
        return "📭 No events today — your schedule is wide open!"

    day_label = now.strftime("%A, %B %-d")
    lines = [f"🗓️ **{day_label}** — {len(events)} event{'s' if len(events) != 1 else ''}:\n"]
    for event in events:
        lines.append(f"  {_format_event(event)}")

    return "\n".join(lines)


def get_upcoming_events(days: int = 7) -> str:
    """Fetch and format events for the next N days."""
    try:
        service = _get_service()
    except RuntimeError as e:
        return str(e)
    except ImportError as e:
        return f"❌ Missing dependencies: {e}"

    tz = _get_user_tz()
    now = _now()
    start = datetime.combine(now.date(), dtime.min, tzinfo=tz)
    end = datetime.combine(now.date() + timedelta(days=days), dtime.max, tzinfo=tz)

    try:
        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            )
            .execute()
        )
    except Exception as e:
        return f"❌ Failed to fetch events: {type(e).__name__}: {str(e)[:200]}"

    events = events_result.get("items", [])

    if not events:
        return f"📭 No events in the next {days} days — all clear!"

    # Group by day
    days_map: dict[str, list[dict]] = {}
    for event in events:
        start_dt = _parse_event_time(event, "start")
        if start_dt:
            day_key = start_dt.strftime("%A, %b %-d")
            days_map.setdefault(day_key, []).append(event)

    lines = [f"🗓️ **Next {days} days** — {len(events)} event{'s' if len(events) != 1 else ''}:\n"]
    for day, day_events in days_map.items():
        lines.append(f"**{day}**")
        for event in day_events:
            lines.append(f"  {_format_event(event)}")
        lines.append("")

    return "\n".join(lines).strip()


def create_event(
    title: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
) -> str:
    """Create a new Google Calendar event.

    Args:
        title: Event title / summary.
        start: Start time as ISO 8601 string (e.g. "2025-07-15T10:00:00")
               or date string ("2025-07-15" for all-day).
        end:   End time as ISO 8601 string, or date for all-day.
        description: Optional event description.
        location: Optional location string.

    Returns:
        Human-readable confirmation or error message.
    """
    try:
        service = _get_service()
    except RuntimeError as e:
        return str(e)
    except ImportError as e:
        return f"❌ Missing dependencies: {e}"

    tz = _get_user_tz()
    tz_name = str(tz)

    # Determine if all-day or timed event
    is_all_day = len(start) == 10  # "YYYY-MM-DD"

    if is_all_day:
        event_body: dict[str, Any] = {
            "summary": title,
            "start": {"date": start},
            "end": {"date": end},
        }
    else:
        # Ensure timezone info
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=tz)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=tz)

        event_body = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": tz_name},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": tz_name},
        }

    if description:
        event_body["description"] = description
    if location:
        event_body["location"] = location

    try:
        created = service.events().insert(calendarId="primary", body=event_body).execute()
    except Exception as e:
        return f"❌ Failed to create event: {type(e).__name__}: {str(e)[:200]}"

    link = created.get("htmlLink", "")
    emoji = _emoji_for(title)

    if is_all_day:
        time_str = f"{start}"
    else:
        time_str = f"{_format_time(start_dt)} – {_format_time(end_dt)}"

    result = f"{emoji} **Event created!**\n\n📌 {title}\n🕐 {time_str}"
    if location:
        result += f"\n📍 {location}"
    if description:
        result += f"\n📝 {description}"
    if link:
        result += f"\n🔗 {link}"

    return result


def find_free_time(date: str = "") -> str:
    """Find open time slots on a given date.

    Args:
        date: Date string "YYYY-MM-DD". Defaults to today.

    Returns:
        Formatted list of free time blocks (within 8 AM – 9 PM).
    """
    try:
        service = _get_service()
    except RuntimeError as e:
        return str(e)
    except ImportError as e:
        return f"❌ Missing dependencies: {e}"

    tz = _get_user_tz()

    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return f"❌ Invalid date format: '{date}'. Use YYYY-MM-DD."
    else:
        target_date = _now().date()

    # Working hours window
    day_start = datetime.combine(target_date, dtime(8, 0), tzinfo=tz)
    day_end = datetime.combine(target_date, dtime(21, 0), tzinfo=tz)

    try:
        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except Exception as e:
        return f"❌ Failed to fetch events: {type(e).__name__}: {str(e)[:200]}"

    events = events_result.get("items", [])

    # Build busy blocks (skip all-day events for gap analysis)
    busy: list[tuple[datetime, datetime]] = []
    for event in events:
        if _is_all_day(event):
            continue
        s = _parse_event_time(event, "start")
        e = _parse_event_time(event, "end")
        if s and e:
            busy.append((s, e))

    # Sort and merge overlapping blocks
    busy.sort(key=lambda b: b[0])
    merged: list[tuple[datetime, datetime]] = []
    for s, e in busy:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # Find gaps
    free_slots: list[tuple[datetime, datetime]] = []
    cursor = day_start

    for s, e in merged:
        if cursor < s:
            free_slots.append((cursor, s))
        cursor = max(cursor, e)

    if cursor < day_end:
        free_slots.append((cursor, day_end))

    # Format output
    day_label = target_date.strftime("%A, %b %-d")

    if not free_slots:
        return f"😬 No free time on **{day_label}** between 8 AM – 9 PM. Packed day!"

    if not merged:
        return f"🎉 **{day_label}** is completely open (8 AM – 9 PM). No events!"

    lines = [f"🕐 **Free time on {day_label}:**\n"]
    for slot_start, slot_end in free_slots:
        duration = slot_end - slot_start
        hours = duration.seconds // 3600
        minutes = (duration.seconds % 3600) // 60
        dur_str = ""
        if hours:
            dur_str += f"{hours}h"
        if minutes:
            dur_str += f" {minutes}m"
        dur_str = dur_str.strip()

        lines.append(
            f"  ✅ {_format_time(slot_start)} – {_format_time(slot_end)}  ({dur_str})"
        )

    lines.append(f"\n{len(merged)} event{'s' if len(merged) != 1 else ''} blocking time.")
    return "\n".join(lines)


def morning_briefing() -> str:
    """Generate a concise morning briefing of today's schedule.

    Returns something like:
      "You have 3 events today: 10 AM Dentist, 2 PM Call with Mike, 5 PM Gym"
    """
    try:
        service = _get_service()
    except RuntimeError as e:
        return str(e)
    except ImportError as e:
        return f"❌ Missing dependencies: {e}"

    tz = _get_user_tz()
    now = _now()
    start_of_day = datetime.combine(now.date(), dtime.min, tzinfo=tz)
    end_of_day = datetime.combine(now.date(), dtime.max, tzinfo=tz)

    try:
        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start_of_day.isoformat(),
                timeMax=end_of_day.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except Exception as e:
        return f"❌ Couldn't check your calendar: {type(e).__name__}: {str(e)[:200]}"

    events = events_result.get("items", [])
    day_label = now.strftime("%A")

    if not events:
        return f"☀️ Good morning! No events on your calendar today ({day_label}). Enjoy the free day!"

    count = len(events)
    event_summaries = [_format_event_short(e) for e in events]
    event_list = ", ".join(event_summaries)

    # Next event
    next_event = None
    for event in events:
        start_dt = _parse_event_time(event, "start")
        if start_dt and start_dt > now:
            next_event = event
            break

    briefing = f"☀️ Good morning! You have **{count} event{'s' if count != 1 else ''}** today ({day_label}):\n"
    briefing += f"  {event_list}"

    if next_event:
        next_start = _parse_event_time(next_event, "start")
        if next_start:
            delta = next_start - now
            mins = int(delta.total_seconds() / 60)
            if mins > 0:
                if mins < 60:
                    briefing += f"\n\n⏰ Next up: **{next_event.get('summary', 'Event')}** in {mins} minutes"
                else:
                    hours = mins // 60
                    remaining_mins = mins % 60
                    if remaining_mins:
                        briefing += f"\n\n⏰ Next up: **{next_event.get('summary', 'Event')}** in {hours}h {remaining_mins}m"
                    else:
                        briefing += f"\n\n⏰ Next up: **{next_event.get('summary', 'Event')}** in {hours}h"

    return briefing
