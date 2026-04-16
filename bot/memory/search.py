"""FTS5 search over memory vault."""

import logging
import sqlite3
from dataclasses import dataclass

import config
from memory.vault import list_notes, read_note

logger = logging.getLogger("memory.search")


@dataclass
class SearchResult:
    path: str
    snippet: str
    rank: float


def _get_db() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_fts():
    """Create FTS5 table if not exists."""
    conn = _get_db()
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                path, content, tokenize='unicode61'
            )
        """)
        conn.commit()
    except Exception as e:
        logger.warning(f"FTS5 not available: {e}. Memory search disabled.")
    finally:
        conn.close()


def reindex_vault():
    """Full reindex: read all .md files and update FTS5."""
    conn = _get_db()
    try:
        conn.execute("DELETE FROM memory_fts")
        notes = list_notes()
        for note_path in notes:
            content = read_note(note_path)
            if content:
                conn.execute(
                    "INSERT INTO memory_fts (path, content) VALUES (?, ?)",
                    (note_path, content),
                )
        conn.commit()
        logger.info(f"Reindexed {len(notes)} notes")
    except Exception as e:
        logger.warning(f"Reindex failed: {e}")
    finally:
        conn.close()


def index_note(path: str, content: str):
    """Index or update a single note."""
    conn = _get_db()
    try:
        conn.execute("DELETE FROM memory_fts WHERE path = ?", (path,))
        conn.execute(
            "INSERT INTO memory_fts (path, content) VALUES (?, ?)",
            (path, content),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"Index note failed: {e}")
    finally:
        conn.close()


def search(query: str, limit: int = 5) -> list[SearchResult]:
    """FTS5 search with ranking."""
    conn = _get_db()
    results = []
    try:
        rows = conn.execute(
            """
            SELECT path, snippet(memory_fts, 1, '»', '«', '...', 40) as snippet,
                   rank
            FROM memory_fts
            WHERE memory_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        results = [
            SearchResult(path=r["path"], snippet=r["snippet"], rank=r["rank"])
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"Search failed: {e}")
    finally:
        conn.close()
    return results
