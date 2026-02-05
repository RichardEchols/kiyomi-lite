"""
Kiyomi CLI Router — Agentic AI via CLI tools

Routes AI requests through Claude, Codex, and Gemini CLIs in AGENTIC mode.
Each CLI runs with full tool access (file I/O, web, code execution, search)
and permission prompts bypassed for headless operation.

Auth is validated before execution. Errors are caught and returned cleanly.
"""
import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Agentic tasks need more time than simple chat
DEFAULT_TIMEOUT = 300  # 5 minutes

# Workspace for created files
WORKSPACE = Path.home() / ".kiyomi" / "workspace"


class CLIRouter:
    """Unified router for AI CLI tools in agentic mode."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout
        WORKSPACE.mkdir(parents=True, exist_ok=True)

    async def chat(
        self,
        message: str,
        provider: str,
        cli_path: Optional[str] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> str:
        """
        Route AI request through a CLI tool in agentic mode.

        The CLI can use its full tool capabilities (read/write files,
        browse the web, execute code, search) without interactive
        permission prompts.

        Args:
            message: User message/prompt
            provider: CLI provider name (claude, codex, gemini, or with -cli suffix)
            cli_path: Optional custom path to CLI binary
            system_prompt: Optional system prompt (passed natively for Claude,
                           prepended to message for others)
        """
        if not message or not message.strip():
            return "No message provided."

        provider = provider.lower().replace("-cli", "")

        cmd = cli_path or provider
        if not self.check_cli_available(cmd):
            return (
                f"{provider.title()} CLI not found. "
                f"Kiyomi can install it automatically — check Settings."
            )

        # Validate auth via config files (fast, no subprocess)
        try:
            from engine.cli_installer import check_cli_auth_bool
            if not check_cli_auth_bool(provider):
                return (
                    f"{provider.title()} CLI is installed but not authenticated. "
                    f"Please sign in through Settings to connect your subscription."
                )
        except ImportError:
            pass

        try:
            if provider == "claude":
                return await self._execute_claude(message, cli_path, system_prompt)
            elif provider == "codex":
                return await self._execute_codex(message, cli_path, system_prompt)
            elif provider == "gemini":
                return await self._execute_gemini(message, cli_path, system_prompt)
            else:
                return f"Unsupported CLI provider: {provider}"
        except Exception as e:
            logger.error(f"CLI router error ({provider}): {e}")
            return f"CLI error: {str(e)[:100]}"

    # ── Claude CLI (agentic) ──────────────────────────────────────────

    async def _execute_claude(
        self, message: str, cli_path: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Execute Claude Code CLI in agentic mode.

        Flags:
            -p                          Non-interactive (print mode)
            --dangerously-skip-permissions  Bypass all tool permission prompts
            --output-format text        Clean text output (no JSON wrapper)
            --system-prompt             Separate system instructions
        """
        cmd = cli_path or "claude"
        if not self.check_cli_available(cmd):
            return "Claude CLI not found."

        args = [
            cmd, "-p", message,
            "--output-format", "text",
            "--dangerously-skip-permissions",
        ]
        if system_prompt:
            args.extend(["--system-prompt", system_prompt])

        return await self._run(args, "Claude")

    # ── Codex CLI (agentic) ───────────────────────────────────────────

    async def _execute_codex(
        self, message: str, cli_path: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Execute OpenAI Codex CLI in agentic mode.

        Flags:
            exec                                    Non-interactive subcommand
            --dangerously-bypass-approvals-and-sandbox  Skip all prompts + sandbox
            --skip-git-repo-check                   Works outside git repos
        """
        cmd = cli_path or "codex"
        if not self.check_cli_available(cmd):
            return "Codex CLI not found."

        # Codex has no --system-prompt; prepend to message
        full_message = message
        if system_prompt:
            full_message = f"{system_prompt}\n\n{message}"

        args = [
            cmd, "exec", full_message,
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
        ]

        return await self._run(args, "Codex")

    # ── Gemini CLI (agentic) ──────────────────────────────────────────

    async def _execute_gemini(
        self, message: str, cli_path: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Execute Google Gemini CLI in agentic mode.

        Flags:
            -p              Non-interactive prompt
            --yolo          Auto-approve all tool actions
            -o text         Clean text output
        """
        cmd = cli_path or "gemini"
        if not self.check_cli_available(cmd):
            return "Gemini CLI not found."

        # Gemini has no --system-prompt; prepend to message
        full_message = message
        if system_prompt:
            full_message = f"{system_prompt}\n\n{message}"

        args = [
            cmd, "-p", full_message,
            "--yolo",
            "-o", "text",
        ]

        return await self._run(args, "Gemini")

    # ── Shared execution ─────────────────────────────────────────────

    async def _run(self, args: list, label: str) -> str:
        """Run a CLI subprocess with proper env, cwd, and timeout."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path.home()),
                env=self._get_env(),
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )

            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace").strip()
                # Some CLIs write useful output to stdout even on non-zero exit
                fallback = stdout.decode("utf-8", errors="replace").strip()
                if fallback and not error_msg:
                    return fallback
                return f"{label} CLI error: {error_msg[:300]}"

            response = stdout.decode("utf-8", errors="replace").strip()
            return response or f"No response from {label} CLI."

        except asyncio.TimeoutError:
            return f"{label} CLI request timed out after {self.timeout} seconds."
        except Exception as e:
            return f"{label} CLI execution error: {str(e)[:200]}"

    def _get_env(self) -> dict:
        """Build environment with PATH covering common CLI install locations."""
        env = os.environ.copy()
        extra_paths = [
            "/usr/local/bin",
            "/opt/homebrew/bin",
            str(Path.home() / ".local" / "bin"),
            str(Path.home() / ".npm-global" / "bin"),
            str(Path.home() / ".cargo" / "bin"),
        ]
        existing = env.get("PATH", "")
        for p in extra_paths:
            if p not in existing:
                existing = f"{p}:{existing}"
        env["PATH"] = existing
        return env

    # ── Utilities ─────────────────────────────────────────────────────

    def check_cli_available(self, cli_name: str) -> bool:
        """Check if CLI tool is available in expanded PATH."""
        return shutil.which(cli_name, path=self._get_env()["PATH"]) is not None

    def get_available_clis(self) -> dict:
        """Get dict of available CLI tools and their paths."""
        expanded = self._get_env()["PATH"]
        clis = {}
        for name in ["claude", "codex", "gemini"]:
            path = shutil.which(name, path=expanded)
            if path:
                clis[name] = path
        return clis

    def detect_best_cli(self) -> Optional[str]:
        """Detect the best available CLI provider."""
        for provider in ["claude", "gemini", "codex"]:
            if self.check_cli_available(provider):
                return f"{provider}-cli"
        return None
