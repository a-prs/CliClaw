#!/bin/bash

# CliClaw Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/a-prs/CliClaw/main/install.sh -o /tmp/install.sh && sudo bash /tmp/install.sh
# Flags: --backend=claude|gemini|codex    Pre-select backend
#        --reconfigure                    Change backend/tokens without reinstalling
#        --upgrade                        Update code only

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
    echo "  Run this instead:"
    echo "    curl -fsSL $SELF_URL -o /tmp/install.sh && sudo bash /tmp/install.sh"
    echo ""
    exit 1
fi

set -eo pipefail

# Parse arguments
CLI_BACKEND=""
MODE="install"  # install | reconfigure | upgrade
for arg in "$@"; do
    case $arg in
        --backend=*) CLI_BACKEND="${arg#*=}" ;;
        --reconfigure) MODE="reconfigure" ;;
        --upgrade) MODE="upgrade" ;;
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

# ============================================================
#  Functions (must be defined before use)
# ============================================================

_install_backend_cli() {
    # Install the CLI binary for selected backend + ensure symlink
    case $CLI_BACKEND in
        claude)
            if ! command -v claude &>/dev/null; then
                info "Installing Claude Code CLI..."
                npm install -g @anthropic-ai/claude-code@latest 2>&1 | tail -3 || fail "Failed to install Claude Code"
            fi
            # Ensure symlink
            CLAUDE_PATH=$(command -v claude 2>/dev/null)
            if [[ -n "$CLAUDE_PATH" && "$CLAUDE_PATH" != "/usr/local/bin/claude" ]]; then
                ln -sf "$CLAUDE_PATH" /usr/local/bin/claude
            elif [[ -z "$CLAUDE_PATH" ]]; then
                NPM_BIN=$(npm config get prefix)/bin
                [[ -f "$NPM_BIN/claude" ]] && ln -sf "$NPM_BIN/claude" /usr/local/bin/claude || fail "Claude CLI not found after install"
            fi
            info "Claude Code CLI ready"
            ;;
        gemini)
            if ! command -v gemini &>/dev/null; then
                info "Installing Gemini CLI..."
                npm install -g @google/gemini-cli@latest 2>&1 | tail -3 || fail "Failed to install Gemini CLI"
            fi
            GEMINI_PATH=$(command -v gemini 2>/dev/null)
            if [[ -n "$GEMINI_PATH" && "$GEMINI_PATH" != "/usr/local/bin/gemini" ]]; then
                ln -sf "$GEMINI_PATH" /usr/local/bin/gemini
            elif [[ -z "$GEMINI_PATH" ]]; then
                NPM_BIN=$(npm config get prefix)/bin
                [[ -f "$NPM_BIN/gemini" ]] && ln -sf "$NPM_BIN/gemini" /usr/local/bin/gemini || fail "Gemini CLI not found after install"
            fi
            info "Gemini CLI ready"
            ;;
        codex)
            if ! command -v codex &>/dev/null; then
                info "Installing Codex CLI..."
                npm install -g @openai/codex@latest 2>&1 | tail -3 || fail "Failed to install Codex CLI"
            fi
            CODEX_PATH=$(command -v codex 2>/dev/null)
            if [[ -n "$CODEX_PATH" && "$CODEX_PATH" != "/usr/local/bin/codex" ]]; then
                ln -sf "$CODEX_PATH" /usr/local/bin/codex
            elif [[ -z "$CODEX_PATH" ]]; then
                NPM_BIN=$(npm config get prefix)/bin
                [[ -f "$NPM_BIN/codex" ]] && ln -sf "$NPM_BIN/codex" /usr/local/bin/codex || fail "Codex CLI not found after install"
            fi
            info "Codex CLI ready"
            ;;
    esac
}

_do_reconfigure() {
    _choose_backend
    _install_backend_cli
    _configure_tokens
    _write_env
    _copy_identity
    _run_auth
    # Mark old sessions as done (they belong to previous backend)
    if [[ -f "$INSTALL_DIR/data/bot.db" ]]; then
        "$INSTALL_DIR/.venv/bin/python" -c "
import sqlite3
conn = sqlite3.connect('$INSTALL_DIR/data/bot.db')
conn.execute(\"UPDATE sessions SET status='done' WHERE status != 'done'\")
conn.commit(); conn.close()
print('Old sessions closed')
" 2>/dev/null || true
    fi
    chown -R cliclaw:cliclaw "$INSTALL_DIR"
    systemctl restart "$SERVICE_NAME" 2>/dev/null || true
    info "Reconfigured! Backend: $CLI_BACKEND"
}

_choose_backend() {
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
    info "Backend: $CLI_BACKEND"
}

_configure_tokens() {
    BACKEND_SPECIFIC_VARS=""

    # Telegram tokens (re-use existing if available)
    if [[ -f "$INSTALL_DIR/.env" ]]; then
        BOT_TOKEN=$(grep "^TELEGRAM_BOT_TOKEN=" "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2-)
        CHAT_ID=$(grep "^TELEGRAM_CHAT_ID=" "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2-)
        GROQ_KEY=$(grep "^GROQ_API_KEY=" "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2-)
    fi

    echo ""
    echo -e "${BOLD}======================================${NC}"
    echo -e "${BOLD}    Configuration${NC}"
    echo -e "${BOLD}======================================${NC}"
    echo ""

    # Telegram Bot Token
    if [[ -n "$BOT_TOKEN" ]]; then
        echo "  Telegram Bot Token: ...${BOT_TOKEN: -6} (existing)"
        read -p "  Change? (Enter to keep / paste new): " new_token
        [[ -n "$new_token" ]] && BOT_TOKEN="$new_token"
    else
        echo "  Step 1: Telegram Bot Token"
        echo "  Get it from @BotFather in Telegram"
        echo ""
        while true; do
            read -p "  Bot token: " BOT_TOKEN
            if [[ "$BOT_TOKEN" =~ ^[0-9]+:.+$ ]]; then break; fi
            warn "  Invalid format. Example: 123456:ABC-DEF..."
        done
    fi

    echo ""

    # Chat ID
    if [[ -n "$CHAT_ID" ]]; then
        echo "  Chat ID: $CHAT_ID (existing)"
        read -p "  Change? (Enter to keep / paste new): " new_id
        [[ -n "$new_id" ]] && CHAT_ID="$new_id"
    else
        echo "  Step 2: Your Telegram Chat ID"
        echo "  Get it from @userinfobot in Telegram"
        echo ""
        while true; do
            read -p "  Chat ID: " CHAT_ID
            if [[ "$CHAT_ID" =~ ^[0-9]+$ ]]; then break; fi
            warn "  Should be a number"
        done
    fi

    echo ""

    # Groq (optional)
    if [[ -n "$GROQ_KEY" ]]; then
        echo "  Groq API key: ...${GROQ_KEY: -6} (existing)"
        read -p "  Change? (Enter to keep / paste new): " new_groq
        [[ -n "$new_groq" ]] && GROQ_KEY="$new_groq"
    else
        echo "  Step 3 (optional): Groq API key for voice messages"
        echo "  Free key: https://console.groq.com/keys"
        echo "  Press Enter to skip"
        echo ""
        read -p "  Groq API key: " GROQ_KEY
    fi

    echo ""

    # Backend-specific auth
    case $CLI_BACKEND in
        gemini)
            echo "  Gemini API key (FREE, no credit card)"
            echo "  Get it at: https://aistudio.google.com/apikey"
            echo "  (Create key in NEW project if previous was blocked)"
            echo ""
            GEMINI_KEY=$(grep "^GEMINI_API_KEY=" "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2-)
            if [[ -n "$GEMINI_KEY" ]]; then
                echo "  Current: ...${GEMINI_KEY: -6}"
                read -p "  Change? (Enter to keep / paste new): " new_gemini
                [[ -n "$new_gemini" ]] && GEMINI_KEY="$new_gemini"
            else
                read -p "  Gemini API key: " GEMINI_KEY
            fi
            BACKEND_SPECIFIC_VARS="GEMINI_API_KEY=$GEMINI_KEY"
            ;;
        codex)
            echo "  Codex auth: login from phone or API key"
            echo ""
            echo "    a) Login from phone (easiest)"
            echo "    b) API key (from platform.openai.com)"
            echo "    c) Skip (set up later)"
            echo ""
            read -p "  Choice [a/b/c]: " codex_auth
            if [[ "$codex_auth" == "b" || "$codex_auth" == "B" ]]; then
                read -p "  OpenAI API key: " OPENAI_KEY
                BACKEND_SPECIFIC_VARS="OPENAI_API_KEY=$OPENAI_KEY"
            fi
            ;;
        claude)
            # OAuth — no key needed, auth happens separately
            ;;
    esac
}

_write_env() {
    cat > "$INSTALL_DIR/.env" << ENVEOF
CLI_BACKEND=$CLI_BACKEND
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
TELEGRAM_CHAT_ID=$CHAT_ID
GROQ_API_KEY=$GROQ_KEY
$BACKEND_SPECIFIC_VARS
ENVEOF
    chmod 600 "$INSTALL_DIR/.env"
    chown cliclaw:cliclaw "$INSTALL_DIR/.env"
    info "Config saved to $INSTALL_DIR/.env"
}

_copy_identity() {
    IDENTITY_SRC="$INSTALL_DIR/workspace/IDENTITY.md"
    # Remove old identity files
    rm -f "$INSTALL_DIR/workspace/CLAUDE.md" "$INSTALL_DIR/workspace/GEMINI.md" "$INSTALL_DIR/workspace/QWEN.md" 2>/dev/null
    # Copy for current backend
    case $CLI_BACKEND in
        claude) cp "$IDENTITY_SRC" "$INSTALL_DIR/workspace/CLAUDE.md" 2>/dev/null ;;
        gemini) cp "$IDENTITY_SRC" "$INSTALL_DIR/workspace/GEMINI.md" 2>/dev/null ;;
    esac
}

_run_auth() {
    case $CLI_BACKEND in
        claude)
            echo ""
            echo -e "${BOLD}======================================${NC}"
            echo -e "${BOLD}    Claude Code Authorization${NC}"
            echo -e "${BOLD}======================================${NC}"
            echo ""
            echo "  A link will appear — open it in your browser."
            echo "  Log in with your Anthropic account (Max plan)."
            echo "  Click 'Authorize' and come back."
            echo ""
            read -p "  Press Enter to start..."

            echo ""
            sudo -u cliclaw HOME="$INSTALL_DIR" BROWSER=echo \
                PATH="/usr/local/bin:/usr/bin:/bin:$PATH" \
                timeout 120 claude /login || true

            echo ""
            read -p "  Authorization OK? (y/n): " claude_ok
            if [[ "$claude_ok" != "y" && "$claude_ok" != "Y" ]]; then
                warn "Login later: sudo -u cliclaw claude /login"
            else
                info "Claude Code: authorized"
            fi
            ;;

        codex)
            if [[ "$codex_auth" == "a" || "$codex_auth" == "A" ]]; then
                echo ""
                echo -e "${BOLD}======================================${NC}"
                echo -e "${BOLD}    Codex Login (from your phone)${NC}"
                echo -e "${BOLD}======================================${NC}"
                echo ""
                echo "  A code and link will appear."
                echo "  Open the link on your phone, enter the code."
                echo ""
                read -p "  Press Enter to start..."

                echo ""
                sudo -u cliclaw HOME="$INSTALL_DIR" \
                    PATH="/usr/local/bin:/usr/bin:/bin:$PATH" \
                    timeout 120 codex login --device-auth || true

                echo ""
                read -p "  Login OK? (y/n): " codex_ok
                if [[ "$codex_ok" != "y" && "$codex_ok" != "Y" ]]; then
                    warn "Login later: sudo -u cliclaw codex login --device-auth"
                fi
            fi
            ;;

        gemini)
            if [[ -n "$GEMINI_KEY" ]]; then
                info "Gemini: API key configured"
            else
                warn "Add GEMINI_API_KEY to $INSTALL_DIR/.env"
            fi
            ;;
    esac
}

# ============================================================
#  MODE: --upgrade
# ============================================================
if [[ "$MODE" == "upgrade" ]]; then
    [[ -d "$INSTALL_DIR/bot" ]] || fail "CliClaw not installed."
    info "Upgrading CliClaw..."
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null
    cd "$INSTALL_DIR" && git pull
    "$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/bot/requirements.txt"
    systemctl restart "$SERVICE_NAME"
    info "Done!"
    exit 0
fi

# ============================================================
#  MODE: --reconfigure
# ============================================================
if [[ "$MODE" == "reconfigure" ]]; then
    [[ -d "$INSTALL_DIR/bot" ]] || fail "CliClaw not installed."
    _do_reconfigure
    exit 0
fi

# ============================================================
#  MODE: install
# ============================================================

# --- Already installed? ---
if [[ -d "$INSTALL_DIR/bot" ]]; then
    warn "CliClaw is already installed at $INSTALL_DIR"
    echo ""
    echo "  Options:"
    echo "    r) Reconfigure (change backend/tokens, keep everything else)"
    echo "    f) Full reinstall (reinstall environment)"
    echo "    q) Quit"
    echo ""
    read -p "  Choice [r/f/q]: " install_choice
    case $install_choice in
        r|R)
            _do_reconfigure
            exit 0
            ;;
        f|F)
            systemctl stop "$SERVICE_NAME" 2>/dev/null || true
            info "Full reinstall..."
            ;;
        *)
            exit 0
            ;;
    esac
fi


# ============================================================
#  Phase 1: Environment (no user input needed)
# ============================================================
info "=== Phase 1: Setting up environment ==="

# Step 1: System packages
info "Installing system packages..."
apt-get update -qq || fail "apt-get update failed"
apt-get install -y python3 python3-venv python3-pip git curl ca-certificates gnupg sudo 2>&1 | tail -3
info "System packages installed"

# Step 2: Swap
TOTAL_RAM=$(free -m | awk '/^Mem:/{print $2}')
if [[ "$TOTAL_RAM" -lt 1500 ]] && [[ ! -f /swapfile ]]; then
    info "Low RAM (${TOTAL_RAM}MB). Creating 2GB swap..."
    fallocate -l 2G /swapfile && chmod 600 /swapfile
    mkswap /swapfile >/dev/null && swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    info "Swap enabled"
fi

# Step 3: Node.js
need_node=false
if command -v node &>/dev/null; then
    NODE_CUR=$(node -v | cut -d. -f1 | tr -d v)
    [[ "$NODE_CUR" -lt "$NODE_MIN_VERSION" ]] && need_node=true || info "Node.js $(node -v) OK"
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
    info "Node.js $(node -v) installed"
fi

# Step 4: System user
if id -u cliclaw &>/dev/null; then
    info "User 'cliclaw' exists"
else
    useradd -r -d "$INSTALL_DIR" -s /bin/bash cliclaw
    info "User 'cliclaw' created"
fi

# Step 5: Clone/update repo
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

# Step 6: Python venv
info "Setting up Python environment..."
python3 -m venv "$INSTALL_DIR/.venv" || fail "Failed to create venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip 2>&1 | tail -1
info "Installing Python dependencies (1-2 min)..."
"$INSTALL_DIR/.venv/bin/pip" install --progress-bar on \
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

# Step 7: Directories
mkdir -p "$INSTALL_DIR/workspace/memory"
mkdir -p "$INSTALL_DIR/data"

# Step 8: Permissions
chown -R cliclaw:cliclaw "$INSTALL_DIR"
echo "cliclaw ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/cliclaw
chmod 440 /etc/sudoers.d/cliclaw
info "Permissions configured"

info "=== Phase 1 complete! ==="
echo ""


# ============================================================
#  Phase 2: Backend selection + installation
# ============================================================
info "=== Phase 2: Backend ==="

_choose_backend
_install_backend_cli

info "=== Phase 2 complete! ==="
echo ""


# ============================================================
#  Phase 3: Tokens and auth (all user input here)
# ============================================================
info "=== Phase 3: Configuration ==="

_configure_tokens
_write_env
_copy_identity
_run_auth

info "=== Phase 3 complete! ==="
echo ""


# ============================================================
#  Phase 4: Start service
# ============================================================
info "Setting up systemd service..."
cp "$INSTALL_DIR/cliclaw.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" -q
systemctl start "$SERVICE_NAME"

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
    echo "  From Telegram: /update, /setup, /status"
    echo ""
    echo "  Reconfigure: sudo bash /tmp/install.sh --reconfigure"
    echo ""
else
    warn "Service failed to start. Logs:"
    echo ""
    journalctl -u "$SERVICE_NAME" --no-pager -n 15
fi

rm -f /tmp/cliclaw-install.sh 2>/dev/null
