"""
Kiyomi Lite — Get to Know You Flow
Friendly onboarding conversation that builds a user profile.
Runs after first setup, asks warm questions, saves everything to memory.
"""
import json
import logging
from pathlib import Path
from datetime import datetime

from config import MEMORY_DIR, load_config, save_config

logger = logging.getLogger(__name__)

# State file tracks progress
_STATE_FILE = MEMORY_DIR / "onboarding_state.json"

# The questions — warm, conversational, not interrogation-like
QUESTIONS = [
    {
        "id": "name_confirm",
        "ask": "First things first — I have your name as {name}. Is that right, or do you go by something else? 😊",
        "category": "identity",
    },
    {
        "id": "about",
        "ask": "Tell me a little about yourself! What do you do, what's your life like? Just a few sentences is perfect. 🌟",
        "category": "about",
    },
    {
        "id": "family",
        "ask": "Who are the important people in your life? Family, partner, kids, pets — I want to know who matters to you! 💛",
        "category": "family",
    },
    {
        "id": "work",
        "ask": "What do you do for work? Or if you're retired/studying, what keeps you busy? 💼",
        "category": "work",
    },
    {
        "id": "interests",
        "ask": "What do you enjoy? Hobbies, shows you're binging, music, cooking, gardening — anything! 🎨",
        "category": "interests",
    },
    {
        "id": "goals",
        "ask": "Any goals you're working toward right now? Health, career, personal — big or small! 🎯",
        "category": "goals",
    },
    {
        "id": "help",
        "ask": "What would be most helpful for me to do for you day-to-day? Reminders? Health tracking? Budgeting? Research? Just being someone to talk to? 🤔",
        "category": "needs",
    },
    {
        "id": "health",
        "ask": "Any health things I should know about? Medications, conditions, fitness goals? Totally optional — only share what you're comfortable with! 💊",
        "category": "health",
    },
    {
        "id": "schedule",
        "ask": "What's your typical day like? When do you wake up, when's bedtime? This helps me know when to check in! ⏰",
        "category": "schedule",
    },
    {
        "id": "import_offer",
        "ask": "Last thing! 📋 If you have any conversations from Gemini, ChatGPT, or Claude that you want me to know about — just copy and paste them here anytime! I'll read through them and remember the important stuff.\n\nOr just say \"done\" and we're all set! 🌸",
        "category": "import",
    },
]


def _load_state() -> dict:
    """Load onboarding state."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text())
        except Exception:
            pass
    return {"step": 0, "answers": {}, "active": False, "complete": False}


def _save_state(state: dict):
    """Save onboarding state."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def is_onboarding_active() -> bool:
    """Check if we're in the middle of onboarding."""
    state = _load_state()
    return state.get("active", False) and not state.get("complete", False)


def is_onboarding_complete() -> bool:
    """Check if onboarding has been completed."""
    state = _load_state()
    return state.get("complete", False)


def start_onboarding(name: str) -> str:
    """Start the Get to Know You flow. Returns the first question."""
    state = {"step": 0, "answers": {}, "active": True, "complete": False}
    _save_state(state)
    
    question = QUESTIONS[0]["ask"].format(name=name)
    return (
        f"Hey {name}! 🌸 I'd love to get to know you so I can be YOUR assistant, "
        f"not a generic chatbot.\n\n"
        f"I'll ask a few quick questions — answer as much or as little as you want. "
        f"Say \"skip\" to skip any question!\n\n"
        f"{question}"
    )


def handle_onboarding_message(user_msg: str) -> str | None:
    """Process user's answer during onboarding.
    
    Returns the next question or completion message.
    Returns None if onboarding isn't active.
    """
    state = _load_state()
    if not state.get("active") or state.get("complete"):
        return None
    
    step = state.get("step", 0)
    config = load_config()
    name = config.get("name", "there")
    
    # Handle skip
    if user_msg.lower().strip() in ("skip", "next", "pass"):
        pass  # Don't save, just move on
    elif step < len(QUESTIONS):
        # Save the answer
        q = QUESTIONS[step]
        state["answers"][q["id"]] = user_msg
        
        # Special: if they corrected their name, update config
        if q["id"] == "name_confirm" and user_msg.lower().strip() not in ("yes", "yeah", "yep", "correct", "that's right", "thats right"):
            # They might have given a different name
            words = user_msg.strip().split()
            if len(words) <= 3 and not any(w in user_msg.lower() for w in ["yes", "correct", "right", "good"]):
                config["name"] = user_msg.strip().title()
                save_config(config)
                name = config["name"]
    
    # Move to next question
    step += 1
    state["step"] = step
    
    # Handle "done" during import step
    if step == len(QUESTIONS) and user_msg.lower().strip() in ("done", "nope", "no", "skip", "that's it", "thats it", "all good", "nothing"):
        state["complete"] = True
        state["active"] = False
        _save_state(state)
        _save_profile(state["answers"], name)
        return (
            f"All done, {name}! 🎉 I feel like I know you so much better now.\n\n"
            f"From here on out, just chat with me naturally. I'll remember everything "
            f"and keep getting better at helping you!\n\n"
            f"Need anything? Just ask! 🌸"
        )
    
    # Check for large paste (import content)
    if step >= len(QUESTIONS) - 1 and len(user_msg) > 500:
        # They pasted a conversation — extract facts and save
        facts = _save_pasted_import(user_msg, name)
        if facts:
            sample = "\n".join(f"• {f}" for f in facts[:5])
            more = f"\n...and {len(facts) - 5} more!" if len(facts) > 5 else ""
            return (
                f"Got it! I read through all of that and learned {len(facts)} things about you! 📖\n\n"
                f"Here's some of what I picked up:\n{sample}{more}\n\n"
                f"Want to paste more? Or say \"done\" when you're finished! 🌸"
            )
        return (
            f"Got it! I saved that conversation. 📖\n\n"
            f"Want to paste more? Or say \"done\" when you're finished! 🌸"
        )
    
    # If we've gone past all questions
    if step >= len(QUESTIONS):
        # Check if this is a paste during the import phase
        if len(user_msg) > 200:
            facts = _save_pasted_import(user_msg, name)
            count = f" ({len(facts)} facts)" if facts else ""
            return f"Saved{count}! Paste more or say \"done\" 🌸"
        
        state["complete"] = True
        state["active"] = False
        _save_state(state)
        _save_profile(state["answers"], name)
        return (
            f"All done, {name}! 🎉 I feel like I know you so much better now.\n\n"
            f"Just chat with me naturally from now on. I'll keep learning! 🌸"
        )
    
    # Ask next question
    _save_state(state)
    question = QUESTIONS[step]["ask"].format(name=name)
    
    # Add warm acknowledgment
    acks = [
        "Love that! ",
        "Great to know! ",
        "Awesome! ",
        "Thanks for sharing! ",
        "Got it! ",
        "Nice! ",
        "That's wonderful! ",
    ]
    import random
    ack = random.choice(acks) if step > 1 else ""
    
    return f"{ack}{question}"


def _save_profile(answers: dict, name: str):
    """Save onboarding answers as a user profile."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    profile_path = MEMORY_DIR / "profile.md"
    
    lines = [
        f"# About {name}",
        f"_Profile built {timestamp}_\n",
    ]
    
    category_labels = {
        "identity": "Identity",
        "about": "About",
        "family": "Family & Relationships",
        "work": "Work & Career",
        "interests": "Interests & Hobbies",
        "goals": "Goals",
        "needs": "How I Can Help",
        "health": "Health",
        "schedule": "Daily Schedule",
    }
    
    for q in QUESTIONS:
        answer = answers.get(q["id"])
        if answer and q["category"] in category_labels:
            label = category_labels[q["category"]]
            lines.append(f"## {label}")
            lines.append(f"{answer}\n")
    
    profile_path.write_text("\n".join(lines))
    logger.info(f"Saved onboarding profile for {name}")


def _save_pasted_import(text: str, name: str) -> list[str]:
    """Save pasted conversation text and extract facts to learned_facts.md.
    
    Returns list of extracted facts.
    """
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    
    # Save raw paste as backup
    import_path = MEMORY_DIR / f"pasted_import_{timestamp}.md"
    content = (
        f"# Pasted Conversation Import\n"
        f"_Pasted by {name} on {timestamp}_\n\n"
        f"{text[:10000]}\n"
    )
    import_path.write_text(content)
    logger.info(f"Saved pasted import ({len(text)} chars) to {import_path}")
    
    # Extract facts from the conversation
    facts = _extract_facts_from_paste(text)
    
    # Append extracted facts to learned_facts.md
    if facts:
        facts_path = MEMORY_DIR / "learned_facts.md"
        existing = facts_path.read_text() if facts_path.exists() else ""
        new_section = f"\n\n## Learned from pasted conversation ({timestamp})\n"
        for fact in facts:
            new_section += f"- {fact}\n"
        facts_path.write_text(existing + new_section)
        logger.info(f"Extracted {len(facts)} facts from paste")
    
    return facts


def _extract_facts_from_paste(text: str) -> list[str]:
    """Extract personal facts from pasted AI conversation text.
    
    Looks for user messages containing personal info patterns.
    Returns a list of fact strings.
    """
    import re
    
    facts = []
    seen = set()  # dedup
    
    # Split into lines, look for user messages
    lines = text.split("\n")
    
    # Personal info patterns (case-insensitive)
    patterns = [
        (r"(?:i am|i'm|im)\s+(.{5,60}?)(?:\.|!|\?|$)", "Is {0}"),
        (r"(?:my name is|call me|i go by)\s+(\w[\w\s]{1,30})", "Name/nickname: {0}"),
        (r"(?:i work|i'm working|im working)\s+(.{5,80})", "Work: {0}"),
        (r"(?:i live|i'm from|im from|i'm in|im in)\s+(.{3,50})", "Location: {0}"),
        (r"(?:i like|i love|i enjoy|i'm into|im into)\s+(.{3,80})", "Likes {0}"),
        (r"(?:i hate|i don't like|i dislike|can't stand)\s+(.{3,80})", "Dislikes {0}"),
        (r"(?:i have|i've got)\s+(?:a |an )?(\d+\s+(?:kid|child|son|daughter|dog|cat|pet).{0,40})", "Has {0}"),
        (r"(?:my (?:wife|husband|partner|spouse|girlfriend|boyfriend))\s+(.{2,40})", "Partner: {0}"),
        (r"(?:my (?:son|daughter|kid|child|baby))\s+(.{2,40})", "Child: {0}"),
        (r"(?:i'm (\d{1,3}) years old|i am (\d{1,3})(?:\s+years)?)", "Age: {0}"),
        (r"(?:my birthday|i was born)\s+(.{3,30})", "Birthday: {0}"),
        (r"(?:allergic to|allergy to)\s+(.{2,40})", "Allergy: {0}"),
        (r"(?:i take|i'm on|medication)\s+(.{3,40})", "Medication: {0}"),
        (r"(?:my goal|i want to|i'm trying to|im trying to)\s+(.{5,80})", "Goal: {0}"),
        (r"(?:my job|i'm a|im a|i work as)\s+(.{3,50})", "Job: {0}"),
        (r"(?:my favorite|favourite)\s+(.{3,60})", "Favorite: {0}"),
    ]
    
    for line in lines:
        line = line.strip()
        if not line or len(line) < 10:
            continue
        
        # Skip obvious AI responses (common AI patterns)
        lower = line.lower()
        if any(skip in lower for skip in [
            "as an ai", "i'm an ai", "as a language model", 
            "i don't have personal", "i can help", "here's",
            "here are", "certainly!", "of course!", "sure!",
            "let me help", "i'd be happy to",
        ]):
            continue
        
        for pattern, template in patterns:
            matches = re.finditer(pattern, line, re.IGNORECASE)
            for match in matches:
                # Get first non-None group
                captured = next((g for g in match.groups() if g), None)
                if captured:
                    captured = captured.strip().rstrip(".,!?;:")
                    if len(captured) > 2 and captured.lower() not in seen:
                        seen.add(captured.lower())
                        fact = template.format(captured)
                        facts.append(fact)
                        if len(facts) >= 25:  # Cap at 25 facts
                            return facts
    
    return facts
