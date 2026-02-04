"""Kiyomi Habits Skill — Build streaks, track daily habits.

"Did you work out today?" "Did you drink enough water?"
Gamified habit tracking that makes people come back every day.

Features:
- Create habits with daily/weekly targets
- Track completions with streaks
- Proactive check-ins ("Hey, you haven't logged your workout today!")
- Weekly habit report card
- Streak celebrations
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from engine.skills.base import Skill

logger = logging.getLogger("kiyomi.skills.habits")

HABITS_FILE = Path.home() / ".kiyomi" / "habits.json"


class HabitsSkill(Skill):
    """Track daily habits and build streaks."""

    name = "habits"
    description = "Track daily habits, build streaks, and stay accountable"

    KEYWORDS = [
        "habit", "streak", "workout", "exercise", "gym", "meditat",
        "journal", "read", "water", "hydrat", "sleep", "coded",
        "track habit", "my habits", "habit report",
    ]

    def __init__(self):
        self.data = self._load()

    def detect(self, message: str) -> bool:
        msg_lower = message.lower()
        return any(kw in msg_lower for kw in self.KEYWORDS)

    def extract(self, message: str, response: str) -> dict | None:
        result = self.process(message, response)
        if result:
            return {"text": result, "type": "habit_log"}
        return None

    def get_prompt_context(self) -> str:
        if not self.data["habits"]:
            return ""
        status = self.get_today_status()
        return f"Habit tracking:\n{status}"

    def get_proactive_nudges(self) -> list[str]:
        uncompleted = self.get_uncompleted_today()
        if not uncompleted:
            return []
        hour = datetime.now().hour
        if hour >= 20:  # Evening nudge
            return [f"🏋️ Don't forget: {', '.join(uncompleted)} — still not logged today!"]
        return []

    def _load(self) -> dict:
        if HABITS_FILE.exists():
            try:
                return json.loads(HABITS_FILE.read_text())
            except json.JSONDecodeError:
                return {"habits": [], "log": {}}
        return {"habits": [], "log": {}}

    def _save(self):
        HABITS_FILE.parent.mkdir(parents=True, exist_ok=True)
        HABITS_FILE.write_text(json.dumps(self.data, indent=2))

    def process(self, user_msg: str, ai_response: str) -> Optional[str]:
        """Detect habit-related messages."""
        msg_lower = user_msg.lower().strip()

        # Check-in patterns: "I worked out" "went to gym" "drank water"
        completion_patterns = {
            "workout": ["worked out", "went to gym", "exercised", "hit the gym", "did my workout", "ran", "jogged", "lifted"],
            "water": ["drank water", "had water", "hydrated", "drank my water"],
            "meditation": ["meditated", "did meditation", "mindfulness"],
            "reading": ["read", "finished reading", "read a chapter"],
            "journaling": ["journaled", "wrote in journal", "diary entry"],
            "sleep": ["slept well", "good sleep", "8 hours", "went to bed early"],
            "no_junk_food": ["ate healthy", "no junk food", "clean eating"],
            "coding": ["coded", "programming", "wrote code", "built"],
        }

        for habit_key, triggers in completion_patterns.items():
            if any(t in msg_lower for t in triggers):
                # Auto-track if habit exists
                matching = [h for h in self.data["habits"] if h["key"] == habit_key]
                if matching:
                    return self._complete_habit(habit_key)

        return None

    def add_habit(self, name: str, key: str = "", frequency: str = "daily", target: int = 1) -> str:
        """Add a new habit to track."""
        key = key or name.lower().replace(" ", "_")

        # Check for duplicates
        if any(h["key"] == key for h in self.data["habits"]):
            return f"You're already tracking '{name}'!"

        habit = {
            "key": key,
            "name": name,
            "frequency": frequency,  # daily, weekly
            "target": target,  # times per day/week
            "created": datetime.now().isoformat(),
            "active": True,
        }
        self.data["habits"].append(habit)
        self._save()

        return f"✅ New habit: **{name}** ({frequency}, {target}x)\nI'll help you stay on track! 💪"

    def _complete_habit(self, key: str) -> str:
        """Log a habit completion."""
        today = datetime.now().strftime("%Y-%m-%d")

        if today not in self.data["log"]:
            self.data["log"][today] = {}
        if key not in self.data["log"][today]:
            self.data["log"][today][key] = 0

        self.data["log"][today][key] += 1
        self._save()

        # Calculate streak
        streak = self._get_streak(key)
        habit = next((h for h in self.data["habits"] if h["key"] == key), None)
        name = habit["name"] if habit else key

        # Celebration messages at milestones
        if streak >= 30:
            return f"🏆 **{name}** logged! {streak}-DAY STREAK! You're UNSTOPPABLE! 🔥🔥🔥"
        elif streak >= 14:
            return f"⭐ **{name}** logged! {streak}-day streak! Two weeks strong! 💪"
        elif streak >= 7:
            return f"🔥 **{name}** logged! {streak}-day streak! One full week! 🎉"
        elif streak >= 3:
            return f"✅ **{name}** logged! {streak}-day streak building! 💪"
        else:
            return f"✅ **{name}** logged for today! Keep it up! 🌟"

    def _get_streak(self, key: str) -> int:
        """Calculate current streak for a habit."""
        today = datetime.now().date()
        streak = 0

        for i in range(365):  # Max 1 year
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            if date in self.data["log"] and key in self.data["log"][date]:
                if self.data["log"][date][key] > 0:
                    streak += 1
                else:
                    break
            else:
                if i == 0:  # Today doesn't count as breaking
                    continue
                break

        return streak

    def get_today_status(self) -> str:
        """Get today's habit completion status."""
        if not self.data["habits"]:
            return "No habits tracked yet. Tell me what you want to build! 💪"

        today = datetime.now().strftime("%Y-%m-%d")
        today_log = self.data["log"].get(today, {})

        lines = ["📊 **Today's Habits:**\n"]

        for habit in self.data["habits"]:
            if not habit.get("active", True):
                continue

            key = habit["key"]
            done = today_log.get(key, 0)
            target = habit.get("target", 1)
            streak = self._get_streak(key)

            if done >= target:
                status = "✅"
            else:
                status = "⬜"

            streak_text = f" 🔥{streak}" if streak >= 3 else ""
            lines.append(f"{status} **{habit['name']}** — {done}/{target}{streak_text}")

        # Summary
        active = [h for h in self.data["habits"] if h.get("active", True)]
        completed = sum(
            1 for h in active
            if today_log.get(h["key"], 0) >= h.get("target", 1)
        )
        total = len(active)
        pct = int(completed / total * 100) if total > 0 else 0

        lines.append(f"\n**Progress:** {completed}/{total} ({pct}%)")

        if pct == 100:
            lines.append("🎉 PERFECT DAY!")
        elif pct >= 75:
            lines.append("Almost there! Finish strong! 💪")
        elif pct >= 50:
            lines.append("Halfway! Keep going!")

        return "\n".join(lines)

    def get_weekly_report(self) -> str:
        """Generate weekly habit report card."""
        if not self.data["habits"]:
            return ""

        today = datetime.now().date()
        lines = ["📈 **Weekly Habit Report Card:**\n"]

        for habit in self.data["habits"]:
            if not habit.get("active", True):
                continue

            key = habit["key"]
            days_done = 0

            for i in range(7):
                date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
                if date in self.data["log"] and self.data["log"][date].get(key, 0) > 0:
                    days_done += 1

            # Visual bar
            bar = "".join("🟩" if i < days_done else "⬜" for i in range(7))
            pct = int(days_done / 7 * 100)
            streak = self._get_streak(key)

            grade = "A+" if pct >= 95 else "A" if pct >= 85 else "B" if pct >= 70 else "C" if pct >= 50 else "D" if pct >= 30 else "F"

            lines.append(f"**{habit['name']}** — {grade}")
            lines.append(f"  {bar} {days_done}/7 days ({pct}%)")
            if streak >= 3:
                lines.append(f"  🔥 Current streak: {streak} days")
            lines.append("")

        return "\n".join(lines)

    def get_uncompleted_today(self) -> list[str]:
        """Get habits not yet done today — for proactive reminders."""
        today = datetime.now().strftime("%Y-%m-%d")
        today_log = self.data["log"].get(today, {})
        uncompleted = []

        for habit in self.data["habits"]:
            if not habit.get("active", True):
                continue
            if today_log.get(habit["key"], 0) < habit.get("target", 1):
                uncompleted.append(habit["name"])

        return uncompleted

    def remove_habit(self, key: str) -> str:
        """Remove a habit."""
        for h in self.data["habits"]:
            if h["key"] == key or h["name"].lower() == key.lower():
                h["active"] = False
                self._save()
                return f"Stopped tracking '{h['name']}'."
        return f"Habit '{key}' not found."


# Singleton
_instance = None

def get_habits_skill() -> HabitsSkill:
    global _instance
    if _instance is None:
        _instance = HabitsSkill()
    return _instance
