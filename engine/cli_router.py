"""
Kiyomi CLI Router — Unified interface for AI CLI tools
Routes AI requests through command-line tools instead of APIs.
"""
import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

class CLIRouter:
    """Unified router for AI CLI tools."""
    
    def __init__(self, timeout: int = 60):
        """Initialize CLI router with configurable timeout."""
        self.timeout = timeout
        
    async def chat(
        self, 
        message: str, 
        provider: str, 
        cli_path: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Route AI chat request through CLI tool.
        
        Args:
            message: User message/prompt
            provider: CLI provider (claude-cli, codex-cli, gemini-cli)
            cli_path: Optional custom path to CLI binary
            **kwargs: Additional arguments (ignored for CLI)
            
        Returns:
            AI response string or error message
        """
        if not message or not message.strip():
            return "No message provided."
            
        provider = provider.lower().replace("-cli", "")  # normalize
        
        try:
            if provider == "claude":
                return await self._execute_claude(message, cli_path)
            elif provider == "codex":
                return await self._execute_codex(message, cli_path)
            elif provider == "gemini":
                return await self._execute_gemini(message, cli_path)
            else:
                return f"Unsupported CLI provider: {provider}"
                
        except Exception as e:
            logger.error(f"CLI router error ({provider}): {e}")
            return f"CLI error: {str(e)[:100]}"
    
    async def _execute_claude(self, message: str, cli_path: Optional[str] = None) -> str:
        """Execute Claude Code CLI."""
        cmd = cli_path or "claude"
        
        if not self.check_cli_available(cmd):
            return "Claude CLI not found. Install Claude Code CLI or check your PATH."
            
        try:
            proc = await asyncio.create_subprocess_exec(
                cmd, "-p", message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            
            if proc.returncode != 0:
                error_msg = stderr.decode('utf-8', errors='replace').strip()
                return f"Claude CLI error: {error_msg[:200]}"
                
            response = stdout.decode('utf-8', errors='replace').strip()
            return response or "No response from Claude CLI."
            
        except asyncio.TimeoutError:
            return f"Claude CLI request timed out after {self.timeout} seconds."
        except Exception as e:
            return f"Claude CLI execution error: {str(e)[:100]}"
    
    async def _execute_codex(self, message: str, cli_path: Optional[str] = None) -> str:
        """Execute OpenAI Codex CLI."""
        cmd = cli_path or "codex"
        
        if not self.check_cli_available(cmd):
            return "Codex CLI not found. Install OpenAI Codex CLI or check your PATH."
            
        try:
            proc = await asyncio.create_subprocess_exec(
                cmd, "-q", message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            
            if proc.returncode != 0:
                error_msg = stderr.decode('utf-8', errors='replace').strip()
                return f"Codex CLI error: {error_msg[:200]}"
                
            response = stdout.decode('utf-8', errors='replace').strip()
            return response or "No response from Codex CLI."
            
        except asyncio.TimeoutError:
            return f"Codex CLI request timed out after {self.timeout} seconds."
        except Exception as e:
            return f"Codex CLI execution error: {str(e)[:100]}"
    
    async def _execute_gemini(self, message: str, cli_path: Optional[str] = None) -> str:
        """Execute Google Gemini CLI."""
        cmd = cli_path or "gemini"
        
        if not self.check_cli_available(cmd):
            return "Gemini CLI not found. Install Google Gemini CLI or check your PATH."
            
        try:
            proc = await asyncio.create_subprocess_exec(
                cmd, "-p", message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            
            if proc.returncode != 0:
                error_msg = stderr.decode('utf-8', errors='replace').strip()
                return f"Gemini CLI error: {error_msg[:200]}"
                
            response = stdout.decode('utf-8', errors='replace').strip()
            return response or "No response from Gemini CLI."
            
        except asyncio.TimeoutError:
            return f"Gemini CLI request timed out after {self.timeout} seconds."
        except Exception as e:
            return f"Gemini CLI execution error: {str(e)[:100]}"
    
    def check_cli_available(self, cli_name: str) -> bool:
        """Check if CLI tool is available in PATH."""
        return shutil.which(cli_name) is not None
    
    def get_available_clis(self) -> dict:
        """Get dict of available CLI tools and their paths."""
        clis = {}
        for name in ["claude", "codex", "gemini"]:
            path = shutil.which(name)
            if path:
                clis[name] = path
        return clis
    
    def detect_best_cli(self) -> Optional[str]:
        """Detect the best available CLI provider."""
        # Preference order: claude > gemini > codex
        for provider in ["claude", "gemini", "codex"]:
            if self.check_cli_available(provider):
                return f"{provider}-cli"
        return None