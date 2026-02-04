"""
Kiyomi CLI Installer — Auto-install AI CLI tools silently
Installs Node.js, npm packages, and handles authentication for AI CLI tools.
User never touches the terminal.
"""
import asyncio
import logging
import shutil
import subprocess
import platform
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# CLI tool mappings
CLI_PACKAGES = {
    "claude": "@anthropic-ai/claude-code", 
    "codex": "@openai/codex",
    "gemini": "@google/gemini-cli",
    "agent-tars": "@agent-tars/cli@latest"
}

# Auth URLs for each provider
AUTH_URLS = {
    "claude": "https://console.anthropic.com/settings/keys",
    "codex": "https://platform.openai.com/api-keys", 
    "gemini": "https://ai.google.dev/gemini-api/docs/api-key",
    "agent-tars": "https://agent-tars.com/guide/get-started/quick-start.html"
}


async def check_prerequisites() -> Dict[str, any]:
    """
    Check if Node.js, npm, and Homebrew are available.
    
    Returns:
        dict: Status of each prerequisite with paths and versions
    """
    logger.info("Checking CLI prerequisites...")
    
    result = {
        "node": {"available": False, "path": None, "version": None},
        "npm": {"available": False, "path": None, "version": None}, 
        "homebrew": {"available": False, "path": None, "version": None},
        "platform": platform.system()
    }
    
    try:
        # Check Node.js
        node_path = shutil.which("node")
        if node_path:
            proc = await asyncio.create_subprocess_exec(
                "node", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                result["node"]["available"] = True
                result["node"]["path"] = node_path
                result["node"]["version"] = stdout.decode().strip()
        
        # Check npm
        npm_path = shutil.which("npm")
        if npm_path:
            proc = await asyncio.create_subprocess_exec(
                "npm", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                result["npm"]["available"] = True
                result["npm"]["path"] = npm_path
                result["npm"]["version"] = stdout.decode().strip()
        
        # Check Homebrew (macOS only)
        if platform.system() == "Darwin":
            brew_path = shutil.which("brew")
            if brew_path:
                proc = await asyncio.create_subprocess_exec(
                    "brew", "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0:
                    result["homebrew"]["available"] = True
                    result["homebrew"]["path"] = brew_path
                    result["homebrew"]["version"] = stdout.decode().strip().split('\n')[0]
    
    except Exception as e:
        logger.error(f"Error checking prerequisites: {e}")
    
    logger.info(f"Prerequisites check complete: {result}")
    return result


async def _install_node_via_homebrew() -> Dict[str, any]:
    """
    Install Node.js via Homebrew silently.
    
    Returns:
        dict: Installation result with success status and details
    """
    logger.info("Installing Node.js via Homebrew...")
    
    try:
        # Update Homebrew first
        proc = await asyncio.create_subprocess_exec(
            "brew", "update",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        
        # Install Node.js
        proc = await asyncio.create_subprocess_exec(
            "brew", "install", "node",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode == 0:
            logger.info("Node.js installed successfully via Homebrew")
            return {
                "success": True,
                "method": "homebrew",
                "stdout": stdout.decode(),
                "stderr": stderr.decode()
            }
        else:
            logger.error(f"Homebrew Node.js installation failed: {stderr.decode()}")
            return {
                "success": False,
                "method": "homebrew",
                "error": stderr.decode(),
                "stdout": stdout.decode()
            }
    
    except Exception as e:
        logger.error(f"Error installing Node.js via Homebrew: {e}")
        return {
            "success": False,
            "method": "homebrew",
            "error": str(e)
        }


async def install_cli(provider: str) -> Dict[str, any]:
    """
    Install specific CLI tool silently via npm.
    
    Args:
        provider: CLI provider name (claude, codex, gemini)
        
    Returns:
        dict: Installation result with status, auth_url, and next steps
    """
    if provider not in CLI_PACKAGES:
        return {
            "success": False,
            "error": f"Unknown provider: {provider}. Supported: {list(CLI_PACKAGES.keys())}"
        }
    
    package = CLI_PACKAGES[provider]
    logger.info(f"Installing {provider} CLI: {package}")
    
    result = {
        "provider": provider,
        "package": package,
        "success": False,
        "auth_url": AUTH_URLS.get(provider),
        "steps_taken": [],
        "next_steps": []
    }
    
    try:
        # Check prerequisites first
        prereqs = await check_prerequisites()
        
        # Install Node.js if missing (macOS only)
        if not prereqs["node"]["available"]:
            if prereqs["platform"] == "Darwin" and prereqs["homebrew"]["available"]:
                result["steps_taken"].append("Installing Node.js via Homebrew...")
                node_install = await _install_node_via_homebrew()
                if not node_install["success"]:
                    result["error"] = f"Failed to install Node.js: {node_install.get('error', 'Unknown error')}"
                    return result
                result["steps_taken"].append("Node.js installed successfully")
            else:
                result["error"] = "Node.js not found and cannot auto-install (requires macOS with Homebrew)"
                result["next_steps"].append("Please install Node.js manually from https://nodejs.org/")
                return result
        
        # Re-check npm availability after potential Node.js installation
        npm_path = shutil.which("npm")
        if not npm_path:
            result["error"] = "npm not found after Node.js installation"
            return result
        
        # Install the CLI package globally
        result["steps_taken"].append(f"Installing {package} globally via npm...")
        proc = await asyncio.create_subprocess_exec(
            "npm", "install", "-g", package,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode == 0:
            result["success"] = True
            result["steps_taken"].append(f"{provider} CLI installed successfully")
            result["stdout"] = stdout.decode()
            
            # Add auth next steps
            if provider == "claude":
                result["next_steps"] = [
                    "1. Visit https://console.anthropic.com/settings/keys",
                    "2. Create a new API key",
                    "3. Run: claude auth",
                    "4. Enter your API key when prompted"
                ]
            elif provider == "codex":
                result["next_steps"] = [
                    "1. Visit https://platform.openai.com/api-keys", 
                    "2. Create a new API key",
                    "3. Run: codex config set api_key YOUR_KEY"
                ]
            elif provider == "gemini":
                result["next_steps"] = [
                    "1. Visit https://ai.google.dev/gemini-api/docs/api-key",
                    "2. Create a new API key", 
                    "3. Run: gemini auth"
                ]
            elif provider == "agent-tars":
                result["next_steps"] = [
                    "1. Agent TARS supports multiple AI providers (anthropic, openai, volcengine)",
                    "2. Get your provider API key",
                    "3. Use with: agent-tars --provider anthropic --apiKey YOUR_KEY",
                    "4. See https://agent-tars.com/guide/get-started/quick-start.html"
                ]
            
            logger.info(f"{provider} CLI installed successfully")
        else:
            result["error"] = f"npm install failed: {stderr.decode()}"
            result["stderr"] = stderr.decode()
            logger.error(f"Failed to install {provider} CLI: {result['error']}")
    
    except Exception as e:
        result["error"] = f"Installation error: {str(e)}"
        logger.error(f"Error installing {provider} CLI: {e}")
    
    return result


async def install_agent_tars() -> Dict[str, any]:
    """
    Install Agent TARS CLI for computer control.
    Requires Node.js >= 22.
    
    Returns:
        dict: Installation result with status and next steps
    """
    logger.info("Installing Agent TARS CLI...")
    
    result = {
        "provider": "agent-tars",
        "package": "@agent-tars/cli@latest",
        "success": False,
        "steps_taken": [],
        "next_steps": []
    }
    
    try:
        # Check prerequisites first
        prereqs = await check_prerequisites()
        
        # Check Node.js version (Agent TARS requires >= 22)
        if not prereqs["node"]["available"]:
            result["error"] = "Node.js not found. Agent TARS requires Node.js >= 22"
            result["next_steps"] = ["Install Node.js >= 22 from https://nodejs.org/"]
            return result
        
        # Check Node.js version
        node_version = prereqs["node"]["version"]
        if node_version:
            try:
                # Extract major version number (e.g., "v22.1.0" -> 22)
                version_num = int(node_version.lstrip('v').split('.')[0])
                if version_num < 22:
                    result["error"] = f"Node.js {version_num} found, but Agent TARS requires >= 22"
                    result["next_steps"] = ["Update Node.js to version 22 or newer from https://nodejs.org/"]
                    return result
            except (ValueError, IndexError):
                logger.warning(f"Could not parse Node.js version: {node_version}")
        
        # Install Agent TARS CLI globally
        result["steps_taken"].append("Installing @agent-tars/cli@latest globally via npm...")
        proc = await asyncio.create_subprocess_exec(
            "npm", "install", "-g", "@agent-tars/cli@latest",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode == 0:
            result["success"] = True
            result["steps_taken"].append("Agent TARS CLI installed successfully")
            result["stdout"] = stdout.decode()
            result["next_steps"] = [
                "Agent TARS is ready! It works with these providers:",
                "• Anthropic Claude: --provider anthropic --apiKey YOUR_KEY",
                "• OpenAI: --provider openai --apiKey YOUR_KEY",
                "• Volcengine: --provider volcengine --apiKey YOUR_KEY",
                "Example: agent-tars --provider anthropic --apiKey sk-... \"open Safari\""
            ]
            logger.info("Agent TARS CLI installed successfully")
        else:
            result["error"] = f"npm install failed: {stderr.decode()}"
            result["stderr"] = stderr.decode()
            logger.error(f"Failed to install Agent TARS CLI: {result['error']}")
    
    except Exception as e:
        result["error"] = f"Installation error: {str(e)}"
        logger.error(f"Error installing Agent TARS CLI: {e}")
    
    return result


async def check_cli_auth(provider: str) -> bool:
    """
    Check if CLI tool is authenticated.
    
    Args:
        provider: CLI provider name (claude, codex, gemini)
        
    Returns:
        bool: True if authenticated, False otherwise
    """
    if provider not in CLI_PACKAGES:
        logger.warning(f"Unknown provider for auth check: {provider}")
        return False
    
    cli_name = provider
    cli_path = shutil.which(cli_name)
    
    if not cli_path:
        logger.info(f"{provider} CLI not found, assuming not authenticated")
        return False
    
    try:
        # Try a simple command to test authentication
        if provider == "claude":
            # Claude CLI: try to get version or check status
            proc = await asyncio.create_subprocess_exec(
                "claude", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        elif provider == "codex":
            # Codex CLI: try to check config
            proc = await asyncio.create_subprocess_exec(
                "codex", "config", "list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        elif provider == "gemini":
            # Gemini CLI: try a simple query
            proc = await asyncio.create_subprocess_exec(
                "gemini", "auth", "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        elif provider == "agent-tars":
            # Agent TARS CLI: check if available (doesn't require persistent auth)
            proc = await asyncio.create_subprocess_exec(
                "agent-tars", "--help",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        else:
            return False
        
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        
        # Check for authentication indicators in output
        if proc.returncode == 0:
            output = stdout.decode().lower()
            error_output = stderr.decode().lower()
            
            # Look for common auth failure patterns
            auth_failures = ["not authenticated", "authentication failed", "api key", "unauthorized", "login required"]
            for failure_pattern in auth_failures:
                if failure_pattern in output or failure_pattern in error_output:
                    return False
            
            return True  # Command succeeded and no auth errors
        else:
            logger.info(f"{provider} CLI auth check failed with return code {proc.returncode}")
            return False
    
    except asyncio.TimeoutError:
        logger.warning(f"Auth check for {provider} timed out")
        return False
    except Exception as e:
        logger.error(f"Error checking {provider} CLI auth: {e}")
        return False


async def auto_setup(provider: str) -> Dict[str, any]:
    """
    Full automated setup flow: check prerequisites → install → return auth instructions.
    
    Args:
        provider: CLI provider name (claude, codex, gemini)
        
    Returns:
        dict: Complete setup result with status, auth_url, and instructions
    """
    logger.info(f"Starting auto-setup for {provider}")
    
    result = {
        "provider": provider,
        "success": False,
        "already_installed": False,
        "already_authenticated": False,
        "installation_result": None,
        "auth_url": AUTH_URLS.get(provider),
        "next_steps": [],
        "summary": ""
    }
    
    try:
        # Check if CLI is already installed and authenticated
        cli_path = shutil.which(provider)
        if cli_path:
            result["already_installed"] = True
            logger.info(f"{provider} CLI already installed at {cli_path}")
            
            # Check authentication
            is_authed = await check_cli_auth(provider)
            if is_authed:
                result["already_authenticated"] = True
                result["success"] = True
                result["summary"] = f"{provider} CLI is already installed and authenticated!"
                result["next_steps"] = [f"You can now use: {provider} -p 'your prompt here'"]
                return result
            else:
                result["summary"] = f"{provider} CLI is installed but needs authentication."
                result["next_steps"] = [
                    f"Visit {AUTH_URLS.get(provider, 'the provider website')} to get your API key",
                    f"Then run the {provider} CLI setup command to authenticate"
                ]
                return result
        
        # CLI not installed - run installation
        if provider == "agent-tars":
            install_result = await install_agent_tars()
        else:
            install_result = await install_cli(provider)
        result["installation_result"] = install_result
        
        if install_result["success"]:
            result["success"] = True
            result["summary"] = f"Successfully installed {provider} CLI!"
            result["next_steps"] = install_result.get("next_steps", [])
        else:
            result["summary"] = f"Failed to install {provider} CLI: {install_result.get('error', 'Unknown error')}"
            if "next_steps" in install_result:
                result["next_steps"] = install_result["next_steps"]
    
    except Exception as e:
        result["summary"] = f"Auto-setup failed: {str(e)}"
        logger.error(f"Auto-setup error for {provider}: {e}")
    
    logger.info(f"Auto-setup complete for {provider}: {result['summary']}")
    return result


# Utility functions for integration

def get_available_providers() -> list:
    """Get list of supported CLI providers."""
    return list(CLI_PACKAGES.keys())


def get_auth_instructions(provider: str) -> Dict[str, any]:
    """Get authentication instructions for a provider."""
    if provider not in CLI_PACKAGES:
        return {"error": f"Unknown provider: {provider}"}
    
    return {
        "provider": provider,
        "auth_url": AUTH_URLS.get(provider),
        "cli_command": f"{provider}",
        "description": f"Authentication setup for {provider} CLI"
    }


async def get_installation_status() -> Dict[str, any]:
    """Get current installation and authentication status for all providers."""
    status = {
        "prerequisites": await check_prerequisites(),
        "providers": {}
    }
    
    for provider in CLI_PACKAGES.keys():
        cli_path = shutil.which(provider)
        is_authenticated = False
        
        if cli_path:
            is_authenticated = await check_cli_auth(provider)
        
        status["providers"][provider] = {
            "installed": bool(cli_path),
            "path": cli_path,
            "authenticated": is_authenticated
        }
    
    return status