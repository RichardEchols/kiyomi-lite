# Kiyomi Self-Update System

The self-update system allows Kiyomi to update herself from GitHub automatically or on demand.

## How It Works

### User-Triggered Updates
Users can trigger updates by saying any of these in Telegram:
- "update"
- "update yourself"
- "check for updates" 
- "upgrade"
- "get latest version"
- "please update"
- "upgrade to latest"

The system **will NOT** trigger on:
- "update my calendar"
- "update the spreadsheet"
- "update my profile"
- etc. (must be about Kiyomi herself)

### Automatic Updates

#### Startup Check
On every startup, Kiyomi:
1. Checks GitHub for new commits
2. If updates are available:
   - **With auto_update=false** (default): Notifies user "Hey! I have updates available. Say 'update' to get the latest features!"
   - **With auto_update=true**: Updates silently and restarts automatically

#### Auto-Update Configuration
Add to `~/.kiyomi/config.json`:
```json
{
  "auto_update": true
}
```

## Update Process

When an update is triggered:
1. **Check**: `git fetch` and compare HEAD vs origin/main
2. **Pull**: `git pull origin main` 
3. **Dependencies**: If `requirements.txt` changed, run `pip install -r requirements.txt`
4. **Restart**: Replace the current process with a fresh one
5. **Notify**: Tell user what was updated

## Files Modified

### New Files
- `engine/updater.py` - Core update functionality

### Modified Files
- `engine/bot.py` - Added update detection early in message handling + startup check
- `engine/config.py` - Added `auto_update` configuration option

## Testing

Run the test suite:
```bash
cd engine/
python3 updater.py
```

Test individual functions:
```python
from updater import is_update_request, check_for_updates
import asyncio

# Test detection
print(is_update_request('update'))  # True
print(is_update_request('update my calendar'))  # False

# Test update check
result = asyncio.run(check_for_updates())
print(result)
```

## Requirements

- Must be running in a git repository
- Git remote 'origin' pointing to GitHub repo
- Main branch is 'main'
- User must have write access to the repo directory (for pulling changes)

## Error Handling

- Git not available: Gracefully degrades, no updates
- Network issues: Returns error message, doesn't crash
- Permission issues: Returns error message to user
- Update conflicts: Handles git pull failures gracefully

## Security

- Only updates from the configured git remote
- Uses same credentials as the current git setup
- No external download of arbitrary code
- Uses `os.execv` for secure process replacement

## Future Enhancements

Possible improvements:
- Update rollback capability
- Scheduled update checks (daily/weekly)
- Update notifications in other channels
- Version pinning/release channels
- Update size/impact warnings