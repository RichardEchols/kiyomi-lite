"""
Kiyomi Lite — Simple Configuration
All config lives in ~/.kiyomi/config.json
"""
import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".kiyomi"
CONFIG_FILE = CONFIG_DIR / "config.json"
MEMORY_DIR = CONFIG_DIR / "memory"
SKILLS_DIR = CONFIG_DIR / "skills"
LOGS_DIR = CONFIG_DIR / "logs"

# Defaults
DEFAULT_CONFIG = {
    "name": "",
    "provider": "gemini",
    "gemini_key": "",
    "anthropic_key": "",
    "openai_key": "",
    "telegram_token": "",
    "telegram_user_id": "",
    "timezone": "UTC",
    "model": "gemini-2.0-flash",
    "imported_chats": False,
    "setup_complete": False,
    # CLI provider settings
    "cli_path": "",  # optional custom CLI path
    "cli_timeout": 60,  # timeout for CLI calls in seconds
}


def ensure_dirs():
    """Create all required directories."""
    for d in [CONFIG_DIR, MEMORY_DIR, SKILLS_DIR, LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load config from ~/.kiyomi/config.json."""
    ensure_dirs()
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            stored = json.load(f)
            # Merge with defaults (adds any new keys)
            config = {**DEFAULT_CONFIG, **stored}
            return config
    return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    """Save config to ~/.kiyomi/config.json."""
    ensure_dirs()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_api_key(config: dict) -> str:
    """Get the active API key based on provider."""
    provider = config.get("provider", "gemini")
    key_map = {
        "gemini": "gemini_key",
        "anthropic": "anthropic_key",
        "openai": "openai_key",
    }
    return config.get(key_map.get(provider, "gemini_key"), "")


def get_model(config: dict) -> str:
    """Get the model name for the active provider."""
    provider = config.get("provider", "gemini")
    model = config.get("model", "")
    if model:
        return model
    # Sensible defaults
    defaults = {
        "gemini": "gemini-2.0-flash",
        "anthropic": "claude-sonnet-4-20250514",
        "openai": "gpt-4o-mini",
        "claude-cli": "claude",  # CLI providers use the CLI name as "model"
        "codex-cli": "codex",
        "gemini-cli": "gemini",
    }
    return defaults.get(provider, "gemini-2.0-flash")


def detect_available_clis() -> dict:
    """Detect which AI CLI tools are installed on the system."""
    import shutil
    
    clis = {}
    cli_tools = {
        "claude-cli": "claude",
        "codex-cli": "codex", 
        "gemini-cli": "gemini"
    }
    
    for provider, cli_name in cli_tools.items():
        cli_path = shutil.which(cli_name)
        if cli_path:
            clis[provider] = cli_path
    
    return clis


def is_cli_provider(provider: str) -> bool:
    """Check if provider is a CLI-based provider."""
    return provider.endswith("-cli")


def get_cli_timeout(config: dict) -> int:
    """Get CLI timeout setting."""
    return config.get("cli_timeout", 60)


def suggest_best_provider(config: dict) -> str:
    """Suggest the best AI provider based on available CLIs and API keys."""
    # Check for available CLIs first (they're "free" if user has subscriptions)
    available_clis = detect_available_clis()
    
    # Preference order for CLI: claude > gemini > codex
    cli_preference = ["claude-cli", "gemini-cli", "codex-cli"]
    for provider in cli_preference:
        if provider in available_clis:
            return provider
    
    # Fallback to API providers if no CLIs available
    if config.get("anthropic_key"):
        return "anthropic"
    if config.get("gemini_key"):
        return "gemini"
    if config.get("openai_key"):
        return "openai"
    
    # Default to free Gemini API
    return "gemini"


def setup_ai_provider_message() -> str:
    """Generate setup message showing available options."""
    available_clis = detect_available_clis()
    
    message = "🤖 **Choose Your AI Provider**\n\n"
    
    if available_clis:
        message += "**✅ Found these CLI tools** (use your existing subscriptions):\n"
        cli_names = {
            "claude-cli": "Claude Pro/Max",
            "codex-cli": "ChatGPT Plus", 
            "gemini-cli": "Gemini Advanced"
        }
        for provider, path in available_clis.items():
            subscription = cli_names.get(provider, provider)
            message += f"• {subscription} ({provider})\n"
        message += "\n"
    
    message += "**API Options** (requires API keys):\n"
    message += "• Anthropic Claude\n"
    message += "• Google Gemini (free tier available)\n"
    message += "• OpenAI GPT\n\n"
    
    if available_clis:
        best = list(available_clis.keys())[0]
        cli_name = {"claude-cli": "Claude", "codex-cli": "Codex", "gemini-cli": "Gemini"}.get(best, best)
        message += f"**Recommendation:** Use {cli_name} CLI since it's already installed!"
    else:
        message += "**Recommendation:** Start with free Gemini API, then add CLI tools later."
    
    return message
