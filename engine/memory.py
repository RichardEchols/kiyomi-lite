"""
Kiyomi Lite — Deep Memory System
"The AI that actually remembers you."

Memory is organized by category. Every meaningful fact gets saved,
deduplicated, and recalled. This is Kiyomi's core product.
"""
import difflib
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from engine.config import MEMORY_DIR, load_config

logger = logging.getLogger(__name__)

# ── Category definitions ────────────────────────────────────

CATEGORIES = {
    "identity": ("identity.md", "Identity"),
    "family": ("family.md", "Family"),
    "work": ("work.md", "Work"),
    "health": ("health.md", "Health"),
    "preferences": ("preferences.md", "Preferences"),
    "goals": ("goals.md", "Goals"),
    "schedule": ("schedule.md", "Schedule"),
    "other": ("other.md", "Other"),
}

# Priority order for loading (most important first)
LOAD_PRIORITY = [
    "identity", "family", "health", "work",
    "preferences", "goals", "schedule", "other",
]

# ── Fact patterns for extraction ────────────────────────────

_FACT_PATTERNS: list[tuple[re.Pattern, str, str]] = []


def _p(pattern: str, category: str, template: str, case_sensitive: bool = False):
    """Register a fact extraction pattern."""
    flags = 0 if case_sensitive else re.IGNORECASE
    _FACT_PATTERNS.append((re.compile(pattern, flags), category, template))


# Identity (case-sensitive for proper name detection)
_p(r"[Mm]y name is ([A-Z][a-z]+(?:\s[A-Z][a-z]+)*)", "identity", "Name: {0}", case_sensitive=True)
_p(r"(?:i'm|i am|im)\s+(\d{1,3})\s*(?:years?\s*old|yo|yrs)", "identity", "Age: {0}")
_p(r"i (?:live|stay|reside) in (.+?)(?:\.|$|,)", "identity", "Lives in: {0}")
_p(r"my (?:email|e-mail) (?:is|address is) (\S+@\S+)", "identity", "Email: {0}")
_p(r"my (?:phone|number|cell) (?:is|number is) ([\d\s\+\-\(\)]+)", "identity", "Phone: {0}")
_p(r"i(?:'m| am) (?:a |an )(?!allergic|trying|going|looking|hoping|planning|thinking|feeling|worried|scared|happy|sad|tired|sick|afraid|working|living|staying|moving|getting|running|eating|taking|having|doing|making|learning)([A-Za-z\s]+?)(?:\s+at\s+|\s+for\s+|\.|$|,)", "work", "Occupation: {0}")

# Family (case-sensitive for proper names)
_p(r"[Mm]y (?:wife|spouse|partner)(?:'s name)? (?:is |named )?([A-Z][a-z]+)", "family", "Wife/Partner: {0}", case_sensitive=True)
_p(r"[Mm]y (?:husband|spouse|partner)(?:'s name)? (?:is |named )?([A-Z][a-z]+)", "family", "Husband/Partner: {0}", case_sensitive=True)
_p(r"[Mm]y (?:son|boy)(?:'s name)? (?:is |named )?([A-Z][a-z]+)", "family", "Son: {0}", case_sensitive=True)
_p(r"[Mm]y (?:daughter|girl)(?:'s name)? (?:is |named )?([A-Z][a-z]+)", "family", "Daughter: {0}", case_sensitive=True)
_p(r"[Mm]y (?:mom|mother)(?:'s name)? (?:is |named )?([A-Z][a-z]+)", "family", "Mother: {0}", case_sensitive=True)
_p(r"[Mm]y (?:dad|father)(?:'s name)? (?:is |named )?([A-Z][a-z]+)", "family", "Father: {0}", case_sensitive=True)
_p(r"[Mm]y (?:brother)(?:'s name)? (?:is |named )?([A-Z][a-z]+)", "family", "Brother: {0}", case_sensitive=True)
_p(r"[Mm]y (?:sister)(?:'s name)? (?:is |named )?([A-Z][a-z]+)", "family", "Sister: {0}", case_sensitive=True)
_p(r"[Mm]y (?:dog|puppy)(?:'s name)? (?:is |named )?([A-Z][a-z]+)", "family", "Dog: {0}", case_sensitive=True)
_p(r"[Mm]y (?:cat|kitten)(?:'s name)? (?:is |named )?([A-Z][a-z]+)", "family", "Cat: {0}", case_sensitive=True)
_p(r"(?:we have|i have) (\d+) (?:kids?|children)", "family", "Number of children: {0}")
_p(r"(?:married|been with|together with) .+? for (\d+) years?", "family", "Married/together for {0} years")

# Work
_p(r"i work (?:at|for) (.+?)(?:\.|$|,| as)", "work", "Works at: {0}")
_p(r"i work as (?:a |an )?(.+?)(?:\.|$|,)", "work", "Works as: {0}")
_p(r"my (?:job|role|position|title) is (.+?)(?:\.|$|,)", "work", "Job title: {0}")
_p(r"i (?:manage|lead|run|own) (.+?)(?:\.|$|,)", "work", "Manages/owns: {0}")
_p(r"i(?:'ve| have) (?:been working|worked) (?:at |for |in )?(.+?) for (\d+)", "work", "Worked at {0} for {1} years")
_p(r"my (?:salary|income|pay) is (.+?)(?:\.|$|,)", "work", "Income: {0}")

# Health
_p(r"i take (.+?)(?:\s+(?:every|daily|twice|once|for|mg|ml))", "health", "Takes medication: {0}")
_p(r"(?:prescribed|prescription for) (.+?)(?:\.|$|,)", "health", "Prescribed: {0}")
_p(r"(?:i'm |i am )?allergic to (.+?)(?:\.|$|,)", "health", "Allergic to: {0}")
_p(r"my doctor(?:'s name)? is ((?:Dr\.?\s*)?[A-Za-z\s]+?)(?:\.|$|,)", "health", "Doctor: {0}")
_p(r"i have (?:been diagnosed with |a |an )?(?:chronic |severe |mild )?(\w+(?:\s\w+){0,3}?)(?:\s+(?:disease|syndrome|disorder|condition|issues?|problems?|pain))(?:\.|$|,)", "health", "Condition: {0}")
_p(r"i have (diabetes|asthma|epilepsy|arthritis|hypertension|depression|anxiety|insomnia|migraines?|fibromyalgia|ADHD|PTSD|anemia|gout|eczema|psoriasis|vertigo|tinnitus|IBS|celiac)", "health", "Condition: {0}")
_p(r"i(?:'ve| have) been diagnosed with (.+?)(?:\.|$|,)", "health", "Diagnosed with: {0}")
_p(r"my blood type is (.+?)(?:\.|$|,)", "health", "Blood type: {0}")
_p(r"(?:my )?blood pressure (?:is |was |reading )?(\d{2,3})\s*(?:over|/)\s*(\d{2,3})", "health", "Blood pressure: {0}/{1}")
_p(r"i (?:speak|know|am fluent in) (.+?)(?:\.|$|,| fluently)", "identity", "Languages: {0}")
_p(r"my (?:mother|father|brother|sister)[\s-]in[\s-]law(?:'s name)? (?:is |named |lives |works )(.+?)(?:\.|$|,)", "family", "In-law: {0}")

# Preferences
_p(r"i (?:like|love|enjoy) (.+?)(?:\.|$|,)", "preferences", "Likes: {0}")
_p(r"i (?:hate|dislike|don't like|can't stand) (.+?)(?:\.|$|,)", "preferences", "Dislikes: {0}")
_p(r"my favorite (.+?) is (.+?)(?:\.|$|,)", "preferences", "Favorite {0}: {1}")
_p(r"i prefer (.+?)(?:\.|$|,| over| to| instead)", "preferences", "Prefers: {0}")

# Goals
_p(r"i (?:want to|wanna|need to|gotta|have to) (.+?)(?:\.|$)", "goals", "Goal: {0}")
_p(r"my goal is (?:to )?(.+?)(?:\.|$)", "goals", "Goal: {0}")
_p(r"i(?:'m| am) (?:trying|working) (?:to|on) (.+?)(?:\.|$)", "goals", "Working on: {0}")
_p(r"(?:saving|planning) (?:for|to) (.+?)(?:\.|$)", "goals", "Planning: {0}")

# Schedule
_p(r"every (?:morning|day) i (.+?)(?:\.|$)", "schedule", "Daily routine: {0}")
_p(r"every (?:evening|night) i (.+?)(?:\.|$)", "schedule", "Evening routine: {0}")
_p(r"my birthday is (.+?)(?:\.|$)", "schedule", "Birthday: {0}")
_p(r"my anniversary is (.+?)(?:\.|$)", "schedule", "Anniversary: {0}")
_p(r"i (?:usually|always|normally) (.+?) (?:at|around|by) (\d.+?)(?:\.|$)", "schedule", "Usually {0} at {1}")

# Dates & events (for smart follow-ups)
_p(r"(?:have|got|there'?s) (?:a |an |my )?(.+?) (?:on |this |next )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", "schedule", "{0} on {1}")
_p(r"(?:have|got|there'?s) (?:a |an |my )?(.+?) (?:on |at )?(\w+ \d+)", "schedule", "{0} on {1}")
_p(r"(?:interview|meeting|appointment|deposition|hearing|court date|presentation|exam|surgery|flight) (?:is |on |at )?(.+?)(?:\.|$)", "schedule", "Upcoming: {0}")
_p(r"(.+?)(?:'s| has a) birthday (?:is )?(?:on )?(.+?)(?:\.|$)", "schedule", "{0}'s birthday: {1}")
_p(r"(?:our |my )anniversary is (.+?)(?:\.|$)", "schedule", "Anniversary: {0}")


# ── Core functions ──────────────────────────────────────────

def save_fact(fact: str, category: str, user_dir: Optional[Path] = None) -> bool:
    """Save a fact to the appropriate category file.
    Returns False if duplicate (>80% similar to existing fact).
    
    Args:
        fact: The fact to save
        category: Category to save it under
        user_dir: Optional user-specific memory directory. If None, uses default MEMORY_DIR.
    """
    fact = (fact or "").strip()
    if not fact:
        return False

    category = category.lower().strip()
    if category not in CATEGORIES:
        category = "other"

    filename, display_name = CATEGORIES[category]
    
    # Use user-specific directory or default
    memory_dir = user_dir if user_dir is not None else MEMORY_DIR
    filepath = memory_dir / filename
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Load existing facts
    existing_facts = []
    if filepath.exists():
        content = filepath.read_text(encoding="utf-8", errors="replace")
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                # Remove "- " prefix, then remove "[YYYY-MM-DD HH:MM] " timestamp
                fact_text = stripped[2:]
                fact_text = re.sub(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] ", "", fact_text).strip()
                if fact_text:
                    existing_facts.append(fact_text)
    else:
        content = f"# {display_name}\n\n"

    # Check for duplicates using SequenceMatcher
    fact_lower = fact.lower()
    for existing in existing_facts:
        ratio = difflib.SequenceMatcher(None, fact_lower, existing.lower()).ratio()
        if ratio > 0.8:
            logger.debug(f"Duplicate fact skipped (ratio={ratio:.2f}): {fact[:60]}")
            return False

    # Append the new fact
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_line = f"- [{timestamp}] {fact}\n"

    if not content.endswith("\n"):
        content += "\n"
    content += new_line

    filepath.write_text(content, encoding="utf-8")
    logger.info(f"Saved fact to {category}: {fact[:60]}")
    return True


def load_all_memory(user_dir: Optional[Path] = None) -> str:
    """Load ALL memory for system prompt injection.
    
    Returns formatted string with section headers.
    Capped at 8000 chars. Prioritized by importance.
    
    Args:
        user_dir: Optional user-specific memory directory. If None, uses default MEMORY_DIR.
    """
    # Use user-specific directory or default
    memory_dir = user_dir if user_dir is not None else MEMORY_DIR
    memory_dir.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []
    total_chars = 0
    max_chars = 8000

    # 1. Category files (in priority order)
    for cat_key in LOAD_PRIORITY:
        if total_chars >= max_chars:
            break
        filename, display_name = CATEGORIES[cat_key]
        filepath = memory_dir / filename
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8", errors="replace").strip()
            if content and content != f"# {display_name}":
                # Remove the header line for cleaner injection
                lines = content.splitlines()
                body = "\n".join(l for l in lines if not l.startswith("# ")).strip()
                if body:
                    remaining = max_chars - total_chars
                    section = f"**{display_name}:**\n{body[:remaining]}"
                    sections.append(section)
                    total_chars += len(section)

    # 2. Profile file (legacy support)
    profile_path = memory_dir / "profile.md"
    if profile_path.exists() and total_chars < max_chars:
        content = profile_path.read_text(encoding="utf-8", errors="replace").strip()
        if content:
            remaining = max_chars - total_chars
            sections.insert(0, f"**Profile:**\n{content[:remaining]}")
            total_chars += min(len(content), remaining)

    # 3. Documents (last 5)
    docs_dir = memory_dir / "documents"
    if docs_dir.exists() and total_chars < max_chars:
        doc_files = sorted(
            [f for f in docs_dir.iterdir() if f.suffix == ".md"],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )[:5]
        for doc_file in doc_files:
            if total_chars >= max_chars:
                break
            content = doc_file.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                remaining = max_chars - total_chars
                truncated = content[:min(2000, remaining)]
                sections.append(f"**Saved Document ({doc_file.stem}):**\n{truncated}")
                total_chars += len(truncated) + 30

    # 4. Recent conversations (last 2 days)
    convos_dir = memory_dir / "conversations"
    if convos_dir.exists() and total_chars < max_chars:
        today = datetime.now()
        for i in range(2):
            if total_chars >= max_chars:
                break
            day = today - timedelta(days=i)
            day_file = convos_dir / f"{day.strftime('%Y-%m-%d')}.md"
            if day_file.exists():
                content = day_file.read_text(encoding="utf-8", errors="replace")
                remaining = max_chars - total_chars
                # Take last N chars to get most recent conversations
                truncated = content[-min(800, remaining):]
                label = "Today" if i == 0 else "Yesterday"
                sections.append(f"**Recent ({label}):**\n{truncated}")
                total_chars += len(truncated) + 20

    return "\n\n".join(sections) if sections else ""


def load_category(category: str) -> str:
    """Load and return contents of a specific category file."""
    if category not in CATEGORIES:
        return ""
    filename, _ = CATEGORIES[category]
    filepath = MEMORY_DIR / filename
    if filepath.exists():
        return filepath.read_text(encoding="utf-8", errors="replace")
    return ""


def extract_facts_from_message(message: str, user_dir: Optional[Path] = None) -> list[tuple[str, str]]:
    """Extract (fact, category) pairs from a user message.
    
    Uses 30+ keyword patterns to detect personal info.
    Returns clean, concise fact strings.
    """
    facts: list[tuple[str, str]] = []
    
    for pattern, category, template in _FACT_PATTERNS:
        match = pattern.search(message)
        if match:
            groups = match.groups()
            # Clean up the captured groups
            cleaned = [g.strip().rstrip(".,!?") for g in groups if g]
            if cleaned:
                try:
                    fact = template.format(*cleaned)
                except (IndexError, KeyError):
                    fact = template.format(cleaned[0]) if cleaned else None
                
                if fact and len(fact) > 3 and len(fact) < 200:
                    # Skip overly generic extractions
                    skip_words = {"it", "that", "this", "something", "things", "stuff"}
                    first_capture = cleaned[0].lower().strip() if cleaned else ""
                    if first_capture not in skip_words:
                        facts.append((fact, category))
    
    return facts


def migrate_old_memory(user_dir: Optional[Path] = None):
    """One-time migration: parse learned_facts.md into categorized files.
    
    Args:
        user_dir: Optional user-specific memory directory. If None, uses default MEMORY_DIR.
    """
    # Use user-specific directory or default
    memory_dir = user_dir if user_dir is not None else MEMORY_DIR
    old_file = memory_dir / "learned_facts.md"
    if not old_file.exists():
        return

    logger.info("Migrating learned_facts.md to categorized memory files...")
    content = old_file.read_text(encoding="utf-8", errors="replace")

    migrated = 0
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue

        # Try to extract category tag: "- [2026-02-02 10:00] [health] fact text"
        cat_match = re.match(
            r"^- \[[\d\-\s:]+\]\s*\[(\w+)\]\s*(.+)$", stripped
        )
        if cat_match:
            category = cat_match.group(1).lower()
            fact = cat_match.group(2).strip()
        else:
            # No category tag — extract fact text and auto-categorize
            fact_match = re.match(r"^- (?:\[[\d\-\s:]+\]\s*)?(.+)$", stripped)
            if not fact_match:
                continue
            fact = fact_match.group(1).strip()
            category = _guess_category(fact)

        if fact:
            save_fact(fact, category, user_dir)
            migrated += 1

    # Rename old file
    old_file.rename(memory_dir / "learned_facts.md.migrated")
    logger.info(f"Migrated {migrated} facts from learned_facts.md")


def _guess_category(fact: str) -> str:
    """Guess category for a fact without a tag."""
    lower = fact.lower()
    if any(w in lower for w in ["name:", "age:", "email:", "phone:", "live"]):
        return "identity"
    if any(w in lower for w in ["wife", "husband", "son", "daughter", "mom", "dad", "pet", "dog", "cat", "kid", "child"]):
        return "family"
    if any(w in lower for w in ["work", "job", "company", "office", "salary", "career"]):
        return "work"
    if any(w in lower for w in ["health", "med", "doctor", "allerg", "diagnos", "blood", "prescription"]):
        return "health"
    if any(w in lower for w in ["like", "love", "hate", "prefer", "favorite"]):
        return "preferences"
    if any(w in lower for w in ["goal", "want to", "plan", "save for", "dream"]):
        return "goals"
    if any(w in lower for w in ["every", "morning", "routine", "birthday", "anniversary", "schedule"]):
        return "schedule"
    return "other"


# ── Conversation logging ────────────────────────────────────

def get_today_file(user_dir: Optional[Path] = None) -> Path:
    """Get today's conversation log path (in conversations/ subfolder).
    
    Args:
        user_dir: Optional user-specific memory directory. If None, uses default MEMORY_DIR.
    """
    config = load_config()
    tz_name = config.get("timezone", "UTC")
    try:
        import pytz
        tz = pytz.timezone(tz_name)
        today = datetime.now(tz).strftime("%Y-%m-%d")
    except Exception:
        today = datetime.now().strftime("%Y-%m-%d")

    # Use user-specific directory or default
    memory_dir = user_dir if user_dir is not None else MEMORY_DIR
    convos_dir = memory_dir / "conversations"
    convos_dir.mkdir(parents=True, exist_ok=True)
    return convos_dir / f"{today}.md"


def log_conversation(user_msg: str, bot_response: str, user_dir: Optional[Path] = None):
    """Log a conversation turn to today's file.
    
    Args:
        user_msg: User's message
        bot_response: Bot's response
        user_dir: Optional user-specific memory directory. If None, uses default MEMORY_DIR.
    """
    try:
        today_file = get_today_file(user_dir)
        config = load_config()
        tz_name = config.get("timezone", "UTC")
        try:
            import pytz
            tz = pytz.timezone(tz_name)
            now = datetime.now(tz)
        except Exception:
            now = datetime.now()

        timestamp = now.strftime("%H:%M")
        entry = f"\n## {timestamp}\n**User:** {user_msg[:300]}\n**Bot:** {bot_response[:300]}\n"

        if today_file.exists():
            content = today_file.read_text(encoding="utf-8", errors="replace")
        else:
            name = config.get("name", "User")
            today = now.strftime("%Y-%m-%d")
            content = f"# Conversations — {today}\n\nChat with {name}.\n"

        content += entry
        today_file.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.error(f"Memory log failed: {e}")


def get_recent_context(days: int = 2, user_dir: Optional[Path] = None) -> str:
    """Get recent conversation context.
    
    Args:
        days: Number of days of context to retrieve
        user_dir: Optional user-specific memory directory. If None, uses default MEMORY_DIR.
    """
    context_parts = []
    try:
        import pytz
        config = load_config()
        tz = pytz.timezone(config.get("timezone", "UTC"))
        today = datetime.now(tz)
    except Exception:
        today = datetime.now()

    # Use user-specific directory or default
    memory_dir = user_dir if user_dir is not None else MEMORY_DIR
    convos_dir = memory_dir / "conversations"
    for i in range(days):
        day = today - timedelta(days=i)
        day_file = convos_dir / f"{day.strftime('%Y-%m-%d')}.md"
        if day_file.exists():
            content = day_file.read_text(encoding="utf-8", errors="replace")
            context_parts.append(content[-800:])

    return "\n---\n".join(context_parts) if context_parts else ""


# ── Profile (legacy support) ────────────────────────────────

def save_profile(info: dict, user_dir: Optional[Path] = None):
    """Save user profile info to memory.
    
    Args:
        info: Dictionary of profile information
        user_dir: Optional user-specific memory directory. If None, uses default MEMORY_DIR.
    """
    # Use user-specific directory or default
    memory_dir = user_dir if user_dir is not None else MEMORY_DIR
    memory_dir.mkdir(parents=True, exist_ok=True)
    profile_file = memory_dir / "profile.md"
    config = load_config()
    name = config.get("name", "User")
    content = f"# About {name}\n\n"
    for key, value in info.items():
        content += f"- **{key}:** {value}\n"
    profile_file.write_text(content, encoding="utf-8")


# ── Memory summary ──────────────────────────────────────────

def get_memory_summary(user_dir: Optional[Path] = None) -> dict:
    """Return summary stats: fact count per category, last updated dates.
    
    Args:
        user_dir: Optional user-specific memory directory. If None, uses default MEMORY_DIR.
    """
    summary = {}
    # Use user-specific directory or default
    memory_dir = user_dir if user_dir is not None else MEMORY_DIR
    memory_dir.mkdir(parents=True, exist_ok=True)
    for cat_key, (filename, display_name) in CATEGORIES.items():
        filepath = memory_dir / filename
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8", errors="replace")
            fact_count = sum(1 for line in content.splitlines() if line.strip().startswith("- "))
            mtime = datetime.fromtimestamp(filepath.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            summary[cat_key] = {"facts": fact_count, "last_updated": mtime}
        else:
            summary[cat_key] = {"facts": 0, "last_updated": None}
    return summary


# ── Contact / Person Lookup ──────────────────────────────────

def lookup_person(name: str, user_dir: Optional[Path] = None) -> str:
    """Search all memory files for mentions of a person.

    Performs case-insensitive partial matching across every category file
    in the user's memory directory. Returns a formatted summary of every matching fact,
    or a friendly "nothing found" message.
    
    Args:
        name: Name to search for
        user_dir: Optional user-specific memory directory. If None, uses default MEMORY_DIR.
    """
    name = (name or "").strip()
    if not name:
        return "I need a name to search for."

    # Use user-specific directory or default
    memory_dir = user_dir if user_dir is not None else MEMORY_DIR
    memory_dir.mkdir(parents=True, exist_ok=True)
    name_lower = name.lower()
    matches: list[tuple[str, str]] = []  # (category_display, fact_text)

    for cat_key in LOAD_PRIORITY:
        filename, display_name = CATEGORIES[cat_key]
        filepath = memory_dir / filename
        if not filepath.exists():
            continue
        for line in filepath.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            # Remove leading "- " and optional timestamp
            fact_text = stripped[2:]
            fact_text = re.sub(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] ", "", fact_text).strip()
            if fact_text and name_lower in fact_text.lower():
                matches.append((display_name, fact_text))

    # Also search documents folder
    docs_dir = memory_dir / "documents"
    if docs_dir.exists():
        for doc_file in docs_dir.iterdir():
            if doc_file.suffix != ".md":
                continue
            content = doc_file.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                if name_lower in line.lower() and line.strip():
                    matches.append(("Documents", line.strip()))

    # Also search recent conversations
    convos_dir = memory_dir / "conversations"
    if convos_dir.exists():
        convo_files = sorted(
            [f for f in convos_dir.iterdir() if f.suffix == ".md"],
            key=lambda f: f.name, reverse=True,
        )[:7]
        for cf in convo_files:
            content = cf.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                if name_lower in line.lower() and line.strip():
                    matches.append((f"Chat {cf.stem}", line.strip()))

    if not matches:
        return f"I don't have any information about {name} yet."

    lines = [f"🔍 Here's what I know about **{name}**:\n"]
    current_cat: Optional[str] = None
    for cat, fact in matches[:30]:
        if cat != current_cat:
            lines.append(f"**{cat}:**")
            current_cat = cat
        lines.append(f"  • {fact}")
    return "\n".join(lines)


# ── Export Memory ────────────────────────────────────────────

CATEGORY_EMOJI = {
    "identity": "🪪",
    "family": "👨‍👩‍👧‍👦",
    "work": "💼",
    "health": "🏥",
    "preferences": "⭐",
    "goals": "🎯",
    "schedule": "📅",
    "other": "📝",
}


def export_memory(user_dir: Optional[Path] = None) -> str:
    """Build a beautiful markdown export of the user's entire memory profile.

    Returns a ready-to-send markdown string.  The caller is responsible for
    converting it to a file / document if desired.
    
    Args:
        user_dir: Optional user-specific memory directory. If None, uses default MEMORY_DIR.
    """
    # Use user-specific directory or default
    memory_dir = user_dir if user_dir is not None else MEMORY_DIR
    memory_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    sections: list[str] = []
    total_facts = 0
    categories_with_data = 0
    oldest_date: Optional[str] = None

    for cat_key in LOAD_PRIORITY:
        filename, display_name = CATEGORIES[cat_key]
        filepath = memory_dir / filename
        if not filepath.exists():
            continue

        content = filepath.read_text(encoding="utf-8", errors="replace")
        facts: list[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            raw = stripped[2:]
            # Extract timestamp for stats, then strip it for display
            ts_match = re.match(r"^\[(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\] (.+)$", raw)
            if ts_match:
                ts_date = ts_match.group(1)
                fact_text = ts_match.group(2).strip()
                if oldest_date is None or ts_date < oldest_date:
                    oldest_date = ts_date
            else:
                fact_text = raw.strip()
            if fact_text:
                facts.append(fact_text)

        if not facts:
            continue

        total_facts += len(facts)
        categories_with_data += 1
        emoji = CATEGORY_EMOJI.get(cat_key, "📌")
        section_lines = [f"## {emoji} {display_name}\n"]
        for f in facts:
            section_lines.append(f"- {f}")
        sections.append("\n".join(section_lines))

    # Build the full document
    header = (
        "# My Personal Profile\n\n"
        f"*Generated by Kiyomi on {now.strftime('%B %d, %Y at %I:%M %p')}*\n"
    )

    if not sections:
        return header + "\n---\n\nNo memories saved yet. Start chatting and I'll remember!\n"

    body = "\n\n".join(sections)

    # Summary stats
    oldest_display = oldest_date if oldest_date else "N/A"
    stats = (
        "\n\n---\n\n"
        "### 📊 Summary\n\n"
        f"| Stat | Value |\n"
        f"|------|-------|\n"
        f"| Total facts | {total_facts} |\n"
        f"| Categories with data | {categories_with_data} / {len(CATEGORIES)} |\n"
        f"| Oldest memory | {oldest_display} |\n"
    )

    return header + "\n---\n\n" + body + stats


# ── Legacy compatibility ────────────────────────────────────

def extract_and_remember(user_msg: str, bot_response: str, user_dir: Optional[Path] = None):
    """Extract important facts from conversation and save to memory.
    Called silently after each conversation turn.
    
    Args:
        user_msg: User's message
        bot_response: Bot's response
        user_dir: Optional user-specific memory directory. If None, uses default MEMORY_DIR.
    """
    facts = extract_facts_from_message(user_msg, user_dir)
    for fact, category in facts:
        save_fact(fact, category, user_dir)


# Run migration on import (safe — only runs once if old file exists)
try:
    migrate_old_memory()
except Exception as e:
    logger.warning(f"Memory migration failed: {e}")
