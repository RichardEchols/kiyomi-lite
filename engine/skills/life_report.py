"""Kiyomi Life Report — Auto-generated weekly life summaries.

Combines:
- Financial data (Plaid)
- Health tracking
- Task completion
- Relationship reminders
- Calendar events
- Memory highlights

Sends a beautiful formatted report every Sunday evening.
This is the "personal CFO + life coach" feature.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from engine.skills.base import Skill

logger = logging.getLogger("kiyomi.skills.life_report")

MEMORY_DIR = Path.home() / ".kiyomi" / "memory"


class LifeReportSkill(Skill):
    """Generate comprehensive life reports."""

    name = "life_report"
    description = "Generates daily and weekly life summary reports"

    KEYWORDS = [
        "weekly report", "week summary", "daily report",
        "life report", "recap", "weekly review",
        "how was my week", "how did i do",
    ]

    def detect(self, message: str) -> bool:
        return any(kw in message.lower() for kw in self.KEYWORDS)

    def extract(self, message: str, response: str) -> dict | None:
        result = self.process(message, response)
        if result:
            return {"text": result, "type": "report"}
        return None

    def get_prompt_context(self) -> str:
        return ""  # Reports are on-demand, not persistent context

    def get_proactive_nudges(self) -> list[str]:
        # Sunday evening nudge
        now = datetime.now()
        if now.weekday() == 6 and now.hour >= 18:
            return ["📊 It's Sunday evening — want your weekly life report? Say 'weekly report'!"]
        return []

    def process(self, user_msg: str, ai_response: str) -> Optional[str]:
        """Detect report requests."""
        msg_lower = user_msg.lower()
        triggers = [
            "weekly report", "week summary", "how was my week",
            "weekly summary", "life report", "recap",
            "how did i do this week", "weekly review",
        ]
        if any(t in msg_lower for t in triggers):
            return self.generate_weekly_report()
        
        daily_triggers = [
            "daily report", "today summary", "how was today",
            "daily summary", "day recap", "end of day",
        ]
        if any(t in msg_lower for t in daily_triggers):
            return self.generate_daily_report()
        
        return None

    def generate_morning_brief(self, config: dict) -> str:
        """Generate a morning briefing."""
        today = datetime.now()
        lines = [
            f"🌅 **Good Morning!** — {today.strftime('%A, %B %d, %Y')}\n",
        ]

        # Weather (if available)
        # TODO: integrate weather API


        # Upcoming birthdays
        try:
            from engine.skills.relationships import get_relationships_skill
            rs = get_relationships_skill()
            bday_text = rs.birthday_reminder_text()
            if bday_text:
                lines.append(bday_text)
                lines.append("")
        except ImportError:
            pass

        # Active reminders
        try:
            from engine.reminders import get_due_reminders
            due = get_due_reminders(datetime.now())
            if due:
                lines.append("⏰ **Reminders:**")
                for r in due[:5]:
                    lines.append(f"  • {r.get('text', '')}")
                lines.append("")
        except ImportError:
            pass

        # Quick financial snapshot
        try:
            from engine.plaid_integration import is_bank_connected
            if is_bank_connected():
                plaid_cfg = config.get("plaid", {})
                from engine.plaid_integration import get_balances
                bal = get_balances(
                    plaid_cfg.get("client_id", ""),
                    plaid_cfg.get("secret", ""),
                    plaid_cfg.get("env", "sandbox"),
                )
                if "error" not in bal:
                    lines.append(f"💰 **Net Worth:** ${bal['net_worth']:,.2f}")
                    lines.append("")
        except ImportError:
            pass

        # Task summary
        try:
            from engine.skills.tasks import get_tasks_skill
            ts = get_tasks_skill()
            pending = ts.get_open_tasks()
            if pending:
                lines.append(f"📋 **Tasks ({len(pending)} pending):**")
                for t in pending[:3]:
                    lines.append(f"  • {t.get('text', '')}")
                if len(pending) > 3:
                    lines.append(f"  ... and {len(pending) - 3} more")
                lines.append("")
        except ImportError:
            pass

        lines.append("Have a great day! 💛")
        return "\n".join(lines)

    def generate_daily_report(self) -> str:
        """Generate end-of-day summary."""
        today = datetime.now()
        lines = [
            f"🌙 **Daily Report** — {today.strftime('%A, %B %d')}\n",
        ]

        # Health summary
        try:
            from engine.skills.health import get_health_skill
            hs = get_health_skill()
            summary = hs.get_morning_brief()
            if summary:
                lines.append("💊 **Health:**")
                lines.append(f"  {summary}")
                lines.append("")
        except (ImportError, Exception):
            pass

        # Tasks completed today
        try:
            from engine.skills.tasks import get_tasks_skill
            ts = get_tasks_skill()
            all_tasks = ts.load_data().get("tasks", [])
            completed_today = [
                t for t in all_tasks
                if t.get("done")
                and t.get("completed_at", "").startswith(today.strftime("%Y-%m-%d"))
            ]
            pending = [t for t in all_tasks if not t.get("done")]

            if completed_today:
                lines.append(f"✅ **Completed ({len(completed_today)}):**")
                for t in completed_today:
                    lines.append(f"  • {t.get('text', '')}")
                lines.append("")

            if pending:
                lines.append(f"📋 **Still Pending ({len(pending)}):**")
                for t in pending[:5]:
                    lines.append(f"  • {t.get('text', '')}")
                lines.append("")
        except (ImportError, Exception):
            pass

        # Spending today
        try:
            from engine.plaid_integration import get_transactions, is_bank_connected
            from engine.config import load_config
            config = load_config()
            if is_bank_connected():
                plaid_cfg = config.get("plaid", {})
                data = get_transactions(
                    plaid_cfg.get("client_id", ""),
                    plaid_cfg.get("secret", ""),
                    plaid_cfg.get("env", "sandbox"),
                    days=1,
                )
                if "error" not in data:
                    txns = data.get("transactions", [])
                    total_spent = sum(t["amount"] for t in txns if t["amount"] > 0)
                    if total_spent > 0:
                        lines.append(f"💸 **Spent Today:** ${total_spent:,.2f}")
                        for t in txns[:5]:
                            if t["amount"] > 0:
                                merchant = t.get("merchant") or t.get("name", "Unknown")
                                lines.append(f"  • {merchant}: ${t['amount']:.2f}")
                        lines.append("")
        except (ImportError, Exception):
            pass

        lines.append("Rest well! 🌙")
        return "\n".join(lines)

    def generate_weekly_report(self) -> str:
        """Generate comprehensive weekly report."""
        today = datetime.now()
        week_start = today - timedelta(days=7)
        lines = [
            f"📊 **Weekly Life Report**",
            f"*{week_start.strftime('%b %d')} — {today.strftime('%b %d, %Y')}*\n",
        ]

        # ── Financial ──
        try:
            from engine.plaid_integration import spending_summary, is_bank_connected
            from engine.config import load_config
            config = load_config()
            if is_bank_connected():
                plaid_cfg = config.get("plaid", {})
                summary = spending_summary(
                    plaid_cfg.get("client_id", ""),
                    plaid_cfg.get("secret", ""),
                    plaid_cfg.get("env", "sandbox"),
                    days=7,
                )
                lines.append("💰 **FINANCES**")
                lines.append(summary)
                lines.append("")
        except (ImportError, Exception) as e:
            logger.debug(f"Financial section skipped: {e}")

        # ── Health ──
        try:
            from engine.skills.health import get_health_skill
            hs = get_health_skill()
            week_summary = hs.get_prompt_context()
            if week_summary:
                lines.append("💊 **HEALTH**")
                lines.append(week_summary)
                lines.append("")
        except (ImportError, Exception):
            pass

        # ── Tasks ──
        try:
            from engine.skills.tasks import get_tasks_skill
            ts = get_tasks_skill()
            all_tasks = ts.load_data().get("tasks", [])
            completed = [t for t in all_tasks if t.get("done")]
            pending = [t for t in all_tasks if not t.get("done")]

            lines.append("📋 **TASKS**")
            lines.append(f"  Completed: {len(completed)} | Pending: {len(pending)}")
            if completed:
                lines.append("  ✅ " + ", ".join(t.get("text", "")[:30] for t in completed[-5:]))
            if pending:
                lines.append(f"  📌 Top priority: {pending[0].get('text', '')}")
            lines.append("")
        except (ImportError, Exception):
            pass

        # ── Relationships ──
        try:
            from engine.skills.relationships import get_relationships_skill
            rs = get_relationships_skill()
            upcoming = rs.get_upcoming_birthdays(14)
            if upcoming:
                lines.append("🎂 **UPCOMING BIRTHDAYS**")
                for b in upcoming:
                    lines.append(f"  • {b['name']} — {b['date']} ({b['days_until']} days)")
                lines.append("")
        except (ImportError, Exception):
            pass

        # ── Kiyomi Stats ──
        try:
            from engine.memory import get_memory_stats
            stats = get_memory_stats()
            lines.append("🧠 **KIYOMI STATS**")
            lines.append(f"  Facts remembered: {stats.get('total_facts', 0)}")
            lines.append(f"  Conversations this week: {stats.get('conversations_7d', '?')}")
            lines.append("")
        except (ImportError, Exception):
            pass

        lines.append("─" * 30)
        lines.append("*Your weekly report, powered by Kiyomi* 💛")

        return "\n".join(lines)


# Singleton
_instance = None

def get_life_report_skill() -> LifeReportSkill:
    global _instance
    if _instance is None:
        _instance = LifeReportSkill()
    return _instance
