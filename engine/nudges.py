"""
Kiyomi Lite — Smart Notifications / "Kiyomi Nudges"
The #1 retention feature. Kiyomi reaches out FIRST — budget alerts,
bill reminders, habit nudges, health checks, birthday heads-ups,
follow-ups on life events, and savings motivation.

Architecture:
  - Each nudge type is a self-contained async checker
  - A dedup layer prevents the same nudge within 24 hours
  - Rate limits: max 3 nudges per check, max 8 per day
  - Quiet hours respected (default 23:00–07:00)
  - Warm, human-sounding copy — never robotic

Called every 2-4 hours from scheduler.py.
"""

import json
import logging
import random
import re
from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

try:
    from config import load_config, CONFIG_DIR
except ImportError:
    from engine.config import load_config, CONFIG_DIR

logger = logging.getLogger("kiyomi.nudges")

# ── Paths ───────────────────────────────────────────────────

KIYOMI_DIR = Path.home() / ".kiyomi"
NUDGE_HISTORY_FILE = KIYOMI_DIR / "nudge_history.json"
BUDGETS_FILE = KIYOMI_DIR / "budgets.json"
MEMORY_DIR = KIYOMI_DIR / "memory"

# ── Constants ───────────────────────────────────────────────

MAX_NUDGES_PER_CHECK = 3
MAX_NUDGES_PER_DAY = 8
NUDGE_DEDUP_HOURS = 24       # Don't repeat same nudge within this window
HISTORY_RETENTION_DAYS = 7   # Prune history entries older than this

# Events worth following up on
FOLLOW_UP_KEYWORDS = [
    "interview", "meeting", "appointment", "deadline",
    "exam", "flight", "surgery", "court", "deposition",
    "hearing", "presentation", "date night",
]


# ════════════════════════════════════════════════════════════
#  NUDGE HISTORY — persistence & dedup
# ════════════════════════════════════════════════════════════

def _load_history() -> dict:
    """Load nudge history from disk."""
    if NUDGE_HISTORY_FILE.exists():
        try:
            return json.loads(NUDGE_HISTORY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"sent": []}


def _save_history(history: dict) -> None:
    """Save nudge history, pruning old entries."""
    cutoff = (datetime.now() - timedelta(days=HISTORY_RETENTION_DAYS)).isoformat()
    history["sent"] = [
        e for e in history["sent"]
        if e.get("ts", "") >= cutoff
    ]
    KIYOMI_DIR.mkdir(parents=True, exist_ok=True)
    NUDGE_HISTORY_FILE.write_text(
        json.dumps(history, indent=2, default=str), encoding="utf-8"
    )


def _was_recently_sent(nudge_key: str) -> bool:
    """Return True if this nudge_key was sent within NUDGE_DEDUP_HOURS."""
    history = _load_history()
    cutoff = datetime.now() - timedelta(hours=NUDGE_DEDUP_HOURS)
    for entry in reversed(history["sent"]):
        if entry.get("key") == nudge_key:
            try:
                sent_at = datetime.fromisoformat(entry["ts"])
                if sent_at > cutoff:
                    return True
            except (ValueError, KeyError):
                pass
            break
    return False


def _record_sent(nudge_key: str, text: str) -> None:
    """Record a nudge as sent."""
    history = _load_history()
    history["sent"].append({
        "key": nudge_key,
        "text": text[:200],
        "ts": datetime.now().isoformat(timespec="seconds"),
    })
    _save_history(history)


def _count_today() -> int:
    """How many nudges have been sent today?"""
    history = _load_history()
    today_str = date.today().isoformat()
    return sum(1 for e in history["sent"] if e.get("ts", "").startswith(today_str))


# ════════════════════════════════════════════════════════════
#  QUIET HOURS
# ════════════════════════════════════════════════════════════

def _is_quiet_time(config: dict, now: Optional[datetime] = None) -> bool:
    """Respect the user's quiet hours."""
    if now is None:
        now = datetime.now()
    start = int(config.get("quiet_start", 23))
    end = int(config.get("quiet_end", 7))
    h = now.hour
    if start > end:
        return h >= start or h < end
    return start <= h < end


# ════════════════════════════════════════════════════════════
#  NUDGE TYPE 1 — Budget Alerts
# ════════════════════════════════════════════════════════════

def _load_budgets() -> dict:
    """Load user-set budgets from ~/.kiyomi/budgets.json.

    Expected format:
    {
        "weekly": {"Food and Drink": 150, "Shopping": 200},
        "monthly": {"Food and Drink": 600, "Entertainment": 100}
    }
    """
    if BUDGETS_FILE.exists():
        try:
            return json.loads(BUDGETS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _check_budget_alerts(config: dict) -> list[tuple[str, str]]:
    """Check spending against user-set budgets.

    Returns list of (nudge_key, nudge_text) tuples.
    """
    nudges: list[tuple[str, str]] = []
    budgets = _load_budgets()
    if not budgets:
        return nudges

    # Try to get Plaid data
    try:
        try:
            from plaid_integration import get_transactions, is_bank_connected
        except ImportError:
            from engine.plaid_integration import get_transactions, is_bank_connected

        if not is_bank_connected():
            return nudges

        plaid_cfg = config.get("plaid", {})
        cid = plaid_cfg.get("client_id", "")
        secret = plaid_cfg.get("secret", "")
        env = plaid_cfg.get("env", "sandbox")
        if not cid or not secret:
            return nudges

        data = get_transactions(cid, secret, env, days=35)
        if "error" in data:
            return nudges
        transactions = data.get("transactions", [])
    except Exception as e:
        logger.debug("Budget alert skipped — Plaid unavailable: %s", e)
        return nudges

    if not transactions:
        return nudges

    today = date.today()
    name = config.get("name", "there")

    # ── Weekly budget check ──
    week_budgets = budgets.get("weekly", {})
    if week_budgets:
        # This week (Monday–Sunday)
        week_start = today - timedelta(days=today.weekday())
        week_spending: dict[str, float] = defaultdict(float)

        for t in transactions:
            if t.get("amount", 0) <= 0:
                continue
            try:
                txn_date = datetime.strptime(t["date"], "%Y-%m-%d").date()
            except (ValueError, TypeError, KeyError):
                continue
            if txn_date >= week_start:
                cat = t.get("category", "Other")
                week_spending[cat] += t["amount"]

        for cat, limit in week_budgets.items():
            spent = week_spending.get(cat, 0)
            remaining = limit - spent
            pct_used = (spent / limit * 100) if limit > 0 else 0

            if pct_used >= 90 and remaining > 0:
                # Almost out
                key = f"budget_weekly_{cat}_{today.isocalendar()[1]}_warning"
                text = (
                    f"💸 Heads up, {name} — you've used {pct_used:.0f}% of your "
                    f"${limit:.0f} weekly {cat} budget. "
                    f"You have about ${remaining:.0f} left for the rest of the week."
                )
                nudges.append((key, text))

            elif remaining < 0:
                # Over budget
                over = abs(remaining)
                key = f"budget_weekly_{cat}_{today.isocalendar()[1]}_over"
                text = (
                    f"🚨 {name}, you've gone ${over:.0f} over your "
                    f"${limit:.0f} weekly {cat} budget. No judgment — "
                    f"just want to keep you in the loop!"
                )
                nudges.append((key, text))

    # ── Monthly budget check ──
    month_budgets = budgets.get("monthly", {})
    if month_budgets:
        month_start = today.replace(day=1)
        month_spending: dict[str, float] = defaultdict(float)

        for t in transactions:
            if t.get("amount", 0) <= 0:
                continue
            try:
                txn_date = datetime.strptime(t["date"], "%Y-%m-%d").date()
            except (ValueError, TypeError, KeyError):
                continue
            if txn_date >= month_start:
                cat = t.get("category", "Other")
                month_spending[cat] += t["amount"]

        for cat, limit in month_budgets.items():
            spent = month_spending.get(cat, 0)
            remaining = limit - spent
            pct_used = (spent / limit * 100) if limit > 0 else 0
            # How far through the month are we?
            day_pct = today.day / 30 * 100

            if pct_used >= 80 and pct_used > day_pct + 15:
                # Spending outpacing calendar
                key = f"budget_monthly_{cat}_{today.strftime('%Y-%m')}_pace"
                text = (
                    f"📊 Your {cat} spending is running a bit hot this month — "
                    f"${spent:.0f} of ${limit:.0f} used and we're only "
                    f"{today.day} days in. You have ${max(remaining, 0):.0f} left."
                )
                nudges.append((key, text))

    # ── Month-over-month comparison (no budget needed) ──
    try:
        try:
            from skills.financial_intelligence import check_spending_alerts
        except ImportError:
            from engine.skills.financial_intelligence import check_spending_alerts

        alerts = check_spending_alerts(threshold_pct=0.30, transactions=transactions)
        for alert in alerts[:1]:  # Only the biggest spike
            key = f"spending_spike_{alert['category']}_{today.strftime('%Y-%m')}"
            cat = alert["category"]
            pct = alert.get("pct_change", 0) * 100
            current = alert.get("current_amount", 0)
            text = (
                f"📈 Your {cat} spending is {pct:.0f}% higher than last month "
                f"so far (${current:,.0f}). Might be worth a look!"
            )
            nudges.append((key, text))
    except Exception:
        pass

    return nudges


# ════════════════════════════════════════════════════════════
#  NUDGE TYPE 2 — Bill Reminders
# ════════════════════════════════════════════════════════════

def _check_bill_reminders(config: dict) -> list[tuple[str, str]]:
    """Alert 3 days before, 1 day before, and day-of upcoming bills."""
    nudges: list[tuple[str, str]] = []

    try:
        try:
            from skills.financial_intelligence import detect_bills
            from plaid_integration import is_bank_connected
        except ImportError:
            from engine.skills.financial_intelligence import detect_bills
            from engine.plaid_integration import is_bank_connected

        if not is_bank_connected():
            return nudges
    except Exception:
        return nudges

    try:
        bills = detect_bills()
    except Exception as e:
        logger.debug("Bill detection failed: %s", e)
        return nudges

    today = date.today()
    name = config.get("name", "there")

    for bill in bills:
        if bill.get("confidence", 0) < 0.5:
            continue

        next_str = bill.get("next_expected")
        if not next_str:
            continue

        try:
            next_date = datetime.strptime(next_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue

        days_until = (next_date - today).days
        merchant = bill.get("merchant", "A bill")
        amount = bill.get("avg_amount", 0)

        if days_until == 0:
            key = f"bill_{merchant}_{next_str}_today"
            text = (
                f"💳 {merchant} (~${amount:,.0f}) hits today, {name}. "
                f"Just making sure you're aware!"
            )
            nudges.append((key, text))

        elif days_until == 1:
            key = f"bill_{merchant}_{next_str}_tomorrow"
            text = (
                f"📅 Heads up — {merchant} (~${amount:,.0f}) "
                f"is expected to charge tomorrow."
            )
            nudges.append((key, text))

        elif days_until == 3:
            key = f"bill_{merchant}_{next_str}_3day"
            text = (
                f"🔔 {merchant} (~${amount:,.0f}) is coming up in 3 days "
                f"({next_date.strftime('%A, %b %d')}). "
                f"Just keeping you in the loop!"
            )
            nudges.append((key, text))

    return nudges


# ════════════════════════════════════════════════════════════
#  NUDGE TYPE 3 — Habit Nudges
# ════════════════════════════════════════════════════════════

def _check_habit_nudges(config: dict) -> list[tuple[str, str]]:
    """Evening nudge for uncompleted habits (after 7pm)."""
    nudges: list[tuple[str, str]] = []
    now = datetime.now()

    # Only nudge in the evening (19:00–22:00)
    if now.hour < 19 or now.hour >= 22:
        return nudges

    try:
        try:
            from skills.habits import get_habits_skill
        except ImportError:
            from engine.skills.habits import get_habits_skill

        skill = get_habits_skill()
        uncompleted = skill.get_uncompleted_today()
    except Exception as e:
        logger.debug("Habit check skipped: %s", e)
        return nudges

    if not uncompleted:
        return nudges

    name = config.get("name", "there")
    today_str = date.today().isoformat()

    if len(uncompleted) == 1:
        habit = uncompleted[0]
        key = f"habit_{habit}_{today_str}"
        templates = [
            f"🏋️ Hey {name}, you haven't logged your {habit} today — it's {now.strftime('%-I%p').lower()}! Still time 💪",
            f"📋 Quick reminder — your {habit} isn't checked off yet today. You got this!",
            f"✨ Don't forget about {habit} today, {name}! Even a little counts.",
        ]
        text = random.choice(templates)
        nudges.append((key, text))

    elif len(uncompleted) <= 3:
        habit_list = " and ".join(uncompleted) if len(uncompleted) == 2 else (
            ", ".join(uncompleted[:-1]) + f", and {uncompleted[-1]}"
        )
        key = f"habits_multi_{today_str}"
        text = (
            f"📋 {name}, you still have {habit_list} to check off today. "
            f"It's {now.strftime('%-I%p').lower()} — there's still time!"
        )
        nudges.append((key, text))

    else:
        key = f"habits_bulk_{today_str}"
        text = (
            f"📋 {name}, you have {len(uncompleted)} habits left to log today "
            f"({', '.join(uncompleted[:2])}, and more). "
            f"Even checking off one or two makes a difference! 🌟"
        )
        nudges.append((key, text))

    return nudges


# ════════════════════════════════════════════════════════════
#  NUDGE TYPE 4 — Health Reminders
# ════════════════════════════════════════════════════════════

def _check_health_reminders(config: dict) -> list[tuple[str, str]]:
    """Medication reminders and vitals logging nudges."""
    nudges: list[tuple[str, str]] = []
    now = datetime.now()
    today_str = date.today().isoformat()
    name = config.get("name", "there")

    try:
        try:
            from skills.health import HealthSkill
        except ImportError:
            from engine.skills.health import HealthSkill

        skill = HealthSkill()
        data = skill.load_data()
    except Exception as e:
        logger.debug("Health check skipped: %s", e)
        return nudges

    if not data:
        return nudges

    # ── Medication reminders ──
    meds = data.get("medications", [])
    if meds:
        # Find unique med names from history
        med_names: set[str] = set()
        for m in meds[-30:]:
            n = m.get("name", "").strip()
            if n:
                med_names.add(n)

        if med_names:
            # Check if any meds logged today
            today_meds = [
                m for m in meds
                if m.get("logged", "").startswith(today_str)
            ]

            if not today_meds:
                # Morning reminder (8-10am) or evening reminder (7-9pm)
                if 8 <= now.hour <= 10:
                    key = f"meds_morning_{today_str}"
                    med_list = ", ".join(list(med_names)[:3])
                    text = (
                        f"💊 Good morning, {name}! "
                        f"Don't forget your meds today ({med_list}). "
                        f"Your health matters! 🌸"
                    )
                    nudges.append((key, text))

                elif 19 <= now.hour <= 21:
                    key = f"meds_evening_{today_str}"
                    text = (
                        f"💊 Hey {name}, I don't see any meds logged today. "
                        f"Did you take them? Just checking in! 💛"
                    )
                    nudges.append((key, text))

    # ── Vitals logging nudge ──
    vitals = data.get("vitals", [])
    if vitals:
        # Check if any vitals logged recently (last 3 days)
        three_days_ago = (date.today() - timedelta(days=3)).isoformat()
        recent_vitals = [
            v for v in vitals
            if v.get("date", "") >= three_days_ago
        ]

        if not recent_vitals and 9 <= now.hour <= 20:
            key = f"vitals_reminder_{today_str}"
            text = (
                f"📊 It's been a few days since you logged your vitals, {name}. "
                f"Want to check in with a quick BP or weight reading?"
            )
            nudges.append((key, text))

    # ── BP trending high ──
    bp_readings = [v for v in vitals if v.get("type") == "blood_pressure"][-5:]
    if len(bp_readings) >= 2:
        high_count = sum(
            1 for r in bp_readings
            if r.get("systolic", 0) >= 140 or r.get("diastolic", 0) >= 90
        )
        if high_count >= 3:
            key = f"bp_high_{today_str}"
            avg_sys = sum(r.get("systolic", 0) for r in bp_readings) // len(bp_readings)
            avg_dia = sum(r.get("diastolic", 0) for r in bp_readings) // len(bp_readings)
            text = (
                f"❤️‍🩹 {name}, your recent BP readings are averaging "
                f"{avg_sys}/{avg_dia} — that's on the high side. "
                f"It might be worth mentioning to your doctor. "
                f"I'm looking out for you! 🫶"
            )
            nudges.append((key, text))

    return nudges


# ════════════════════════════════════════════════════════════
#  NUDGE TYPE 5 — Relationship Reminders
# ════════════════════════════════════════════════════════════

def _check_relationship_reminders(config: dict) -> list[tuple[str, str]]:
    """Birthday and anniversary reminders."""
    nudges: list[tuple[str, str]] = []
    name = config.get("name", "there")

    try:
        try:
            from skills.relationships import get_relationships_skill
        except ImportError:
            from engine.skills.relationships import get_relationships_skill

        skill = get_relationships_skill()
        upcoming = skill.get_upcoming_birthdays(days=7)
    except Exception as e:
        logger.debug("Relationship check skipped: %s", e)
        return nudges

    for bday in upcoming:
        person = bday["name"]
        days_until = bday["days_until"]
        rel = bday.get("relationship", "")
        rel_label = f" (your {rel})" if rel else ""

        if days_until == 0:
            key = f"birthday_{person}_today"
            text = (
                f"🎂 It's {person}'s birthday TODAY{rel_label}! "
                f"Want me to help you pick out a gift or write a message?"
            )
            nudges.append((key, text))

        elif days_until == 1:
            key = f"birthday_{person}_tomorrow"
            text = (
                f"🎁 {person}'s birthday is TOMORROW{rel_label}! "
                f"Need help with a gift idea or a heartfelt message?"
            )
            nudges.append((key, text))

        elif days_until <= 3:
            key = f"birthday_{person}_{days_until}d"
            text = (
                f"🎂 {person}'s birthday{rel_label} is in {days_until} days "
                f"({bday.get('date', '')})! "
                f"Want me to help plan something special?"
            )
            nudges.append((key, text))

    return nudges


# ════════════════════════════════════════════════════════════
#  NUDGE TYPE 6 — Smart Follow-Ups
# ════════════════════════════════════════════════════════════

def _check_smart_followups(config: dict) -> list[tuple[str, str]]:
    """Scan memory for events that happened yesterday or today."""
    nudges: list[tuple[str, str]] = []
    name = config.get("name", "there")

    if not MEMORY_DIR.exists():
        return nudges

    now = datetime.now()
    today = now.date()
    yesterday = today - timedelta(days=1)

    # Also scan the schedule memory file for upcoming events
    schedule_file = MEMORY_DIR / "schedule.md"

    # Date patterns
    iso_date_re = re.compile(r"(\d{4}-\d{2}-\d{2})")
    month_names = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "jun": 6, "jul": 7, "aug": 8, "sep": 9,
        "oct": 10, "nov": 11, "dec": 12,
    }
    natural_date_re = re.compile(
        r"(" + "|".join(month_names.keys()) + r")\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?",
        re.IGNORECASE,
    )

    # Scan memory files for event mentions
    files_to_scan: list[Path] = []
    for md_file in MEMORY_DIR.glob("*.md"):
        files_to_scan.append(md_file)

    seen_events: set[str] = set()

    for md_file in files_to_scan:
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        for line in content.splitlines():
            line_lower = line.lower()

            # Must contain a follow-up-worthy keyword
            matched_keyword = None
            for kw in FOLLOW_UP_KEYWORDS:
                if kw in line_lower:
                    matched_keyword = kw
                    break
            if not matched_keyword:
                continue

            # Strip leading timestamp prefix before extracting event dates
            stripped = re.sub(
                r"^-?\s*\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\]\s*", "", line
            )

            # Extract dates from the stripped text
            event_dates: list[date] = []

            for m in iso_date_re.finditer(stripped):
                try:
                    event_dates.append(
                        datetime.strptime(m.group(1), "%Y-%m-%d").date()
                    )
                except ValueError:
                    continue

            for m in natural_date_re.finditer(stripped):
                month_str = m.group(1).lower()
                day_num = int(m.group(2))
                year_num = int(m.group(3)) if m.group(3) else now.year
                month_num = month_names.get(month_str)
                if month_num:
                    try:
                        event_dates.append(date(year_num, month_num, day_num))
                    except ValueError:
                        continue

            for event_date in event_dates:
                dedup = f"{matched_keyword}_{event_date.isoformat()}"
                if dedup in seen_events:
                    continue
                seen_events.add(dedup)

                if event_date == today:
                    key = f"followup_{matched_keyword}_{today.isoformat()}_today"
                    templates = [
                        f"🍀 Good luck with your {matched_keyword} today, {name}! You've totally got this 💪",
                        f"🌟 Hey {name}, your {matched_keyword} is today — sending you good vibes! ✨",
                    ]
                    nudges.append((key, random.choice(templates)))

                elif event_date == yesterday:
                    key = f"followup_{matched_keyword}_{yesterday.isoformat()}_after"
                    templates = [
                        f"💬 How did your {matched_keyword} go yesterday, {name}? I'd love to hear about it!",
                        f"👋 Hey {name}, just thinking about your {matched_keyword} yesterday — how did it go?",
                    ]
                    nudges.append((key, random.choice(templates)))

    return nudges


# ════════════════════════════════════════════════════════════
#  NUDGE TYPE 7 — Savings Motivation
# ════════════════════════════════════════════════════════════

def _check_savings_motivation(config: dict) -> list[tuple[str, str]]:
    """Progress updates on active savings goals."""
    nudges: list[tuple[str, str]] = []
    name = config.get("name", "there")

    try:
        try:
            from skills.financial_intelligence import get_goal_progress
            from plaid_integration import is_bank_connected
        except ImportError:
            from engine.skills.financial_intelligence import get_goal_progress
            from engine.plaid_integration import is_bank_connected

        if not is_bank_connected():
            return nudges

        progress_list = get_goal_progress()
    except Exception as e:
        logger.debug("Savings motivation skipped: %s", e)
        return nudges

    today_str = date.today().isoformat()

    for gp in progress_list:
        goal_name = gp.get("name", "Savings Goal")
        target = gp.get("target", 0)
        saved = gp.get("saved", 0)
        pct = gp.get("pct_complete", 0)
        on_track = gp.get("on_track", False)
        days_remaining = gp.get("days_remaining", 0)

        if target <= 0:
            continue

        key_base = f"savings_{goal_name}_{today_str}"

        # Milestone celebrations
        if 95 <= pct <= 100:
            key = f"{key_base}_almost"
            text = (
                f"🎉 {name}, you're SO close! ${saved:,.0f} of ${target:,.0f} "
                f"saved — that's {pct:.0f}%! Just a little more! 🚀"
            )
            nudges.append((key, text))

        elif pct >= 75:
            key = f"{key_base}_75"
            text = (
                f"💪 {name}, you're {pct:.0f}% to your ${target:,.0f} "
                f"{goal_name} — ${saved:,.0f} saved so far! Keep that momentum!"
            )
            nudges.append((key, text))

        elif pct >= 50:
            key = f"{key_base}_50"
            text = (
                f"🌟 Halfway there! You've saved ${saved:,.0f} of ${target:,.0f} "
                f"({pct:.0f}%). You're doing great, {name}!"
            )
            nudges.append((key, text))

        elif pct >= 25:
            key = f"{key_base}_25"
            text = (
                f"📊 {pct:.0f}% of the way to your ${target:,.0f} {goal_name}! "
                f"${saved:,.0f} saved so far. Every dollar counts 💛"
            )
            nudges.append((key, text))

        # Behind-pace nudge (only if past the 25% mark AND behind)
        if not on_track and pct >= 25 and days_remaining > 0:
            daily_target = gp.get("daily_target", 0)
            key = f"{key_base}_behind"
            text = (
                f"📉 Your {goal_name} is a little behind pace — "
                f"${saved:,.0f} of ${target:,.0f} with {days_remaining} days left. "
                f"Need about ${daily_target:,.0f}/day to catch up. You can do it! 💪"
            )
            nudges.append((key, text))

    return nudges


# ════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ════════════════════════════════════════════════════════════

# All checkers in priority order
_NUDGE_CHECKERS = [
    ("bill_reminders",        _check_bill_reminders),
    ("health_reminders",      _check_health_reminders),
    ("habit_nudges",          _check_habit_nudges),
    ("budget_alerts",         _check_budget_alerts),
    ("relationship_reminders", _check_relationship_reminders),
    ("smart_followups",       _check_smart_followups),
    ("savings_motivation",    _check_savings_motivation),
]


async def run_nudge_check(bot, chat_id: str, config: dict) -> list[str]:
    """Run all nudge checks and send any that fire.

    Called every 2-4 hours from scheduler.py.

    Returns:
        List of nudge texts that were actually sent.
    """
    now = datetime.now()

    # ── Gate: quiet hours ──
    if _is_quiet_time(config, now):
        logger.info("Nudge check skipped — quiet hours")
        return []

    # ── Gate: daily cap ──
    sent_today = _count_today()
    if sent_today >= MAX_NUDGES_PER_DAY:
        logger.info("Nudge check skipped — daily cap reached (%d/%d)", sent_today, MAX_NUDGES_PER_DAY)
        return []

    remaining_today = MAX_NUDGES_PER_DAY - sent_today
    budget = min(MAX_NUDGES_PER_CHECK, remaining_today)

    # ── Collect candidates from all checkers ──
    candidates: list[tuple[str, str, str]] = []  # (checker_name, key, text)

    for checker_name, checker_fn in _NUDGE_CHECKERS:
        try:
            results = checker_fn(config)
            for key, text in results:
                candidates.append((checker_name, key, text))
        except Exception as e:
            logger.error("Nudge checker '%s' failed: %s", checker_name, e)

    if not candidates:
        logger.info("Nudge check — no candidates from any checker")
        return []

    # ── Filter: dedup against recent history ──
    fresh: list[tuple[str, str, str]] = []
    for checker_name, key, text in candidates:
        if not _was_recently_sent(key):
            fresh.append((checker_name, key, text))

    if not fresh:
        logger.info("Nudge check — all candidates recently sent, nothing new")
        return []

    # ── Diversify: at most 1 nudge per checker type ──
    # This prevents 3 bill reminders in one batch — spread the love
    selected: list[tuple[str, str]] = []  # (key, text)
    seen_types: set[str] = set()

    for checker_name, key, text in fresh:
        if len(selected) >= budget:
            break
        if checker_name in seen_types:
            continue
        seen_types.add(checker_name)
        selected.append((key, text))

    # If we still have room, allow seconds from same type
    if len(selected) < budget:
        for checker_name, key, text in fresh:
            if len(selected) >= budget:
                break
            if (key, text) not in selected:
                selected.append((key, text))

    if not selected:
        return []

    # ── Send nudges ──
    sent_texts: list[str] = []

    for key, text in selected:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
            )
            _record_sent(key, text)
            sent_texts.append(text)
            logger.info("Nudge sent [%s]: %s", key, text[:80])
        except Exception as e:
            logger.error("Failed to send nudge [%s]: %s", key, e)

    logger.info(
        "Nudge check complete — %d sent (%d candidates, %d fresh, %d today total)",
        len(sent_texts), len(candidates), len(fresh), sent_today + len(sent_texts),
    )

    return sent_texts


# ════════════════════════════════════════════════════════════
#  UTILITIES
# ════════════════════════════════════════════════════════════

def get_nudge_stats() -> dict:
    """Return nudge statistics for debugging / status display."""
    history = _load_history()
    today_str = date.today().isoformat()
    today_count = sum(1 for e in history["sent"] if e.get("ts", "").startswith(today_str))

    # Count by type (extract from key prefix)
    by_type: dict[str, int] = defaultdict(int)
    for e in history["sent"]:
        key = e.get("key", "")
        prefix = key.split("_")[0] if key else "unknown"
        by_type[prefix] += 1

    return {
        "total_in_history": len(history["sent"]),
        "sent_today": today_count,
        "daily_limit": MAX_NUDGES_PER_DAY,
        "per_check_limit": MAX_NUDGES_PER_CHECK,
        "by_type": dict(by_type),
    }


def clear_nudge_history() -> str:
    """Clear all nudge history. Returns confirmation message."""
    _save_history({"sent": []})
    return "🗑️ Nudge history cleared."
