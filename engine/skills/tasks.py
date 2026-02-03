"""
Kiyomi Lite — Task Manager Skill
Detects to-dos, deadlines, and follow-ups in natural conversation.
Extracts task text, due dates, and priority.
"""
import re
import uuid
from datetime import datetime, timedelta

try:
    from skills.base import Skill
except ImportError:
    from engine.skills.base import Skill

# ── Keywords ────────────────────────────────────────────────

TASK_KEYWORDS = [
    "need to", "have to", "should", "must", "don't forget",
    "dont forget", "to-do", "todo", "to do list", "task",
    "deadline", "due", "remind me", "remember to", "gotta",
    "make sure", "follow up", "follow-up", "schedule",
]

PRIORITY_KEYWORDS = {
    "high": ["urgent", "important", "asap", "critical", "emergency", "right away", "immediately"],
    "medium": ["soon", "this week", "when you can", "fairly important"],
    "low": ["eventually", "whenever", "no rush", "low priority", "someday"],
}

# ── Date parsing ────────────────────────────────────────────

DAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3,
    "fri": 4, "sat": 5, "sun": 6,
}


def _parse_due_date(text: str) -> str | None:
    """Parse a due date from natural language. Returns YYYY-MM-DD or None."""
    lower = text.lower()
    now = datetime.now()
    today = now.date()

    # "today"
    if "today" in lower:
        return today.isoformat()

    # "tonight"
    if "tonight" in lower:
        return today.isoformat()

    # "tomorrow"
    if "tomorrow" in lower:
        return (today + timedelta(days=1)).isoformat()

    # "next week"
    if "next week" in lower:
        # Next Monday
        days_ahead = 7 - today.weekday()
        return (today + timedelta(days=days_ahead)).isoformat()

    # "this weekend"
    if "this weekend" in lower:
        days_to_sat = (5 - today.weekday()) % 7
        if days_to_sat == 0:
            days_to_sat = 7
        return (today + timedelta(days=days_to_sat)).isoformat()

    # "by <day>" or "on <day>" or just "<day>"
    for day_name, day_num in DAY_NAMES.items():
        pattern = rf'\b(?:by|on|this|next)?\s*{day_name}\b'
        if re.search(pattern, lower):
            days_ahead = (day_num - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7  # Next occurrence
            # "next <day>" means add a full week
            if f"next {day_name}" in lower:
                days_ahead += 7
            return (today + timedelta(days=days_ahead)).isoformat()

    # "in X days"
    m = re.search(r'in\s+(\d+)\s+days?', lower)
    if m:
        return (today + timedelta(days=int(m.group(1)))).isoformat()

    # "in X weeks"
    m = re.search(r'in\s+(\d+)\s+weeks?', lower)
    if m:
        return (today + timedelta(weeks=int(m.group(1)))).isoformat()

    # Explicit date: "Feb 15", "February 15", "2/15", "02/15"
    m = re.search(
        r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|'
        r'jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|'
        r'oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})',
        lower,
    )
    if m:
        try:
            date_str = m.group(0) + f" {now.year}"
            parsed = datetime.strptime(date_str, "%b %d %Y")
            if parsed.date() < today:
                parsed = parsed.replace(year=now.year + 1)
            return parsed.date().isoformat()
        except ValueError:
            # Try full month name
            for fmt in ["%B %d %Y", "%b %d %Y"]:
                try:
                    parsed = datetime.strptime(date_str, fmt)
                    if parsed.date() < today:
                        parsed = parsed.replace(year=now.year + 1)
                    return parsed.date().isoformat()
                except ValueError:
                    continue

    # MM/DD format
    m = re.search(r'\b(\d{1,2})/(\d{1,2})\b', lower)
    if m:
        try:
            month, day = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12 and 1 <= day <= 31:
                d = today.replace(month=month, day=day)
                if d < today:
                    d = d.replace(year=d.year + 1)
                return d.isoformat()
        except ValueError:
            pass

    # "end of week"
    if "end of week" in lower:
        days_to_fri = (4 - today.weekday()) % 7
        if days_to_fri == 0:
            days_to_fri = 7
        return (today + timedelta(days=days_to_fri)).isoformat()

    # "end of month"
    if "end of month" in lower:
        if now.month == 12:
            eom = today.replace(year=now.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            eom = today.replace(month=now.month + 1, day=1) - timedelta(days=1)
        return eom.isoformat()

    return None


def _detect_priority(text: str) -> str:
    """Detect task priority from keywords."""
    lower = text.lower()
    for level, keywords in PRIORITY_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return level
    return "medium"


def _extract_task_text(text: str) -> str:
    """Extract the actual task from the message, stripping trigger phrases."""
    result = text
    # Remove common prefixes
    prefixes = [
        r"^i\s+need\s+to\s+",
        r"^i\s+have\s+to\s+",
        r"^i\s+should\s+",
        r"^i\s+must\s+",
        r"^i\s+gotta\s+",
        r"^don'?t\s+forget\s+(?:to\s+)?",
        r"^remind\s+me\s+(?:to\s+)?",
        r"^remember\s+to\s+",
        r"^make\s+sure\s+(?:to\s+|i\s+)?",
    ]
    for p in prefixes:
        result = re.sub(p, "", result, flags=re.IGNORECASE).strip()

    # Remove trailing date phrases
    date_suffixes = [
        r"\s+(?:by|on|before)\s+(?:tomorrow|today|tonight|next\s+\w+|this\s+\w+|monday|tuesday|wednesday|thursday|friday|saturday|sunday).*$",
        r"\s+tomorrow\s*$",
        r"\s+today\s*$",
        r"\s+tonight\s*$",
    ]
    for p in date_suffixes:
        result = re.sub(p, "", result, flags=re.IGNORECASE).strip()

    return result if result else text[:80]


# ── Skill class ─────────────────────────────────────────────

class TaskSkill(Skill):
    name = "tasks"
    description = "Tracks to-dos, deadlines, and follow-ups"

    MAX_TASKS = 200

    def detect(self, message: str) -> bool:
        lower = message.lower()
        for kw in TASK_KEYWORDS:
            if kw in lower:
                return True
        return False

    def extract(self, message: str, response: str) -> dict | None:
        task_text = _extract_task_text(message)
        due_date = _parse_due_date(message)
        priority = _detect_priority(message)

        entry = {
            "id": uuid.uuid4().hex[:8],
            "text": task_text,
            "due": due_date,
            "priority": priority,
            "done": False,
            "created": self.now(),
        }

        return entry

    def get_prompt_context(self) -> str:
        data = self.load_data()
        tasks = data.get("tasks", [])
        open_tasks = [t for t in tasks if not t.get("done")]

        if not open_tasks:
            return "📋 Tasks: No open tasks."

        today = self.today()
        now = datetime.now().date()

        # Sort: overdue first, then by due date (None at end)
        def sort_key(t):
            due = t.get("due")
            if due is None:
                return (2, "9999-99-99")  # No due date = last
            if due < today:
                return (0, due)  # Overdue = first
            return (1, due)  # Upcoming

        open_tasks.sort(key=sort_key)

        lines = [f"📋 Tasks — {len(open_tasks)} open:"]
        for t in open_tasks[:15]:  # Cap at 15 for prompt context
            due = t.get("due")
            priority = t.get("priority", "medium")
            text = t.get("text", "")

            prefix = ""
            if due and due < today:
                prefix = "⚠️ OVERDUE "
            elif due == today:
                prefix = "🔴 TODAY "
            elif priority == "high":
                prefix = "🔺 "

            due_str = f" (due {due})" if due else ""
            lines.append(f"  {prefix}• {text}{due_str}")

        return "\n".join(lines)

    def get_proactive_nudges(self) -> list[str]:
        nudges = []
        data = self.load_data()
        tasks = data.get("tasks", [])
        open_tasks = [t for t in tasks if not t.get("done")]

        if not open_tasks:
            return nudges

        today = self.today()
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        overdue = [t for t in open_tasks if t.get("due") and t["due"] < today]
        due_today = [t for t in open_tasks if t.get("due") == today]
        due_tomorrow = [t for t in open_tasks if t.get("due") == tomorrow]

        if overdue:
            task_list = ", ".join(t["text"] for t in overdue[:3])
            nudges.append(
                f"⚠️ You have {len(overdue)} overdue task{'s' if len(overdue) > 1 else ''}: {task_list}"
            )

        if due_today:
            task_list = ", ".join(t["text"] for t in due_today[:3])
            nudges.append(
                f"📋 Due today: {task_list}"
            )

        if due_tomorrow:
            task_list = ", ".join(t["text"] for t in due_tomorrow[:3])
            nudges.append(
                f"📋 Due tomorrow: {task_list}"
            )

        return nudges

    # ── Extra helpers ───────────────────────────────────────

    def complete_task(self, task_id: str) -> bool:
        """Mark a task as done by ID."""
        data = self.load_data()
        tasks = data.get("tasks", [])
        for t in tasks:
            if t.get("id") == task_id:
                t["done"] = True
                t["completed"] = self.now()
                self.save_data(data)
                return True
        return False

    def get_open_tasks(self) -> list[dict]:
        """Return all open (not done) tasks."""
        data = self.load_data()
        return [t for t in data.get("tasks", []) if not t.get("done")]


# Compatibility function for imports
def get_tasks_skill():
    """Return an instance of TaskSkill."""
    return TaskSkill()
