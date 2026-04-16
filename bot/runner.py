"""Generic CLI runner — backend-agnostic with in-memory message queue."""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Callable

from backends.base import CLIBackend, CLIResult

logger = logging.getLogger("runner")

# In-memory state
_is_busy = False
_message_queue: list[dict] = []
_backend: Optional[CLIBackend] = None


def init_runner(backend: CLIBackend):
    """Set the active backend. Called once at startup."""
    global _backend
    _backend = backend
    logger.info(f"Runner initialized with backend: {backend.display_name}")


def is_busy() -> bool:
    return _is_busy


def queue_length() -> int:
    return len(_message_queue)


def get_backend() -> Optional[CLIBackend]:
    return _backend


async def run_cli(
    prompt: str,
    session_id: Optional[str] = None,
    on_result: Optional[Callable] = None,
    queue_max: int = 5,
) -> dict:
    """Run CLI backend. If busy, queue the message.

    Returns:
        {"status": "started"} — task launched
        {"status": "queued", "position": N} — added to queue
        {"status": "queue_full"} — rejected
        {"status": "no_backend"} — backend not configured
    """
    global _is_busy

    if not _backend:
        return {"status": "no_backend"}

    if _is_busy:
        if len(_message_queue) >= queue_max:
            return {"status": "queue_full"}
        _message_queue.append({"text": prompt, "session_id": session_id, "callback": on_result})
        return {"status": "queued", "position": len(_message_queue)}

    _is_busy = True
    asyncio.create_task(_process_prompt(prompt, session_id, on_result))
    return {"status": "started"}


async def _process_prompt(
    prompt: str,
    session_id: Optional[str],
    on_result: Optional[Callable],
):
    """Execute backend and drain the queue."""
    global _is_busy

    try:
        # Inject memory context
        augmented_prompt = await _inject_memory(prompt)

        result = await _backend.execute(augmented_prompt, session_id)

        new_session_id = result.session_id if result else session_id
        result_text = result.text if result else ""

        if on_result:
            await on_result(result_text, new_session_id)

        # Extract facts to memory (non-blocking)
        if result_text:
            asyncio.create_task(_extract_memory(prompt, result_text))

        # Drain queued messages
        while _message_queue:
            queued = _message_queue.pop(0)
            sid = new_session_id or queued.get("session_id")

            q_augmented = await _inject_memory(queued["text"])
            qr = await _backend.execute(q_augmented, sid)
            q_text = qr.text if qr else ""
            q_sid = qr.session_id if qr else sid
            new_session_id = q_sid

            if qr and qr.text:
                asyncio.create_task(_extract_memory(queued["text"], qr.text))

            cb = queued.get("callback")
            if cb:
                await cb(q_text, q_sid)

    except Exception as e:
        logger.error(f"Error in _process_prompt: {e}", exc_info=True)
        if on_result:
            await on_result(f"Error: {e}", session_id)
    finally:
        _is_busy = False


async def execute_direct(prompt: str, session_id: Optional[str] = None) -> Optional[CLIResult]:
    """Direct execution without queue. Used by scheduler."""
    if not _backend:
        return None
    return await _backend.execute(prompt, session_id)


async def _inject_memory(prompt: str) -> str:
    """Search memory vault and prepend relevant context to prompt."""
    try:
        from memory.hooks import inject_context
        return await inject_context(prompt)
    except ImportError:
        return prompt
    except Exception as e:
        logger.warning(f"Memory inject failed: {e}")
        return prompt


async def _extract_memory(user_prompt: str, assistant_response: str):
    """Extract facts from conversation and save to memory vault."""
    try:
        from memory.hooks import extract_and_save
        await extract_and_save(user_prompt, assistant_response)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Memory extract failed: {e}")
