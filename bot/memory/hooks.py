"""Memory hooks — inject context before session, extract facts after."""

import re
import logging
from datetime import datetime

import config
from memory.search import search, index_note
from memory.vault import read_note, append_note, save_note, list_notes

logger = logging.getLogger("memory.hooks")


def get_memory_context(query: str) -> str | None:
    """Search memory vault, return formatted context string or None.

    Always includes facts.md (core knowledge).
    Uses FTS5 with OR for session log search.
    """
    if not config.MEMORY_ENABLED:
        return None

    context_parts = []

    # 1. Always include facts.md — it's small and contains core user knowledge
    facts = read_note("facts.md")
    if facts and facts.strip():
        context_parts.append(f"[User facts]:\n{facts[:2000].strip()}")

    # 2. Search session logs with OR (not AND) for broader matching
    query_text = re.sub(r'[^\w\s]', ' ', query[:200]).strip()
    if query_text:
        # Convert "какой мой любимый цвет" → "какой OR мой OR любимый OR цвет"
        words = [w for w in query_text.split() if len(w) > 2]
        if words:
            or_query = " OR ".join(words)
            results = search(or_query, limit=config.MEMORY_INJECT_LIMIT)
            for r in results:
                if r.path == "facts.md":
                    continue  # already included above
                content = read_note(r.path)
                if content:
                    snippet = content[:500].strip()
                    context_parts.append(f"[{r.path}]: {snippet}")

    if not context_parts:
        return None

    logger.info(f"Found {len(context_parts)} memory notes for context")
    return "\n".join(context_parts)


async def inject_context(prompt: str) -> str:
    """For CLI backends: prepend memory context to prompt string."""
    context = get_memory_context(prompt)
    if not context:
        return prompt
    return f"[Memory context:]\n{context}\n\n{prompt}"


async def extract_and_save(user_prompt: str, assistant_response: str):
    """Extract facts from conversation and save to memory vault.

    Uses simple heuristics — no extra CLI call (zero cost).
    """
    if not config.MEMORY_ENABLED:
        return

    # Save session log
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    short_prompt = user_prompt[:50].replace("/", "-").replace("\\", "-").strip()
    log_filename = f"sessions/{timestamp}_{_slugify(short_prompt)}.md"

    log_content = f"# {short_prompt}\n\n"
    log_content += f"**User:** {user_prompt[:500]}\n\n"
    log_content += f"**Assistant:** {assistant_response[:1000]}\n"

    save_note(log_filename, log_content)
    index_note(log_filename, log_content)

    # Extract explicit "remember" requests
    remember_patterns = [
        r"запомни[:\s]+(.+?)(?:\.|$)",
        r"remember[:\s]+(.+?)(?:\.|$)",
        r"сохрани[:\s]+(.+?)(?:\.|$)",
    ]

    for pattern in remember_patterns:
        matches = re.findall(pattern, user_prompt, re.IGNORECASE)
        for match in matches:
            fact = match.strip()
            if len(fact) > 10:
                append_note("facts.md", fact)
                full_content = read_note("facts.md") or fact
                index_note("facts.md", full_content)
                logger.info(f"Extracted explicit fact: {fact[:60]}...")

    # Extract schedule entries from assistant response (for API backends)
    _extract_schedules(assistant_response)


def _extract_schedules(response: str):
    """Parse schedule JSON from model response and write to schedules.json.

    API backends can't write files, so we parse their text output.
    """
    import json

    # Find JSON blocks that look like schedule entries
    json_blocks = re.findall(r'\{[^{}]*"cron"[^{}]*\}', response, re.DOTALL)
    if not json_blocks:
        return

    schedules_path = config.WORK_DIR / "schedules.json"

    # Load existing schedules
    existing = []
    if schedules_path.exists():
        try:
            existing = json.loads(schedules_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    added = 0
    for block in json_blocks:
        try:
            entry = json.loads(block)
            # Validate required fields
            if "cron" in entry and ("prompt" in entry or "description" in entry):
                if "id" not in entry:
                    entry["id"] = f"auto-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                if "enabled" not in entry:
                    entry["enabled"] = True
                # Avoid duplicates
                existing_ids = {e.get("id") for e in existing}
                if entry["id"] not in existing_ids:
                    existing.append(entry)
                    added += 1
        except json.JSONDecodeError:
            continue

    if added > 0:
        schedules_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"Auto-saved {added} schedule(s) from model response")


def _slugify(text: str) -> str:
    """Convert text to filename-safe slug."""
    text = re.sub(r'[^\w\s-]', '', text.lower())
    text = re.sub(r'[\s]+', '-', text)
    return text[:40]
