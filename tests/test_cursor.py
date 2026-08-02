import hashlib
import json
import sqlite3

from threadkeeper.parsers import cursor


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_parse_cursor_workspace_fixture(cursor_fixture):
    user_dir, ws_dir = cursor_fixture
    sessions = cursor.parse_cursor_workspace(ws_dir, user_dir)

    ids = sorted(s["id"] for s, _ in sessions)
    assert ids == ["cursor-chat-tab1", "cursor-composer-comp1"]

    by_id = {s["id"]: (s, e) for s, e in sessions}

    chat_session, chat_events = by_id["cursor-chat-tab1"]
    assert chat_session["source"] == "cursor"
    assert chat_session["cwd"] == "/Users/test/health-analyst"
    kinds = [e["kind"] for e in chat_events]
    assert kinds == ["user", "assistant"]
    assert chat_events[0]["text"] == "Why does login fail with OAuth?"
    assert chat_events[1]["model"] == "gpt-4"

    composer_session, composer_events = by_id["cursor-composer-comp1"]
    assert composer_session["cwd"] == "/Users/test/health-analyst"
    kinds = [e["kind"] for e in composer_events]
    # assistant bubble carries text + thinking + a tool_use/tool_result pair
    assert kinds == ["user", "assistant", "thinking", "tool_use", "tool_result"]
    tool_use = next(e for e in composer_events if e["kind"] == "tool_use")
    assert tool_use["tool_name"] == "read_file"
    assert json.loads(tool_use["tool_input"]) == {"path": "src/app/dashboard/page.tsx"}


def test_cursor_snapshot_never_mutates_original(cursor_fixture):
    user_dir, ws_dir = cursor_fixture
    db_path = ws_dir / "state.vscdb"
    before = _sha256(db_path)

    cursor.parse_cursor_workspace(ws_dir, user_dir)

    after = _sha256(db_path)
    assert before == after
    # the parser must not leave its own -wal/-shm journal on the original DB
    assert not (ws_dir / "state.vscdb-wal").exists()
    assert not (ws_dir / "state.vscdb-shm").exists()


def test_cursor_reads_wal_only_unflushed_data(tmp_path):
    """A workspace with data still sitting in -wal (not yet checkpointed into
    the main file) must still be readable via the snapshot copy (F1.3)."""
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    (ws_dir / "workspace.json").write_text(json.dumps({"folder": "file:///tmp/wal-project"}))
    db_path = ws_dir / "state.vscdb"

    raw_conn = sqlite3.connect(str(db_path))
    try:
        raw_conn.execute("PRAGMA journal_mode=WAL")
        raw_conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
        raw_conn.commit()
        chat = {
            "tabs": [
                {
                    "tabId": "wal1",
                    "chatTitle": "WAL only",
                    "lastSendTime": 1,
                    "bubbles": [{"type": "user", "text": "Is this in the WAL?", "timingInfo": {"clientStartTime": 1}}],
                }
            ]
        }
        raw_conn.execute(
            "INSERT INTO ItemTable VALUES (?, ?)",
            ("workbench.panel.aichat.view.aichat.chatdata", json.dumps(chat)),
        )
        raw_conn.commit()
        assert (ws_dir / "state.vscdb-wal").exists()

        sessions = cursor.parse_cursor_workspace(ws_dir, tmp_path / "no-global-storage-here")
    finally:
        raw_conn.close()

    assert len(sessions) == 1
    _session, events = sessions[0]
    assert any(e["text"] == "Is this in the WAL?" for e in events)


def test_workspace_content_hash_changes_when_data_changes(cursor_fixture):
    user_dir, ws_dir = cursor_fixture
    h1 = cursor.workspace_content_hash(ws_dir, user_dir)
    h2 = cursor.workspace_content_hash(ws_dir, user_dir)
    assert h1 == h2  # deterministic, unchanged content

    conn = sqlite3.connect(str(ws_dir / "state.vscdb"))
    conn.execute(
        "UPDATE ItemTable SET value = ? WHERE key = ?",
        (json.dumps({"tabs": []}), "workbench.panel.aichat.view.aichat.chatdata"),
    )
    conn.commit()
    conn.close()

    h3 = cursor.workspace_content_hash(ws_dir, user_dir)
    assert h3 != h1


def test_parse_cursor_global_modern_layout(tmp_path):
    """Modern Cursor: composers live only in global cursorDiskKV (no workspace ItemTable index)."""
    user_dir = tmp_path / "cursor-user"
    ws_dir = user_dir / "workspaceStorage" / "wsabc"
    global_dir = user_dir / "globalStorage"
    ws_dir.mkdir(parents=True)
    global_dir.mkdir(parents=True)
    (ws_dir / "workspace.json").write_text(json.dumps({"folder": "file:///Users/test/modern-proj"}))
    # empty workspace DB (no composer.composerData)
    ws_conn = sqlite3.connect(str(ws_dir / "state.vscdb"))
    ws_conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
    ws_conn.commit()
    ws_conn.close()

    t0 = 1751360400000
    g_conn = sqlite3.connect(str(global_dir / "state.vscdb"))
    g_conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value BLOB)")
    g_conn.execute(
        "INSERT INTO cursorDiskKV VALUES (?, ?)",
        (
            "composerData:mod1",
            json.dumps(
                {
                    "composerId": "mod1",
                    "name": "Modern composer",
                    "createdAt": t0,
                    "lastUpdatedAt": t0 + 1000,
                    "workspaceIdentifier": {"id": "wsabc"},
                    "fullConversationHeadersOnly": [{"bubbleId": "b1"}, {"bubbleId": "b2"}],
                }
            ),
        ),
    )
    g_conn.execute(
        "INSERT INTO cursorDiskKV VALUES (?, ?)",
        (
            "bubbleId:mod1:b1",
            json.dumps({"type": 1, "text": "hello modern", "timingInfo": {"clientStartTime": t0}}),
        ),
    )
    g_conn.execute(
        "INSERT INTO cursorDiskKV VALUES (?, ?)",
        (
            "bubbleId:mod1:b2",
            json.dumps({"type": 2, "text": "hi back", "timingInfo": {"clientStartTime": t0 + 100}}),
        ),
    )
    g_conn.commit()
    g_conn.close()

    # workspace-local parse finds nothing (modern layout)
    assert cursor.parse_cursor_workspace(ws_dir, user_dir) == []

    sessions = cursor.parse_cursor_global(user_dir)
    assert len(sessions) == 1
    session, events = sessions[0]
    assert session["id"] == "cursor-composer-mod1"
    assert session["cwd"] == "/Users/test/modern-proj"
    assert [e["kind"] for e in events] == ["user", "assistant"]
    assert events[0]["text"] == "hello modern"
