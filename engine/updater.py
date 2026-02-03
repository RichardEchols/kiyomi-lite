"""
Kiyomi Self-Update System
Handles automatic updates from GitHub repository.
"""
import subprocess
import os
import logging
import sys
import asyncio
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def is_update_request(message: str) -> bool:
    """Detect if user is asking to update Kiyomi herself.
    
    Args:
        message: User's message text
        
    Returns:
        True if message is requesting an update of Kiyomi
    """
    message_lower = message.lower().strip()
    
    # Direct update keywords - must be about Kiyomi herself
    update_patterns = [
        r'\bupdate\s*(yourself|kiyomi)\b',
        r'\bupgrade\s*(yourself|kiyomi)\b', 
        r'\bcheck\s+for\s+updates?\b',
        r'\bget\s+latest\s+version\b',
        r'\bupdate\s+to\s+latest\b',
        r'\bupgrade\s+to\s+latest\b',
        r'\bplease\s+update\b',
        r'\bplease\s+upgrade\b',
        r'^update$',  # Just "update" alone
        r'^upgrade$'  # Just "upgrade" alone
    ]
    
    # Check if any pattern matches
    for pattern in update_patterns:
        if re.search(pattern, message_lower):
            return True
    
    # Check for standalone "update" but make sure it's not about something else
    if 'update' in message_lower:
        # Exclude common false positives
        false_positives = [
            'calendar', 'spreadsheet', 'document', 'profile', 'status',
            'schedule', 'appointment', 'meeting', 'reminder', 'task',
            'file', 'record', 'database', 'contact', 'address'
        ]
        
        # If "update" appears with these words, it's probably not about Kiyomi
        for fp in false_positives:
            if fp in message_lower:
                return False
        
        # If "update" appears alone or with personal pronouns, it's probably about Kiyomi
        update_indicators = ['update me', 'update us', 'need an update', 'want an update']
        for indicator in update_indicators:
            if indicator in message_lower:
                return True
    
    return False


def get_current_version() -> str:
    """Get current version from git commit hash or VERSION file.
    
    Returns:
        Version string (commit hash or version number)
    """
    try:
        # Try to get git commit hash
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        logger.warning(f"Could not get git version: {e}")
    
    # Fallback: check for VERSION file
    try:
        version_file = Path(__file__).parent / "VERSION"
        if version_file.exists():
            return version_file.read_text().strip()
    except Exception as e:
        logger.warning(f"Could not read VERSION file: {e}")
    
    return "unknown"


def get_changelog(since_commit: str) -> str:
    """Get human-readable changelog since a specific commit.
    
    Args:
        since_commit: Git commit hash to compare from
        
    Returns:
        Formatted changelog string
    """
    try:
        result = subprocess.run(
            ['git', 'log', f'{since_commit}..HEAD', '--oneline', '--max-count=10'],
            capture_output=True,
            text=True,
            timeout=15
        )
        
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split('\n')
            formatted_changes = []
            for line in lines:
                if line.strip():
                    # Format: "abc1234 Add new feature"
                    parts = line.split(' ', 1)
                    if len(parts) >= 2:
                        commit_hash = parts[0]
                        message = parts[1]
                        formatted_changes.append(f"• {message}")
                    else:
                        formatted_changes.append(f"• {line}")
            
            return '\n'.join(formatted_changes) if formatted_changes else "No detailed changes available"
        else:
            return "No changes found"
    
    except Exception as e:
        logger.error(f"Error getting changelog: {e}")
        return f"Could not retrieve changelog: {str(e)}"


async def check_for_updates() -> dict:
    """Check if updates are available on GitHub.
    
    Returns:
        Dictionary with keys: available, current, latest, changes
    """
    try:
        current_version = get_current_version()
        
        # Fetch latest changes from remote
        logger.info("Fetching latest updates from GitHub...")
        fetch_result = await asyncio.create_subprocess_exec(
            'git', 'fetch', 'origin', 'main',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await fetch_result.communicate()
        
        if fetch_result.returncode != 0:
            return {
                'available': False,
                'current': current_version,
                'latest': current_version,
                'changes': 'Could not fetch from remote repository'
            }
        
        # Check if we're behind origin/main
        status_result = await asyncio.create_subprocess_exec(
            'git', 'rev-list', '--count', 'HEAD..origin/main',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await status_result.communicate()
        
        if status_result.returncode != 0:
            return {
                'available': False,
                'current': current_version,
                'latest': current_version,
                'changes': 'Could not check update status'
            }
        
        commits_behind = int(stdout.decode().strip()) if stdout.decode().strip().isdigit() else 0
        updates_available = commits_behind > 0
        
        # Get latest commit hash from origin/main
        latest_result = await asyncio.create_subprocess_exec(
            'git', 'rev-parse', '--short', 'origin/main',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        latest_stdout, _ = await latest_result.communicate()
        latest_version = latest_stdout.decode().strip() if latest_result.returncode == 0 else current_version
        
        # Get changes if updates are available
        changes = ""
        if updates_available:
            changes = get_changelog(current_version)
            logger.info(f"Found {commits_behind} new commits available")
        else:
            logger.info("No updates available")
        
        return {
            'available': updates_available,
            'current': current_version,
            'latest': latest_version,
            'changes': changes
        }
        
    except Exception as e:
        logger.error(f"Error checking for updates: {e}")
        return {
            'available': False,
            'current': get_current_version(),
            'latest': 'unknown',
            'changes': f'Update check failed: {str(e)}'
        }


async def perform_update() -> dict:
    """Pull latest code from GitHub and prepare for restart.
    
    Returns:
        Dictionary with keys: success, message, changes
    """
    try:
        current_version = get_current_version()
        logger.info(f"Starting update from version {current_version}")
        
        # Store the requirements.txt hash before update to detect changes
        requirements_path = Path(__file__).parent / "requirements.txt"
        old_req_hash = ""
        if requirements_path.exists():
            try:
                old_req_hash = subprocess.run(
                    ['git', 'hash-object', str(requirements_path)],
                    capture_output=True,
                    text=True,
                    timeout=5
                ).stdout.strip()
            except Exception:
                pass
        
        # Pull latest changes
        logger.info("Pulling latest changes from GitHub...")
        pull_result = await asyncio.create_subprocess_exec(
            'git', 'pull', 'origin', 'main',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await pull_result.communicate()
        
        if pull_result.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown git error"
            logger.error(f"Git pull failed: {error_msg}")
            return {
                'success': False,
                'message': f'Update failed: {error_msg}',
                'changes': ''
            }
        
        pull_output = stdout.decode()
        logger.info(f"Git pull result: {pull_output}")
        
        # Check if requirements.txt changed and update dependencies if needed
        new_req_hash = ""
        requirements_updated = False
        if requirements_path.exists():
            try:
                new_req_hash = subprocess.run(
                    ['git', 'hash-object', str(requirements_path)],
                    capture_output=True,
                    text=True,
                    timeout=5
                ).stdout.strip()
                
                if old_req_hash and new_req_hash != old_req_hash:
                    logger.info("requirements.txt changed, updating dependencies...")
                    pip_result = await asyncio.create_subprocess_exec(
                        sys.executable, '-m', 'pip', 'install', '-r', str(requirements_path),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    pip_stdout, pip_stderr = await pip_result.communicate()
                    
                    if pip_result.returncode == 0:
                        requirements_updated = True
                        logger.info("Dependencies updated successfully")
                    else:
                        pip_error = pip_stderr.decode() if pip_stderr else "Unknown pip error"
                        logger.warning(f"Dependency update failed: {pip_error}")
            
            except Exception as e:
                logger.warning(f"Could not check/update requirements: {e}")
        
        # Get the changelog from old version to new
        new_version = get_current_version()
        changes = get_changelog(current_version) if current_version != new_version else "Already up to date"
        
        success_message = f"✅ Updated successfully from {current_version} to {new_version}"
        if requirements_updated:
            success_message += "\n📦 Dependencies updated"
        
        logger.info(f"Update completed: {current_version} -> {new_version}")
        
        return {
            'success': True,
            'message': success_message,
            'changes': changes
        }
        
    except Exception as e:
        logger.error(f"Update failed with exception: {e}")
        return {
            'success': False,
            'message': f'Update failed: {str(e)}',
            'changes': ''
        }


async def restart_bot():
    """Restart the Kiyomi bot process.
    
    Uses os.execv to replace the current process with a new instance.
    This function does not return if successful.
    """
    try:
        logger.info("Restarting bot process...")
        
        # Get the current executable and arguments
        executable = sys.executable
        args = sys.argv.copy()
        
        # Ensure the first argument is the executable name (required by execv)
        if args and not args[0].endswith(('python', 'python3', 'Kiyomi')):
            args.insert(0, executable)
        
        logger.info(f"Restarting with: {executable} {args}")
        
        # Replace current process with new one
        os.execv(executable, args)
        
    except Exception as e:
        logger.error(f"Failed to restart bot: {e}")
        # Fallback: try subprocess approach
        try:
            logger.info("Trying subprocess restart approach...")
            import subprocess
            subprocess.Popen([sys.executable] + sys.argv)
            # Exit the current process
            sys.exit(0)
        except Exception as fallback_error:
            logger.error(f"Subprocess restart also failed: {fallback_error}")
            raise RuntimeError(f"Could not restart bot: {e}")


# Test functions for development
if __name__ == "__main__":
    # Test the is_update_request function
    test_cases = [
        ("update", True),
        ("update yourself", True),
        ("check for updates", True),
        ("upgrade", True),
        ("get latest version", True),
        ("update my calendar", False),
        ("update the spreadsheet", False),
        ("I need to update my profile", False),
        ("update Kiyomi", True),
        ("please update yourself", True),
    ]
    
    print("Testing is_update_request function:")
    for message, expected in test_cases:
        result = is_update_request(message)
        status = "✅" if result == expected else "❌"
        print(f"{status} '{message}' -> {result} (expected {expected})")
    
    # Test version functions
    print(f"\nCurrent version: {get_current_version()}")
    
    # Test async functions
    async def test_async():
        print("\nTesting update check...")
        result = await check_for_updates()
        print(f"Update check result: {result}")
    
    asyncio.run(test_async())