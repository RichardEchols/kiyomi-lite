"""
Kiyomi Lite — Lawyer / Legal Practice Skill
Tracks: cases, court deadlines, billable hours, client notes, opposing counsel
"""
import re
from datetime import datetime, timedelta

try:
    from skills.base import Skill
except ImportError:
    from engine.skills.base import Skill


# ── Keyword sets for detection ──────────────────────────────

LAWYER_KEYWORDS = [
    "case", "client", "court", "hearing", "deposition", "filing",
    "deadline", "billable", "opposing", "plaintiff", "defendant",
    "settlement", "statute of limitations", "motion", "brief",
    "counsel", "judge", "trial", "verdict", "discovery",
    "subpoena", "plea", "docket", "retainer", "arbitration",
    "mediation", "litigation", "appeal", "injunction",
]

# ── Regex patterns for extraction ───────────────────────────

# Case name: "the Johnson case", "Smith v. Jones", "Case No. 2024-1234"
CASE_NAME_PATTERN = re.compile(
    r"(?:the\s+)?(\w+(?:\s+\w+)?)\s+case"
    r"|(\w+)\s+v\.?\s+(\w+)"
    r"|case\s+(?:no\.?\s*)?(\d[\w\-]+)",
    re.IGNORECASE,
)

# Court date: "hearing on Friday", "court date March 15", "trial on 2025-03-20"
COURT_DATE_PATTERN = re.compile(
    r"(?:hearing|trial|court date|deposition|filing deadline|mediation|arbitration)"
    r"\s+(?:is\s+)?(?:on|for|set for|scheduled for)\s+"
    r"(.+?)(?:\.|,|$)",
    re.IGNORECASE,
)

# Billable hours: "billed 3.5 hours", "2 hours on the case", "logged 1.5h"
BILLABLE_PATTERN = re.compile(
    r"(?:billed?|logged?|worked|spent)\s+"
    r"(\d+(?:\.\d{1,2})?)\s*(?:hours?|hrs?|h)\b",
    re.IGNORECASE,
)

# Deadline: "deadline is March 15", "due by next Friday", "filing due 2025-04-01"
DEADLINE_PATTERN = re.compile(
    r"(?:deadline|due|due by|must file by|respond by|file by)\s+"
    r"(?:is\s+)?(?:on\s+)?(.+?)(?:\.|,|$)",
    re.IGNORECASE,
)

# Statute of limitations: "statute expires in 6 months", "SOL runs out April 2025"
SOL_PATTERN = re.compile(
    r"(?:statute\s+(?:of\s+limitations?)?|sol)\s+"
    r"(?:expires?|runs?\s+out|ends?|is)\s+"
    r"(?:in\s+|on\s+)?(.+?)(?:\.|,|$)",
    re.IGNORECASE,
)

# Client name: "client John Smith", "meeting with client Davis"
CLIENT_PATTERN = re.compile(
    r"client\s+(?:named?\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
)

# Opposing counsel: "opposing counsel Smith", "opposing attorney Jane Doe"
OPPOSING_PATTERN = re.compile(
    r"opposing\s+(?:counsel|attorney|lawyer)\s+"
    r"(?:is\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
)


class LawyerSkill(Skill):
    name = "lawyer"
    description = "Tracks cases, court deadlines, billable hours, and client notes"

    def detect(self, message: str) -> bool:
        """Check if message contains legal-practice-related keywords."""
        lower = message.lower()
        return any(kw in lower for kw in LAWYER_KEYWORDS)

    def extract(self, message: str, response: str = "") -> dict | None:
        """Pull structured legal data from the conversation.
        Returns dict with 'entries' list of {category, entry} dicts, or None.
        """
        lower = message.lower()
        entries = []

        # ── Determine active case context ──
        case_ref = self._extract_case_ref(message)

        # ── Case mentions / updates ──
        if case_ref:
            case_entry = {
                "case": case_ref,
                "text": message[:300],
                "date": self.now(),
            }
            # Attach opposing counsel if mentioned
            opp_match = OPPOSING_PATTERN.search(message)
            if opp_match:
                case_entry["opposing_counsel"] = opp_match.group(1).strip()
            # Attach client if mentioned
            client_match = CLIENT_PATTERN.search(message)
            if client_match:
                case_entry["client"] = client_match.group(1).strip()

            entries.append({"category": "cases", "entry": case_entry})

        # ── Deadlines ──
        deadline_match = DEADLINE_PATTERN.search(message)
        if deadline_match:
            entries.append({
                "category": "deadlines",
                "entry": {
                    "case": case_ref or "general",
                    "deadline": deadline_match.group(1).strip()[:80],
                    "text": message[:200],
                    "logged": self.now(),
                    "completed": False,
                },
            })

        # ── Court dates ──
        court_match = COURT_DATE_PATTERN.search(message)
        if court_match:
            entries.append({
                "category": "deadlines",
                "entry": {
                    "type": "court_date",
                    "case": case_ref or "general",
                    "when": court_match.group(1).strip()[:80],
                    "text": message[:200],
                    "logged": self.now(),
                    "completed": False,
                },
            })

        # ── Statute of limitations ──
        sol_match = SOL_PATTERN.search(message)
        if sol_match:
            entries.append({
                "category": "deadlines",
                "entry": {
                    "type": "statute_of_limitations",
                    "case": case_ref or "general",
                    "expires": sol_match.group(1).strip()[:80],
                    "logged": self.now(),
                    "completed": False,
                },
            })

        # ── Billable hours ──
        bill_match = BILLABLE_PATTERN.search(message)
        if bill_match:
            entries.append({
                "category": "billing",
                "entry": {
                    "case": case_ref or "general",
                    "hours": float(bill_match.group(1)),
                    "description": message[:200],
                    "date": self.now(),
                    "invoiced": False,
                },
            })

        # ── Client notes (catch-all for client-related info) ──
        if "client" in lower and not entries:
            entries.append({
                "category": "client_notes",
                "entry": {
                    "case": case_ref or "general",
                    "text": message[:300],
                    "date": self.now(),
                },
            })

        if not entries:
            return None

        return {
            "skill": self.name,
            "entries": entries,
        }

    def get_prompt_context(self) -> str:
        """Build context string for the AI system prompt."""
        lines = ["⚖️ Legal Practice Tracker:"]
        data = self.load_data()

        if not data:
            return ""

        has_content = False

        # ── Active cases summary ──
        cases = data.get("cases", [])
        if cases:
            has_content = True
            # Group by case name, show most recent update per case
            seen = {}
            for c in cases:
                case_name = c.get("case", "unknown")
                seen[case_name] = c  # last one wins
            lines.append("  📁 Active Cases:")
            for name, info in list(seen.items())[-5:]:
                opp = f" (vs. {info['opposing_counsel']})" if info.get("opposing_counsel") else ""
                client = f" — client: {info['client']}" if info.get("client") else ""
                lines.append(f"    - {name}{opp}{client} — updated {info.get('date', '?')}")

        # ── Upcoming deadlines ──
        deadlines = data.get("deadlines", [])
        active_deadlines = [d for d in deadlines if not d.get("completed")]
        if active_deadlines:
            has_content = True
            lines.append("  📅 Upcoming Deadlines:")
            for d in active_deadlines[-5:]:
                dtype = d.get("type", "deadline")
                when = d.get("deadline") or d.get("when") or d.get("expires") or "?"
                case = d.get("case", "")
                label = "⚠️ SOL" if dtype == "statute_of_limitations" else (
                    "🏛️ Court" if dtype == "court_date" else "📋 Due"
                )
                lines.append(f"    - {label}: {when} ({case})")

        # ── Unbilled hours ──
        billing = data.get("billing", [])
        unbilled = [b for b in billing if not b.get("invoiced")]
        if unbilled:
            has_content = True
            total_hours = sum(b.get("hours", 0) for b in unbilled)
            by_case = {}
            for b in unbilled:
                c = b.get("case", "general")
                by_case[c] = by_case.get(c, 0) + b.get("hours", 0)
            lines.append(f"  💰 Unbilled Hours: {total_hours:.1f}h total")
            for case, hours in list(by_case.items())[-5:]:
                lines.append(f"    - {case}: {hours:.1f}h")

        # ── Recent client notes ──
        notes = data.get("client_notes", [])[-3:]
        if notes:
            has_content = True
            lines.append("  📝 Recent Client Notes:")
            for n in notes:
                lines.append(f"    - [{n.get('case', '?')}] {n.get('text', '')[:80]} — {n.get('date', '')}")

        if not has_content:
            return ""

        return "\n".join(lines)

    def get_proactive_nudges(self) -> list[str]:
        """Return actionable nudges for the lawyer."""
        nudges = []
        data = self.load_data()

        if not data:
            return nudges

        # 1. Approaching deadlines (logged in last 7 days, not completed)
        deadlines = data.get("deadlines", [])
        active_deadlines = [d for d in deadlines if not d.get("completed")]
        if active_deadlines:
            for d in active_deadlines[-5:]:
                dtype = d.get("type", "deadline")
                case = d.get("case", "unknown")
                when = d.get("deadline") or d.get("when") or d.get("expires") or "?"
                if dtype == "statute_of_limitations":
                    nudges.append(
                        f"⚠️ STATUTE OF LIMITATIONS alert for {case}: "
                        f"expires {when}. Verify this hasn't passed!"
                    )
                else:
                    nudges.append(
                        f"📅 Upcoming deadline for {case}: {when}. "
                        f"Make sure you're prepared."
                    )

        # 2. Unbilled hours reminder
        billing = data.get("billing", [])
        unbilled = [b for b in billing if not b.get("invoiced")]
        if unbilled:
            total = sum(b.get("hours", 0) for b in unbilled)
            if total >= 5:
                nudges.append(
                    f"💰 You have {total:.1f} unbilled hours across "
                    f"{len(unbilled)} entries. Time to invoice?"
                )

        # 3. Cases without recent activity (no update in last 3 entries)
        cases = data.get("cases", [])
        if len(cases) >= 3:
            recent_cases = {c.get("case") for c in cases[-3:]}
            all_cases = {c.get("case") for c in cases}
            stale = all_cases - recent_cases
            for c in list(stale)[:2]:
                nudges.append(
                    f"📁 The {c} case hasn't had any recent updates. "
                    f"Any developments?"
                )

        return nudges

    # ── Private helpers ─────────────────────────────────────

    @staticmethod
    def _extract_case_ref(message: str) -> str | None:
        """Try to pull a case name/number from the message."""
        match = CASE_NAME_PATTERN.search(message)
        if not match:
            return None
        # "the Johnson case"
        if match.group(1):
            return match.group(1).strip()
        # "Smith v. Jones"
        if match.group(2) and match.group(3):
            return f"{match.group(2).strip()} v. {match.group(3).strip()}"
        # "Case No. 2024-1234"
        if match.group(4):
            return f"Case #{match.group(4).strip()}"
        return None

    @staticmethod
    def _format_entry(category: str, entry: dict) -> str:
        """Format a single entry for display."""
        if category == "cases":
            case = entry.get("case", "?")
            opp = f" vs. {entry['opposing_counsel']}" if entry.get("opposing_counsel") else ""
            return f"{case}{opp} — {entry.get('date', '')}"
        elif category == "deadlines":
            dtype = entry.get("type", "deadline")
            when = entry.get("deadline") or entry.get("when") or entry.get("expires") or "?"
            status = "✅" if entry.get("completed") else "⏳"
            return f"{status} {dtype}: {when} ({entry.get('case', '?')})"
        elif category == "billing":
            inv = "✅" if entry.get("invoiced") else "💰"
            return f"{inv} {entry.get('hours', 0):.1f}h — {entry.get('case', '?')} — {entry.get('date', '')}"
        elif category == "client_notes":
            return f"[{entry.get('case', '?')}] {entry.get('text', '')[:60]} — {entry.get('date', '')}"
        return str(entry)
