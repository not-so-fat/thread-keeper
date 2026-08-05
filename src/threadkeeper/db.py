"""SQLite store: schema, project upsert, whole-session replace, collection_state.

Ported 1:1 (schema + F2.3 replace-session semantics) from Chronicle's
``server/db.js``, plus the thread-keeper-new ``collection_state`` table and
the ``host`` column (PRD §7.1, OD-7).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / ".thread-keeper"


def get_data_dir() -> Path:
    """Resolve the data dir: ``THREAD_KEEPER_DATA_DIR`` env override, else ``~/.thread-keeper``."""
    override = os.environ.get("THREAD_KEEPER_DATA_DIR")
    data_dir = Path(override).expanduser() if override else DEFAULT_DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_db_path() -> Path:
    return get_data_dir() / "thread-keeper.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  source TEXT NOT NULL,
  file_path TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  message_count INTEGER DEFAULT 0,
  first_prompt TEXT,
  context_tokens INTEGER,
  name TEXT,
  summary TEXT,
  usage TEXT,
  host TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  seq INTEGER NOT NULL,
  uuid TEXT,
  ts TEXT,
  kind TEXT NOT NULL,
  text TEXT,
  tool_name TEXT,
  tool_input TEXT,
  tool_use_id TEXT,
  model TEXT,
  injected INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
CREATE TABLE IF NOT EXISTS collection_state (
  file_path TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  last_size INTEGER,
  last_mtime TEXT,
  last_hash TEXT,
  last_collected_at TEXT NOT NULL
);
"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open (and lazily init) the thread-keeper SQLite store."""
    path = Path(db_path) if db_path is not None else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def upsert_project(conn: sqlite3.Connection, physical_path: str) -> int:
    """Insert-or-get a project keyed by physical path (OD-7: merge by path)."""
    name = os.path.basename(physical_path.rstrip("/")) or physical_path
    conn.execute(
        "INSERT INTO projects (path, name) VALUES (?, ?) ON CONFLICT(path) DO NOTHING",
        (physical_path, name),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM projects WHERE path = ?", (physical_path,)).fetchone()
    return row["id"]


def replace_session(
    conn: sqlite3.Connection,
    session: dict,
    events: list[dict],
    *,
    host: str | None = None,
    host_explicit: bool = False,
) -> None:
    """Delete + reinsert a session and its messages in one transaction (F2.3).

    Preserves a user-set ``name`` across re-imports. Preserves the prior
    ``host`` unless ``host_explicit`` is True (i.e. ``--host`` was passed for
    this ingest), in which case ``host`` overrides it. A brand-new session
    with no prior row and no explicit override gets ``host`` as its default
    (local hostname for local roots).
    """
    conn.execute("BEGIN")
    try:
        prev = conn.execute(
            "SELECT name, host FROM sessions WHERE id = ?", (session["id"],)
        ).fetchone()
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session["id"],))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session["id"],))

        new_name = session.get("name") or (prev["name"] if prev else None)
        if host_explicit:
            new_host = host
        else:
            new_host = prev["host"] if prev else host

        conn.execute(
            """INSERT INTO sessions
                 (id, project_id, source, file_path, started_at, ended_at,
                  message_count, first_prompt, context_tokens, name, summary, usage, host)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session["id"],
                session["project_id"],
                session["source"],
                session["file_path"],
                session.get("started_at"),
                session.get("ended_at"),
                len(events),
                session.get("first_prompt"),
                session.get("context_tokens"),
                new_name,
                session.get("summary"),
                session.get("usage"),
                new_host,
            ),
        )
        conn.executemany(
            """INSERT INTO messages
                 (session_id, seq, uuid, ts, kind, text, tool_name, tool_input, tool_use_id, model, injected)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    session["id"],
                    i,
                    e.get("uuid"),
                    e.get("ts"),
                    e["kind"],
                    e.get("text"),
                    e.get("tool_name"),
                    e.get("tool_input"),
                    e.get("tool_use_id"),
                    e.get("model"),
                    1 if e.get("injected") else 0,
                )
                for i, e in enumerate(events)
            ],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_collection_state(conn: sqlite3.Connection, file_path: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM collection_state WHERE file_path = ?", (file_path,)
    ).fetchone()


def set_collection_state(
    conn: sqlite3.Connection,
    file_path: str,
    source: str,
    *,
    last_size: int | None = None,
    last_mtime: str | None = None,
    last_hash: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO collection_state (file_path, source, last_size, last_mtime, last_hash, last_collected_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(file_path) DO UPDATE SET
             source=excluded.source, last_size=excluded.last_size,
             last_mtime=excluded.last_mtime, last_hash=excluded.last_hash,
             last_collected_at=excluded.last_collected_at""",
        (file_path, source, last_size, last_mtime, last_hash),
    )
    conn.commit()


def has_changed(
    conn: sqlite3.Connection,
    file_path: str,
    *,
    size: int | None = None,
    mtime: str | None = None,
    content_hash: str | None = None,
) -> bool:
    """Whole-file change detector (F3.3): compares size+mtime (JSONL) or hash (Cursor)."""
    prev = get_collection_state(conn, file_path)
    if prev is None:
        return True
    if content_hash is not None:
        return prev["last_hash"] != content_hash
    return prev["last_size"] != size or prev["last_mtime"] != mtime
