"""Memory vault — simple markdown files in workspace/memory/."""

import logging
from pathlib import Path
from datetime import datetime

import config

logger = logging.getLogger("memory.vault")


def ensure_dirs():
    """Create memory directory structure."""
    config.MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def save_note(filename: str, content: str):
    """Save or overwrite a markdown note."""
    ensure_dirs()
    path = config.MEMORY_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info(f"Saved note: {filename}")


def append_note(filename: str, text: str):
    """Append text to an existing note, or create it."""
    ensure_dirs()
    path = config.MEMORY_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n\n## {timestamp}\n{text}"

    path.write_text(existing + entry, encoding="utf-8")
    logger.info(f"Appended to note: {filename}")


def read_note(filename: str) -> str | None:
    """Read a note's content."""
    path = config.MEMORY_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def list_notes() -> list[str]:
    """List all .md files in memory vault."""
    if not config.MEMORY_DIR.exists():
        return []
    return [
        str(p.relative_to(config.MEMORY_DIR))
        for p in config.MEMORY_DIR.rglob("*.md")
    ]


def delete_note(filename: str) -> bool:
    """Delete a note."""
    path = config.MEMORY_DIR / filename
    if path.exists():
        path.unlink()
        logger.info(f"Deleted note: {filename}")
        return True
    return False


def vault_stats() -> dict:
    """Get vault statistics."""
    notes = list_notes()
    total_size = sum(
        (config.MEMORY_DIR / n).stat().st_size
        for n in notes
        if (config.MEMORY_DIR / n).exists()
    )
    return {
        "note_count": len(notes),
        "total_size_kb": round(total_size / 1024, 1),
    }
