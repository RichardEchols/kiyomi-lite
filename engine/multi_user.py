"""
Kiyomi Lite — Multi-User Support
"Shared bot, separate memories."

Manages user profiles and memory directories. Each user gets their own
isolated memory space while sharing one bot instance.
"""
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

from engine.config import CONFIG_DIR

logger = logging.getLogger(__name__)

USERS_FILE = CONFIG_DIR / "users.json"
DEFAULT_MEMORY_SUBDIR = "memory"  # Original location for backward compatibility

class UserManager:
    """Manages user profiles and memory directories."""
    
    def __init__(self):
        self._users_cache: Optional[Dict] = None
    
    def _load_users(self) -> Dict:
        """Load users from users.json file."""
        if self._users_cache is not None:
            return self._users_cache
            
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        
        if USERS_FILE.exists():
            try:
                with open(USERS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._users_cache = data
                    return data
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load users.json: {e}")
        
        # Create default structure
        default_data = {"users": []}
        self._users_cache = default_data
        return default_data
    
    def _save_users(self, data: Dict):
        """Save users data to users.json file."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(USERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._users_cache = data
            logger.info(f"Saved {len(data.get('users', []))} users to {USERS_FILE}")
        except IOError as e:
            logger.error(f"Failed to save users.json: {e}")
            raise
    
    def get_or_create_user(self, telegram_id: str, first_name: str) -> Dict:
        """Get existing user or create a new one. Auto-creates on first message."""
        data = self._load_users()
        
        # Look for existing user
        for user in data.get("users", []):
            if user.get("telegram_id") == telegram_id:
                return user
        
        # Create new user
        user_id = f"user_{len(data.get('users', [])) + 1}_{telegram_id}"
        
        # Generate memory directory name
        # First user keeps default location for backward compatibility
        if not data.get("users"):
            memory_dir = DEFAULT_MEMORY_SUBDIR
        else:
            # Clean first_name for filesystem safety
            clean_name = "".join(c for c in first_name.lower() if c.isalnum() or c == "_")[:20]
            if not clean_name:
                clean_name = f"user_{len(data['users']) + 1}"
            memory_dir = f"memory_{clean_name}"
        
        new_user = {
            "id": user_id,
            "name": first_name,
            "telegram_id": telegram_id,
            "memory_dir": memory_dir,
            "created_at": datetime.now().isoformat()
        }
        
        data.setdefault("users", []).append(new_user)
        self._save_users(data)
        
        logger.info(f"Created new user: {first_name} (ID: {telegram_id}) -> {memory_dir}")
        return new_user
    
    def get_user_memory_dir(self, telegram_id: str) -> Optional[Path]:
        """Get the memory directory path for a user."""
        data = self._load_users()
        
        for user in data.get("users", []):
            if user.get("telegram_id") == telegram_id:
                memory_subdir = user.get("memory_dir", DEFAULT_MEMORY_SUBDIR)
                return CONFIG_DIR / memory_subdir
        
        return None
    
    def list_users(self) -> List[Dict]:
        """Return all registered users."""
        data = self._load_users()
        return data.get("users", [])
    
    def switch_user(self, telegram_id: str) -> Optional[Dict]:
        """Switch to a different user (admin function). Returns user data if found."""
        data = self._load_users()
        
        for user in data.get("users", []):
            if user.get("telegram_id") == telegram_id:
                return user
        
        return None
    
    def get_user_by_telegram_id(self, telegram_id: str) -> Optional[Dict]:
        """Get user data by Telegram ID."""
        data = self._load_users()
        
        for user in data.get("users", []):
            if user.get("telegram_id") == telegram_id:
                return user
        
        return None
    
    def update_user(self, telegram_id: str, updates: Dict) -> bool:
        """Update user data. Returns True if successful."""
        data = self._load_users()
        
        for user in data.get("users", []):
            if user.get("telegram_id") == telegram_id:
                user.update(updates)
                user["updated_at"] = datetime.now().isoformat()
                self._save_users(data)
                logger.info(f"Updated user {telegram_id}: {updates}")
                return True
        
        return False
    
    def get_stats(self) -> Dict:
        """Get user statistics."""
        data = self._load_users()
        users = data.get("users", [])
        
        return {
            "total_users": len(users),
            "users_by_memory_dir": {u.get("memory_dir"): u.get("name") for u in users},
            "creation_dates": [u.get("created_at") for u in users if u.get("created_at")]
        }


# Global instance
_user_manager = UserManager()

# Convenience functions
def get_or_create_user(telegram_id: str, first_name: str) -> Dict:
    """Get existing user or create a new one. Auto-creates on first message."""
    return _user_manager.get_or_create_user(telegram_id, first_name)

def get_user_memory_dir(telegram_id: str) -> Optional[Path]:
    """Get the memory directory path for a user."""
    return _user_manager.get_user_memory_dir(telegram_id)

def list_users() -> List[Dict]:
    """Return all registered users."""
    return _user_manager.list_users()

def switch_user(telegram_id: str) -> Optional[Dict]:
    """Switch to a different user (admin function). Returns user data if found."""
    return _user_manager.switch_user(telegram_id)

def get_user_by_telegram_id(telegram_id: str) -> Optional[Dict]:
    """Get user data by Telegram ID."""
    return _user_manager.get_user_by_telegram_id(telegram_id)

def update_user(telegram_id: str, updates: Dict) -> bool:
    """Update user data. Returns True if successful."""
    return _user_manager.update_user(telegram_id, updates)

def get_user_stats() -> Dict:
    """Get user statistics."""
    return _user_manager.get_stats()