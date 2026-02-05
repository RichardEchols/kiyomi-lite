"""
Kiyomi CLI Installer — Auto-install AI CLI tools silently
Installs Node.js, npm packages, and handles subscription-based OAuth.
User never touches the terminal.

Auth model: Users authenticate with their existing AI subscriptions
(Claude Pro, ChatGPT Plus, Google One AI Premium) via browser OAuth.
No API keys needed.
"""
import asyncio
import json
import logging
import shutil
import platform
import webbrowser
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

HOME = Path.home()

# ── PATH expansion (PyInstaller bundles have a stripped PATH) ────

_EXTRA_PATHS = [
    "/usr/local/bin",
    "/opt/homebrew/bin",
    str(HOME / ".local" / "bin"),
    str(HOME / ".npm-global" / "bin"),
    str(HOME / ".cargo" / "bin"),
    str(HOME / ".nvm" / "current" / "bin"),
]


def _expanded_path() -> str:
    """Build PATH string with common macOS CLI install locations."""
    import os
    existing = os.environ.get("PATH", "")
    for p in _EXTRA_PATHS:
        if p not in existing:
            existing = f"{p}:{existing}"
    return existing


def _which(name: str) -> Optional[str]:
    """shutil.which() with expanded PATH for PyInstaller compatibility."""
    return shutil.which(name, path=_expanded_path())


def _get_env() -> dict:
    """Build subprocess env with expanded PATH."""
    import os
    env = os.environ.copy()
    env["PATH"] = _expanded_path()
    return env


# ── CLI package registry ─────────────────────────────────────────

CLI_PACKAGES = {
    "claude": "@anthropic-ai/claude-code",
    "codex": "@openai/codex",
    "gemini": "@google/gemini-cli",
}

# ── Auth config: where each CLI stores credentials on disk ───────

AUTH_CONFIG = {
    "claude": {
        "config_file": HOME / ".claude.json",
        "auth_key": "oauthAccount",          # dict with accountUuid, emailAddress, etc.
        "validate": lambda data: (
            isinstance(data.get("oauthAccount"), dict)
            and bool(data["oauthAccount"].get("accountUuid"))
        ),
        "auth_command": ["claude", "-p", "hello", "--output-format", "json"],
        "subscription": "Claude Pro / Max ($20/mo)",
        "display_name": "Claude",
    },
    "codex": {
        "config_file": HOME / ".codex" / "auth.json",
        "auth_key": "tokens",                # dict with access_token, refresh_token, etc.
        "validate": lambda data: (
            isinstance(data.get("tokens"), dict)
            and bool(data["tokens"].get("access_token"))
        ),
        "status_command": ["codex", "login", "status"],
        "auth_command": ["codex", "login"],
        "subscription": "ChatGPT Plus ($20/mo)",
        "display_name": "Codex",
    },
    "gemini": {
        "config_file": HOME / ".gemini" / "oauth_creds.json",
        "auth_key": "refresh_token",         # OAuth refresh token
        "validate": lambda data: (
            bool(data.get("access_token"))
            and bool(data.get("refresh_token"))
        ),
        "auth_command": ["gemini", "-p", "hello"],
        "subscription": "Google One AI Premium ($20/mo)",
        "display_name": "Gemini",
    },
}


# ═══════════════════════════════════════════════════════════════════
# AUTH VERIFICATION — file-based, no subprocess needed
# ═══════════════════════════════════════════════════════════════════

def check_cli_installed(provider: str) -> Optional[str]:
    """Check if a CLI binary is on PATH.

    Returns the binary path if found, None otherwise.
    """
    return _which(provider)


def check_cli_auth(provider: str) -> dict:
    """Check if a CLI is authenticated by inspecting config files on disk.

    Returns dict with:
        authenticated (bool): True if valid credentials found
        subscription (str|None): Subscription tier if detectable
        account (str|None): Account identifier (email, etc.)
        detail (str): Human-readable status message
    """
    result = {
        "authenticated": False,
        "subscription": None,
        "account": None,
        "detail": "",
    }

    cfg = AUTH_CONFIG.get(provider)
    if not cfg:
        result["detail"] = f"Unknown provider: {provider}"
        return result

    config_file = cfg["config_file"]
    if not config_file.exists():
        result["detail"] = f"No auth config found at {config_file}"
        return result

    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        result["detail"] = f"Could not read {config_file}: {e}"
        return result

    # Run the provider-specific validation
    if cfg["validate"](data):
        result["authenticated"] = True
        result["subscription"] = cfg["subscription"]

        # Extract account identifier for display
        if provider == "claude":
            acct = data.get("oauthAccount", {})
            result["account"] = acct.get("emailAddress") or acct.get("displayName")
        elif provider == "codex":
            auth_mode = data.get("auth_mode", "unknown")
            # "chatgpt" means subscription auth, "api_key" means API key
            if auth_mode == "chatgpt":
                result["subscription"] = "ChatGPT Plus (subscription)"
                result["account"] = "ChatGPT subscription"
            elif auth_mode == "api_key":
                result["subscription"] = "OpenAI API key"
                result["account"] = "API key"
            else:
                result["account"] = auth_mode
        elif provider == "gemini":
            result["account"] = "Google OAuth"

        result["detail"] = f"Authenticated via {result['subscription']}"
        logger.info(f"{provider} auth verified: {result['detail']}")
    else:
        result["detail"] = f"Config exists but credentials are incomplete"
        logger.info(f"{provider} auth incomplete at {config_file}")

    return result


def check_cli_auth_bool(provider: str) -> bool:
    """Simple boolean auth check (convenience wrapper)."""
    return check_cli_auth(provider)["authenticated"]


# ═══════════════════════════════════════════════════════════════════
# AUTH TRIGGER — launches browser-based OAuth flow
# ═══════════════════════════════════════════════════════════════════

async def launch_cli_auth(provider: str) -> dict:
    """Launch the CLI's native OAuth flow (opens browser for login).

    This triggers the subscription-based auth — the user logs in
    with their existing Claude/ChatGPT/Google account, no API keys needed.

    Returns dict with:
        launched (bool): True if auth flow was triggered
        detail (str): Status message
        needs_browser (bool): True if user needs to interact with browser
    """
    result = {
        "launched": False,
        "detail": "",
        "needs_browser": False,
    }

    cfg = AUTH_CONFIG.get(provider)
    if not cfg:
        result["detail"] = f"Unknown provider: {provider}"
        return result

    # Must be installed first
    cli_path = _which(provider)
    if not cli_path:
        result["detail"] = f"{provider} CLI not installed"
        return result

    # Already authenticated?
    auth_status = check_cli_auth(provider)
    if auth_status["authenticated"]:
        result["launched"] = False
        result["detail"] = f"Already authenticated: {auth_status['detail']}"
        return result

    auth_cmd = cfg["auth_command"]
    logger.info(f"Launching {provider} OAuth flow: {' '.join(auth_cmd)}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *auth_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_get_env(),
        )

        result["launched"] = True
        result["needs_browser"] = True
        result["detail"] = (
            f"Opening {cfg['display_name']} login... "
            f"Sign in with your {cfg['subscription']} account in the browser."
        )

        # Wait for auth to complete (generous timeout for browser interaction)
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=180
            )
            output = stdout.decode(errors="replace")
            errors = stderr.decode(errors="replace")

            # Verify auth completed
            post_auth = check_cli_auth(provider)
            if post_auth["authenticated"]:
                result["detail"] = f"Authenticated successfully: {post_auth['detail']}"
            else:
                result["detail"] = (
                    f"Auth flow completed but credentials not detected. "
                    f"stdout: {output[:200]}, stderr: {errors[:200]}"
                )
                logger.warning(f"{provider} auth flow completed but no creds: {errors[:200]}")

        except asyncio.TimeoutError:
            # User may still be in the browser — check if auth landed
            proc.kill()
            await proc.wait()
            post_auth = check_cli_auth(provider)
            if post_auth["authenticated"]:
                result["detail"] = f"Authenticated successfully: {post_auth['detail']}"
            else:
                result["detail"] = "Auth timed out. Try running the login again."
                logger.warning(f"{provider} auth timed out after 180s")

    except FileNotFoundError:
        result["detail"] = f"{provider} CLI binary not found at {cli_path}"
        result["launched"] = False
    except Exception as e:
        result["detail"] = f"Error launching auth: {e}"
        result["launched"] = False
        logger.error(f"Error launching {provider} auth: {e}")

    return result


async def launch_codex_auth_status() -> Optional[str]:
    """Run `codex login status` and return the output string.

    Returns None if codex is not installed or command fails.
    """
    codex_path = _which("codex")
    if not codex_path:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            codex_path, "login", "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_get_env(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            return stdout.decode(errors="replace").strip()
    except Exception as e:
        logger.debug(f"codex login status failed: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════
# PREREQUISITES — Node.js, npm, Homebrew
# ═══════════════════════════════════════════════════════════════════

async def check_prerequisites() -> Dict[str, any]:
    """Check if Node.js, npm, and Homebrew are available."""
    logger.info("Checking CLI prerequisites...")

    result = {
        "node": {"available": False, "path": None, "version": None},
        "npm": {"available": False, "path": None, "version": None},
        "homebrew": {"available": False, "path": None, "version": None},
        "platform": platform.system(),
    }

    async def _check_tool(binary: str, result_key: str = "", version_flag: str = "--version"):
        key = result_key or binary
        path = _which(binary)
        if not path:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                path, version_flag,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_get_env(),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                result[key]["available"] = True
                result[key]["path"] = path
                result[key]["version"] = stdout.decode().strip().split("\n")[0]
        except Exception:
            pass

    await asyncio.gather(
        _check_tool("node"),
        _check_tool("npm"),
        *(
            [_check_tool("brew", "homebrew")]
            if platform.system() == "Darwin"
            else []
        ),
    )

    logger.info(f"Prerequisites: {result}")
    return result


# ═══════════════════════════════════════════════════════════════════
# INSTALLATION — silent npm install
# ═══════════════════════════════════════════════════════════════════

async def _install_node_via_homebrew() -> dict:
    """Install Node.js via Homebrew silently."""
    logger.info("Installing Node.js via Homebrew...")
    brew_path = _which("brew")
    if not brew_path:
        return {"success": False, "error": "Homebrew not found"}
    try:
        proc = await asyncio.create_subprocess_exec(
            brew_path, "install", "node",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_get_env(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode == 0:
            logger.info("Node.js installed via Homebrew")
            return {"success": True}
        else:
            return {"success": False, "error": stderr.decode()[:500]}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def install_cli(provider: str) -> dict:
    """Install a CLI tool silently via npm.

    Returns dict with:
        success (bool)
        error (str|None): Error message if failed
        steps (list[str]): What was done
    """
    if provider not in CLI_PACKAGES:
        return {"success": False, "error": f"Unknown provider: {provider}", "steps": []}

    package = CLI_PACKAGES[provider]
    steps = []

    # Already installed?
    if _which(provider):
        return {"success": True, "steps": [f"{provider} already installed"], "error": None}

    # Check prerequisites
    prereqs = await check_prerequisites()

    if not prereqs["node"]["available"]:
        if prereqs["platform"] == "Darwin" and prereqs["homebrew"]["available"]:
            steps.append("Installing Node.js via Homebrew")
            node_result = await _install_node_via_homebrew()
            if not node_result["success"]:
                return {
                    "success": False,
                    "error": f"Node.js install failed: {node_result.get('error', '?')}",
                    "steps": steps,
                }
            steps.append("Node.js installed")
        else:
            return {
                "success": False,
                "error": "Node.js not found. Install from https://nodejs.org/",
                "steps": steps,
            }

    npm_path = _which("npm")
    if not npm_path:
        return {"success": False, "error": "npm not found after Node.js install", "steps": steps}

    # Install the CLI package globally
    steps.append(f"Installing {package}")
    logger.info(f"npm install -g {package}")

    try:
        proc = await asyncio.create_subprocess_exec(
            npm_path, "install", "-g", package,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_get_env(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        if proc.returncode == 0:
            steps.append(f"{provider} CLI installed")
            logger.info(f"{provider} CLI installed successfully")
            return {"success": True, "steps": steps, "error": None}
        else:
            err = stderr.decode()[:500]
            logger.error(f"npm install {package} failed: {err}")
            return {"success": False, "error": f"npm install failed: {err}", "steps": steps}

    except asyncio.TimeoutError:
        return {"success": False, "error": "npm install timed out", "steps": steps}
    except Exception as e:
        return {"success": False, "error": str(e), "steps": steps}


# ═══════════════════════════════════════════════════════════════════
# AUTO-SETUP — full install + auth flow
# ═══════════════════════════════════════════════════════════════════

async def auto_setup(provider: str) -> dict:
    """Full automated setup: install CLI → trigger browser OAuth.

    Returns dict with:
        provider (str)
        installed (bool)
        authenticated (bool)
        subscription (str|None)
        account (str|None)
        summary (str): Human-readable status
        needs_auth (bool): True if user needs to complete browser auth
    """
    logger.info(f"Auto-setup starting for {provider}")

    result = {
        "provider": provider,
        "installed": False,
        "authenticated": False,
        "subscription": None,
        "account": None,
        "summary": "",
        "needs_auth": False,
    }

    # Step 1: Check if already installed
    cli_path = check_cli_installed(provider)
    if cli_path:
        result["installed"] = True
    else:
        # Install it
        install_result = await install_cli(provider)
        if install_result["success"]:
            result["installed"] = True
        else:
            result["summary"] = f"Install failed: {install_result.get('error', '?')}"
            return result

    # Step 2: Check if already authenticated
    auth = check_cli_auth(provider)
    if auth["authenticated"]:
        result["authenticated"] = True
        result["subscription"] = auth["subscription"]
        result["account"] = auth["account"]
        result["summary"] = f"Ready! {auth['detail']}"
        return result

    # Step 3: Need auth — trigger browser OAuth
    result["needs_auth"] = True
    cfg = AUTH_CONFIG.get(provider, {})
    result["summary"] = (
        f"{cfg.get('display_name', provider)} CLI installed. "
        f"Sign in with your {cfg.get('subscription', 'subscription')} to activate."
    )

    return result


# ═══════════════════════════════════════════════════════════════════
# STATUS — comprehensive scan of all CLIs
# ═══════════════════════════════════════════════════════════════════

def detect_all() -> dict:
    """Scan all supported CLIs: installed + authenticated status.

    Returns dict keyed by provider name:
        {
            "claude": {
                "installed": True,
                "path": "/usr/local/bin/claude",
                "authenticated": True,
                "subscription": "Claude Pro / Max ($20/mo)",
                "account": "user@example.com",
            },
            ...
        }
    """
    status = {}
    for provider in CLI_PACKAGES:
        cli_path = check_cli_installed(provider)
        auth = check_cli_auth(provider) if cli_path else {
            "authenticated": False,
            "subscription": None,
            "account": None,
            "detail": "Not installed",
        }
        status[provider] = {
            "installed": bool(cli_path),
            "path": cli_path,
            "authenticated": auth["authenticated"],
            "subscription": auth["subscription"],
            "account": auth["account"],
            "detail": auth["detail"],
        }
    return status


async def get_installation_status() -> dict:
    """Get full installation/auth status (async version with prereqs)."""
    return {
        "prerequisites": await check_prerequisites(),
        "providers": detect_all(),
    }


# ═══════════════════════════════════════════════════════════════════
# UTILITY — for integration with onboarding / app.py
# ═══════════════════════════════════════════════════════════════════

def get_available_providers() -> list:
    """Get list of supported CLI providers."""
    return list(CLI_PACKAGES.keys())


def get_subscription_info() -> list:
    """Get subscription info for onboarding UI display."""
    return [
        {
            "provider": provider,
            "display_name": cfg["display_name"],
            "subscription": cfg["subscription"],
            "installed": bool(_which(provider)),
            "authenticated": check_cli_auth(provider)["authenticated"],
        }
        for provider, cfg in AUTH_CONFIG.items()
    ]


def get_best_provider() -> Optional[str]:
    """Detect the best available & authenticated CLI provider.

    Priority: claude > gemini > codex (matches router.py preference order).
    Returns provider name string or None.
    """
    for provider in ["claude", "gemini", "codex"]:
        path = check_cli_installed(provider)
        if path and check_cli_auth_bool(provider):
            return provider
    return None
