#!/usr/bin/env python3
"""
Kiyomi Lite — Menu Bar App
Sits in your menu bar. Runs the engine. Opens Telegram.
That's it. Simple.
"""
import os
import sys
import json
import signal
import subprocess
import threading
import webbrowser
import logging
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# --- PyInstaller resource path helper ---
def _resource_path(relative: str) -> Path:
    """Get absolute path to resource, works in dev and PyInstaller bundle."""
    if getattr(sys, '_MEIPASS', None):
        return Path(sys._MEIPASS) / relative
    return Path(__file__).parent / relative

# Setup
APP_DIR = _resource_path(".")
ENGINE_DIR = _resource_path("engine")
ONBOARDING_DIR = _resource_path("onboarding")
CONFIG_DIR = Path.home() / ".kiyomi"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOGS_DIR = CONFIG_DIR / "logs"

# Ensure dirs
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
(CONFIG_DIR / "memory").mkdir(parents=True, exist_ok=True)
(CONFIG_DIR / "skills").mkdir(parents=True, exist_ok=True)

# Raw debug log (no dependency on logging module working)
_debug_log = LOGS_DIR / "debug.log"
def _dbg(msg):
    try:
        with open(_debug_log, "a") as f:
            import datetime
            f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")
    except Exception:
        pass

_dbg(f"app.py starting. sys._MEIPASS={getattr(sys, '_MEIPASS', 'N/A')}")
_dbg(f"APP_DIR={APP_DIR}")
_dbg(f"ENGINE_DIR={ENGINE_DIR} exists={ENGINE_DIR.exists()}")
_dbg(f"ONBOARDING_DIR={ONBOARDING_DIR} exists={ONBOARDING_DIR.exists()}")

# In PyInstaller --windowed mode, sys.stdout/stderr are None
# Only use file handler to avoid crashes
_log_handlers = [logging.FileHandler(LOGS_DIR / "app.log")]
if sys.stdout is not None:
    _log_handlers.append(logging.StreamHandler())

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=_log_handlers,
)
logger = logging.getLogger("kiyomi-app")

# Engine process
engine_process = None


def is_setup_complete() -> bool:
    """Check if initial setup has been done with minimum required fields.

    Returns True only if config exists, setup_complete is True,
    AND the essential fields (provider + telegram token) are present.
    This prevents partial configs from skipping onboarding.
    """
    if not CONFIG_FILE.exists():
        return False
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        if not config.get("setup_complete", False):
            return False
        # Must have at least a provider and telegram token
        has_provider = bool(config.get("provider") or config.get("subscription"))
        has_telegram = bool(config.get("telegram_token"))
        return has_provider and has_telegram
    except Exception:
        return False


def load_config() -> dict:
    """Load config."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


_engine_thread = None
_engine_retries = 0
_MAX_ENGINE_RETRIES = 3


def _restart_process():
    """Restart the current process to pick up new config."""
    try:
        _dbg("Restarting app to apply config changes...")
        exe = sys.executable
        argv = sys.argv[:] if sys.argv else [exe]
        # Avoid duplicating the executable in argv.
        if argv and argv[0] == exe:
            args = [exe] + argv[1:]
        else:
            args = [exe] + argv
        os.execv(exe, args)
    except Exception as e:
        _dbg(f"Restart failed: {type(e).__name__}: {e}")
        # Fall back to starting engine without restart.
        try:
            start_engine()
        except Exception:
            pass


def start_engine():
    """Start the Kiyomi engine (Telegram bot) IN-PROCESS.
    
    Runs the bot directly in a background thread using the PyInstaller-bundled
    packages. No subprocess, no pip install, no system Python needed.
    Works on any Mac — even ones with zero developer tools installed.
    """
    global _engine_thread, _engine_retries, engine_process
    
    # Check if already running
    if _engine_thread and _engine_thread.is_alive():
        logger.info("Engine already running")
        return
    
    if _engine_retries >= _MAX_ENGINE_RETRIES:
        _dbg(f"Engine failed {_MAX_ENGINE_RETRIES} times, giving up")
        logger.error("Engine failed to start after multiple attempts")
        return

    bot_path = ENGINE_DIR / "bot.py"
    if not bot_path.exists():
        logger.error(f"Engine not found at {bot_path}")
        return

    logger.info("Starting Kiyomi engine (in-process)...")
    _dbg(f"start_engine: ENGINE_DIR={ENGINE_DIR}")
    
    # Add engine dir to path so engine modules can import each other
    engine_str = str(ENGINE_DIR)
    parent_str = str(ENGINE_DIR.parent)
    if engine_str not in sys.path:
        sys.path.insert(0, engine_str)
    if parent_str not in sys.path:
        sys.path.insert(0, parent_str)
    
    def _run_engine():
        global _engine_retries
        try:
            _dbg("Engine thread starting...")
            # Import the bot module from the bundled engine
            import importlib
            # Clear any cached imports to pick up fresh
            for mod_name in list(sys.modules.keys()):
                if mod_name.startswith('engine.') or mod_name in (
                    'config', 'router', 'ai', 'memory', 'reminders',
                    'skills_integration', 'proactive'
                ):
                    del sys.modules[mod_name]
            
            from engine.bot import main_threaded
            _dbg("Bot module imported, calling main_threaded()...")
            main_threaded()
        except Exception as e:
            _dbg(f"Engine thread crashed: {type(e).__name__}: {e}")
            logger.error(f"Engine error: {e}", exc_info=True)
            _engine_retries += 1
    
    _engine_thread = threading.Thread(target=_run_engine, daemon=True, name="kiyomi-engine")
    _engine_thread.start()
    
    # Quick check that it's alive after 2 seconds
    import time
    time.sleep(2)
    if _engine_thread.is_alive():
        _engine_retries = 0
        _dbg("Engine thread alive after 2s ✓")
        logger.info("Engine started successfully")
    else:
        _dbg("Engine thread died within 2s!")
        _engine_retries += 1


def stop_engine():
    """Stop the Kiyomi engine."""
    global _engine_thread
    if _engine_thread and _engine_thread.is_alive():
        logger.info("Stopping engine...")
        # The thread is daemon, so it dies with the process.
        # For graceful stop, we'd need a stop event — but daemon is fine for quit.
        _engine_thread = None
        logger.info("Engine stopped")


def engine_running() -> bool:
    """Check if engine is running."""
    return _engine_thread is not None and _engine_thread.is_alive()


class OnboardingHandler(BaseHTTPRequestHandler):
    """Serve onboarding wizard + handle config saves + file imports.
    
    Uses BaseHTTPRequestHandler (NOT SimpleHTTPRequestHandler) because
    PyInstaller --windowed bundles break method resolution on SHRS subclasses.
    """
    
    CONTENT_TYPES = {
        '.html': 'text/html; charset=utf-8',
        '.js': 'application/javascript',
        '.css': 'text/css',
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.ico': 'image/x-icon',
        '.svg': 'image/svg+xml',
        '.json': 'application/json',
    }
    
    def _send_json(self, status: int, data: dict):
        """Send JSON response."""
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    
    def _send_file(self, filepath: str):
        """Serve a file from the onboarding directory."""
        full_path = ONBOARDING_DIR / filepath
        if not full_path.exists() or not full_path.is_file():
            self.send_error(404, "File not found")
            return
        data = full_path.read_bytes()
        ext = Path(filepath).suffix.lower()
        ct = self.CONTENT_TYPES.get(ext, 'application/octet-stream')
        self.send_response(200)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    
    def do_GET(self):
        """Handle GET — serve files + config save endpoint."""
        from urllib.parse import urlparse, parse_qs
        import base64
        parsed = urlparse(self.path)
        path = parsed.path
        
        # API: Bot pool status (how many pre-made bots available)
        if path == "/api/telegram/pool":
            try:
                sys.path.insert(0, str(ENGINE_DIR.parent))
                from engine.bot_pool import get_pool_status, has_available_bots
                status = get_pool_status()
                status["has_bots"] = has_available_bots()
                self._send_json(200, status)
            except Exception as e:
                logger.error(f"Bot pool status error: {e}")
                self._send_json(200, {"has_bots": False, "total": 0, "available": 0, "claimed": 0})
            return

        # API: CLI status (detect installed + authenticated CLIs)
        if path == "/api/cli/status":
            try:
                sys.path.insert(0, str(ENGINE_DIR))
                sys.path.insert(0, str(ENGINE_DIR.parent))
                from engine.cli_installer import detect_all, get_subscription_info, get_best_provider
                status = detect_all()
                self._send_json(200, {
                    "providers": status,
                    "subscriptions": get_subscription_info(),
                    "best_provider": get_best_provider(),
                })
            except Exception as e:
                logger.error(f"CLI status error: {e}")
                self._send_json(500, {"error": str(e)})
            return

        # API: config save
        if path == "/api/config":
            params = parse_qs(parsed.query)
            if "data" in params:
                try:
                    raw = base64.b64decode(params["data"][0])
                    config = json.loads(raw)
                    config["setup_complete"] = True
                    with open(CONFIG_FILE, "w") as f:
                        json.dump(config, f, indent=2)
                    self._send_json(200, {"status": "ok"})
                    if engine_running():
                        logger.info("Config saved! Restarting app to apply changes...")
                        threading.Thread(target=_restart_process, daemon=True).start()
                    else:
                        logger.info("Config saved! Starting engine...")
                        threading.Thread(target=start_engine, daemon=True).start()
                    return
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                    return
            self._send_json(400, {"error": "Missing data parameter"})
            return
        
        # Serve static files
        if path == "/" or path == "":
            path = "/index.html"
        self._send_file(path.lstrip("/"))
    
    def _parse_multipart(self) -> tuple:
        """Parse a multipart/form-data POST and return (filename, file_bytes).
        
        Returns (None, None) on failure.
        """
        content_type = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in content_type:
            return None, None
        
        # Extract boundary
        boundary = None
        for part in content_type.split(';'):
            part = part.strip()
            if part.startswith('boundary='):
                boundary = part.split('=', 1)[1].strip('"')
                break
        if not boundary:
            return None, None
        
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        boundary_bytes = boundary.encode()
        parts = body.split(b'--' + boundary_bytes)
        
        for part in parts:
            if b'Content-Disposition' not in part:
                continue
            
            # Split headers from body at double newline
            if b'\r\n\r\n' in part:
                header_section, file_data = part.split(b'\r\n\r\n', 1)
            elif b'\n\n' in part:
                header_section, file_data = part.split(b'\n\n', 1)
            else:
                continue
            
            header_str = header_section.decode('utf-8', errors='replace')
            
            # Extract filename from Content-Disposition
            filename = None
            for line in header_str.split('\n'):
                if 'filename=' in line:
                    match = __import__('re').search(r'filename="?([^";\r\n]+)"?', line)
                    if match:
                        filename = match.group(1).strip()
                        break
            
            if filename:
                # Strip trailing boundary markers
                if file_data.endswith(b'\r\n'):
                    file_data = file_data[:-2]
                elif file_data.endswith(b'\n'):
                    file_data = file_data[:-1]
                # Remove trailing -- if present (end boundary)
                if file_data.endswith(b'--'):
                    file_data = file_data[:-2]
                if file_data.endswith(b'\r\n'):
                    file_data = file_data[:-2]
                return filename, file_data
        
        return None, None
    
    def do_POST(self):
        """Handle API endpoints."""
        if self.path == "/api/config":
            self._handle_config()
        elif self.path == "/api/import":
            self._handle_import()
        elif self.path == "/api/telegram/claim":
            self._handle_telegram_claim()
        elif self.path == "/api/cli/install":
            self._handle_cli_install()
        elif self.path == "/api/cli/auth":
            self._handle_cli_auth()
        else:
            self._send_json(404, {"error": "Not found"})
    
    def _handle_config(self):
        """Save config from onboarding wizard."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        try:
            config = json.loads(body)
            config["setup_complete"] = True
            
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
            
            self._send_json(200, {"status": "ok"})
            
            if engine_running():
                logger.info("Config saved! Restarting app to apply changes...")
                threading.Thread(target=_restart_process, daemon=True).start()
            else:
                logger.info("Config saved! Starting engine...")
                threading.Thread(target=start_engine, daemon=True).start()
            
        except Exception as e:
            self._send_json(500, {"error": str(e)})
    
    def _handle_import(self):
        """Accept uploaded file and run import_brain processing."""
        import tempfile
        
        try:
            filename, file_data = self._parse_multipart()
            
            if not filename or not file_data:
                self._send_json(400, {"error": "No file uploaded. Send as multipart/form-data with field name 'file'."})
                return
            
            # Validate extension
            if not (filename.endswith('.json') or filename.endswith('.zip')):
                self._send_json(400, {"error": f"Unsupported file type: {filename}. Use .json or .zip"})
                return
            
            # Save to temp file and process
            suffix = '.zip' if filename.endswith('.zip') else '.json'
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(file_data)
                tmp_path = tmp.name
            
            logger.info(f"Importing file: {filename} ({len(file_data)} bytes)")
            
            # Run import_brain
            sys.path.insert(0, str(APP_DIR))
            from import_brain import import_file
            result = import_file(tmp_path)
            
            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            
            # Update config to mark import done
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE) as f:
                    config = json.load(f)
                config["imported_chats"] = True
                config["import_source"] = result.source
                with open(CONFIG_FILE, "w") as f:
                    json.dump(config, f, indent=2)
            
            self._send_json(200, {
                "status": "ok",
                "conversations": result.conversations,
                "messages": result.messages,
                "facts_count": len(result.facts),
                "facts": result.facts[:50],
                "source": result.source,
                "errors": result.errors,
            })
            
            logger.info(f"Import complete: {result.conversations} conversations, {result.messages} messages, {len(result.facts)} facts")
            
        except Exception as e:
            logger.error(f"Import error: {e}")
            self._send_json(500, {"error": str(e)})
    
    def _handle_telegram_claim(self):
        """Claim a pre-made Telegram bot from the pool."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body) if body else {}
            claimed_by = data.get("name", "onboarding")

            sys.path.insert(0, str(ENGINE_DIR.parent))
            from engine.bot_pool import claim_bot

            result = claim_bot(claimed_by=claimed_by)
            if result:
                self._send_json(200, {
                    "status": "ok",
                    "token": result["token"],
                    "username": result["username"],
                    "display_name": result["display_name"],
                    "deep_link": result["deep_link"],
                })
            else:
                self._send_json(200, {
                    "status": "empty",
                    "error": "No bots available. Please set up manually.",
                })
        except Exception as e:
            logger.error(f"Telegram claim error: {e}")
            self._send_json(500, {"error": str(e)})

    def _handle_cli_install(self):
        """Install a CLI tool silently."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body) if body else {}
            provider = data.get("provider", "")
            if not provider:
                self._send_json(400, {"error": "Missing 'provider' field"})
                return

            sys.path.insert(0, str(ENGINE_DIR))
            sys.path.insert(0, str(ENGINE_DIR.parent))
            from engine.cli_installer import install_cli, check_cli_auth

            import asyncio
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(install_cli(provider))
            finally:
                loop.close()

            # After install, check auth status
            auth = check_cli_auth(provider)
            result["authenticated"] = auth["authenticated"]
            result["subscription"] = auth.get("subscription")

            self._send_json(200, result)
        except Exception as e:
            logger.error(f"CLI install error: {e}")
            self._send_json(500, {"error": str(e)})

    def _handle_cli_auth(self):
        """Trigger browser-based OAuth for a CLI provider."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body) if body else {}
            provider = data.get("provider", "")
            if not provider:
                self._send_json(400, {"error": "Missing 'provider' field"})
                return

            sys.path.insert(0, str(ENGINE_DIR))
            sys.path.insert(0, str(ENGINE_DIR.parent))
            from engine.cli_installer import launch_cli_auth

            import asyncio
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(launch_cli_auth(provider))
            finally:
                loop.close()

            self._send_json(200, result)
        except Exception as e:
            logger.error(f"CLI auth error: {e}")
            self._send_json(500, {"error": str(e)})

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


def start_onboarding_server(port=8765):
    """Start a simple HTTP server for the onboarding wizard."""
    import socket
    
    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True
        allow_reuse_port = True
        
        def server_bind(self):
            """Bind without calling socket.getfqdn() (hangs in PyInstaller)."""
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            # Call TCPServer.server_bind directly, skip HTTPServer.server_bind
            # which does socket.getfqdn() that can hang in bundled apps
            import socketserver
            socketserver.TCPServer.server_bind(self)
            # Set server_name/port manually (HTTPServer normally does this via getfqdn)
            self.server_name = "127.0.0.1"
            self.server_port = self.server_address[1]
    
    for attempt_port in [port, port + 1, port + 2]:
        try:
            _dbg(f"Trying to bind port {attempt_port}...")
            server = ReusableHTTPServer(('127.0.0.1', attempt_port), OnboardingHandler)
            _dbg(f"Server created on port {attempt_port}, starting thread...")
            def _serve(s):
                _dbg("serve_forever() starting")
                try:
                    s.serve_forever()
                except Exception as e:
                    _dbg(f"serve_forever() crashed: {e}")
            thread = threading.Thread(target=_serve, args=(server,), daemon=True)
            thread.start()
            _dbg(f"Thread started, thread.is_alive()={thread.is_alive()}")
            import time
            time.sleep(0.5)
            _dbg(f"After 0.5s sleep, thread.is_alive()={thread.is_alive()}")
            logger.info(f"Onboarding server started on http://127.0.0.1:{attempt_port}")
            return server, attempt_port
        except Exception as e:
            _dbg(f"Port {attempt_port} failed: {type(e).__name__}: {e}")
            logger.warning(f"Port {attempt_port} unavailable: {e}")
    
    logger.error("Could not bind to any port!")
    return None, None


def open_onboarding(port=8765):
    """Open the onboarding wizard in default browser."""
    webbrowser.open(f"http://127.0.0.1:{port}/index.html")


def open_telegram():
    """Open Telegram to chat with Kiyomi."""
    config = load_config()
    token = config.get("telegram_token", "")
    if token:
        # Extract bot username from token (first part before :)
        # User should have their bot link saved
        webbrowser.open("https://telegram.org")
    else:
        webbrowser.open("https://telegram.org")


def _acquire_lock() -> bool:
    """Ensure only one instance of Kiyomi runs at a time.
    
    Uses a lock file with PID. If another instance is running, exit gracefully.
    """
    lock_file = CONFIG_DIR / "kiyomi.lock"
    
    # Check existing lock
    if lock_file.exists():
        try:
            old_pid = int(lock_file.read_text().strip())
            if old_pid == os.getpid():
                # After an execv restart we keep the same PID; treat lock as ours.
                _dbg("Lock file matches current PID; refreshing lock")
            else:
                # Check if that process is actually running
                os.kill(old_pid, 0)  # signal 0 = just check, don't kill
                # Process exists — another instance is running
                _dbg(f"Another instance running (PID {old_pid}). Exiting.")
                logger.info(f"Kiyomi already running (PID {old_pid}). Exiting duplicate.")
                return False
        except (ValueError, ProcessLookupError, PermissionError):
            # Stale lock file or process gone — clean up
            _dbg("Stale lock file found, cleaning up")
            pass
    
    # Write our PID
    lock_file.write_text(str(os.getpid()))
    _dbg(f"Lock acquired (PID {os.getpid()})")
    
    # Clean up lock on exit
    import atexit
    atexit.register(lambda: lock_file.unlink(missing_ok=True))
    
    return True


def main():
    """Main entry point."""
    _dbg("main() entered")
    
    # Single-instance guard
    if not _acquire_lock():
        # Another Kiyomi is already running — just open the browser to it
        import webbrowser
        webbrowser.open("http://127.0.0.1:8765/")
        sys.exit(0)
    
    try:
        import rumps
        HAS_RUMPS = True
        _dbg("rumps imported OK")
    except ImportError:
        HAS_RUMPS = False
        _dbg("rumps NOT available")
    
    # Always start the onboarding/API server (needed for Settings + import endpoint)
    _dbg("Starting onboarding server...")
    server, actual_port = start_onboarding_server()
    if not server:
        logger.error("Failed to start server. Exiting.")
        sys.exit(1)
    
    if not is_setup_complete():
        # First run — show onboarding
        logger.info("First run detected. Opening onboarding...")
        open_onboarding(actual_port)
        
        if not HAS_RUMPS:
            # No menu bar — just keep server running
            logger.info("Waiting for setup to complete...")
            try:
                while not is_setup_complete():
                    import time
                    time.sleep(2)
                logger.info("Setup complete! Starting engine...")
                start_engine()
                # Keep running with auto-restart
                signal.signal(signal.SIGINT, lambda s, f: (stop_engine(), sys.exit(0)))
                signal.signal(signal.SIGTERM, lambda s, f: (stop_engine(), sys.exit(0)))
                while True:
                    import time
                    time.sleep(60)
                    # Auto-restart engine if it dies
                    if not engine_running() and is_setup_complete():
                        start_engine()
            except KeyboardInterrupt:
                stop_engine()
                sys.exit(0)
        else:
            # Has rumps — show menu bar immediately
            pass  # Falls through to rumps app below
    else:
        # Already set up — start engine
        start_engine()
    
    if HAS_RUMPS:
        # Menu bar app
        class KiyomiApp(rumps.App):
            def __init__(self):
                super().__init__(
                    "Kiyomi",
                    title="🌸",
                    quit_button=None,
                )
                self._port = actual_port
                self._status_item = rumps.MenuItem("Status: Starting...", callback=None)
                self.menu = [
                    rumps.MenuItem("Open Telegram", callback=self._open_telegram),
                    rumps.MenuItem("Settings", callback=self._open_settings),
                    None,  # Separator
                    self._status_item,
                    None,
                    rumps.MenuItem("Quit Kiyomi", callback=self._quit),
                ]
                # Start status checker
                self._timer = rumps.Timer(self._check_status, 10)
                self._timer.start()
            
            def _open_telegram(self, _):
                open_telegram()
            
            def _open_settings(self, _):
                open_onboarding(self._port)
            
            def _check_status(self, _):
                if engine_running():
                    self._status_item.title = "Status: Running ✅"
                else:
                    self._status_item.title = "Status: Stopped ❌"
                    # Try to restart
                    if is_setup_complete():
                        start_engine()
            
            def _quit(self, _):
                stop_engine()
                rumps.quit_application()
        
        app = KiyomiApp()
        app.run()
    else:
        # No rumps — just keep running
        logger.info("Menu bar not available (install rumps). Running in background.")
        signal.signal(signal.SIGINT, lambda s, f: (stop_engine(), sys.exit(0)))
        signal.signal(signal.SIGTERM, lambda s, f: (stop_engine(), sys.exit(0)))
        try:
            while True:
                import time
                time.sleep(60)
                # Auto-restart engine if it dies
                if not engine_running() and is_setup_complete():
                    start_engine()
        except KeyboardInterrupt:
            stop_engine()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise
