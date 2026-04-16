# CliClaw

Universal AI assistant in Telegram. Choose your backend: **Claude Code**, **Codex CLI**, or **Qwen Code**.

## Features

- **Multi-backend**: Claude Code, Codex CLI, Qwen Code — choose at install
- **Voice messages**: Groq Whisper API (free)
- **Session management**: create, switch, close — inline buttons
- **Memory vault**: auto-saves facts from conversations, injects context from past sessions
- **Scheduled tasks**: natural language cron ("remind me tomorrow at 14:00")
- **Self-update**: /update command from Telegram
- **Image support**: send photos for vision analysis
- **One-command install** on Ubuntu/Debian VPS

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/a-prs/CliClaw/main/install.sh -o /tmp/install.sh && sudo bash /tmp/install.sh
```

Or with backend pre-selected:
```bash
sudo bash /tmp/install.sh --backend=claude
```

## Backends

| Backend | Auth | Cost |
|---|---|---|
| Claude Code | `ANTHROPIC_API_KEY` | Paid (Anthropic API) |
| Codex CLI | `OPENAI_API_KEY` | Paid (OpenAI API) |
| Qwen Code | OAuth (browser) | Free (1000 req/day) |

## Commands

- `/menu` — control panel
- `/new` — new session
- `/sessions` — session list
- `/status` — system status (backend, memory, voice)
- `/setup` — configure voice, API keys
- `/update` — update bot from GitHub

## Memory

The bot automatically:
- Saves session logs to `workspace/memory/sessions/`
- Extracts explicit "remember" requests to `workspace/memory/facts.md`
- Searches memory before each prompt and injects relevant context

FTS5 full-text search — finds facts in <1ms.

## Server Requirements

- 1 vCPU, 512MB+ RAM (swap auto-created if <1.5GB)
- Ubuntu 22.04+ / Debian 11+
- No GPU needed

## Architecture

```
bot/
  main.py          — Telegram bot (aiogram 3.x)
  runner.py        — Generic CLI runner with queue
  backends/        — Claude, Codex, Qwen strategies
  memory/          — Vault (markdown files) + FTS5 search + hooks
  voice.py         — Groq Whisper API
  formatting.py    — Markdown → Telegram HTML
  scheduler.py     — Cron-like task scheduler
```

## License

MIT
