"""
Kiyomi Lite — Proactive Scheduler
Makes Kiyomi reach out. Fires reminders, sends morning briefs, runs skill nudges.
Runs every 60 seconds in the background.
"""
import asyncio
import glob
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from engine.config import load_config, CONFIG_DIR

logger = logging.getLogger("kiyomi.scheduler")

# Events worth following up on
FOLLOW_UP_KEYWORDS = [
    "interview", "meeting", "appointment", "deadline", "birthday",
    "anniversary", "exam", "flight", "surgery", "court", "deposition",
    "hearing", "presentation", "wedding",
]

MEMORY_DIR = Path.home() / ".kiyomi" / "memory"


class Scheduler:
    """Background scheduler that makes Kiyomi proactive."""

    def __init__(self, bot, chat_id: str):
        self.bot = bot
        self.chat_id = chat_id
        self._morning_sent_today: str = ""  # "YYYY-MM-DD" of last morning brief
        self._last_nudge_check: float = 0.0
        self._last_follow_up_check: float = 0.0
        self._last_smart_nudge_check: float = 0.0  # Smart Nudges (nudges.py)
        self._weekly_digest_sent: str = ""  # "YYYY-MM-DD" of last digest

    async def run(self):
        """Main loop — runs every 60 seconds."""
        logger.info("🌸 Scheduler started — checking every 60 seconds")
        while True:
            await asyncio.sleep(60)
            try:
                config = load_config()
                now = datetime.now()

                # Quiet hours — no messages
                if self._is_quiet(now, config):
                    continue

                # 1. Fire due reminders
                await self._fire_reminders(now)

                # 2. Morning brief (once per day)
                await self._morning_brief(now, config)

                # 3. Skill nudges (every 2 hours)
                await self._skill_nudges(now)

                # 4. Smart follow-ups (once per hour)
                await self._check_follow_ups(config)

                # 5. Weekly digest (Sundays at 10 AM)
                await self._send_weekly_digest(config)

                # 6. Smart nudges — budget, bills, habits, health, etc. (every 3 hours)
                await self._smart_nudges(now, config)

            except Exception as e:
                logger.error(f"Scheduler tick error: {e}")

    # ── Reminders ───────────────────────────────────────────

    async def _fire_reminders(self, now: datetime):
        """Check reminders.json, fire any that are due."""
        try:
            from reminders import get_due_reminders, mark_reminder_sent
        except ImportError:
            from engine.reminders import get_due_reminders, mark_reminder_sent

        due = get_due_reminders(now)
        for r in due:
            text = r.get("text", "Something to do")
            msg = f"⏰ Reminder: {text}"
            try:
                await self.bot.send_message(chat_id=self.chat_id, text=msg)
                mark_reminder_sent(r["id"], now)
                logger.info(f"Fired reminder: {text[:50]}")
            except Exception as e:
                logger.error(f"Failed to send reminder: {e}")

    # ── Morning Brief ───────────────────────────────────────

    async def _morning_brief(self, now: datetime, config: dict):
        """Send morning brief once per day at configured time."""
        today_str = now.strftime("%Y-%m-%d")
        if self._morning_sent_today == today_str:
            return

        brief_hour = int(config.get("morning_brief_hour", 8))
        brief_minute = int(config.get("morning_brief_minute", 30))

        if now.hour == brief_hour and now.minute >= brief_minute:
            brief = self._build_morning_brief(config)
            if brief:
                try:
                    await self.bot.send_message(chat_id=self.chat_id, text=brief)
                    logger.info("Morning brief sent")
                except Exception as e:
                    logger.error(f"Failed to send morning brief: {e}")
            self._morning_sent_today = today_str

    def _build_morning_brief(self, config: dict) -> str:
        """Aggregate morning data from all skills + reminders."""
        name = config.get("name", "there")
        bot_name = config.get("bot_name", "Kiyomi")
        lines = [f"🌸 Good morning, {name}! Here's your daily check-in:\n"]

        # Weather (free, no API key)
        try:
            import urllib.request
            location = config.get("location", "")
            if location:
                url = f"https://wttr.in/{location.replace(' ', '+')}?format=%C+%t+%h+%w"
                req = urllib.request.Request(url, headers={"User-Agent": "Kiyomi/2.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    weather = resp.read().decode().strip()
                    if weather and "Unknown" not in weather:
                        lines.append(f"🌤 Weather: {weather}")
        except Exception:
            pass

        # Collect morning brief from skills
        try:
            try:
                from skills_integration import get_all_skills
            except ImportError:
                from engine.skills_integration import get_all_skills

            for skill in get_all_skills():
                try:
                    brief = skill.get_morning_brief()
                    if brief:
                        lines.append(brief)
                except Exception as e:
                    logger.debug(f"Skill {skill.name} morning brief failed: {e}")
        except Exception:
            pass

        # Today's reminders
        try:
            try:
                from reminders import list_active_reminders
            except ImportError:
                from engine.reminders import list_active_reminders

            reminders = list_active_reminders()
            if reminders:
                lines.append(f"\n⏰ Reminders today:")
                for r in reminders[:5]:
                    lines.append(f"  • {r.get('text', '?')} ({r.get('time', '?')})")
        except Exception:
            pass

        if len(lines) == 1:
            lines.append("No special updates today — have a great day! 💛")

        lines.append(f"\n_Talk to me anytime — {bot_name} is here!_")
        return "\n".join(lines)

    # ── Skill Nudges ────────────────────────────────────────

    async def _skill_nudges(self, now: datetime):
        """Run skill proactive checks every 2 hours."""
        if time.time() - self._last_nudge_check < 7200:  # 2 hours
            return
        self._last_nudge_check = time.time()

        try:
            try:
                from skills.proactive import (
                    collect_nudges, should_send_nudge,
                    record_nudge, format_nudge_message,
                )
            except ImportError:
                from engine.skills.proactive import (
                    collect_nudges, should_send_nudge,
                    record_nudge, format_nudge_message,
                )

            nudges = collect_nudges()
            fresh = [n for n in nudges if should_send_nudge(n)]
            if fresh:
                msg = format_nudge_message(fresh)
                try:
                    await self.bot.send_message(
                        chat_id=self.chat_id, text=msg, parse_mode="Markdown"
                    )
                    for n in fresh:
                        record_nudge(n)
                    logger.info(f"Sent {len(fresh)} proactive nudge(s)")
                except Exception as e:
                    logger.error(f"Failed to send nudges: {e}")
        except ImportError:
            logger.debug("Proactive module not available")
        except Exception as e:
            logger.error(f"Nudge check failed: {e}")

    # ── Smart Follow-Ups ──────────────────────────────────

    async def _check_follow_ups(self, config: dict):
        """Scan memory for events happening today or yesterday, send follow-ups."""
        # Run once per hour max
        if time.time() - self._last_follow_up_check < 3600:
            return
        self._last_follow_up_check = time.time()

        if not MEMORY_DIR.exists():
            return

        now = datetime.now()
        today = now.date()
        yesterday = today - timedelta(days=1)
        name = config.get("name", "there")

        # Date patterns to look for in memory facts
        # Matches YYYY-MM-DD anywhere in a line
        date_pattern = re.compile(r"(\d{4}-\d{2}-\d{2})")
        # Also match natural dates like "February 3" or "Feb 3, 2026"
        month_names = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
            "jan": 1, "feb": 2, "mar": 3, "apr": 4,
            "jun": 6, "jul": 7, "aug": 8, "sep": 9,
            "oct": 10, "nov": 11, "dec": 12,
        }
        natural_date_pattern = re.compile(
            r"(" + "|".join(month_names.keys()) + r")\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?",
            re.IGNORECASE,
        )

        messages_to_send: list[str] = []

        for md_file in MEMORY_DIR.glob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

            for line in content.splitlines():
                line_lower = line.lower()

                # Check if line mentions a follow-up-worthy event
                matched_keyword = None
                for kw in FOLLOW_UP_KEYWORDS:
                    if kw in line_lower:
                        matched_keyword = kw
                        break
                if not matched_keyword:
                    continue

                # Extract dates from this line
                event_dates: list[datetime] = []

                # ISO dates (YYYY-MM-DD) — skip the timestamp prefix
                # Strip the leading timestamp like "[2026-02-03 00:57]"
                line_after_ts = line
                ts_match = re.match(r"^-?\s*\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\]\s*", line)
                ts_date = None
                if ts_match:
                    line_after_ts = line[ts_match.end():]
                    # Parse the timestamp date for fallback
                    ts_str = re.search(r"(\d{4}-\d{2}-\d{2})", line[:ts_match.end()])
                    if ts_str:
                        try:
                            ts_date = datetime.strptime(ts_str.group(1), "%Y-%m-%d").date()
                        except ValueError:
                            pass

                for m in date_pattern.finditer(line_after_ts):
                    try:
                        d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                        event_dates.append(d)
                    except ValueError:
                        continue

                # Natural dates
                for m in natural_date_pattern.finditer(line_after_ts):
                    month_str = m.group(1).lower()
                    day_num = int(m.group(2))
                    year_num = int(m.group(3)) if m.group(3) else now.year
                    month_num = month_names.get(month_str)
                    if month_num:
                        try:
                            d = datetime(year_num, month_num, day_num).date()
                            event_dates.append(d)
                        except ValueError:
                            continue

                # Check each extracted date
                for event_date in event_dates:
                    kw_display = matched_keyword
                    if event_date == today:
                        messages_to_send.append(
                            f"🍀 Good luck at your {kw_display} today, {name}! You've got this! 💪"
                        )
                    elif event_date == yesterday:
                        messages_to_send.append(
                            f"💬 How did your {kw_display} go yesterday, {name}? I'd love to hear about it!"
                        )

        # Send collected follow-ups (deduplicate)
        seen = set()
        for msg in messages_to_send:
            if msg in seen:
                continue
            seen.add(msg)
            try:
                await self.bot.send_message(chat_id=self.chat_id, text=msg)
                logger.info(f"Follow-up sent: {msg[:60]}")
            except Exception as e:
                logger.error(f"Failed to send follow-up: {e}")

    # ── Weekly Digest ───────────────────────────────────────

    async def _send_weekly_digest(self, config: dict):
        """Send a weekly digest every Sunday at 10 AM."""
        now = datetime.now()

        # Only fire on Sunday (weekday 6) at 10 AM
        if now.weekday() != 6 or now.hour != 10:
            return

        today_str = now.strftime("%Y-%m-%d")
        if self._weekly_digest_sent == today_str:
            return

        self._weekly_digest_sent = today_str

        name = config.get("name", "there")
        bot_name = config.get("bot_name", "Kiyomi")

        # Calculate the start of this week (Monday)
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Timestamp pattern: [2026-02-03 00:56]
        ts_pattern = re.compile(r"\[(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}\]")

        # ── Count facts added this week, by category ──
        facts_by_category: dict[str, int] = {}
        total_new_facts = 0

        if MEMORY_DIR.exists():
            for md_file in MEMORY_DIR.glob("*.md"):
                if md_file.name.endswith(".migrated"):
                    continue
                category = md_file.stem.replace("_", " ").title()
                count = 0
                try:
                    content = md_file.read_text(encoding="utf-8")
                    for match in ts_pattern.finditer(content):
                        try:
                            fact_date = datetime.strptime(match.group(1), "%Y-%m-%d")
                            if fact_date >= week_start:
                                count += 1
                        except ValueError:
                            continue
                except Exception:
                    continue
                if count > 0:
                    facts_by_category[category] = count
                    total_new_facts += count

        # ── Reminders completed this week ──
        completed_count = 0
        try:
            reminders_path = Path.home() / ".kiyomi" / "reminders.json"
            if reminders_path.exists():
                reminders_data = json.loads(reminders_path.read_text(encoding="utf-8"))
                for r in reminders_data:
                    sent_at = r.get("sent_at") or r.get("completed_at")
                    if sent_at:
                        try:
                            sent_dt = datetime.fromisoformat(sent_at)
                            if sent_dt >= week_start:
                                completed_count += 1
                        except (ValueError, TypeError):
                            continue
        except Exception:
            pass

        # ── Active goals ──
        goals: list[str] = []
        goals_file = MEMORY_DIR / "goals.md"
        if goals_file.exists():
            try:
                for line in goals_file.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("- "):
                        # Extract the goal text after timestamp
                        goal_text = re.sub(r"^-\s*\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\]\s*", "", stripped)
                        if goal_text and not goal_text.startswith("#"):
                            goals.append(goal_text)
            except Exception:
                pass

        # ── Upcoming events ──
        upcoming: list[str] = []
        schedule_file = MEMORY_DIR / "schedule.md"
        if schedule_file.exists():
            try:
                for line in schedule_file.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("- "):
                        event_text = re.sub(r"^-\s*\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\]\s*", "", stripped)
                        if event_text and not event_text.startswith("#"):
                            upcoming.append(event_text)
            except Exception:
                pass

        # ── Build the digest message ──
        lines = [
            f"📊 *Your Week in Review*",
            f"_{week_start.strftime('%b %d')} – {now.strftime('%b %d, %Y')}_\n",
        ]

        # New things learned
        if facts_by_category:
            lines.append(f"🧠 *New Things I Learned About You:* {total_new_facts} facts")
            for cat, count in sorted(facts_by_category.items()):
                lines.append(f"  • {cat}: {count}")
            lines.append("")
        else:
            lines.append("🧠 No new facts this week — let's chat more!\n")

        # Reminders completed
        if completed_count > 0:
            lines.append(f"✅ *Reminders Completed:* {completed_count}")
        else:
            lines.append("✅ *Reminders Completed:* None this week")
        lines.append("")

        # Active goals
        if goals:
            lines.append("🎯 *Active Goals:*")
            for g in goals[:5]:
                lines.append(f"  • {g}")
            lines.append("")
        else:
            lines.append("🎯 No goals set yet — want to add some?\n")

        # Upcoming events
        if upcoming:
            lines.append("📅 *Coming Up:*")
            for e in upcoming[:5]:
                lines.append(f"  • {e}")
            lines.append("")
        else:
            lines.append("📅 No upcoming events on file\n")

        # Motivational closing
        closings = [
            f"Keep going, {name} — you're building something great! 🌟",
            f"Another week in the books, {name}. Proud of you! 💛",
            f"One week at a time, {name}. You're doing amazing! 🌸",
            f"Here's to an even better week ahead, {name}! 🚀",
        ]
        # Deterministic pick based on week number
        week_num = now.isocalendar()[1]
        closing = closings[week_num % len(closings)]
        lines.append(f"_{closing}_")
        lines.append(f"\n— {bot_name} 💕")

        digest = "\n".join(lines)

        try:
            await self.bot.send_message(
                chat_id=self.chat_id, text=digest, parse_mode="Markdown"
            )
            logger.info("Weekly digest sent")
        except Exception as e:
            logger.error(f"Failed to send weekly digest: {e}")

    # ── Smart Nudges (nudges.py) ──────────────────────────────

    async def _smart_nudges(self, now: datetime, config: dict):
        """Run Kiyomi Smart Nudges every 3 hours.

        This is the #1 retention feature — proactive budget alerts,
        bill reminders, habit nudges, health checks, birthday
        reminders, follow-ups, and savings motivation.
        """
        if time.time() - self._last_smart_nudge_check < 10800:  # 3 hours
            return
        self._last_smart_nudge_check = time.time()

        try:
            try:
                from nudges import run_nudge_check
            except ImportError:
                from engine.nudges import run_nudge_check

            sent = await run_nudge_check(self.bot, self.chat_id, config)
            if sent:
                logger.info(f"Smart nudges sent {len(sent)} notification(s)")
        except ImportError:
            logger.debug("Smart nudges module not available")
        except Exception as e:
            logger.error(f"Smart nudge check failed: {e}")

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _is_quiet(now: datetime, config: dict) -> bool:
        """Return True if current time is in quiet hours."""
        start = int(config.get("quiet_start", 23))
        end = int(config.get("quiet_end", 7))
        h = now.hour
        if start > end:
            return h >= start or h < end
        return start <= h < end
