"""Automated collection: fast-path (hook) + sweep (backstop / cold-start backfill).

Implements the fast-path/sweep split (F3.1) and the whole-file change-detection
watermark (F3.3). Every ingestion path routes through ``db.replace_session``
so any mode is idempotent (F2.3). Cold-start backfill (F3.6) falls out
naturally: an empty ``collection_state`` means every file looks "changed" on
the first sweep.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .parsers import claude_code, codex, cursor

SOURCES = ("claude-code", "codex", "cursor")


def default_host() -> str:
    return socket.gethostname()


def resolve_claude_root(override: str | None = None) -> Path:
    return Path(override or os.environ.get("THREAD_KEEPER_CLAUDE_ROOT") or claude_code.DEFAULT_CLAUDE_PROJECTS_DIR)


def resolve_codex_root(override: str | None = None) -> Path:
    return Path(override or os.environ.get("THREAD_KEEPER_CODEX_ROOT") or codex.DEFAULT_CODEX_SESSIONS_DIR)


def resolve_cursor_root(override: str | None = None) -> Path:
    return Path(override or os.environ.get("THREAD_KEEPER_CURSOR_ROOT") or cursor.cursor_user_dir())


@dataclass
class SweepResult:
    ingested: int = 0
    skipped_unchanged: int = 0
    errored: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "SweepResult") -> None:
        self.ingested += other.ingested
        self.skipped_unchanged += other.skipped_unchanged
        self.errored += other.errored
        self.errors.extend(other.errors)


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _walk_jsonl(root: Path):
    """Recursive JSONL walk (Codex date-nested rollout dirs)."""
    if not root.exists():
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            if fn.endswith(".jsonl"):
                yield Path(dirpath) / fn


def _iter_claude_session_files(root: Path):
    """Claude Code session files only — mirrors Chronicle's non-recursive scan.

    Layout: ``~/.claude/projects/<munged-cwd>/<session-uuid>.jsonl``.
    Nested ``<session-uuid>/subagents/agent-*.jsonl`` share the parent
    ``sessionId`` and would otherwise wipe the main session via replace_session;
    they are sidechains (skipped by the parser) and must not be ingested as
    sessions. Also accepts ``*.jsonl`` directly under ``root`` (test fixtures).
    """
    if not root.exists():
        return
    for entry in sorted(root.iterdir()):
        if entry.is_file() and entry.suffix == ".jsonl":
            yield entry
        elif entry.is_dir():
            for child in sorted(entry.iterdir()):
                if child.is_file() and child.suffix == ".jsonl":
                    yield child


# --- Claude Code -------------------------------------------------------


def _ingest_claude_file(conn, file: Path, host: str | None, host_explicit: bool, *, force: bool = False) -> bool:
    stat = file.stat()
    mtime_iso = _iso_mtime(file)
    if not force and not db.has_changed(conn, str(file), size=stat.st_size, mtime=mtime_iso):
        return False
    session, events = claude_code.parse_claude_session(file)
    session.pop("skipped", None)
    project_id = db.upsert_project(conn, session.pop("cwd", None) or "(unknown-cwd)")
    session["project_id"] = project_id
    db.replace_session(conn, session, events, host=host, host_explicit=host_explicit)
    db.set_collection_state(conn, str(file), "claude-code", last_size=stat.st_size, last_mtime=mtime_iso)
    return True


def sweep_claude_code(conn, root: Path, host: str | None, host_explicit: bool) -> SweepResult:
    result = SweepResult()
    for file in _iter_claude_session_files(root):
        try:
            if _ingest_claude_file(conn, file, host, host_explicit):
                result.ingested += 1
            else:
                result.skipped_unchanged += 1
        except Exception as exc:  # never let one bad file abort the sweep
            result.errored += 1
            result.errors.append(f"{file}: {exc}")
    return result


def _find_claude_file(root: Path, session_id: str) -> Path | None:
    target = f"{session_id}.jsonl"
    for file in _iter_claude_session_files(root):
        if file.name == target:
            return file
    return None


# --- Codex ---------------------------------------------------------------


def _ingest_codex_file(conn, file: Path, host: str | None, host_explicit: bool, *, force: bool = False) -> bool:
    stat = file.stat()
    mtime_iso = _iso_mtime(file)
    if not force and not db.has_changed(conn, str(file), size=stat.st_size, mtime=mtime_iso):
        return False
    session, events = codex.parse_codex_session(file)
    session.pop("skipped", None)
    project_id = db.upsert_project(conn, session.pop("cwd", None) or "(unknown-cwd)")
    session["project_id"] = project_id
    db.replace_session(conn, session, events, host=host, host_explicit=host_explicit)
    db.set_collection_state(conn, str(file), "codex", last_size=stat.st_size, last_mtime=mtime_iso)
    return True


def sweep_codex(conn, root: Path, host: str | None, host_explicit: bool) -> SweepResult:
    result = SweepResult()
    for file in _walk_jsonl(root):
        try:
            if _ingest_codex_file(conn, file, host, host_explicit):
                result.ingested += 1
            else:
                result.skipped_unchanged += 1
        except Exception as exc:
            result.errored += 1
            result.errors.append(f"{file}: {exc}")
    return result


def _find_codex_file(root: Path, session_id: str) -> Path | None:
    """Best-effort file resolution for the Codex fast path.

    The primary ``notify`` trigger (OD-6) carries no session id or file path
    (PRD §7.3), so this applies the idle-heuristic (F3.5): try an id/filename
    match first, then fall back to the most-recently-modified rollout file.
    """
    candidates = list(_walk_jsonl(root))
    if not candidates:
        return None
    raw_id = session_id[len("codex-"):] if session_id.startswith("codex-") else session_id
    for file in candidates:
        if raw_id and raw_id in file.name:
            return file
    for file in candidates:
        try:
            with open(file, encoding="utf-8") as fh:
                first_line = fh.readline()
            o = json.loads(first_line)
            p = o.get("payload") or o
            if p.get("id") == raw_id:
                return file
        except (OSError, json.JSONDecodeError):
            continue
    return max(candidates, key=lambda f: f.stat().st_mtime)


# --- Cursor ----------------------------------------------------------------


def _ingest_cursor_workspace(conn, ws_dir: Path, user_dir: Path, host: str | None, host_explicit: bool, *, force: bool = False) -> bool:
    file_path = str(ws_dir / "state.vscdb")
    content_hash = cursor.workspace_content_hash(ws_dir, user_dir)
    if not force and not db.has_changed(conn, file_path, content_hash=content_hash):
        return False
    for session, events in cursor.parse_cursor_workspace(ws_dir, user_dir):
        session.pop("skipped", None)
        project_id = db.upsert_project(conn, session.pop("cwd", None) or "(unknown-cwd)")
        session["project_id"] = project_id
        db.replace_session(conn, session, events, host=host, host_explicit=host_explicit)
    db.set_collection_state(conn, file_path, "cursor", last_hash=content_hash)
    return True


def _ingest_cursor_global(conn, user_dir: Path, host: str | None, host_explicit: bool, *, force: bool = False) -> bool:
    """Ingest modern Cursor composers from global ``cursorDiskKV`` (F1.3/US-3)."""
    global_db = user_dir / "globalStorage" / "state.vscdb"
    if not global_db.exists():
        return False
    file_path = str(global_db)
    content_hash = cursor.global_content_hash(user_dir)
    if not force and not db.has_changed(conn, file_path, content_hash=content_hash):
        return False
    for session, events in cursor.parse_cursor_global(user_dir):
        session.pop("skipped", None)
        project_id = db.upsert_project(conn, session.pop("cwd", None) or "(unknown-cwd)")
        session["project_id"] = project_id
        db.replace_session(conn, session, events, host=host, host_explicit=host_explicit)
    db.set_collection_state(conn, file_path, "cursor", last_hash=content_hash)
    return True


def sweep_cursor(conn, root: Path, host: str | None, host_explicit: bool) -> SweepResult:
    """Walk workspace DBs (legacy chat) + global composer store (modern Cursor)."""
    result = SweepResult()
    ws_root = root / "workspaceStorage"
    if ws_root.exists():
        for d in sorted(p for p in ws_root.iterdir() if p.is_dir()):
            if not (d / "state.vscdb").exists():
                continue
            try:
                if _ingest_cursor_workspace(conn, d, root, host, host_explicit):
                    result.ingested += 1
                else:
                    result.skipped_unchanged += 1
            except Exception as exc:
                result.errored += 1
                result.errors.append(f"{d}: {exc}")
    try:
        if _ingest_cursor_global(conn, root, host, host_explicit):
            result.ingested += 1
        else:
            # unchanged OR no global DB — only count skip when the DB exists
            if (root / "globalStorage" / "state.vscdb").exists():
                result.skipped_unchanged += 1
    except Exception as exc:
        result.errored += 1
        result.errors.append(f"{root / 'globalStorage'}: {exc}")
    return result


def _find_cursor_workspace(root: Path, session_id: str) -> Path | None:
    """Resolve a legacy chat/composer session id to its workspaceStorage dir."""
    ws_root = root / "workspaceStorage"
    if not ws_root.exists():
        return None
    for d in sorted(p for p in ws_root.iterdir() if p.is_dir()):
        if not (d / "state.vscdb").exists():
            continue
        try:
            sessions = cursor.parse_cursor_workspace(d, root)
        except Exception:
            continue
        if any(s["id"] == session_id for s, _ in sessions):
            return d
    return None


# --- Public entry points ----------------------------------------------------


def collect_sweep(
    conn,
    *,
    sources: tuple[str, ...] | None = None,
    claude_root: str | None = None,
    codex_root: str | None = None,
    cursor_root: str | None = None,
    host: str | None = None,
) -> dict[str, SweepResult]:
    """Walk one or more sources' log dirs, ingesting anything changed since last run."""
    host_explicit = host is not None
    effective_host = host if host_explicit else default_host()
    sources = sources or SOURCES
    results: dict[str, SweepResult] = {}
    if "claude-code" in sources:
        results["claude-code"] = sweep_claude_code(conn, resolve_claude_root(claude_root), effective_host, host_explicit)
    if "codex" in sources:
        results["codex"] = sweep_codex(conn, resolve_codex_root(codex_root), effective_host, host_explicit)
    if "cursor" in sources:
        results["cursor"] = sweep_cursor(conn, resolve_cursor_root(cursor_root), effective_host, host_explicit)
    return results


def collect_fast_path(
    conn,
    *,
    source: str,
    session_id: str,
    transcript: str | None = None,
    claude_root: str | None = None,
    codex_root: str | None = None,
    cursor_root: str | None = None,
    host: str | None = None,
) -> bool:
    """Ingest exactly the named session (F3.1 fast path). Raises on failure —
    callers (the CLI) are responsible for the "always exit 0" contract (F3.1/NFR-5).
    """
    host_explicit = host is not None
    effective_host = host if host_explicit else default_host()

    if source == "claude-code":
        file = Path(transcript) if transcript else _find_claude_file(resolve_claude_root(claude_root), session_id)
        if file is None or not file.exists():
            raise FileNotFoundError(f"claude-code transcript not found for session {session_id!r}")
        return _ingest_claude_file(conn, file, effective_host, host_explicit, force=True)

    if source == "codex":
        file = Path(transcript) if transcript else _find_codex_file(resolve_codex_root(codex_root), session_id)
        if file is None or not file.exists():
            raise FileNotFoundError(f"codex rollout file not found for session {session_id!r}")
        return _ingest_codex_file(conn, file, effective_host, host_explicit, force=True)

    if source == "cursor":
        root = resolve_cursor_root(cursor_root)
        # Modern composers live in globalStorage; legacy chat/composers in workspaceStorage.
        if session_id.startswith("cursor-composer-") and (root / "globalStorage" / "state.vscdb").exists():
            if any(s["id"] == session_id for s, _ in cursor.parse_cursor_global(root)):
                return _ingest_cursor_global(conn, root, effective_host, host_explicit, force=True)
        ws_dir = Path(transcript) if transcript else _find_cursor_workspace(root, session_id)
        if ws_dir is None or not (ws_dir / "state.vscdb").exists():
            raise FileNotFoundError(f"cursor workspace not found for session {session_id!r}")
        return _ingest_cursor_workspace(conn, ws_dir, root, effective_host, host_explicit, force=True)

    raise ValueError(f"unknown source: {source!r}")


def collect_codex_notify(conn, *, codex_root: str | None = None, host: str | None = None) -> bool:
    """Fast path for Codex's ``notify`` trigger (OD-6).

    ``notify`` carries no session id (PRD §7.3), so this applies the
    idle-heuristic (F3.5) directly: reparse the most-recently-modified
    rollout file whole and replace its session.
    """
    root = resolve_codex_root(codex_root)
    candidates = list(_walk_jsonl(root))
    if not candidates:
        return False
    file = max(candidates, key=lambda f: f.stat().st_mtime)
    host_explicit = host is not None
    effective_host = host if host_explicit else default_host()
    return _ingest_codex_file(conn, file, effective_host, host_explicit, force=True)


def status(conn) -> dict:
    """Store path, row counts, and per-file watermarks (for ``thread-keeper status``)."""
    counts = {
        table: conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        for table in ("projects", "sessions", "messages", "collection_state")
    }
    sessions_by_source = {
        row["source"]: row["c"]
        for row in conn.execute("SELECT source, COUNT(*) AS c FROM sessions GROUP BY source")
    }
    watermarks = [
        dict(row)
        for row in conn.execute(
            "SELECT file_path, source, last_size, last_mtime, last_hash, last_collected_at "
            "FROM collection_state ORDER BY last_collected_at DESC"
        )
    ]
    return {
        "db_path": str(db.get_db_path()),
        "counts": counts,
        "sessions_by_source": sessions_by_source,
        "watermarks": watermarks,
    }
