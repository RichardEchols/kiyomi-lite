"""
Kiyomi Lite — Import Your Brain
Parse exported AI chat histories and extract what matters.
Supports: ChatGPT (conversations.json), Gemini (Takeout), Claude exports.
User drags a file → we silently learn who they are.
"""
import json
import logging
import re
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Where Kiyomi stores memory
MEMORY_DIR = Path.home() / ".kiyomi" / "memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)


class ImportResult:
    """Summary of what was imported."""
    def __init__(self):
        self.conversations: int = 0
        self.messages: int = 0
        self.facts: list[str] = []
        self.errors: list[str] = []
        self.source: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "conversations": self.conversations,
            "messages": self.messages,
            "facts_count": len(self.facts),
            "facts": self.facts[:50],  # Cap at 50 for display
            "errors": self.errors,
            "source": self.source,
        }


def import_file(file_path: str) -> ImportResult:
    """Main entry point. Detect format and import.
    
    Args:
        file_path: Path to uploaded file (.json or .zip)
    
    Returns:
        ImportResult with summary of what was imported
    """
    path = Path(file_path)
    result = ImportResult()

    if not path.exists():
        result.errors.append(f"File not found: {file_path}")
        return result

    try:
        if path.suffix == ".zip":
            return _import_zip(path)
        elif path.suffix == ".json":
            return _import_json(path)
        else:
            result.errors.append(f"Unsupported file type: {path.suffix}")
            return result
    except Exception as e:
        logger.error(f"Import failed: {e}")
        result.errors.append(f"Import failed: {str(e)}")
        return result


def _import_zip(zip_path: Path) -> ImportResult:
    """Import from a zip file (e.g., Google Takeout, ChatGPT full export)."""
    result = ImportResult()

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmpdir)
        except zipfile.BadZipFile:
            result.errors.append("Invalid zip file")
            return result

        tmppath = Path(tmpdir)

        # Look for known files inside the zip
        # ChatGPT export: conversations.json at root
        chatgpt_file = tmppath / "conversations.json"
        if chatgpt_file.exists():
            return _import_chatgpt(chatgpt_file)

        # Google Takeout: look for Gemini/Bard JSON files
        for json_file in tmppath.rglob("*.json"):
            name_lower = json_file.name.lower()
            if "gemini" in name_lower or "bard" in name_lower or "myactivity" in name_lower:
                sub_result = _import_gemini_takeout(json_file)
                result.conversations += sub_result.conversations
                result.messages += sub_result.messages
                result.facts.extend(sub_result.facts)
                result.source = "gemini_takeout"

        # If we found nothing, try treating all JSON files as conversations
        if result.conversations == 0:
            for json_file in tmppath.rglob("*.json"):
                sub_result = _import_json(json_file)
                result.conversations += sub_result.conversations
                result.messages += sub_result.messages
                result.facts.extend(sub_result.facts)

        if result.conversations == 0:
            result.errors.append("No recognizable chat exports found in zip")

    return result


def _import_json(json_path: Path) -> ImportResult:
    """Auto-detect JSON format and import."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        result = ImportResult()
        result.errors.append(f"Could not parse JSON: {str(e)[:100]}")
        return result

    # ChatGPT format: list of conversation objects with "mapping"
    if isinstance(data, list) and len(data) > 0:
        first = data[0]
        if isinstance(first, dict) and "mapping" in first:
            return _import_chatgpt(json_path, data=data)
        # Claude format: list of objects with "chat_messages"
        if isinstance(first, dict) and "chat_messages" in first:
            return _import_claude(data)
        # Generic: list of message-like objects
        if isinstance(first, dict) and ("content" in first or "text" in first or "message" in first):
            return _import_generic_messages(data)

    # Single conversation object
    if isinstance(data, dict):
        if "mapping" in data:
            return _import_chatgpt(json_path, data=[data])
        if "chat_messages" in data:
            return _import_claude([data])

    # Fallback: extract any text we find
    return _import_raw_json(data)


def _import_chatgpt(json_path: Path, data: list = None) -> ImportResult:
    """Import ChatGPT conversations.json format."""
    result = ImportResult()
    result.source = "chatgpt"

    if data is None:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

    all_user_messages = []

    for conv in data:
        if not isinstance(conv, dict):
            continue
        mapping = conv.get("mapping", {})
        if not mapping:
            continue

        result.conversations += 1
        for node_id, node in mapping.items():
            msg = node.get("message")
            if not msg:
                continue
            role = msg.get("author", {}).get("role", "")
            content = msg.get("content", {})

            text = ""
            if isinstance(content, dict):
                parts = content.get("parts", [])
                text = " ".join(str(p) for p in parts if isinstance(p, str))
            elif isinstance(content, str):
                text = content

            if text.strip():
                result.messages += 1
                if role == "user":
                    all_user_messages.append(text.strip())

    # Extract facts from user messages
    result.facts = _extract_facts(all_user_messages)
    _save_import(result, all_user_messages)
    return result


def _import_claude(data: list) -> ImportResult:
    """Import Claude export format."""
    result = ImportResult()
    result.source = "claude"
    all_user_messages = []

    for conv in data:
        if not isinstance(conv, dict):
            continue
        messages = conv.get("chat_messages", [])
        if messages:
            result.conversations += 1
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            result.messages += 1
            if msg.get("sender") == "human":
                text = msg.get("text", "")
                if text.strip():
                    all_user_messages.append(text.strip())

    result.facts = _extract_facts(all_user_messages)
    _save_import(result, all_user_messages)
    return result


def _import_gemini_takeout(json_path: Path) -> ImportResult:
    """Import Google Takeout Gemini/Bard activity."""
    result = ImportResult()
    result.source = "gemini_takeout"
    all_user_messages = []

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return result

    items = data if isinstance(data, list) else [data]

    for item in items:
        if not isinstance(item, dict):
            continue
        # Google Takeout Activity format
        title = item.get("title", "")
        if title:
            result.conversations += 1
        # textSegments or subtitles contain content
        for sub in item.get("subtitles", []):
            text = sub.get("name", "")
            if text.strip():
                result.messages += 1
                all_user_messages.append(text.strip())

    result.facts = _extract_facts(all_user_messages)
    _save_import(result, all_user_messages)
    return result


def _import_generic_messages(data: list) -> ImportResult:
    """Import a generic list of message objects."""
    result = ImportResult()
    result.source = "generic"
    all_user_messages = []

    result.conversations = 1
    for msg in data:
        if not isinstance(msg, dict):
            continue
        text = msg.get("content") or msg.get("text") or msg.get("message") or ""
        role = msg.get("role") or msg.get("sender") or msg.get("author") or "user"
        if text.strip():
            result.messages += 1
            if role in ("user", "human"):
                all_user_messages.append(text.strip())

    result.facts = _extract_facts(all_user_messages)
    _save_import(result, all_user_messages)
    return result


def _import_raw_json(data) -> ImportResult:
    """Last resort: extract any string values from arbitrary JSON."""
    result = ImportResult()
    result.source = "raw"
    texts = []

    def _walk(obj, depth=0):
        if depth > 10:
            return
        if isinstance(obj, str) and len(obj) > 20:
            texts.append(obj)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, depth + 1)
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v, depth + 1)

    _walk(data)
    result.messages = len(texts)
    result.conversations = 1 if texts else 0
    result.facts = _extract_facts(texts[:500])  # cap for performance
    _save_import(result, texts[:500])
    return result


# --- Fact Extraction ---

# Patterns that suggest personal information
FACT_PATTERNS = [
    (r"(?:my name is|i'm called|call me)\s+([A-Z][a-z]+)", "name"),
    (r"(?:i live in|i'm from|i'm based in)\s+(.+?)(?:\.|,|\band\b|!|$)", "location"),
    (r"(?:i work at|i work for|my job is|i'm a)\s+(.+?)(?:\.|,|\band\b|!|$)", "work"),
    (r"(?:my (?:wife|husband|partner|spouse)(?:'s name)? is)\s+(\w+)", "partner"),
    (r"(?:my (?:son|daughter|kid|child)(?:'s name)? is)\s+(\w+)", "family"),
    (r"(?:i (?:really )?(?:love|enjoy|like))\s+(.+?)(?:\.|,|\band\b|!|$)", "likes"),
    (r"(?:i (?:hate|dislike|can't stand))\s+(.+?)(?:\.|,|\band\b|!|$)", "dislikes"),
    (r"(?:my budget is|i (?:make|earn))\s+(.+?)(?:\.|,|\band\b|!|$)", "finances"),
    (r"(?:my email is|email me at)\s+([\w.+-]+@[\w.-]+)", "email"),
    (r"(?:my birthday is|born on)\s+(.+?)(?:\.|,|\band\b|!|$)", "birthday"),
    (r"(?:i'm learning|i'm studying|i want to learn)\s+(.+?)(?:\.|,|\band\b|!|$)", "learning"),
    (r"(?:my goal is|i want to)\s+(.+?)(?:\.|,|\band\b|!|$)", "goals"),
    (r"(?:i'm allergic to|i can't eat)\s+(.+?)(?:\.|,|\band\b|!|$)", "health"),
    (r"(?:my favorite (?:\w+) is)\s+(.+?)(?:\.|,|\band\b|!|$)", "favorites"),
    (r"(?:i have (?:a |an )?(?:dog|cat|pet) (?:named|called))\s+(\w+)", "pets"),
]


def _extract_facts(messages: list[str]) -> list[str]:
    """Extract personal facts from user messages using pattern matching."""
    facts = []
    seen = set()

    for msg in messages:
        msg_lower = msg.lower()
        for pattern, category in FACT_PATTERNS:
            matches = re.findall(pattern, msg_lower, re.IGNORECASE)
            for match in matches:
                match_clean = match.strip()[:100]
                if match_clean and match_clean not in seen and len(match_clean) > 2:
                    facts.append(f"{category}: {match_clean}")
                    seen.add(match_clean)

    return facts


# --- Save to Memory ---

def _save_import(result: ImportResult, user_messages: list[str]):
    """Save imported data to Kiyomi's memory system."""
    if not result.facts and not user_messages:
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Save extracted facts to profile
    if result.facts:
        profile_path = MEMORY_DIR / "imported_profile.md"
        lines = [
            f"# Imported Profile",
            f"_Imported from {result.source} on {timestamp}_",
            f"_Found {result.conversations} conversations, {result.messages} messages_\n",
        ]
        # Group facts by category
        categories: dict[str, list[str]] = {}
        for fact in result.facts:
            cat, _, detail = fact.partition(": ")
            categories.setdefault(cat, []).append(detail)

        for cat, details in sorted(categories.items()):
            lines.append(f"## {cat.title()}")
            for detail in details:
                lines.append(f"- {detail}")
            lines.append("")

        profile_path.write_text("\n".join(lines))
        logger.info(f"Saved {len(result.facts)} facts to {profile_path}")

    # Save a conversation summary (sample of user messages for context)
    if user_messages:
        summary_path = MEMORY_DIR / "import_summary.md"
        sample = user_messages[:100]  # Keep first 100 messages as sample
        lines = [
            f"# Chat Import Summary",
            f"_Imported from {result.source} on {timestamp}_",
            f"_Total: {result.conversations} conversations, {result.messages} messages_\n",
            "## Sample User Messages\n",
        ]
        for msg in sample:
            # Truncate long messages
            short = msg[:200] + "..." if len(msg) > 200 else msg
            lines.append(f"- {short}")

        summary_path.write_text("\n".join(lines))
        logger.info(f"Saved import summary to {summary_path}")


# --- CLI for testing ---

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python import_brain.py <file.json|file.zip>")
        sys.exit(1)

    result = import_file(sys.argv[1])
    print(f"\nSource: {result.source}")
    print(f"Conversations: {result.conversations}")
    print(f"Messages: {result.messages}")
    print(f"Facts extracted: {len(result.facts)}")
    if result.facts:
        print("\nFacts found:")
        for f in result.facts[:20]:
            print(f"  • {f}")
    if result.errors:
        print(f"\nErrors: {result.errors}")
