"""Memory hooks — inject context before session, extract facts after."""

import re
import logging
from datetime import datetime

import config
from memory.search import search, index_note
from memory.vault import read_note, append_note, save_note, list_notes

logger = logging.getLogger("memory.hooks")


async def inject_context(prompt: str) -> str:
    """Search memory vault and prepend relevant context to prompt."""
    if not config.MEMORY_ENABLED:
        return prompt

    # Extract keywords from prompt (simple: first 200 chars, stripped of punctuation)
    query_text = re.sub(r'[^\w\s]', ' ', prompt[:200]).strip()
    if not query_text:
        return prompt

    results = search(query_text, limit=config.MEMORY_INJECT_LIMIT)
    if not results:
        return prompt

    # Read content of top results
    context_parts = []
    for r in results:
        content = read_note(r.path)
        if content:
            # Take first 500 chars of each note
            snippet = content[:500].strip()
            context_parts.append(f"[{r.path}]: {snippet}")

    if not context_parts:
        return prompt

    memory_block = "\n".join(context_parts)
    augmented = f"[Memory context — relevant notes from previous sessions:]\n{memory_block}\n\n{prompt}"

    logger.info(f"Injected {len(context_parts)} memory notes into prompt")
    return augmented


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
                index_note("facts.md", fact)
                logger.info(f"Extracted explicit fact: {fact[:60]}...")


def _slugify(text: str) -> str:
    """Convert text to filename-safe slug."""
    text = re.sub(r'[^\w\s-]', '', text.lower())
    text = re.sub(r'[\s]+', '-', text)
    return text[:40]
