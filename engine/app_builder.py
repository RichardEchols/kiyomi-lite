"""
Kiyomi App Builder — Generate single-file HTML applications
Lets users request apps like "build me a client intake form" and generates working web apps.
"""
import asyncio
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from engine.ai import chat
from engine.router import classify_message, pick_model
from engine.config import get_api_key, get_cli_timeout

logger = logging.getLogger(__name__)

# App request keywords/patterns
APP_KEYWORDS = [
    "build me", "create an app", "make me a website", "make me a tool",
    "build a form", "create a calculator", "make a tracker", "build a dashboard",
    "I need an app that", "can you make me a", "create a website", "build an app",
    "make an app", "generate an app", "create a tool", "build a tool",
    "make me a form", "create a form", "build a calculator", "make a calculator",
    "create a dashboard", "make a dashboard", "build a tracker", "create a tracker"
]

# Additional patterns (regex) for more nuanced detection
APP_PATTERNS = [
    r"\b(build|create|make|generate)\s+(me\s+)?(a|an)?\s*(\w+\s+)*(app|website|tool|form|calculator|tracker|dashboard|page|portal|system)",
    r"I\s+need\s+(a|an)\s+(\w+\s+)*(app|website|tool|form|calculator|tracker|dashboard|page|portal|system)",
    r"\bcan\s+you\s+(build|create|make)\s+(me\s+)?(a|an)?\s*(\w+\s+)*(app|website|tool|form|calculator|tracker|dashboard|page|portal|system)",
]

# Words that should prevent triggering (not software-related)
EXCLUDE_KEYWORDS = [
    "build my confidence", "make me happy", "create happiness", "build trust",
    "make me feel", "build relationships", "create memories", "make me laugh",
    "build muscle", "make me stronger", "create energy", "build stamina"
]


def is_app_request(message: str) -> bool:
    """
    Detect if user wants an app/website/tool built.
    
    Keywords: 'build me', 'create an app', 'make me a website', 'make me a tool',
    'build a form', 'create a calculator', 'make a tracker', 'build a dashboard',
    'I need an app that', 'can you make me a'
    
    Should NOT trigger on: 'build my confidence', 'make me happy', etc.
    Must involve creating a SOFTWARE THING.
    
    Args:
        message: User's message text
        
    Returns:
        bool: True if this appears to be an app building request
    """
    if not message or not isinstance(message, str):
        return False
    
    message_lower = message.lower().strip()
    
    # First check exclusions (non-software related)
    for exclude in EXCLUDE_KEYWORDS:
        if exclude in message_lower:
            logger.debug(f"App request excluded (non-software): '{exclude}' in '{message[:50]}...'")
            return False
    
    # Check exact keyword matches
    for keyword in APP_KEYWORDS:
        if keyword in message_lower:
            # Additional validation - must contain software-related context
            software_indicators = [
                "form", "app", "website", "tool", "calculator", "tracker", "dashboard",
                "html", "web", "interface", "ui", "program", "software", "application",
                "tracks", "tracking", "monitor", "log", "record", "manage", "organize"
            ]
            
            if any(indicator in message_lower for indicator in software_indicators):
                logger.info(f"App request detected (keyword): '{keyword}' in '{message[:50]}...'")
                return True
    
    # Check regex patterns  
    for pattern in APP_PATTERNS:
        if re.search(pattern, message_lower, re.IGNORECASE):
            logger.info(f"App request detected (pattern): '{pattern}' in '{message[:50]}...'")
            return True
    
    return False


def _generate_app_prompt(user_request: str) -> str:
    """
    Create a detailed prompt that will generate good single-file apps.
    
    The prompt should instruct the AI to:
    - Create a complete, self-contained HTML file
    - Include all CSS inline in <style> tags
    - Include all JS inline in <script> tags
    - Use modern, clean design (think Apple-style)
    - Make it responsive (works on phone and desktop)
    - Include sample/placeholder data
    - NO external dependencies (no CDN links)
    - Local storage for data persistence
    
    Args:
        user_request: What the user wants (e.g., "a client intake form for a law firm")
        
    Returns:
        str: Detailed prompt for the AI
    """
    
    prompt = f"""Create a complete, single-file HTML application for: {user_request}

REQUIREMENTS:
1. SINGLE FILE: Everything must be in ONE HTML file - no external dependencies
2. INLINE EVERYTHING: CSS in <style> tags, JavaScript in <script> tags
3. NO CDN LINKS: No Bootstrap, jQuery, Chart.js, or any external libraries
4. MODERN DESIGN: Clean, Apple-style interface with subtle gradients and rounded corners
5. RESPONSIVE: Must work perfectly on both phone and desktop
6. SAMPLE DATA: Include realistic placeholder/sample data so it doesn't look empty
7. LOCAL STORAGE: Use localStorage to save and persist data
8. FUNCTIONALITY: Must be fully functional, not just a mockup

DESIGN GUIDELINES:
- Clean, minimal aesthetic with plenty of white space
- Soft color palette (blues, grays, whites)
- Rounded corners (border-radius: 8px)
- Subtle shadows for depth
- Good typography (system fonts like -apple-system, BlinkMacSystemFont)
- Smooth hover effects and transitions
- Mobile-first responsive design

FEATURES TO INCLUDE BASED ON APP TYPE:

For FORMS (intake, contact, survey):
- Input validation with clear error messages
- Success message after submission
- Progress indicators for multi-step forms
- Auto-save to localStorage
- Export functionality (JSON/CSV)
- Clear form button

For TRACKERS (expense, habit, time):
- Add/Edit/Delete entries
- Filter and search functionality
- Summary statistics
- Charts using inline SVG (no external chart libraries)
- Export data functionality
- Date/time handling

For CALCULATORS:
- Clear input validation
- History of calculations
- Copy result to clipboard
- Keyboard shortcuts
- Error handling

For DASHBOARDS:
- Multiple widgets/sections
- Charts using inline SVG
- Real-time updates
- Responsive grid layout
- Data filtering options

TECHNICAL REQUIREMENTS:
- Use semantic HTML5 elements
- Include proper meta tags for mobile
- Add favicon (data URI)
- Include keyboard navigation
- Add loading states for actions
- Error handling and user feedback
- Clean, commented code structure

SAMPLE DATA:
Always include realistic sample data so the app looks functional immediately:
- For forms: 2-3 example entries
- For trackers: A week's worth of sample data
- For calculators: Example calculations in history
- For dashboards: Mock data showing trends

The output should start with <!DOCTYPE html> and end with </html>. Make it production-ready and beautiful!"""

    return prompt.strip()


def _extract_html(ai_response: str) -> str:
    """
    Extract HTML content from AI response.
    The AI might wrap it in code blocks or add explanation text.
    Find the <!DOCTYPE html> or <html> and extract just the HTML.
    
    Args:
        ai_response: Full response from AI
        
    Returns:
        str: Extracted HTML content
    """
    if not ai_response:
        return ""
    
    response = ai_response.strip()
    
    # Look for HTML content wrapped in code blocks
    code_block_patterns = [
        r"```html\s*\n(.*?)\n```",
        r"```\s*\n(.*?)\n```",
        r"`{3,}html\s*\n(.*?)\n`{3,}",
        r"`{3,}\s*\n(.*?)\n`{3,}"
    ]
    
    for pattern in code_block_patterns:
        match = re.search(pattern, response, re.DOTALL | re.IGNORECASE)
        if match:
            html_content = match.group(1).strip()
            if "<!DOCTYPE html>" in html_content or "<html" in html_content:
                logger.info("HTML extracted from code block")
                return html_content
    
    # Look for HTML content not in code blocks
    # Find from <!DOCTYPE html> or <html> to </html>
    html_patterns = [
        r"(<!DOCTYPE html>.*?</html>)",
        r"(<html.*?</html>)"
    ]
    
    for pattern in html_patterns:
        match = re.search(pattern, response, re.DOTALL | re.IGNORECASE)
        if match:
            logger.info("HTML extracted from plain text")
            return match.group(1).strip()
    
    # If we can't find proper HTML structure, check if the entire response looks like HTML
    if "<html" in response.lower() and "</html>" in response.lower():
        logger.info("Using entire response as HTML")
        return response
    
    logger.warning("No HTML content found in AI response")
    return ""


def _sanitize_filename(name: str) -> str:
    """
    Sanitize a filename by removing/replacing invalid characters.
    
    Args:
        name: Raw filename
        
    Returns:
        str: Safe filename for filesystem
    """
    # Remove HTML tags if present
    name = re.sub(r'<[^>]+>', '', name)
    
    # Replace invalid filename characters
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    
    # Replace multiple spaces/underscores with single underscore
    name = re.sub(r'[\s_]+', '_', name)
    
    # Remove leading/trailing underscores and dots
    name = name.strip('_.')
    
    # Limit length
    if len(name) > 50:
        name = name[:50]
    
    # Ensure it's not empty
    if not name:
        name = "kiyomi_app"
    
    return name


def _extract_app_info(html_content: str) -> tuple:
    """
    Extract app name and description from HTML content.
    
    Args:
        html_content: The generated HTML
        
    Returns:
        tuple: (app_name, description)
    """
    app_name = "Kiyomi App"
    description = "A custom web application"
    
    # Try to extract title
    title_match = re.search(r'<title>(.*?)</title>', html_content, re.IGNORECASE)
    if title_match:
        app_name = title_match.group(1).strip()
    
    # Try to extract description from meta tag
    desc_match = re.search(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
    if desc_match:
        description = desc_match.group(1).strip()
    else:
        # Try to extract from h1 or first p tag
        h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', html_content, re.IGNORECASE)
        if h1_match:
            description = f"Application: {h1_match.group(1).strip()}"
    
    return app_name, description


async def build_app(prompt: str, config: dict) -> dict:
    """
    Generate a complete single-file HTML application.
    
    Args:
        prompt: What the user wants (e.g., "a client intake form for a law firm")
        config: User's config (for AI provider selection)
    
    Returns: {
        "success": bool,
        "file_path": str,  # Where the HTML file was saved
        "app_name": str,   # Human-friendly name
        "description": str, # What it does
        "error": str       # If failed
    }
    
    Steps:
    1. Create a detailed prompt for the AI that asks it to generate a COMPLETE, 
       SINGLE-FILE HTML application with embedded CSS and JavaScript
    2. Route through the user's AI provider (Claude CLI, Codex CLI, Gemini CLI, or API)
    3. Extract the HTML from the response
    4. Save to ~/Documents/Kiyomi Apps/{app_name}.html
    5. Open in default browser
    """
    try:
        logger.info(f"Building app for prompt: '{prompt[:100]}...'")
        
        # Step 1: Create detailed prompt for AI
        full_prompt = _generate_app_prompt(prompt)
        
        # Step 2: Route through user's AI provider
        task_type = "complex"  # App generation is complex work
        provider, model = pick_model(task_type, config)
        api_key = config.get(f"{provider}_key", "") or get_api_key(config)
        
        logger.info(f"Using AI provider: {provider} with model: {model}")
        
        # Chat with AI to generate the app
        ai_response = await chat(
            message=full_prompt,
            provider=provider,
            model=model,
            api_key=api_key,
            system_prompt="You are an expert web developer who creates beautiful, functional single-file HTML applications. Always follow the requirements exactly.",
            history=[],
            tools_enabled=False,  # Don't need tools for this
            cli_path=config.get("cli_path", ""),
            cli_timeout=get_cli_timeout(config),
        )
        
        if not ai_response or "error" in ai_response.lower()[:100]:
            return {
                "success": False,
                "file_path": "",
                "app_name": "",
                "description": "",
                "error": f"AI failed to generate app: {ai_response[:200]}..."
            }
        
        # Step 3: Extract HTML from response
        html_content = _extract_html(ai_response)
        
        if not html_content:
            return {
                "success": False,
                "file_path": "",
                "app_name": "",
                "description": "",
                "error": "Could not extract HTML content from AI response"
            }
        
        # Extract app info for naming
        app_name, description = _extract_app_info(html_content)
        safe_name = _sanitize_filename(app_name)
        
        # Step 4: Save to ~/Documents/Kiyomi Apps/
        apps_dir = Path.home() / "Documents" / "Kiyomi Apps"
        apps_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate unique filename if file exists
        base_filename = f"{safe_name}.html"
        file_path = apps_dir / base_filename
        counter = 1
        
        while file_path.exists():
            file_path = apps_dir / f"{safe_name}_{counter}.html"
            counter += 1
        
        # Write the HTML file
        file_path.write_text(html_content, encoding="utf-8")
        logger.info(f"App saved to: {file_path}")
        
        # Step 5: Open in default browser
        try:
            subprocess.run(["open", str(file_path)], check=False)
            logger.info(f"Opened app in browser: {file_path}")
        except Exception as e:
            logger.warning(f"Could not open app in browser: {e}")
        
        return {
            "success": True,
            "file_path": str(file_path),
            "app_name": app_name,
            "description": description,
            "error": ""
        }
        
    except Exception as e:
        logger.error(f"App building error: {e}")
        return {
            "success": False,
            "file_path": "",
            "app_name": "",
            "description": "",
            "error": f"Failed to build app: {str(e)[:200]}"
        }


def get_recent_apps(limit: int = 10) -> list:
    """
    Get list of recently created apps.
    
    Args:
        limit: Maximum number of apps to return
        
    Returns:
        list: List of app info dicts
    """
    try:
        apps_dir = Path.home() / "Documents" / "Kiyomi Apps"
        if not apps_dir.exists():
            return []
        
        apps = []
        for html_file in apps_dir.glob("*.html"):
            if html_file.is_file():
                # Get file stats
                stat = html_file.stat()
                created = time.strftime('%Y-%m-%d %H:%M', time.localtime(stat.st_mtime))
                
                # Try to extract app info from file
                try:
                    content = html_file.read_text(encoding="utf-8", errors="ignore")[:2000]
                    app_name, description = _extract_app_info(content)
                except Exception:
                    app_name = html_file.stem.replace("_", " ").title()
                    description = "Custom web application"
                
                apps.append({
                    "file_path": str(html_file),
                    "app_name": app_name,
                    "description": description,
                    "created": created,
                    "size_kb": round(stat.st_size / 1024, 1)
                })
        
        # Sort by creation time (newest first)
        apps.sort(key=lambda x: x["created"], reverse=True)
        return apps[:limit]
        
    except Exception as e:
        logger.error(f"Error getting recent apps: {e}")
        return []


def cleanup_old_apps(max_age_days: int = 30):
    """
    Clean up old generated apps from ~/Documents/Kiyomi Apps/.
    
    Args:
        max_age_days: Maximum age in days before cleanup
    """
    try:
        apps_dir = Path.home() / "Documents" / "Kiyomi Apps"
        if not apps_dir.exists():
            return
        
        cutoff_time = time.time() - (max_age_days * 24 * 3600)
        
        for app_file in apps_dir.glob("*.html"):
            if app_file.is_file() and app_file.stat().st_mtime < cutoff_time:
                try:
                    app_file.unlink()
                    logger.debug(f"Cleaned up old app: {app_file}")
                except Exception as e:
                    logger.warning(f"Could not clean up {app_file}: {e}")
    
    except Exception as e:
        logger.error(f"Error during app cleanup: {e}")


def get_app_stats() -> dict:
    """Get stats about app generation usage."""
    try:
        apps_dir = Path.home() / "Documents" / "Kiyomi Apps"
        if not apps_dir.exists():
            return {"total_apps": 0, "total_size_mb": 0}
        
        total_files = 0
        total_size = 0
        
        for app_file in apps_dir.glob("*.html"):
            if app_file.is_file():
                total_files += 1
                total_size += app_file.stat().st_size
        
        return {
            "total_apps": total_files,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "apps_directory": str(apps_dir)
        }
    
    except Exception as e:
        logger.error(f"Error getting app stats: {e}")
        return {"error": str(e)}