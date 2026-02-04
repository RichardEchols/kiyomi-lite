"""
Kiyomi Lite — Model Router
Routes messages to the right AI provider (Gemini, Claude, GPT).
User never sees this — it just works.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def classify_message(text: str) -> str:
    """Classify what kind of task this is.
    
    Returns: 'simple', 'writing', 'building'
    """
    text_lower = text.lower()
    
    building_words = [
        'build me', 'build a ', 'create app', 'create an app',
        'create a script', 'create a program', 'create a bot',
        'create a website', 'create a site', 'create a tool',
        'create a database', 'create a schema', 'create a pipeline',
        'create a function', 'create a class', 'create a component',
        'create a api', 'create a server', 'create a service',
        'python script', 'bash script', 'shell script',
        'make a website', 'make me a website', 'make me a ',
        'write code', 'write a script', 'implement',
        'deploy', 'set up a server', 'configure',
        'code a ', 'code an ', 'code me',
        'automate this', 'automation script',
    ]
    writing_words = [
        'write me', 'write a ', 'draft', 'compose', 'essay',
        'business plan', 'marketing', 'strategy', 'proposal',
        'analyze', 'analysis', 'explain in detail', 'research',
        'generate report', 'summarize', 'summary', 'outline',
        'resume', 'cover letter', 'help me write',
        'create a plan', 'create a summary', 'create a report',
        'create a list', 'create a document', 'create a draft',
    ]
    simple_words = [
        'good morning', 'hello', 'hi', 'hey', 'what time',
        'remind me', 'schedule', 'weather', 'remember',
        'how are you', 'thank', 'good night', 'good evening',
        'what can you do', 'tell me about',
    ]
    
    if any(kw in text_lower for kw in building_words):
        return 'building'
    if any(kw in text_lower for kw in writing_words):
        return 'writing'
    if any(kw in text_lower for kw in simple_words):
        return 'simple'
    if len(text.split()) < 8:
        return 'simple'
    return 'writing'


def pick_model(task_type: str, config: dict) -> tuple[str, str]:
    """Pick the best model for this task.
    
    Returns: (provider, model_name)
    
    Considers both API and CLI providers.
    CLI providers are preferred if available (user's existing subscriptions).
    """
    provider = config.get("provider", "gemini")
    
    # Check for CLI providers first (they use existing subscriptions)
    from .config import detect_available_clis
    available_clis = detect_available_clis()
    
    # Check API providers
    has_gemini = bool(config.get("gemini_key"))
    has_anthropic = bool(config.get("anthropic_key"))
    has_openai = bool(config.get("openai_key"))
    
    if task_type == 'simple':
        # Simple tasks → fastest available (CLI preferred)
        if "gemini-cli" in available_clis:
            return ('gemini-cli', 'gemini')
        if "claude-cli" in available_clis:
            return ('claude-cli', 'claude')
        if has_gemini:
            return ('gemini', 'gemini-2.0-flash')
        if has_openai:
            return ('openai', 'gpt-5.2')
        if has_anthropic:
            return ('anthropic', 'claude-sonnet-4-20250514')
    
    elif task_type == 'building':
        # Complex tasks → best quality (Claude preferred)
        if "claude-cli" in available_clis:
            return ('claude-cli', 'claude')
        if has_anthropic:
            return ('anthropic', 'claude-sonnet-4-20250514')
        if "gemini-cli" in available_clis:
            return ('gemini-cli', 'gemini')
        if has_gemini:
            return ('gemini', 'gemini-2.0-flash')
        if has_openai:
            return ('openai', 'gpt-5.2')
    
    else:  # writing
        # Writing tasks → good balance (CLI preferred)
        if "claude-cli" in available_clis:
            return ('claude-cli', 'claude')
        if "gemini-cli" in available_clis:
            return ('gemini-cli', 'gemini')
        if has_gemini:
            return ('gemini', 'gemini-2.0-flash')
        if has_openai:
            return ('openai', 'gpt-5.2')
        if has_anthropic:
            return ('anthropic', 'claude-sonnet-4-20250514')
    
    # Fallback to configured provider
    from .config import get_model
    return (provider, get_model(config))
