"""
Kiyomi Lite — Health Tracker Skill
Tracks: medications, vitals, symptoms, appointments, exercise
"""
import re
from datetime import datetime, timedelta

try:
    from skills.base import Skill
except ImportError:
    from engine.skills.base import Skill


# ── Keyword sets for detection ──────────────────────────────

HEALTH_KEYWORDS = [
    "medication", "meds", "medicine", "pill", "pills", "prescribed",
    "took my", "taking my", "take my",
    "blood pressure", "bp", "weight", "weigh", "lbs", "pounds", "kg",
    "temperature", "temp",
    "symptom", "symptoms", "headache", "fever", "nausea", "dizzy",
    "pain", "fatigue", "cough", "sore",
    "doctor", "appointment", "dr.", "dr ", "clinic", "hospital",
    "exercise", "workout", "walked", "ran", "steps", "gym",
    "jog", "jogged", "run", "mile", "miles", "pushup", "pushups",
]

# ── Regex patterns for extraction ───────────────────────────

BP_PATTERN = re.compile(
    r"(\d{2,3})\s*(?:/|over|\\)\s*(\d{2,3})",  # 130/80, 130 over 80, 130\80
)
# Also match "140 88" when preceded by BP context words
BP_SPACE_PATTERN = re.compile(
    r"(\d{2,3})\s+(\d{2,3})"  # 140 88 (space-separated)
)
WEIGHT_PATTERN = re.compile(
    r"(\d{2,4}(?:\.\d{1,2})?)\s*(?:lbs?|pounds?|kg|kilos?)",
    re.IGNORECASE,
)
# Fallback: "I weigh 264 today" (no unit → assume lbs)
WEIGHT_VERB_PATTERN = re.compile(
    r"(?:weigh|weight\b[:\s]*)\s*(\d{2,4}(?:\.\d{1,2})?)\b",
    re.IGNORECASE,
)
TEMP_PATTERN = re.compile(
    r"(\d{2,3}(?:\.\d{1,2})?)\s*(?:°|degrees?|f|fahrenheit|c|celsius)",
    re.IGNORECASE,
)
MED_PATTERN = re.compile(
    r"(?:took|taken|take|taking)\s+(?:my\s+)?(.+?)(?:\s+(\d+\s*mg|\d+\s*ml))?(?:\.|$)",
    re.IGNORECASE,
)
# Secondary pattern: "meds - lisinopril", "meds: metformin 500mg", "medication lisinopril"
MED_NAME_AFTER_PATTERN = re.compile(
    r"(?:meds?|medications?|medicine|pills?)\s*[-:–]\s*([A-Za-z][A-Za-z\-]+)(?:\s+(\d+\s*mg|\d+\s*ml))?",
    re.IGNORECASE,
)
# Words that look like med names but aren't
MED_FALSE_POSITIVES = {
    "my", "a", "the", "some", "blood", "blood pressure", "bp",
    "temperature", "temp", "it", "that", "this",
    "morning", "evening", "night", "daily", "afternoon",
    "meds", "medication", "medications", "medicine", "pills", "pill",
}
EXERCISE_PATTERN = re.compile(
    r"(?:walked|ran|jogged|did|completed)\s+(.+?)(?:,|\.|$)",
    re.IGNORECASE,
)
STEPS_PATTERN = re.compile(
    r"(\d{1,6})\s*steps",
    re.IGNORECASE,
)
APPOINTMENT_PATTERN = re.compile(
    r"(?:appointment|seeing|visit|going to)\s+(?:with\s+)?(?:the\s+)?(.+?)(?:\s+(?:on|at|tomorrow|next)\s+(.+?))?(?:\s|$|,|\.)",
    re.IGNORECASE,
)

# Symptom keywords to scan for
SYMPTOM_WORDS = [
    "headache", "fever", "nausea", "dizzy", "dizziness", "pain",
    "fatigue", "tired", "cough", "sore throat", "congestion",
    "insomnia", "anxiety", "cramp", "cramps", "ache", "swelling",
    "rash", "vomiting", "chills",
]


class HealthSkill(Skill):
    name = "health"
    description = "Tracks medications, vitals, symptoms, appointments, and exercise"

    def detect(self, message: str) -> bool:
        """Check if message contains health-related keywords."""
        lower = message.lower()
        return any(kw in lower for kw in HEALTH_KEYWORDS)

    def extract(self, message: str, response: str) -> dict | None:
        """Extract structured health data from the message.
        Returns dict with 'entries' list of {category, entry} dicts, or None.
        """
        lower = message.lower()
        entries = []

        # ── Blood pressure ──
        # Check keyword-based first
        bp_match = None
        if "blood pressure" in lower or "bp" in lower:
            bp_match = BP_PATTERN.search(message)
            if not bp_match:
                bp_match = BP_SPACE_PATTERN.search(message)
                if bp_match:
                    s, d = int(bp_match.group(1)), int(bp_match.group(2))
                    if not (80 <= s <= 250 and 40 <= d <= 150):
                        bp_match = None
        
        # If no keyword, try pattern-based with validation (for "It was 145 over 92")
        if not bp_match:
            bp_match = BP_PATTERN.search(message)
            if bp_match:
                s, d = int(bp_match.group(1)), int(bp_match.group(2))
                if not (80 <= s <= 250 and 40 <= d <= 150):
                    bp_match = None
            if not bp_match:
                bp_match = BP_SPACE_PATTERN.search(message)
                if bp_match:
                    s, d = int(bp_match.group(1)), int(bp_match.group(2))
                    if not (80 <= s <= 250 and 40 <= d <= 150):
                        bp_match = None
        
        if bp_match:
            entries.append({
                "category": "vitals",
                "entry": {
                    "type": "blood_pressure",
                    "systolic": int(bp_match.group(1)),
                    "diastolic": int(bp_match.group(2)),
                    "value": f"{bp_match.group(1)}/{bp_match.group(2)}",
                    "date": self.now(),
                },
            })

        # ── Weight ──
        weight_match = WEIGHT_PATTERN.search(message)
        if not weight_match:
            weight_match = WEIGHT_VERB_PATTERN.search(message)
        if weight_match:
            unit = "lbs"
            if re.search(r"kg|kilo", message, re.IGNORECASE):
                unit = "kg"
            entries.append({
                "category": "vitals",
                "entry": {
                    "type": "weight",
                    "value": float(weight_match.group(1)),
                    "unit": unit,
                    "date": self.now(),
                },
            })

        # ── Temperature ──
        temp_match = TEMP_PATTERN.search(message)
        if temp_match:
            entries.append({
                "category": "vitals",
                "entry": {
                    "type": "temperature",
                    "value": float(temp_match.group(1)),
                    "date": self.now(),
                },
            })

        # ── Medications ──
        med_found = False
        # First try the specific "meds - <name>" pattern (higher priority)
        med_name_match = MED_NAME_AFTER_PATTERN.search(message)
        if med_name_match:
            name = med_name_match.group(1).strip()
            dose = med_name_match.group(2).strip() if med_name_match.group(2) else ""
            if len(name) < 40 and name.lower().strip() not in MED_FALSE_POSITIVES:
                entries.append({
                    "category": "medications",
                    "entry": {
                        "name": name,
                        "dose": dose,
                        "logged": self.now(),
                    },
                })
                med_found = True

        # Fallback to the general "took <name>" pattern
        if not med_found:
            med_match = MED_PATTERN.search(message)
            if med_match and any(kw in lower for kw in ["medication", "meds", "medicine", "pill", "took", "taking", "take"]):
                raw_name = med_match.group(1).strip()
                dose = med_match.group(2).strip() if med_match.group(2) else ""
                # Split on "and" / "&" / "," for multiple meds
                med_names = re.split(r'\s+and\s+|\s*&\s*|\s*,\s*', raw_name)
                for name in med_names:
                    name = re.sub(r'^(?:and|the|my)\s+', '', name.strip(), flags=re.IGNORECASE).strip()
                    # Filter: skip if empty, too long, or any word is a false positive
                    name_words = set(name.lower().split())
                    if name and len(name) < 40 and not (name_words & MED_FALSE_POSITIVES):
                        entries.append({
                            "category": "medications",
                            "entry": {
                                "name": name,
                                "dose": dose,
                                "logged": self.now(),
                            },
                        })

        # ── Symptoms ──
        # Use word-boundary check to avoid substring matches (headache ≠ ache)
        found_symptoms = []
        for s in SYMPTOM_WORDS:
            if re.search(r'\b' + re.escape(s) + r'\b', lower):
                # Skip if this is a substring of an already-matched longer symptom
                if not any(s != fs and s in fs for fs in found_symptoms):
                    found_symptoms.append(s)
        if found_symptoms:
            entries.append({
                "category": "symptoms",
                "entry": {
                    "symptoms": found_symptoms,
                    "text": message[:200],
                    "date": self.now(),
                },
            })

        # ── Exercise ──
        steps_match = STEPS_PATTERN.search(message)
        exercise_match = EXERCISE_PATTERN.search(message)
        if steps_match or exercise_match or any(kw in lower for kw in ["workout", "gym", "exercise"]):
            exercise_entry = {"date": self.now()}
            if steps_match:
                exercise_entry["steps"] = int(steps_match.group(1))
            if exercise_match:
                exercise_entry["activity"] = exercise_match.group(1).strip()[:60]
            elif any(kw in lower for kw in ["workout", "gym", "exercise"]):
                exercise_entry["activity"] = "general workout"
            entries.append({
                "category": "exercise",
                "entry": exercise_entry,
            })

        # ── Appointments ──
        if any(kw in lower for kw in ["appointment", "doctor", "dr.", "dr ", "clinic", "hospital"]):
            appt_match = APPOINTMENT_PATTERN.search(message)
            entry = {"text": message[:200], "date": self.now()}
            if appt_match:
                entry["with"] = appt_match.group(1).strip()[:60]
                if appt_match.group(2):
                    entry["when"] = appt_match.group(2).strip()[:40]
            entries.append({
                "category": "appointments",
                "entry": entry,
            })

        if not entries:
            return None

        return {
            "skill": self.name,
            "entries": entries,
        }

    def get_prompt_context(self) -> str:
        """Build context string for the AI system prompt."""
        lines = ["📋 Health Tracker:"]
        data = self.load_data()

        if not data:
            return ""

        categories = ["medications", "vitals", "symptoms", "exercise", "appointments"]
        labels = {
            "medications": "💊 Recent Medications",
            "vitals": "📊 Recent Vitals",
            "symptoms": "🤒 Recent Symptoms",
            "exercise": "🏃 Recent Exercise",
            "appointments": "📅 Appointments",
        }

        has_content = False
        for cat in categories:
            recent = data.get(cat, [])[-3:]
            if recent:
                has_content = True
                lines.append(f"  {labels[cat]}:")
                for entry in recent:
                    lines.append(f"    - {self._format_entry(cat, entry)}")

        # Check for missed meds today
        missed = self._check_missed_meds_today()
        if missed:
            has_content = True
            lines.append(f"  ⚠️ No medications logged today!")

        if not has_content:
            return ""

        return "\n".join(lines)

    def get_proactive_nudges(self) -> list[str]:
        """Return actionable nudges."""
        nudges = []
        data = self.load_data()

        if not data:
            return nudges

        # 1. Missed meds today
        if self._check_missed_meds_today():
            nudges.append("💊 You haven't logged any medications today. Did you take your meds?")

        # 2. BP trending high
        bp_trend = self._check_bp_trend()
        if bp_trend:
            nudges.append(bp_trend)

        # 3. Upcoming appointments (from text mentions)
        appts = data.get("appointments", [])[-5:]
        if appts:
            latest = appts[-1]
            nudges.append(f"📅 Recent appointment note: {latest.get('text', 'appointment')[:80]}")

        return nudges

    # ── Private helpers ─────────────────────────────────────

    def _check_missed_meds_today(self) -> bool:
        """Return True if user has medication history but none logged today."""
        data = self.load_data()
        meds = data.get("medications", [])
        if not meds:
            return False  # No med history = nothing to miss
        today = self.today()
        today_meds = [m for m in meds if m.get("logged", "").startswith(today)]
        return len(today_meds) == 0

    def _check_bp_trend(self) -> str | None:
        """Check if recent BP readings are trending high."""
        data = self.load_data()
        vitals = data.get("vitals", [])
        bp_readings = [v for v in vitals if v.get("type") == "blood_pressure"][-5:]
        if len(bp_readings) < 2:
            return None

        high_count = sum(
            1 for r in bp_readings
            if r.get("systolic", 0) >= 140 or r.get("diastolic", 0) >= 90
        )
        if high_count >= 2:
            avg_sys = sum(r.get("systolic", 0) for r in bp_readings) // len(bp_readings)
            avg_dia = sum(r.get("diastolic", 0) for r in bp_readings) // len(bp_readings)
            return f"📊 Your blood pressure has been elevated recently (avg {avg_sys}/{avg_dia}). Consider talking to your doctor."

        return None

    def get_morning_brief(self) -> str:
        """Health section for morning brief."""
        data = self.load_data()
        lines = []
        
        # Medication reminder
        meds = data.get("medications", [])
        if meds:
            unique_meds = set()
            for m in meds[-10:]:
                name = m.get("name", "").lower()
                if name:
                    unique_meds.add(m.get("name", "?"))
            if unique_meds:
                lines.append(f"💊 Meds to take: {', '.join(unique_meds)}")
        
        # Appointments coming up
        appts = data.get("appointments", [])
        if appts:
            today = self.today()
            upcoming = [a for a in appts if a.get("date", "") >= today]
            if upcoming:
                next_appt = upcoming[0]
                lines.append(f"🏥 Upcoming: {next_appt.get('with', next_appt.get('text', '?'))} — {next_appt.get('date', '?')}")
        
        return "\n".join(lines) if lines else ""

    @staticmethod
    def _format_entry(category: str, entry: dict) -> str:
        """Format a single entry for display."""
        if category == "medications":
            dose = f" ({entry['dose']})" if entry.get("dose") else ""
            return f"{entry.get('name', '?')}{dose} — {entry.get('logged', '')}"
        elif category == "vitals":
            vtype = entry.get("type", "?")
            if vtype == "blood_pressure":
                return f"BP: {entry.get('value', '?')} — {entry.get('date', '')}"
            elif vtype == "weight":
                return f"Weight: {entry.get('value', '?')} {entry.get('unit', '')} — {entry.get('date', '')}"
            elif vtype == "temperature":
                return f"Temp: {entry.get('value', '?')}° — {entry.get('date', '')}"
            return f"{vtype}: {entry.get('value', '?')} — {entry.get('date', '')}"
        elif category == "symptoms":
            return f"{', '.join(entry.get('symptoms', []))} — {entry.get('date', '')}"
        elif category == "exercise":
            parts = []
            if entry.get("activity"):
                parts.append(entry["activity"])
            if entry.get("steps"):
                parts.append(f"{entry['steps']} steps")
            return f"{' / '.join(parts) or 'exercise'} — {entry.get('date', '')}"
        elif category == "appointments":
            who = entry.get("with", "")
            return f"{who or entry.get('text', '?')[:50]} — {entry.get('date', '')}"
        return str(entry)


# Compatibility function for imports
def get_health_skill():
    """Return an instance of HealthSkill."""
    return HealthSkill()
