#!/bin/bash

# CliClaw Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/a-prs/CliClaw/main/install.sh -o /tmp/install.sh && sudo bash /tmp/install.sh
# Or with backend: sudo bash /tmp/install.sh --backend=claude

INSTALL_DIR="/opt/cliclaw"
REPO_URL="https://github.com/a-prs/CliClaw.git"
SERVICE_NAME="cliclaw"
NODE_MIN_VERSION=18
SELF_URL="https://raw.githubusercontent.com/a-prs/CliClaw/main/install.sh"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
fail()  { echo -e "${RED}[x]${NC} $1"; exit 1; }

# Check stdin is terminal
if [ ! -t 0 ]; then
    echo ""
    echo "  Run this instead (download first, then execute):"
    echo ""
    echo "    curl -fsSL $SELF_URL -o /tmp/install.sh && sudo bash /tmp/install.sh"
    echo ""
    exit 1
fi

set -eo pipefail

# Parse arguments
CLI_BACKEND=""
for arg in "$@"; do
    case $arg in
        --backend=*) CLI_BACKEND="${arg#*=}" ;;
        --upgrade)
            info "Upgrading CliClaw..."
            git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null
            cd "$INSTALL_DIR" && git pull
            "$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/bot/requirements.txt"
            systemctl restart "$SERVICE_NAME"
            info "Done! Check: systemctl status $SERVICE_NAME"
            exit 0
            ;;
    esac
done

echo ""
echo -e "${BOLD}======================================${NC}"
echo -e "${BOLD}    CliClaw Installer${NC}"
echo -e "${BOLD}    Universal AI Assistant${NC}"
echo -e "${BOLD}======================================${NC}"
echo ""

# --- Check root ---
if [[ $EUID -ne 0 ]]; then
    fail "Run as root: sudo bash $0"
fi

# --- Check if already installed ---
if [[ -d "$INSTALL_DIR/bot" ]]; then
    warn "CliClaw is already installed at $INSTALL_DIR"
    read -p "  Reinstall? (y/N): " reinstall
    if [[ "$reinstall" != "y" && "$reinstall" != "Y" ]]; then
        info "To update: cd $INSTALL_DIR && git pull && systemctl restart $SERVICE_NAME"
        exit 0
    fi
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
fi


# ============================================================
#  Step 0: Choose backend
# ============================================================
if [[ -z "$CLI_BACKEND" ]]; then
    echo ""
    echo -e "${BOLD}  Which AI assistant do you want to use?${NC}"
    echo ""
    echo "    1) Claude Code  (Anthropic — login by link, Max subscription)"
    echo "    2) Gemini CLI   (Google — FREE, just API key, no card)"
    echo "    3) Codex CLI    (OpenAI — login from phone, ChatGPT subscription)"
    echo ""
    while true; do
        read -p "  Your choice [1-3]: " backend_choice
        case $backend_choice in
            1) CLI_BACKEND="claude"; break ;;
            2) CLI_BACKEND="gemini"; break ;;
            3) CLI_BACKEND="codex"; break ;;
            *) warn "Enter 1, 2, or 3" ;;
        esac
    done
fi

case $CLI_BACKEND in
    claude) info "Backend: Claude Code" ;;
    gemini) info "Backend: Gemini CLI" ;;
    codex)  info "Backend: Codex CLI" ;;
    *)      fail "Unknown backend: $CLI_BACKEND. Use: claude, gemini, codex" ;;
esac


# ============================================================
#  Step 1: System dependencies
# ============================================================
info "Installing system packages..."
apt-get update -qq || fail "apt-get update failed"
apt-get install -y python3 python3-venv python3-pip git curl ca-certificates gnupg sudo 2>&1 | tail -3
info "System packages installed"


# ============================================================
#  Step 1.5: Swap (if RAM < 1.5GB)
# ============================================================
TOTAL_RAM=$(free -m | awk '/^Mem:/{print $2}')
if [[ "$TOTAL_RAM" -lt 1500 ]] && [[ ! -f /swapfile ]]; then
    info "Low RAM (${TOTAL_RAM}MB). Creating 2GB swap..."
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    info "Swap enabled (2GB)"
fi


# ============================================================
#  Step 2: Node.js (for CLI tools)
# ============================================================
need_node=false

if command -v node &>/dev/null; then
    NODE_CUR=$(node -v | cut -d. -f1 | tr -d v)
    if [[ "$NODE_CUR" -lt "$NODE_MIN_VERSION" ]]; then
        need_node=true
    else
        info "Node.js $(node -v) OK"
    fi
else
    need_node=true
fi

if [[ "$need_node" == "true" ]]; then
    info "Installing Node.js 20.x..."
    mkdir -p /etc/apt/keyrings
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg 2>/dev/null
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list
    apt-get update -qq || fail "Failed to add NodeSource"
    apt-get install -y nodejs 2>&1 | tail -3 || fail "Failed to install Node.js"
    command -v node &>/dev/null || fail "Node.js not found"
    command -v npm  &>/dev/null || fail "npm not found"
    info "Node.js $(node -v) installed"
fi


# ============================================================
#  Step 3: Install CLI backend
# ============================================================
BACKEND_SPECIFIC_VARS=""

case $CLI_BACKEND in
    claude)
        if ! command -v claude &>/dev/null; then
            info "Installing Claude Code CLI..."
            npm install -g @anthropic-ai/claude-code@latest 2>&1 | tail -3 || fail "Failed to install Claude Code"
            if ! command -v claude &>/dev/null; then
                NPM_BIN=$(npm config get prefix)/bin
                [[ -f "$NPM_BIN/claude" ]] && ln -sf "$NPM_BIN/claude" /usr/local/bin/claude
            fi
        fi
        info "Claude Code CLI ready"
        # Auth happens in Step 11 (OAuth login)
        ;;

    gemini)
        if ! command -v gemini &>/dev/null; then
            info "Installing Gemini CLI..."
            npm install -g @google/gemini-cli@latest 2>&1 | tail -3 || fail "Failed to install Gemini CLI"
            if ! command -v gemini &>/dev/null; then
                NPM_BIN=$(npm config get prefix)/bin
                [[ -f "$NPM_BIN/gemini" ]] && ln -sf "$NPM_BIN/gemini" /usr/local/bin/gemini
            fi
        fi
        info "Gemini CLI ready"

        echo ""
        echo "  Google AI API key (FREE, no credit card)."
        echo "  Get it at: https://aistudio.google.com/apikey"
        echo ""
        read -p "  Gemini API key: " GEMINI_KEY
        BACKEND_SPECIFIC_VARS="GEMINI_API_KEY=$GEMINI_KEY"
        ;;

    codex)
        if ! command -v codex &>/dev/null; then
            info "Installing Codex CLI..."
            npm install -g @openai/codex@latest 2>&1 | tail -3 || fail "Failed to install Codex CLI"
            if ! command -v codex &>/dev/null; then
                NPM_BIN=$(npm config get prefix)/bin
                [[ -f "$NPM_BIN/codex" ]] && ln -sf "$NPM_BIN/codex" /usr/local/bin/codex
            fi
        fi
        info "Codex CLI ready"

        echo ""
        echo "  How do you want to authenticate?"
        echo "    a) Login from phone (easiest — no API key needed)"
        echo "    b) API key (from platform.openai.com)"
        echo "    c) I'll set it up later"
        echo ""
        read -p "  Choice [a/b/c]: " codex_auth
        if [[ "$codex_auth" == "b" || "$codex_auth" == "B" ]]; then
            read -p "  OpenAI API key: " OPENAI_KEY
            BACKEND_SPECIFIC_VARS="OPENAI_API_KEY=$OPENAI_KEY"
        fi
        # Device auth happens in Step 11
        ;;
esac


# ============================================================
#  Step 4: Create system user
# ============================================================
if id -u cliclaw &>/dev/null; then
    info "User 'cliclaw' exists"
else
    info "Creating user 'cliclaw'..."
    useradd -r -d "$INSTALL_DIR" -s /bin/bash cliclaw
fi


# ============================================================
#  Step 5: Clone repository
# ============================================================
info "Downloading CliClaw..."

if [[ -d "$INSTALL_DIR/.git" ]]; then
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null
    cd "$INSTALL_DIR" && git pull
    info "Repository updated"
else
    if [[ -d "$INSTALL_DIR" ]]; then
        [[ -f "$INSTALL_DIR/.env" ]] && cp "$INSTALL_DIR/.env" /tmp/_cliclaw_env_backup
        rm -rf "$INSTALL_DIR"
    fi
    git clone "$REPO_URL" "$INSTALL_DIR" || fail "Failed to clone: $REPO_URL"
    [[ -f /tmp/_cliclaw_env_backup ]] && mv /tmp/_cliclaw_env_backup "$INSTALL_DIR/.env"
    info "Repository cloned"
fi


# ============================================================
#  Step 6: Python venv + dependencies
# ============================================================
info "Creating Python environment..."
python3 -m venv "$INSTALL_DIR/.venv" || fail "Failed to create venv"

info "Upgrading pip..."
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip 2>&1 | tail -1

info "Installing Python dependencies (1-2 minutes)..."
"$INSTALL_DIR/.venv/bin/pip" install \
    --progress-bar on \
    -r "$INSTALL_DIR/bot/requirements.txt" 2>&1 \
    | while IFS= read -r line; do
        if [[ "$line" == *"Collecting"* ]] || [[ "$line" == *"Successfully"* ]]; then
            echo -e "  ${GREEN}>>>${NC} $line"
        elif [[ "$line" == *"ERROR"* ]]; then
            echo -e "  ${RED}!!!${NC} $line"
        fi
    done

if ! "$INSTALL_DIR/.venv/bin/python" -c "import aiogram" 2>/dev/null; then
    warn "Retrying pip install..."
    "$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/bot/requirements.txt" || fail "pip install failed"
fi
info "Python environment ready"


# ============================================================
#  Step 7: Directories
# ============================================================
mkdir -p "$INSTALL_DIR/workspace/memory"
mkdir -p "$INSTALL_DIR/data"


# ============================================================
#  Step 8: Configuration (.env)
# ============================================================
if [[ -f "$INSTALL_DIR/.env" ]]; then
    info "Config .env exists, keeping it"
else
    echo ""
    echo -e "${BOLD}======================================${NC}"
    echo -e "${BOLD}    Configuration${NC}"
    echo -e "${BOLD}======================================${NC}"
    echo ""

    echo "  Step 1: Telegram Bot Token"
    echo "  Get it from @BotFather in Telegram"
    echo ""
    while true; do
        read -p "  Bot token: " BOT_TOKEN
        if [[ "$BOT_TOKEN" =~ ^[0-9]+:.+$ ]]; then break; fi
        warn "  Invalid format. Example: 123456:ABC-DEF..."
    done

    echo ""
    echo "  Step 2: Your Telegram Chat ID"
    echo "  Get it from @userinfobot in Telegram"
    echo ""
    while true; do
        read -p "  Chat ID: " CHAT_ID
        if [[ "$CHAT_ID" =~ ^[0-9]+$ ]]; then break; fi
        warn "  Should be a number"
    done

    echo ""
    echo "  Step 3 (optional): Groq API key for voice messages"
    echo "  Free key: https://console.groq.com/keys"
    echo "  Press Enter to skip"
    echo ""
    read -p "  Groq API key: " GROQ_KEY

    cat > "$INSTALL_DIR/.env" << ENVEOF
CLI_BACKEND=$CLI_BACKEND
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
TELEGRAM_CHAT_ID=$CHAT_ID
GROQ_API_KEY=$GROQ_KEY
$BACKEND_SPECIFIC_VARS
ENVEOF

    chmod 600 "$INSTALL_DIR/.env"
    info "Config saved"
fi


# ============================================================
#  Step 9: Copy IDENTITY.md for the backend
# ============================================================
IDENTITY_SRC="$INSTALL_DIR/workspace/IDENTITY.md"
case $CLI_BACKEND in
    claude) cp "$IDENTITY_SRC" "$INSTALL_DIR/workspace/CLAUDE.md" 2>/dev/null ;;
    gemini) cp "$IDENTITY_SRC" "$INSTALL_DIR/workspace/GEMINI.md" 2>/dev/null ;;
esac


# ============================================================
#  Step 10: Permissions
# ============================================================
chown -R cliclaw:cliclaw "$INSTALL_DIR"
echo "cliclaw ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/cliclaw
chmod 440 /etc/sudoers.d/cliclaw
info "Permissions configured"


# ============================================================
#  Step 11: Backend-specific auth
# ============================================================
case $CLI_BACKEND in
    claude)
        echo ""
        echo -e "${BOLD}======================================${NC}"
        echo -e "${BOLD}    Claude Code Authorization${NC}"
        echo -e "${BOLD}======================================${NC}"
        echo ""
        echo "  A link will appear — open it in your browser."
        echo "  Log in with your Anthropic account (Max subscription)."
        echo "  Click 'Authorize' — and you're done."
        echo ""
        read -p "  Press Enter to start..."

        echo ""
        sudo -u cliclaw HOME="$INSTALL_DIR" BROWSER=echo \
            PATH="/usr/local/bin:/usr/bin:/bin:$PATH" \
            timeout 120 claude /login || true

        echo ""
        read -p "  Authorization OK? (y/n): " claude_login_ok
        if [[ "$claude_login_ok" != "y" && "$claude_login_ok" != "Y" ]]; then
            warn "You can login later: sudo -u cliclaw claude /login"
        else
            info "Claude Code: authorized"
        fi
        ;;

    gemini)
        if [[ -z "$GEMINI_KEY" ]]; then
            warn "No API key set. Add GEMINI_API_KEY to $INSTALL_DIR/.env later"
        else
            info "Gemini CLI: API key configured"
        fi
        ;;

    codex)
        if [[ "$codex_auth" == "a" || "$codex_auth" == "A" ]]; then
            echo ""
            echo -e "${BOLD}======================================${NC}"
            echo -e "${BOLD}    Codex Login (from your phone)${NC}"
            echo -e "${BOLD}======================================${NC}"
            echo ""
            echo "  A code and a link will appear below."
            echo "  Open the link on your phone, enter the code."
            echo "  That's it — no API key needed."
            echo ""
            read -p "  Press Enter to start..."

            echo ""
            sudo -u cliclaw HOME="$INSTALL_DIR" \
                PATH="/usr/local/bin:/usr/bin:/bin:$PATH" \
                timeout 120 codex login --device-auth || true

            echo ""
            read -p "  Login OK? (y/n): " codex_login_ok
            if [[ "$codex_login_ok" != "y" && "$codex_login_ok" != "Y" ]]; then
                warn "You can login later: sudo -u cliclaw codex login --device-auth"
            fi
        elif [[ -z "$OPENAI_KEY" ]]; then
            warn "No API key set. Add OPENAI_API_KEY to $INSTALL_DIR/.env later"
        else
            info "Codex CLI: API key configured"
        fi
        ;;
esac


# ============================================================
#  Step 12: systemd service
# ============================================================
info "Setting up systemd service..."
cp "$INSTALL_DIR/cliclaw.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" -q
systemctl start "$SERVICE_NAME"


# ============================================================
#  Step 13: Verify
# ============================================================
sleep 3

echo ""
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo -e "${BOLD}======================================${NC}"
    echo -e "    ${GREEN}CliClaw is running!${NC}"
    echo -e "${BOLD}======================================${NC}"
    echo ""
    echo "  Backend: $CLI_BACKEND"
    echo "  Send a message to your bot in Telegram."
    echo ""
    echo "  Commands:"
    echo "    systemctl status $SERVICE_NAME"
    echo "    journalctl -u $SERVICE_NAME -f"
    echo ""
    echo "  Update from Telegram: /update"
    if [[ -z "$GROQ_KEY" ]]; then
        echo "  Voice: disabled (add via /setup in Telegram)"
    fi
    echo ""
else
    warn "Service failed to start. Logs:"
    echo ""
    journalctl -u "$SERVICE_NAME" --no-pager -n 15
fi

rm -f /tmp/cliclaw-install.sh 2>/dev/null
