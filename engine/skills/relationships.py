"""Kiyomi Relationships Skill — Never forget a birthday, anniversary, or important detail.

Tracks people the user mentions, their relationships, birthdays, and key facts.
Proactively reminds about upcoming birthdays and anniversaries.

This is what makes a personal assistant PERSONAL.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from skills.base import Skill

logger = logging.getLogger("kiyomi.skills.relationships")

RELATIONSHIPS_FILE = Path.home() / ".kiyomi" / "relationships.json"


class RelationshipsSkill(Skill):
    """Track and remember important people in the user's life."""

    name = "relationships"
    description = "Tracks people, birthdays, anniversaries, and relationship details"

    # Intent patterns
    INTENT_PATTERNS = [
        r"(?:my |the )?(wife|husband|partner|girlfriend|boyfriend|fiancé|fiancée)\s+(\w+)",
        r"(?:my |the )?(mom|dad|mother|father|brother|sister|son|daughter|aunt|uncle|cousin|grandma|grandpa|grandmother|grandfather)\s+(\w+)",
        r"(?:my |the )?(boss|coworker|colleague|friend|buddy|pal|bestie|roommate|neighbor)\s+(\w+)",
        r"(\w+)(?:'s|s)\s+birthday\s+(?:is\s+)?(\w+\s+\d+)",
        r"(\w+)\s+(?:turns|is turning)\s+(\d+)",
        r"(?:married|anniversary)\s+(?:on\s+)?(\w+\s+\d+)",
        r"(\w+)\s+(?:likes?|loves?|hates?|enjoys?|prefers?)\s+(.+)",
    ]

    KEYWORDS = [
        "wife", "husband", "partner", "girlfriend", "boyfriend",
        "mom", "dad", "mother", "father", "brother", "sister",
        "son", "daughter", "boss", "friend", "coworker",
        "birthday", "anniversary", "married",
    ]

    def __init__(self):
        self.people = self._load()

    def detect(self, message: str) -> bool:
        msg_lower = message.lower()
        return any(kw in msg_lower for kw in self.KEYWORDS)

    def extract(self, message: str, response: str) -> dict | None:
        self.process(message, response)
        return None  # Relationships handles its own storage

    def get_prompt_context(self) -> str:
        if not self.people:
            return ""
        people_list = [f"- {p['name']} ({p.get('relationship', 'unknown')})" for p in self.people.values()]
        return f"People I know about:\n" + "\n".join(people_list[:10])

    def get_proactive_nudges(self) -> list[str]:
        upcoming = self.get_upcoming_birthdays(3)
        nudges = []
        for b in upcoming:
            if b["days_until"] == 0:
                nudges.append(f"🎂 It's {b['name']}'s birthday TODAY!")
            elif b["days_until"] == 1:
                nudges.append(f"🎂 {b['name']}'s birthday is TOMORROW!")
        return nudges

    def _load(self) -> dict:
        """Load relationship data."""
        if RELATIONSHIPS_FILE.exists():
            try:
                return json.loads(RELATIONSHIPS_FILE.read_text())
            except json.JSONDecodeError:
                return {}
        return {}

    def _save(self):
        """Save relationship data."""
        RELATIONSHIPS_FILE.parent.mkdir(parents=True, exist_ok=True)
        RELATIONSHIPS_FILE.write_text(json.dumps(self.people, indent=2))

    def process(self, user_msg: str, ai_response: str) -> Optional[str]:
        """Extract relationship info from conversation."""
        msg_lower = user_msg.lower()

        # Detect "my [relationship] [name]" patterns
        rel_patterns = {
            "wife": r"(?:my\s+)?wife\s+(\w+)",
            "husband": r"(?:my\s+)?husband\s+(\w+)",
            "partner": r"(?:my\s+)?partner\s+(\w+)",
            "girlfriend": r"(?:my\s+)?girlfriend\s+(\w+)",
            "boyfriend": r"(?:my\s+)?boyfriend\s+(\w+)",
            "mom": r"(?:my\s+)?(?:mom|mother)\s+(\w+)",
            "dad": r"(?:my\s+)?(?:dad|father)\s+(\w+)",
            "brother": r"(?:my\s+)?brother\s+(\w+)",
            "sister": r"(?:my\s+)?sister\s+(\w+)",
            "son": r"(?:my\s+)?son\s+(\w+)",
            "daughter": r"(?:my\s+)?daughter\s+(\w+)",
            "boss": r"(?:my\s+)?boss\s+(\w+)",
            "friend": r"(?:my\s+)?(?:best\s+)?friend\s+(\w+)",
            "coworker": r"(?:my\s+)?(?:coworker|colleague)\s+(\w+)",
        }

        for rel, pattern in rel_patterns.items():
            match = re.search(pattern, msg_lower)
            if match:
                name = match.group(1).capitalize()
                self._add_person(name, relationship=rel)

        # Detect birthdays: "Sarah's birthday is March 15" or "birthday March 15"
        bday_patterns = [
            r"(\w+)(?:'s|s)\s+birthday\s+(?:is\s+)?(\w+\s+\d{1,2})",
            r"birthday\s+(?:is\s+)?(?:on\s+)?(\w+\s+\d{1,2})\s+(?:for\s+)?(\w+)",
        ]
        for pattern in bday_patterns:
            match = re.search(pattern, msg_lower)
            if match:
                groups = match.groups()
                # Figure out which group is name vs date
                name = groups[0].capitalize()
                date_str = groups[1] if len(groups) > 1 else None
                if date_str:
                    self._add_person(name, birthday=date_str)

        # Detect likes/preferences: "Sarah loves sushi"
        pref_match = re.search(
            r"(\w+)\s+(?:likes?|loves?|enjoys?|is into)\s+(.+?)(?:\.|$)",
            msg_lower,
        )
        if pref_match:
            name = pref_match.group(1).capitalize()
            preference = pref_match.group(2).strip()
            if name in self.people:
                self._add_fact(name, f"Likes: {preference}")

        return None  # Silent processing

    def _add_person(
        self,
        name: str,
        relationship: str = "",
        birthday: str = "",
    ):
        """Add or update a person."""
        if name not in self.people:
            self.people[name] = {
                "name": name,
                "relationship": relationship,
                "birthday": birthday,
                "facts": [],
                "added": datetime.now().isoformat(),
                "last_mentioned": datetime.now().isoformat(),
            }
            logger.info(f"New person tracked: {name} ({relationship})")
        else:
            if relationship:
                self.people[name]["relationship"] = relationship
            if birthday:
                self.people[name]["birthday"] = birthday
            self.people[name]["last_mentioned"] = datetime.now().isoformat()

        self._save()

    def _add_fact(self, name: str, fact: str):
        """Add a fact about a person."""
        if name in self.people:
            facts = self.people[name].get("facts", [])
            if fact not in facts:
                facts.append(fact)
                self.people[name]["facts"] = facts[-20:]  # Keep last 20
                self._save()

    def get_upcoming_birthdays(self, days: int = 30) -> list[dict]:
        """Get birthdays coming up in the next N days."""
        upcoming = []
        today = datetime.now()

        for name, data in self.people.items():
            bday_str = data.get("birthday", "")
            if not bday_str:
                continue

            try:
                # Parse various date formats
                for fmt in ["%B %d", "%b %d", "%m/%d", "%m-%d"]:
                    try:
                        bday = datetime.strptime(bday_str, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    continue

                # Set to this year
                bday = bday.replace(year=today.year)
                if bday < today:
                    bday = bday.replace(year=today.year + 1)

                days_until = (bday - today).days
                if 0 <= days_until <= days:
                    upcoming.append({
                        "name": name,
                        "relationship": data.get("relationship", ""),
                        "birthday": bday_str,
                        "days_until": days_until,
                        "date": bday.strftime("%B %d"),
                    })
            except Exception:
                continue

        return sorted(upcoming, key=lambda x: x["days_until"])

    def get_person(self, name: str) -> Optional[dict]:
        """Look up a person by name."""
        # Exact match
        if name in self.people:
            return self.people[name]
        # Case-insensitive
        for key, data in self.people.items():
            if key.lower() == name.lower():
                return data
        return None

    def get_all_people(self) -> list[dict]:
        """Get all tracked people."""
        return sorted(
            self.people.values(),
            key=lambda x: x.get("last_mentioned", ""),
            reverse=True,
        )

    def format_person(self, data: dict) -> str:
        """Format a person's info nicely."""
        lines = []
        rel = data.get("relationship", "")
        rel_emoji = {
            "wife": "💕", "husband": "💕", "partner": "💕",
            "girlfriend": "❤️", "boyfriend": "❤️",
            "mom": "👩", "dad": "👨", "mother": "👩", "father": "👨",
            "brother": "👦", "sister": "👧",
            "son": "👦", "daughter": "👧",
            "boss": "💼", "coworker": "🏢", "colleague": "🏢",
            "friend": "🤝",
        }.get(rel, "👤")

        lines.append(f"{rel_emoji} **{data['name']}**")
        if rel:
            lines.append(f"   Relationship: {rel.capitalize()}")
        if data.get("birthday"):
            lines.append(f"   🎂 Birthday: {data['birthday']}")
        for fact in data.get("facts", [])[:5]:
            lines.append(f"   • {fact}")

        return "\n".join(lines)

    def birthday_reminder_text(self) -> str:
        """Generate birthday reminder text for daily brief."""
        upcoming = self.get_upcoming_birthdays(14)  # Next 2 weeks
        if not upcoming:
            return ""

        lines = ["🎂 **Upcoming Birthdays:**"]
        for b in upcoming:
            if b["days_until"] == 0:
                lines.append(f"  🎉 TODAY — {b['name']}!")
            elif b["days_until"] == 1:
                lines.append(f"  ⏰ TOMORROW — {b['name']}")
            else:
                lines.append(f"  📅 {b['date']} ({b['days_until']} days) — {b['name']}")

        return "\n".join(lines)


# Singleton
_instance = None

def get_relationships_skill() -> RelationshipsSkill:
    global _instance
    if _instance is None:
        _instance = RelationshipsSkill()
    return _instance
