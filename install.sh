#!/bin/bash
# Kiyomi Bot — One-Line Installer for macOS
# Usage: curl -fsSL https://kiyomibot.ai/install.sh | bash
set -euo pipefail

KIYOMI_DIR="$HOME/.kiyomi"
KIYOMI_ENGINE="$KIYOMI_DIR/engine"
KIYOMI_VENV="$KIYOMI_DIR/venv"
KIYOMI_CONFIG="$KIYOMI_DIR/config.json"
REPO_URL="https://github.com/RichardEchols/kiyomi-lite"
BRANCH="main"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

print_banner() {
  echo ""
  echo -e "${CYAN}${BOLD}"
  echo "  🌸 Kiyomi Bot Installer"
  echo "  The AI That Actually Remembers You"
  echo -e "${NC}"
  echo ""
}

info()  { echo -e "${BLUE}▸${NC} $1"; }
ok()    { echo -e "${GREEN}✓${NC} $1"; }
fail()  { echo -e "${RED}✗${NC} $1"; exit 1; }
ask()   { echo -ne "${CYAN}?${NC} $1: "; read -r REPLY; }

# ─── Preflight ─────────────────────────────────────────────
print_banner

# macOS only
[[ "$(uname)" == "Darwin" ]] || fail "Kiyomi only runs on macOS right now."

# Python 3.10+
if ! command -v python3 &>/dev/null; then
  fail "Python 3 not found. Install it: brew install python3"
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ]]; then
  fail "Python 3.10+ required (found $PY_VERSION). Run: brew install python3"
fi
ok "Python $PY_VERSION"

# Git
if ! command -v git &>/dev/null; then
  info "Installing Xcode Command Line Tools (this may take a few minutes)..."
  xcode-select --install 2>/dev/null || true
  echo "  Press Enter after the install dialog finishes."
  read -r
fi
ok "Git available"

# ─── Install ──────────────────────────────────────────────
info "Installing to $KIYOMI_DIR..."
mkdir -p "$KIYOMI_DIR"

# Clone or update
if [[ -d "$KIYOMI_ENGINE/.git" ]]; then
  info "Updating existing install..."
  cd "$KIYOMI_ENGINE"
  git pull --ff-only origin "$BRANCH" 2>/dev/null || git fetch origin && git reset --hard "origin/$BRANCH"
else
  info "Downloading Kiyomi..."
  rm -rf "$KIYOMI_ENGINE"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$KIYOMI_ENGINE"
fi
ok "Engine downloaded"

# Python venv
info "Setting up Python environment..."
if [[ ! -d "$KIYOMI_VENV" ]]; then
  python3 -m venv "$KIYOMI_VENV"
fi
source "$KIYOMI_VENV/bin/activate"

# Install deps
if [[ -f "$KIYOMI_ENGINE/requirements.txt" ]]; then
  pip install --quiet --upgrade pip
  pip install --quiet -r "$KIYOMI_ENGINE/requirements.txt"
fi
ok "Dependencies installed"

# ─── Setup ────────────────────────────────────────────────
if [[ ! -f "$KIYOMI_CONFIG" ]]; then
  echo ""
  echo -e "${BOLD}Let's set up Kiyomi!${NC}"
  echo ""

  ask "Your name"
  USER_NAME="$REPLY"

  ask "Bot name (default: Kiyomi)"
  BOT_NAME="${REPLY:-Kiyomi}"

  echo ""
  echo "  Which AI provider? (Gemini is free!)"
  echo "  1) Gemini (recommended — free tier)"
  echo "  2) Claude (Anthropic)"
  echo "  3) OpenAI (GPT)"
  echo ""
  ask "Pick 1-3 (default: 1)"
  PROVIDER_CHOICE="${REPLY:-1}"

  case "$PROVIDER_CHOICE" in
    2) PROVIDER="anthropic"; MODEL="claude-sonnet-4-20250514" ;;
    3) PROVIDER="openai";    MODEL="gpt-4o" ;;
    *)  PROVIDER="gemini";    MODEL="gemini-2.0-flash" ;;
  esac

  API_KEY=""
  if [[ "$PROVIDER" == "gemini" ]]; then
    echo ""
    echo "  Get a free Gemini API key: https://aistudio.google.com/apikey"
    ask "Gemini API key"
    API_KEY="$REPLY"
  elif [[ "$PROVIDER" == "anthropic" ]]; then
    ask "Anthropic API key"
    API_KEY="$REPLY"
  elif [[ "$PROVIDER" == "openai" ]]; then
    ask "OpenAI API key"
    API_KEY="$REPLY"
  fi

  echo ""
  echo "  Telegram lets Kiyomi message you on your phone."
  echo "  Create a bot: talk to @BotFather on Telegram → /newbot"
  ask "Telegram bot token (or press Enter to skip)"
  BOT_TOKEN="$REPLY"

  # Write config
  GEMINI_KEY=""; ANTHROPIC_KEY=""; OPENAI_KEY=""
  case "$PROVIDER" in
    gemini)    GEMINI_KEY="$API_KEY" ;;
    anthropic) ANTHROPIC_KEY="$API_KEY" ;;
    openai)    OPENAI_KEY="$API_KEY" ;;
  esac

  cat > "$KIYOMI_CONFIG" << CONF
{
  "name": "$USER_NAME",
  "bot_name": "$BOT_NAME",
  "provider": "$PROVIDER",
  "model": "$MODEL",
  "gemini_key": "$GEMINI_KEY",
  "anthropic_key": "$ANTHROPIC_KEY",
  "openai_key": "$OPENAI_KEY",
  "bot_token": "$BOT_TOKEN",
  "telegram_token": "$BOT_TOKEN",
  "telegram_user_id": "",
  "setup_complete": true,
  "imported_chats": false,
  "timezone": "$(readlink /etc/localtime | sed 's|.*/zoneinfo/||')"
}
CONF
  ok "Config saved to $KIYOMI_CONFIG"
else
  ok "Config already exists"
fi

# ─── Launch script ────────────────────────────────────────
LAUNCH_SCRIPT="$KIYOMI_DIR/start.sh"
cat > "$LAUNCH_SCRIPT" << 'LAUNCH'
#!/bin/bash
source "$HOME/.kiyomi/venv/bin/activate"
cd "$HOME/.kiyomi/engine/engine"
python bot.py
LAUNCH
chmod +x "$LAUNCH_SCRIPT"

# Shell alias
SHELL_RC="$HOME/.zshrc"
[[ "$(basename "$SHELL")" == "bash" ]] && SHELL_RC="$HOME/.bashrc"
if ! grep -q "alias kiyomi=" "$SHELL_RC" 2>/dev/null; then
  echo '' >> "$SHELL_RC"
  echo '# Kiyomi Bot' >> "$SHELL_RC"
  echo 'alias kiyomi="$HOME/.kiyomi/start.sh"' >> "$SHELL_RC"
  ok "Added 'kiyomi' command to your shell"
fi

# ─── Done ─────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}  🌸 Kiyomi is installed!${NC}"
echo ""
echo "  Start Kiyomi:"
echo -e "    ${CYAN}kiyomi${NC}  (open a new terminal first)"
echo ""
echo "  Or run directly:"
echo -e "    ${CYAN}~/.kiyomi/start.sh${NC}"
echo ""
echo "  Config: ~/.kiyomi/config.json"
echo "  Update: curl -fsSL https://kiyomibot.ai/install.sh | bash"
echo ""
echo -e "  ${BOLD}Kiyomi remembers. Always. 🌸${NC}"
echo ""
