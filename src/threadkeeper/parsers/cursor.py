"""Cursor parser — ports Chronicle's ``server/parsers/cursor.js``, plus the
2026 global-``composerData`` layout (composer index left the per-workspace
``ItemTable``; bubbles still live in global ``cursorDiskKV``).

Read-only guarantee (NFR-4): every ``state.vscdb`` (+ ``-wal``/``-shm``) is
always snapshot-copied to a temp dir before opening; the original DB is never
opened read-write and never mutated.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote


def cursor_user_dir() -> Path:
    override = os.environ.get("THREAD_KEEPER_CURSOR_ROOT")
    if override:
        return Path(override)
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        return home / "Library" / "Application Support" / "Cursor" / "User"
    if system == "Windows":
        appdata = os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
        return Path(appdata) / "Cursor" / "User"
    return home / ".config" / "Cursor" / "User"


@contextmanager
def open_snapshot(db_path: str | Path):
    """Copy the SQLite file (+ -wal/-shm) to a temp dir and open the copy read-only."""
    db_path = Path(db_path)
    tmp_dir = Path(tempfile.mkdtemp(prefix="thread-keeper-cursor-"))
    copy_path = tmp_dir / db_path.name
    try:
        shutil.copyfile(db_path, copy_path)
        for ext in ("-wal", "-shm"):
            src = Path(str(db_path) + ext)
            if src.exists():
                shutil.copyfile(src, Path(str(copy_path) + ext))
        conn = sqlite3.connect(str(copy_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _item_table_get(conn: sqlite3.Connection, key: str):
    try:
        row = conn.execute("SELECT value FROM ItemTable WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None
    except (sqlite3.Error, json.JSONDecodeError, TypeError):
        return None


def _disk_kv_get(conn: sqlite3.Connection, key: str):
    try:
        row = conn.execute("SELECT value FROM cursorDiskKV WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None
    except (sqlite3.Error, json.JSONDecodeError, TypeError):
        return None


def workspace_folder(ws_dir: str | Path) -> str | None:
    try:
        meta = json.loads((Path(ws_dir) / "workspace.json").read_text(encoding="utf-8"))
        uri = meta.get("folder") or meta.get("workspace")
        if uri and uri.startswith("file://"):
            return unquote(uri[len("file://") :])
    except (OSError, json.JSONDecodeError):
        pass
    return None


def workspace_id_map(user_dir: str | Path) -> dict[str, str | None]:
    """Map Cursor workspaceStorage directory names → physical folder paths."""
    ws_root = Path(user_dir) / "workspaceStorage"
    if not ws_root.exists():
        return {}
    return {
        d.name: workspace_folder(d)
        for d in ws_root.iterdir()
        if d.is_dir() and (d / "state.vscdb").exists()
    }


def _ms_to_iso(ms) -> str | None:
    if not ms:
        return None
    from datetime import datetime, timezone

    try:
        # Cursor sometimes stores ms epoch, sometimes ISO strings.
        if isinstance(ms, str):
            return ms if "T" in ms else None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return None


def _as_dict(value):
    """Cursor sometimes stores nested objects as JSON strings (e.g. ``thinking``)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.startswith("{"):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def bubble_to_event(b: dict) -> list[dict] | None:
    """Cursor bubble -> normalized events. type 1/'user' = user, 2/'ai' = assistant."""
    ts = (_as_dict(b.get("timingInfo")).get("clientStartTime") or b.get("createdAt"))
    iso = _ms_to_iso(ts)
    events: list[dict] = []
    text = b.get("text") or _as_dict(b.get("richText")).get("text") or ""
    is_user = b.get("type") in (1, "user")
    if text.strip():
        events.append(
            {
                "ts": iso,
                "kind": "user" if is_user else "assistant",
                "text": text,
                "model": b.get("modelType"),
            }
        )
    thinking = _as_dict(b.get("thinking")).get("text")
    if thinking:
        events.append({"ts": iso, "kind": "thinking", "text": thinking})
    for t in b.get("toolResults") or []:
        events.append(
            {
                "ts": iso,
                "kind": "tool_use",
                "tool_name": t.get("toolName") or t.get("name") or "tool",
                "tool_input": json.dumps(t.get("args") or {}),
                "tool_use_id": t.get("toolCallId"),
            }
        )
        if t.get("result") is not None:
            result = t["result"]
            events.append(
                {
                    "ts": iso,
                    "kind": "tool_result",
                    "text": result if isinstance(result, str) else json.dumps(result),
                    "tool_use_id": t.get("toolCallId"),
                }
            )
    return events or None


def _make_session(
    session_id: str,
    file_path: str,
    folder: str | None,
    title,
    events: list[dict],
    created_at,
) -> dict:
    timestamps = sorted(e["ts"] for e in events if e.get("ts"))
    fallback = _ms_to_iso(created_at)
    return {
        "id": session_id,
        "source": "cursor",
        "file_path": file_path,
        "cwd": folder,
        "started_at": timestamps[0] if timestamps else fallback,
        "ended_at": timestamps[-1] if timestamps else fallback,
        "first_prompt": (next((e["text"] for e in events if e["kind"] == "user"), None) or title or "")[:200],
        "skipped": 0,
    }


def _resolve_composer_events(global_conn: sqlite3.Connection, composer_id: str, data: dict) -> list[dict]:
    """Resolve bubbles for one composer from conversation / headers / conversationMap."""
    events: list[dict] = []
    conv = data.get("conversation")
    if not conv:
        headers = data.get("fullConversationHeadersOnly") or []
        if headers:
            conv = []
            for h in headers:
                bubble = _disk_kv_get(global_conn, f"bubbleId:{composer_id}:{h.get('bubbleId')}")
                if bubble:
                    conv.append(bubble)
        elif data.get("conversationMap"):
            # Newer layout: bubble ids keyed in conversationMap; fetch each bubble.
            conv = []
            for bubble_id in data["conversationMap"]:
                bubble = _disk_kv_get(global_conn, f"bubbleId:{composer_id}:{bubble_id}")
                if bubble:
                    conv.append(bubble)
    for b in conv or []:
        ev = bubble_to_event(b)
        if ev:
            events.extend(ev)
    return events


def _composer_folder(data: dict, id_map: dict[str, str | None]) -> str | None:
    """Map a composer's ``workspaceIdentifier`` to a physical project path.

    Unknown / empty-window ids resolve to ``None``; the collector then buckets
    the session under ``(unknown-cwd)`` (same rule as JSONL parsers with no cwd).
    """
    wid = data.get("workspaceIdentifier") or {}
    if not isinstance(wid, dict):
        return None
    wid_id = wid.get("id")
    if wid_id and id_map.get(wid_id):
        return id_map[wid_id]
    return None


def parse_cursor_workspace(ws_dir: str | Path, user_dir: str | Path | None = None) -> list[tuple[dict, list[dict]]]:
    """Parse legacy chat tabs + workspace-local composers in one workspace DB.

    Modern Cursor stores composers only in global ``cursorDiskKV`` — those are
    handled by ``parse_cursor_global``. This function still covers the
    workspace-local ``composer.composerData`` layout Chronicle knew, plus
    legacy aichat tabs.
    """
    ws_dir = Path(ws_dir)
    user_dir = Path(user_dir) if user_dir is not None else cursor_user_dir()
    db_path = ws_dir / "state.vscdb"
    folder = workspace_folder(ws_dir)
    out: list[tuple[dict, list[dict]]] = []
    file_path = str(db_path)

    with open_snapshot(db_path) as ws_conn:
        global_db = user_dir / "globalStorage" / "state.vscdb"
        global_ctx = open_snapshot(global_db) if global_db.exists() else None
        global_conn = global_ctx.__enter__() if global_ctx else None
        try:
            chat = _item_table_get(ws_conn, "workbench.panel.aichat.view.aichat.chatdata")
            for tab in (chat or {}).get("tabs") or []:
                events: list[dict] = []
                for b in tab.get("bubbles") or []:
                    ev = bubble_to_event(b)
                    if ev:
                        events.extend(ev)
                session = _make_session(
                    f"cursor-chat-{tab.get('tabId')}",
                    file_path,
                    folder,
                    tab.get("chatTitle"),
                    events,
                    tab.get("lastSendTime"),
                )
                out.append((session, events))

            composers = _item_table_get(ws_conn, "composer.composerData")
            for c in (composers or {}).get("allComposers") or []:
                events = []
                conv = c.get("conversation")
                if not conv and global_conn is not None:
                    data = _disk_kv_get(global_conn, f"composerData:{c.get('composerId')}") or {}
                    events = _resolve_composer_events(global_conn, c.get("composerId"), {**c, **data})
                else:
                    for b in conv or []:
                        ev = bubble_to_event(b)
                        if ev:
                            events.extend(ev)
                session = _make_session(
                    f"cursor-composer-{c.get('composerId')}",
                    file_path,
                    folder,
                    c.get("name") or (c.get("text") or "")[:100],
                    events,
                    c.get("createdAt"),
                )
                out.append((session, events))
        finally:
            if global_ctx:
                global_ctx.__exit__(None, None, None)

    return [(s, e) for s, e in out if e]


def parse_cursor_global(user_dir: str | Path | None = None) -> list[tuple[dict, list[dict]]]:
    """Parse every composer stored in global ``cursorDiskKV`` (modern Cursor).

    Enumerates ``composerData:<id>`` keys, resolves bubbles via
    ``fullConversationHeadersOnly`` / ``conversationMap``, and attributes
    each session to a project via ``workspaceIdentifier`` → workspaceStorage.
    """
    user_dir = Path(user_dir) if user_dir is not None else cursor_user_dir()
    global_db = user_dir / "globalStorage" / "state.vscdb"
    if not global_db.exists():
        return []
    id_map = workspace_id_map(user_dir)
    file_path = str(global_db)
    out: list[tuple[dict, list[dict]]] = []

    with open_snapshot(global_db) as global_conn:
        keys = [
            row[0]
            for row in global_conn.execute(
                "SELECT key FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
            )
        ]
        for key in keys:
            data = _disk_kv_get(global_conn, key)
            if not data:
                continue
            composer_id = data.get("composerId") or key.split(":", 1)[-1]
            if data.get("isDraft") and not (data.get("fullConversationHeadersOnly") or data.get("conversation")):
                continue
            events = _resolve_composer_events(global_conn, composer_id, data)
            if not events:
                continue
            folder = _composer_folder(data, id_map)
            title = data.get("name") or (data.get("text") or "")[:100]
            session = _make_session(
                f"cursor-composer-{composer_id}",
                file_path,
                folder,
                title,
                events,
                data.get("createdAt"),
            )
            out.append((session, events))
    return out


def workspace_content_hash(ws_dir: str | Path, user_dir: str | Path | None = None) -> str:
    """Change-detector hash for one workspace DB (collection_state.last_hash).

    Per ``docs/contracts/collection_state.json``: hash of each composer's
    ``fullConversationHeadersOnly``, plus legacy aichat tab ids/titles so
    workspace-local chat changes are detected too.
    """
    ws_dir = Path(ws_dir)
    db_path = ws_dir / "state.vscdb"
    with open_snapshot(db_path) as ws_conn:
        chat = _item_table_get(ws_conn, "workbench.panel.aichat.view.aichat.chatdata")
        composers = _item_table_get(ws_conn, "composer.composerData")
    chat_summary = [
        {"tabId": t.get("tabId"), "lastSendTime": t.get("lastSendTime"), "n": len(t.get("bubbles") or [])}
        for t in (chat or {}).get("tabs") or []
    ]
    composer_summary = [
        {
            "composerId": c.get("composerId"),
            "fullConversationHeadersOnly": c.get("fullConversationHeadersOnly"),
        }
        for c in (composers or {}).get("allComposers") or []
    ]
    blob = json.dumps({"chat": chat_summary, "composers": composer_summary}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def global_content_hash(user_dir: str | Path | None = None) -> str:
    """Change-detector hash for global composer store (collection_state.last_hash).

    Per contract: hash of ``fullConversationHeadersOnly`` per composer
    (keyed by composerId). Detects append/edit without reading every bubble.
    """
    user_dir = Path(user_dir) if user_dir is not None else cursor_user_dir()
    global_db = user_dir / "globalStorage" / "state.vscdb"
    if not global_db.exists():
        return hashlib.sha256(b"").hexdigest()
    with open_snapshot(global_db) as global_conn:
        rows = global_conn.execute(
            "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
        ).fetchall()
        summary = []
        for key, value in rows:
            try:
                data = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                summary.append((key, None))
                continue
            composer_id = data.get("composerId") or key.split(":", 1)[-1]
            summary.append((composer_id, data.get("fullConversationHeadersOnly")))
    blob = json.dumps(sorted(summary, key=lambda x: str(x[0])), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
