"""CliClaw — Universal AI assistant via Telegram + any CLI backend."""

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
)
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

import config
from config import BOT_TOKEN, ADMIN_CHAT_ID, MESSAGE_QUEUE_MAX
from db import (
    init_db,
    create_session,
    get_session,
    get_active_sessions,
    set_session_done,
    set_session_active,
    save_message,
)
from runner import run_cli, is_busy, queue_length, init_runner, get_backend
from backends import get_backend as create_backend
from formatting import md_to_telegram_html, split_message
from voice import transcribe_voice
from scheduler import run_scheduler, _load_schedules
from memory.vault import vault_stats
from memory.search import init_fts, reindex_vault

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("bot")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Track which session user is focused on
user_focus: dict[int, str] = {}  # chat_id -> session_id

# Track pending setup state
_awaiting_setup: dict[int, str] = {}  # chat_id -> what we're waiting for

SESSIONS_PER_PAGE = 5
SERVICE_NAME = "cliclaw"


# ==================== Security ====================

def is_admin(message: Message) -> bool:
    return message.chat.id == ADMIN_CHAT_ID


def is_admin_cb(callback: CallbackQuery) -> bool:
    return callback.message.chat.id == ADMIN_CHAT_ID


# ==================== Keyboards ====================

def build_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="\ud83d\udccb \u0421\u0435\u0441\u0441\u0438\u0438", callback_data="sessions:0"),
            InlineKeyboardButton(text="\u2795 \u041d\u043e\u0432\u0430\u044f", callback_data="new_session"),
        ],
        [
            InlineKeyboardButton(text="\ud83d\udcca \u0421\u0442\u0430\u0442\u0443\u0441", callback_data="status"),
            InlineKeyboardButton(text="\ud83d\uddd1 \u0417\u0430\u043a\u0440\u044b\u0442\u044c \u0432\u0441\u0435", callback_data="close_all"),
        ],
    ])


def build_sessions_keyboard(sessions: list[dict], page: int = 0, focus_id: str = None) -> InlineKeyboardMarkup:
    total = len(sessions)
    start = page * SESSIONS_PER_PAGE
    end = start + SESSIONS_PER_PAGE
    page_sessions = sessions[start:end]

    buttons = []
    for s in page_sessions:
        icon = {"active": "\u26a1", "idle": "\ud83d\udca4"}.get(s["status"], "\u2753")
        marker = "\ud83d\udc49 " if s["session_id"] == focus_id else ""
        name = s["name"][:28] + ".." if len(s["name"]) > 28 else s["name"]

        buttons.append([
            InlineKeyboardButton(
                text=f"{marker}{icon} {name}",
                callback_data=f'switch:{s["session_id"]}',
            ),
            InlineKeyboardButton(text="\u274c", callback_data=f'close:{s["session_id"]}'),
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="\u2b05 \u041d\u0430\u0437\u0430\u0434", callback_data=f"sessions:{page - 1}"))
    if end < total:
        nav.append(InlineKeyboardButton(text="\u0412\u043f\u0435\u0440\u0451\u0434 \u27a1", callback_data=f"sessions:{page + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="\ud83c\udfe0 \u041c\u0435\u043d\u044e", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ==================== Text extraction ====================

async def extract_text(message: Message) -> tuple[str | None, str | None]:
    """Extract text and optional image path from message."""
    image_path = None

    if message.photo:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        save_path = config.WORK_DIR / f"image_{photo.file_id[-8:]}.jpg"
        config.WORK_DIR.mkdir(parents=True, exist_ok=True)
        await bot.download_file(file.file_path, destination=str(save_path))
        image_path = str(save_path)
        logger.info(f"Photo saved: {image_path}")

    if message.text:
        return message.text, image_path

    if message.voice or message.audio:
        voice_obj = message.voice or message.audio
        transcript = await transcribe_voice(voice_obj, bot)
        if transcript is None:
            return None, None
        return transcript, image_path

    if message.caption:
        return message.caption, image_path

    if image_path:
        return "\u041e\u043f\u0438\u0448\u0438 \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435", image_path

    return None, None


# ==================== Commands ====================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not is_admin(message):
        return

    backend = get_backend()
    backend_name = backend.display_name if backend else "not configured"
    voice_status = "\u2705 Groq" if config.GROQ_API_KEY else "\u274c /setup"

    await message.reply(
        f"\ud83e\udd16 <b>CliClaw</b> \u2014 \u0442\u0432\u043e\u0439 AI-\u0430\u0441\u0441\u0438\u0441\u0442\u0435\u043d\u0442\n\n"
        f"\ud83d\udd27 \u0411\u044d\u043a\u0435\u043d\u0434: {backend_name}\n"
        f"\ud83c\udf99 \u0413\u043e\u043b\u043e\u0441\u043e\u0432\u044b\u0435: {voice_status}\n"
        f"\ud83e\udde0 \u041f\u0430\u043c\u044f\u0442\u044c: {'on' if config.MEMORY_ENABLED else 'off'}\n\n"
        f"\u041f\u0440\u043e\u0441\u0442\u043e \u043d\u0430\u043f\u0438\u0448\u0438 \u043c\u043d\u0435.",
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(),
    )


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    if not is_admin(message):
        return
    await message.reply(
        "\ud83c\udfae <b>\u041f\u0430\u043d\u0435\u043b\u044c \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(),
    )


@dp.message(Command("new"))
async def cmd_new(message: Message):
    if not is_admin(message):
        return
    user_focus[message.chat.id] = "__force_new__"
    await message.reply(
        "\u041e\u0442\u043f\u0440\u0430\u0432\u044c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u2014 \u043e\u043d\u043e \u0441\u0442\u0430\u043d\u0435\u0442 \u043d\u0430\u0447\u0430\u043b\u043e\u043c \u043d\u043e\u0432\u043e\u0439 \u0441\u0435\u0441\u0441\u0438\u0438.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="\u274c \u041e\u0442\u043c\u0435\u043d\u0430", callback_data="cancel_new")],
        ]),
    )


@dp.message(Command("sessions"))
async def cmd_sessions(message: Message):
    if not is_admin(message):
        return
    sessions = get_active_sessions()
    if not sessions:
        await message.reply(
            "\u041d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0445 \u0441\u0435\u0441\u0441\u0438\u0439.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="\u2795 \u041d\u043e\u0432\u0430\u044f", callback_data="new_session")],
                [InlineKeyboardButton(text="\ud83c\udfe0 \u041c\u0435\u043d\u044e", callback_data="menu")],
            ]),
        )
        return

    focus_id = user_focus.get(message.chat.id)
    await message.reply(
        f"<b>\u0421\u0435\u0441\u0441\u0438\u0438</b> ({len(sessions)} \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0445)",
        parse_mode=ParseMode.HTML,
        reply_markup=build_sessions_keyboard(sessions, 0, focus_id),
    )


@dp.message(Command("status"))
async def cmd_status(message: Message):
    if not is_admin(message):
        return
    await _send_status(message.chat.id)


@dp.message(Command("update"))
async def cmd_update(message: Message):
    if not is_admin(message):
        return

    status_msg = await message.reply("\ud83d\udd04 \u041f\u0440\u043e\u0432\u0435\u0440\u044f\u044e \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u044f...")

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "fetch", "origin", "main",
            cwd=str(config.PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        proc = await asyncio.create_subprocess_exec(
            "git", "rev-list", "--count", "HEAD..origin/main",
            cwd=str(config.PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        behind = int(stdout.decode().strip() or "0")

        if behind == 0:
            await status_msg.edit_text("\u2705 \u0423\u0436\u0435 \u0430\u043a\u0442\u0443\u0430\u043b\u044c\u043d\u0430\u044f \u0432\u0435\u0440\u0441\u0438\u044f.")
            return

        await status_msg.edit_text(f"\u2b07 \u041d\u0430\u0439\u0434\u0435\u043d\u043e {behind} \u043a\u043e\u043c\u043c\u0438\u0442(\u043e\u0432). \u041e\u0431\u043d\u043e\u0432\u043b\u044f\u044e...")

        proc = await asyncio.create_subprocess_exec(
            "git", "pull", "origin", "main",
            cwd=str(config.PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error = stderr.decode()[:500]
            await status_msg.edit_text(f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430:\n<pre>{error}</pre>", parse_mode=ParseMode.HTML)
            return

        proc = await asyncio.create_subprocess_exec(
            str(config.PROJECT_ROOT / ".venv" / "bin" / "pip"),
            "install", "-q", "-r", str(config.PROJECT_ROOT / "bot" / "requirements.txt"),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        await status_msg.edit_text(f"\u2705 \u041e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u043e ({behind} \u043a\u043e\u043c\u043c\u0438\u0442\u043e\u0432). \u041f\u0435\u0440\u0435\u0437\u0430\u043f\u0443\u0441\u043a...")

        proc = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", "restart", SERVICE_NAME,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    except Exception as e:
        logger.error(f"Update failed: {e}", exc_info=True)
        try:
            await status_msg.edit_text(f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u044f: {e}")
        except Exception:
            pass


@dp.message(Command("setup"))
async def cmd_setup(message: Message):
    if not is_admin(message):
        return

    buttons = []
    if not config.GROQ_API_KEY:
        buttons.append([InlineKeyboardButton(text="\ud83c\udf99 \u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c Groq \u043a\u043b\u044e\u0447 (\u0433\u043e\u043b\u043e\u0441)", callback_data="setup:groq")])
    else:
        buttons.append([InlineKeyboardButton(text="\ud83d\udd04 \u041e\u0431\u043d\u043e\u0432\u0438\u0442\u044c Groq \u043a\u043b\u044e\u0447", callback_data="setup:groq")])

    buttons.append([InlineKeyboardButton(text="\ud83c\udfe0 \u041c\u0435\u043d\u044e", callback_data="menu")])

    backend = get_backend()
    voice_status = "\u2705" if config.GROQ_API_KEY else "\u274c"
    await message.reply(
        f"\u2699\ufe0f <b>\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438</b>\n\n"
        f"\ud83d\udd27 \u0411\u044d\u043a\u0435\u043d\u0434: {backend.display_name if backend else '?'}\n"
        f"\ud83c\udf99 \u0413\u043e\u043b\u043e\u0441\u043e\u0432\u044b\u0435: {voice_status}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@dp.callback_query(F.data == "setup:groq")
async def cb_setup_groq(callback: CallbackQuery):
    if not is_admin_cb(callback):
        return
    _awaiting_setup[callback.message.chat.id] = "groq_key"
    await callback.message.edit_text(
        "<b>\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430 Groq API</b>\n\n"
        "1. \u041f\u0435\u0440\u0435\u0439\u0434\u0438: https://console.groq.com/keys\n"
        "2. \u0421\u043e\u0437\u0434\u0430\u0439 \u0431\u0435\u0441\u043f\u043b\u0430\u0442\u043d\u044b\u0439 API \u043a\u043b\u044e\u0447\n"
        "3. \u041e\u0442\u043f\u0440\u0430\u0432\u044c \u0435\u0433\u043e \u0441\u044e\u0434\u0430\n\n"
        "\u041a\u043b\u044e\u0447 \u0432\u044b\u0433\u043b\u044f\u0434\u0438\u0442 \u0442\u0430\u043a: <code>gsk_...</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="\u274c \u041e\u0442\u043c\u0435\u043d\u0430", callback_data="setup:cancel")],
        ]),
    )
    await callback.answer()


@dp.callback_query(F.data == "setup:cancel")
async def cb_setup_cancel(callback: CallbackQuery):
    if not is_admin_cb(callback):
        return
    _awaiting_setup.pop(callback.message.chat.id, None)
    await callback.message.edit_text("\u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e.", reply_markup=build_main_menu())
    await callback.answer()


# ==================== Callback handlers ====================

@dp.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery):
    if not is_admin_cb(callback):
        return
    await callback.message.edit_text(
        "\ud83c\udfae <b>\u041f\u0430\u043d\u0435\u043b\u044c \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("sessions:"))
async def cb_sessions(callback: CallbackQuery):
    if not is_admin_cb(callback):
        return
    page = int(callback.data.split(":")[1])
    sessions = get_active_sessions()

    if not sessions:
        await callback.message.edit_text(
            "\u041d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0445 \u0441\u0435\u0441\u0441\u0438\u0439.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="\u2795 \u041d\u043e\u0432\u0430\u044f", callback_data="new_session")],
                [InlineKeyboardButton(text="\ud83c\udfe0 \u041c\u0435\u043d\u044e", callback_data="menu")],
            ]),
        )
        await callback.answer()
        return

    focus_id = user_focus.get(callback.message.chat.id)
    await callback.message.edit_text(
        f"<b>\u0421\u0435\u0441\u0441\u0438\u0438</b> ({len(sessions)} \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0445)",
        parse_mode=ParseMode.HTML,
        reply_markup=build_sessions_keyboard(sessions, page, focus_id),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("switch:"))
async def cb_switch(callback: CallbackQuery):
    if not is_admin_cb(callback):
        return
    session_id = callback.data.split(":", 1)[1]
    session = get_session(session_id)
    if not session:
        await callback.answer("\u0421\u0435\u0441\u0441\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430", show_alert=True)
        return

    user_focus[callback.message.chat.id] = session_id
    created = datetime.fromisoformat(session["created_at"]).strftime("%d.%m %H:%M")
    summary = session.get("summary", "") or "\u041d\u0435\u0442 \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u044f"

    await callback.message.edit_text(
        f"<b>{session['name']}</b>\n\n"
        f"\u0421\u0442\u0430\u0442\u0443\u0441: {session['status']}\n"
        f"\u0421\u043e\u0437\u0434\u0430\u043d\u0430: {created}\n\n"
        f"<i>{summary[:150]}</i>\n\n"
        f"\u041f\u0435\u0440\u0435\u043a\u043b\u044e\u0447\u0435\u043d\u043e. \u041e\u0442\u043f\u0440\u0430\u0432\u044c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="\ud83d\udccb \u0421\u0435\u0441\u0441\u0438\u0438", callback_data="sessions:0")],
            [InlineKeyboardButton(text="\ud83c\udfe0 \u041c\u0435\u043d\u044e", callback_data="menu")],
        ]),
    )
    await callback.answer(f"\u0421\u0435\u0441\u0441\u0438\u044f: {session['name'][:30]}")


@dp.callback_query(F.data.startswith("close:"))
async def cb_close(callback: CallbackQuery):
    if not is_admin_cb(callback):
        return
    session_id = callback.data.split(":", 1)[1]
    session = get_session(session_id)
    if not session:
        await callback.answer("\u0421\u0435\u0441\u0441\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430", show_alert=True)
        return

    set_session_done(session_id)
    if user_focus.get(callback.message.chat.id) == session_id:
        user_focus.pop(callback.message.chat.id, None)

    await callback.answer(f"\u0417\u0430\u043a\u0440\u044b\u0442\u043e: {session['name'][:30]}", show_alert=True)

    sessions = get_active_sessions()
    if sessions:
        focus_id = user_focus.get(callback.message.chat.id)
        await callback.message.edit_text(
            f"<b>\u0421\u0435\u0441\u0441\u0438\u0438</b> ({len(sessions)} \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0445)\n"
            f"\u0417\u0430\u043a\u0440\u044b\u0442\u043e: {session['name']}",
            parse_mode=ParseMode.HTML,
            reply_markup=build_sessions_keyboard(sessions, 0, focus_id),
        )
    else:
        await callback.message.edit_text(
            f"\u0417\u0430\u043a\u0440\u044b\u0442\u043e: {session['name']}\n\u041d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0445 \u0441\u0435\u0441\u0441\u0438\u0439.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="\u2795 \u041d\u043e\u0432\u0430\u044f", callback_data="new_session")],
                [InlineKeyboardButton(text="\ud83c\udfe0 \u041c\u0435\u043d\u044e", callback_data="menu")],
            ]),
        )


@dp.callback_query(F.data == "new_session")
async def cb_new_session(callback: CallbackQuery):
    if not is_admin_cb(callback):
        return
    user_focus[callback.message.chat.id] = "__force_new__"
    await callback.message.edit_text(
        "\u041e\u0442\u043f\u0440\u0430\u0432\u044c \u043f\u0435\u0440\u0432\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u2014 \u043e\u043d\u043e \u0441\u0442\u0430\u043d\u0435\u0442 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435\u043c \u0441\u0435\u0441\u0441\u0438\u0438.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="\u274c \u041e\u0442\u043c\u0435\u043d\u0430", callback_data="cancel_new")],
        ]),
    )
    await callback.answer()


@dp.callback_query(F.data == "cancel_new")
async def cb_cancel_new(callback: CallbackQuery):
    if not is_admin_cb(callback):
        return
    user_focus.pop(callback.message.chat.id, None)
    await callback.message.edit_text("\u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e.", reply_markup=build_main_menu())
    await callback.answer()


@dp.callback_query(F.data == "close_all")
async def cb_close_all(callback: CallbackQuery):
    if not is_admin_cb(callback):
        return
    sessions = get_active_sessions()
    if not sessions:
        await callback.answer("\u041d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0445 \u0441\u0435\u0441\u0441\u0438\u0439", show_alert=True)
        return

    await callback.message.edit_text(
        f"<b>\u0417\u0430\u043a\u0440\u044b\u0442\u044c \u0432\u0441\u0435 {len(sessions)} \u0441\u0435\u0441\u0441\u0438\u0439?</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="\u2705 \u0414\u0430, \u0437\u0430\u043a\u0440\u044b\u0442\u044c", callback_data="confirm_close_all"),
                InlineKeyboardButton(text="\u274c \u041e\u0442\u043c\u0435\u043d\u0430", callback_data="menu"),
            ],
        ]),
    )
    await callback.answer()


@dp.callback_query(F.data == "confirm_close_all")
async def cb_confirm_close_all(callback: CallbackQuery):
    if not is_admin_cb(callback):
        return
    sessions = get_active_sessions()
    for s in sessions:
        set_session_done(s["session_id"])
    user_focus.pop(callback.message.chat.id, None)
    await callback.message.edit_text(
        f"\u0417\u0430\u043a\u0440\u044b\u0442\u043e: {len(sessions)} \u0441\u0435\u0441\u0441\u0438\u0439.",
        reply_markup=build_main_menu(),
    )
    await callback.answer()


@dp.callback_query(F.data == "status")
async def cb_status(callback: CallbackQuery):
    if not is_admin_cb(callback):
        return
    await _send_status(callback.message.chat.id, edit_message=callback.message)
    await callback.answer()


@dp.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer("\u0420\u0430\u0431\u043e\u0442\u0430\u044e...")


# ==================== Main message handler ====================

@dp.message(F.chat.id == ADMIN_CHAT_ID)
async def handle_message(message: Message):
    """Main handler: extract text -> route to session -> run CLI."""

    # 0. Check if we're awaiting setup input
    setup_state = _awaiting_setup.get(message.chat.id)
    if setup_state == "groq_key":
        # Voice/photo/non-text → cancel setup mode, process normally
        if not message.text:
            _awaiting_setup.pop(message.chat.id, None)
        else:
            key = message.text.strip()
            if key.startswith("gsk_") and len(key) > 20:
                config.set_env_var("GROQ_API_KEY", key)
                config.reload_groq_key()
                _awaiting_setup.pop(message.chat.id, None)
                await message.reply(
                    "\u2705 Groq API \u043a\u043b\u044e\u0447 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d. \u0413\u043e\u043b\u043e\u0441\u043e\u0432\u044b\u0435 \u0432\u043a\u043b\u044e\u0447\u0435\u043d\u044b.\n"
                    "\u041e\u0442\u043f\u0440\u0430\u0432\u044c \u0433\u043e\u043b\u043e\u0441\u043e\u0432\u043e\u0435 \u0434\u043b\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438.",
                    reply_markup=build_main_menu(),
                )
            elif key.startswith("/"):
                # User sent a command instead of key — cancel setup
                _awaiting_setup.pop(message.chat.id, None)
                # Don't return — let the message be processed normally below
            else:
                await message.reply(
                    "\u274c \u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u0444\u043e\u0440\u043c\u0430\u0442. \u041a\u043b\u044e\u0447 \u043d\u0430\u0447\u0438\u043d\u0430\u0435\u0442\u0441\u044f \u0441 <code>gsk_</code>\n"
                    "\u041e\u0442\u043f\u0440\u0430\u0432\u044c \u043a\u043b\u044e\u0447 \u0438\u043b\u0438 \u043b\u044e\u0431\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u0434\u043b\u044f \u043e\u0442\u043c\u0435\u043d\u044b.",
                    parse_mode=ParseMode.HTML,
                )
            return

    # 1. Extract text and optional image
    text, image_path = await extract_text(message)

    if text is None and (message.voice or message.audio):
        await message.reply(
            "\ud83c\udf99 \u0413\u043e\u043b\u043e\u0441\u043e\u0432\u044b\u0435 \u0442\u0440\u0435\u0431\u0443\u044e\u0442 Groq API \u043a\u043b\u044e\u0447.\n"
            "\u0417\u0430\u043f\u0443\u0441\u0442\u0438 /setup (\u0431\u0435\u0441\u043f\u043b\u0430\u0442\u043d\u043e, 1 \u043c\u0438\u043d\u0443\u0442\u0430).",
        )
        return

    if not text:
        await message.reply("\u041e\u0442\u043f\u0440\u0430\u0432\u044c \u0442\u0435\u043a\u0441\u0442, \u0433\u043e\u043b\u043e\u0441\u043e\u0432\u043e\u0435 \u0438\u043b\u0438 \u0444\u043e\u0442\u043e.")
        return

    # Show transcribed voice text
    if message.voice or message.audio:
        if text.startswith("["):
            await message.reply(text)
            return
        await message.reply(f"<i>\ud83c\udf99 \u0413\u043e\u043b\u043e\u0441:</i> {md_to_telegram_html(text)}", parse_mode=ParseMode.HTML)

    # Append image reference for vision
    if image_path:
        text = f"{text} @{image_path}"

    # 2. Route to session
    force_new = user_focus.get(message.chat.id) == "__force_new__"

    if force_new:
        user_focus.pop(message.chat.id, None)
        session_id = None
        session_name = text[:50]
    else:
        focus_id = user_focus.get(message.chat.id)
        if focus_id and focus_id != "__force_new__":
            session = get_session(focus_id)
            if session and session["status"] != "done":
                session_id = focus_id
                session_name = session["name"]
            else:
                session_id = None
                session_name = text[:50]
        else:
            session_id = None
            session_name = text[:50]

    save_message("user", text, session_id)

    # 3. Show "working" indicator
    if session_id:
        status_text = f"\ud83d\udd04 \u041f\u0440\u043e\u0434\u043e\u043b\u0436\u0430\u044e: <b>{session_name}</b>"
    else:
        status_text = f"\u2795 \u041d\u043e\u0432\u0430\u044f \u0437\u0430\u0434\u0430\u0447\u0430: <i>{session_name}</i>"

    status_msg = await message.reply(
        status_text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="\u23f3 \u0420\u0430\u0431\u043e\u0442\u0430\u044e...", callback_data="noop")],
        ]),
    )

    if session_id:
        set_session_active(session_id)

    # 4. Run CLI
    async def on_result(result_text: str, returned_session_id: str):
        if returned_session_id:
            user_focus[message.chat.id] = returned_session_id
            if not session_id:
                existing = get_session(returned_session_id)
                if not existing:
                    create_session(returned_session_id, session_name)

        save_message("assistant", result_text or "", returned_session_id)

        # Cleanup downloaded image
        if image_path:
            try:
                Path(image_path).unlink(missing_ok=True)
            except OSError:
                pass

        # Delete "working" status
        try:
            await status_msg.delete()
        except TelegramBadRequest:
            pass

        # Send result — text WITHOUT buttons
        if result_text:
            html = md_to_telegram_html(result_text)
            chunks = split_message(html)

            for i, chunk in enumerate(chunks):
                try:
                    await bot.send_message(ADMIN_CHAT_ID, chunk, parse_mode=ParseMode.HTML)
                except TelegramBadRequest as e:
                    logger.warning(f"HTML parse failed, sending plain text: {e}")
                    await bot.send_message(ADMIN_CHAT_ID, result_text[:4000] if i == 0 else chunk[:4000])
                    break

        # Separate control message with buttons
        await bot.send_message(
            ADMIN_CHAT_ID,
            "\u2022\u2022\u2022",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="\ud83d\udccb \u0421\u0435\u0441\u0441\u0438\u0438", callback_data="sessions:0"),
                    InlineKeyboardButton(text="\u2795 \u041d\u043e\u0432\u0430\u044f", callback_data="new_session"),
                ],
            ]),
        )

    result = await run_cli(
        prompt=text,
        session_id=session_id,
        on_result=on_result,
        queue_max=MESSAGE_QUEUE_MAX,
    )

    if result["status"] == "queued":
        try:
            await status_msg.edit_text(
                f"\u23f3 \u0417\u0430\u043d\u044f\u0442. \u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u0432 \u043e\u0447\u0435\u0440\u0435\u0434\u0438 ({result['position']}/{MESSAGE_QUEUE_MAX}).",
            )
        except TelegramBadRequest:
            pass
    elif result["status"] == "queue_full":
        try:
            await status_msg.edit_text("\u274c \u041e\u0447\u0435\u0440\u0435\u0434\u044c \u043f\u043e\u043b\u043d\u0430. \u0414\u043e\u0436\u0434\u0438\u0441\u044c \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0438\u044f.")
        except TelegramBadRequest:
            pass
    elif result["status"] == "no_backend":
        try:
            await status_msg.edit_text("\u274c \u0411\u044d\u043a\u0435\u043d\u0434 \u043d\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d. \u041f\u0440\u043e\u0432\u0435\u0440\u044c CLI_BACKEND \u0432 .env")
        except TelegramBadRequest:
            pass


# ==================== Helpers ====================

async def _send_status(chat_id: int, edit_message=None):
    sessions = get_active_sessions()
    active = [s for s in sessions if s["status"] == "active"]
    idle = [s for s in sessions if s["status"] == "idle"]

    focus_id = user_focus.get(chat_id)
    focus_session = get_session(focus_id) if focus_id and focus_id != "__force_new__" else None

    backend = get_backend()
    backend_name = backend.display_name if backend else "?"
    voice_status = "\u2705 Groq" if config.GROQ_API_KEY else "\u274c"
    schedules = _load_schedules()
    active_schedules = [s for s in schedules if s.get("enabled", True)]

    mem = vault_stats()

    text = f"<b>\ud83d\udcca \u0421\u0442\u0430\u0442\u0443\u0441</b>\n\n"
    text += f"\ud83d\udd27 \u0411\u044d\u043a\u0435\u043d\u0434: {backend_name}\n"
    text += f"\u26a1 \u0410\u043a\u0442\u0438\u0432\u043d\u044b\u0445: {len(active)}\n"
    text += f"\ud83d\udca4 \u041e\u0436\u0438\u0434\u0430\u044e\u0442: {len(idle)}\n"
    text += f"\ud83d\udce8 \u041e\u0447\u0435\u0440\u0435\u0434\u044c: {queue_length()}\n\n"

    if focus_session:
        text += f"\ud83d\udc49 \u0422\u0435\u043a\u0443\u0449\u0430\u044f: <b>{focus_session['name']}</b>\n"
    else:
        text += f"\ud83d\udc49 \u0422\u0435\u043a\u0443\u0449\u0430\u044f: <i>\u043d\u0435\u0442</i>\n"

    text += f"\ud83c\udf99 \u0413\u043e\u043b\u043e\u0441: {voice_status}\n"
    text += f"\ud83d\udcc5 \u0417\u0430\u0434\u0430\u0447 \u043f\u043e \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044e: {len(active_schedules)}\n"
    text += f"\ud83e\udde0 \u041f\u0430\u043c\u044f\u0442\u044c: {mem['note_count']} \u0437\u0430\u043c\u0435\u0442\u043e\u043a ({mem['total_size_kb']} KB)\n"
    text += f"\u23f3 \u0417\u0430\u043d\u044f\u0442: {'da' if is_busy() else '\u043d\u0435\u0442'}"

    if edit_message:
        await edit_message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=build_main_menu())
    else:
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=build_main_menu())


async def setup_bot_commands():
    commands = [
        BotCommand(command="menu", description="\ud83c\udfae \u041f\u0430\u043d\u0435\u043b\u044c \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f"),
        BotCommand(command="sessions", description="\ud83d\udccb \u0421\u043f\u0438\u0441\u043e\u043a \u0441\u0435\u0441\u0441\u0438\u0439"),
        BotCommand(command="new", description="\u2795 \u041d\u043e\u0432\u0430\u044f \u0441\u0435\u0441\u0441\u0438\u044f"),
        BotCommand(command="status", description="\ud83d\udcca \u0421\u0442\u0430\u0442\u0443\u0441 \u0441\u0438\u0441\u0442\u0435\u043c\u044b"),
        BotCommand(command="setup", description="\u2699\ufe0f \u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438"),
        BotCommand(command="update", description="\ud83d\udd04 \u041e\u0431\u043d\u043e\u0432\u0438\u0442\u044c \u0431\u043e\u0442\u0430"),
    ]
    await bot.set_my_commands(commands)


async def _scheduler_send_result(text: str, description: str):
    """Callback for scheduler — send task result to Telegram."""
    header = f"<b>[\ud83d\udcc5 \u041f\u043e \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044e] {md_to_telegram_html(description)}</b>\n\n"
    html = header + md_to_telegram_html(text)
    chunks = split_message(html)

    for chunk in chunks:
        try:
            await bot.send_message(ADMIN_CHAT_ID, chunk, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            await bot.send_message(ADMIN_CHAT_ID, f"[\ud83d\udcc5] {description}\n\n{text[:3900]}")


async def main():
    init_db()

    # Init memory FTS
    init_fts()
    reindex_vault()

    # Init CLI backend
    backend = create_backend(
        name=config.CLI_BACKEND,
        bin_path=config.get_backend_bin(),
        work_dir=str(config.WORK_DIR),
        timeout=config.CLI_TIMEOUT,
    )
    init_runner(backend)

    await setup_bot_commands()
    logger.info(f"CliClaw starting... Backend: {backend.display_name}")
    logger.info(f"Admin chat ID: {ADMIN_CHAT_ID}")
    logger.info(f"Voice: {'Groq' if config.GROQ_API_KEY else 'disabled'}")
    logger.info(f"Memory: {'enabled' if config.MEMORY_ENABLED else 'disabled'}")

    # Start scheduler in background
    asyncio.create_task(run_scheduler(
        run_qwen_fn=None,
        send_result_fn=_scheduler_send_result,
    ))
    logger.info("Scheduler started")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
