"""
Kiyomi Lite — Skill Registry
Auto-discovers and loads all skills from this package.
"""
import importlib
import logging
import pkgutil
from pathlib import Path

try:
    from skills.base import Skill
except ImportError:
    from engine.skills.base import Skill

log = logging.getLogger(__name__)

# Global registry: skill_name -> Skill instance
_registry: dict[str, Skill] = {}


def discover_skills() -> dict[str, Skill]:
    """Auto-discover all Skill subclasses in engine/skills/*.py."""
    global _registry

    if _registry:
        return _registry

    package_dir = Path(__file__).parent

    for _, module_name, _ in pkgutil.iter_modules([str(package_dir)]):
        if module_name in ("base", "__init__"):
            continue
        try:
            # Try both import paths (depends on sys.path setup)
            try:
                module = importlib.import_module(f"skills.{module_name}")
            except ImportError:
                module = importlib.import_module(f"engine.skills.{module_name}")
            # Find all Skill subclasses in the module
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Skill)
                    and attr is not Skill
                    and hasattr(attr, "name")
                    and attr.name
                ):
                    instance = attr()
                    _registry[instance.name] = instance
                    log.info("Loaded skill: %s", instance.name)
        except Exception as e:
            log.error("Failed to load skill module %s: %s", module_name, e)

    return _registry


def get_skill(name: str) -> Skill | None:
    """Get a skill by name."""
    registry = discover_skills()
    return registry.get(name)


def get_all_skills() -> list[Skill]:
    """Get all loaded skills."""
    return list(discover_skills().values())


def run_detect(message: str) -> list[Skill]:
    """Return all skills that detect relevance in this message."""
    return [s for s in get_all_skills() if s.detect(message)]


def run_extract(message: str, response: str) -> list[dict]:
    """Run extract on all relevant skills, return results."""
    results = []
    for skill in run_detect(message):
        try:
            result = skill.extract(message, response)
            if result:
                results.append(result)
        except Exception as e:
            log.error("Skill %s extract failed: %s", skill.name, e)
    return results


def build_skills_context() -> str:
    """Gather prompt context from all skills for the system prompt."""
    parts = []
    for skill in get_all_skills():
        try:
            ctx = skill.get_prompt_context()
            if ctx:
                parts.append(ctx)
        except Exception as e:
            log.error("Skill %s context failed: %s", skill.name, e)
    return "\n\n".join(parts)


def collect_nudges() -> list[str]:
    """Collect proactive nudges from all skills."""
    nudges = []
    for skill in get_all_skills():
        try:
            nudges.extend(skill.get_proactive_nudges())
        except Exception as e:
            log.error("Skill %s nudges failed: %s", skill.name, e)
    return nudges
