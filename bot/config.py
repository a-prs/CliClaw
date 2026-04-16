"""CliClaw configuration — loaded from .env."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (/opt/cliclaw/.env)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)

# Telegram
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

# CLI Backend: claude | codex | qwen
CLI_BACKEND = os.getenv("CLI_BACKEND", "claude")

# Backend binaries
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")
CODEX_BIN = os.getenv("CODEX_BIN", "codex")
QWEN_BIN = os.getenv("QWEN_BIN", "qwen")

# Backend-specific API keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Common CLI settings
CLI_TIMEOUT = int(os.getenv("CLI_TIMEOUT", "600"))

# Working directory for CLI
WORK_DIR = Path(os.getenv("CLI_WORK_DIR", str(PROJECT_ROOT / "workspace")))

# Groq Whisper API (optional, for voice messages)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Database
DB_PATH = PROJECT_ROOT / "data" / "bot.db"

# Memory
MEMORY_ENABLED = os.getenv("MEMORY_ENABLED", "true").lower() == "true"
MEMORY_DIR = WORK_DIR / "memory"
MEMORY_INJECT_LIMIT = int(os.getenv("MEMORY_INJECT_LIMIT", "5"))

# Limits
MESSAGE_QUEUE_MAX = 5
SESSION_IDLE_TIMEOUT_HOURS = 48


def get_backend_bin() -> str:
    """Return the binary path for the configured backend."""
    return {
        "claude": CLAUDE_BIN,
        "codex": CODEX_BIN,
        "qwen": QWEN_BIN,
    }.get(CLI_BACKEND, "claude")


def set_env_var(key: str, value: str):
    """Write or update a variable in .env file and apply to current process."""
    lines = []
    found = False

    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                found = True
                break

    if not found:
        lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(lines) + "\n")
    os.environ[key] = value


def reload_groq_key():
    """Reload GROQ_API_KEY from .env into module-level variable."""
    global GROQ_API_KEY
    load_dotenv(ENV_PATH, override=True)
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
