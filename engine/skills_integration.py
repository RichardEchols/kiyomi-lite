"""
Kiyomi Lite — Skills Integration
Bridges the skill system with the bot.
This file handles:
1. Running skills after each message (post-message hook)
2. Building skill context for the AI system prompt
3. Starting the proactive check loop
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_all_skills() -> list:
    """Load all available skills. Graceful if skills aren't installed yet."""
    skills = []
    try:
        from skills import get_all_skills as _get_all
        skills = _get_all()
    except ImportError:
        try:
            from engine.skills import get_all_skills as _get_all
            skills = _get_all()
        except ImportError:
            logger.warning("Skills module not found — running without skills")
    except Exception as e:
        logger.error(f"Failed to load skills: {e}")
    return skills


def run_post_message_hook(user_msg: str, bot_response: str):
    """Run all skills' detect + extract after each message.
    
    Called silently after every conversation turn.
    If a skill detects relevant data, it extracts and stores it.
    """
    for skill in get_all_skills():
        try:
            if skill.detect(user_msg):
                data = skill.extract(user_msg, bot_response)
                if not data:
                    continue
                
                # Handle different extract return formats:
                # 1. Health returns: {"skill": "health", "entries": [{"category": "vitals", "entry": {...}}]}
                # 2. Budget returns: {"type": "expense", "amount": 45.0, "category": "groceries", ...}
                # 3. Tasks returns: {"id": "...", "text": "...", "due": "...", ...}
                
                # Get skill-specific max limit (falls back to base default of 100)
                skill_max = getattr(skill, 'MAX_TRANSACTIONS', None) \
                    or getattr(skill, 'MAX_TASKS', None) \
                    or getattr(skill, 'MAX_ENTRIES', None) \
                    or 100

                if "entries" in data:
                    # Multi-entry format (health)
                    for item in data["entries"]:
                        category = item.get("category", "general")
                        entry = item.get("entry", item)
                        skill.store(category, entry, max_per_category=skill_max)
                elif "amount" in data:
                    # Budget format — store under "transactions"
                    skill.store("transactions", data, max_per_category=skill_max)
                elif "text" in data and "done" in data:
                    # Task format
                    skill.store("tasks", data, max_per_category=skill_max)
                else:
                    # Generic: store under 'general'
                    skill.store("general", data, max_per_category=skill_max)
                
                logger.info(f"Skill '{skill.name}' stored data from message")
        except Exception as e:
            logger.error(f"Skill '{skill.name}' hook failed: {e}")


def get_skills_prompt_context(skills=None) -> str:
    """Get combined context from all skills for the AI system prompt.

    Args:
        skills: Optional list of skills. If None, loads all skills automatically.
    """
    contexts = []

    # Use provided skills or load all
    skill_list = skills if skills is not None else get_all_skills()

    for skill in skill_list:
        try:
            ctx = skill.get_prompt_context()
            if ctx and ctx.strip():
                contexts.append(f"### {skill.name}\n{ctx}")
        except Exception as e:
            logger.error(f"Skill '{skill.name}' context failed: {e}")

    if not contexts:
        return ""

    return (
        "\n\n## What I'm Tracking For You\n"
        "I keep track of certain things you tell me. Here's what I know:\n\n"
        + "\n\n".join(contexts)
    )


# Compatibility alias
def get_skills_context(skills=None) -> str:
    """Compatibility alias for get_skills_prompt_context."""
    return get_skills_prompt_context(skills)


def get_proactive_nudges() -> list[str]:
    """Collect proactive nudges from all skills."""
    nudges = []
    for skill in get_all_skills():
        try:
            skill_nudges = skill.get_proactive_nudges()
            if skill_nudges:
                nudges.extend(skill_nudges)
        except Exception as e:
            logger.error(f"Skill '{skill.name}' nudges failed: {e}")
    return nudges


def get_skill_capabilities_prompt() -> str:
    """Tell the AI what skills are available so it can mention them naturally."""
    skills = get_all_skills()
    if not skills:
        return ""
    
    lines = [
        "\n\n## My Capabilities",
        "I can help you track these things automatically — just mention them naturally:\n"
    ]
    
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
    
    lines.append(
        "\nWhen you mention any of these topics, I'll silently track the data for you. "
        "You don't need to use any special commands — just talk to me normally!"
    )
    
    return "\n".join(lines)
