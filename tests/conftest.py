from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from threadkeeper import db as tk_db

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    """Isolate THREAD_KEEPER_DATA_DIR to a temp dir for every test."""
    d = tmp_path / "thread-keeper-data"
    monkeypatch.setenv("THREAD_KEEPER_DATA_DIR", str(d))
    return d


@pytest.fixture
def conn(data_dir) -> sqlite3.Connection:
    connection = tk_db.connect()
    yield connection
    connection.close()


def _write_item_table(conn: sqlite3.Connection, key: str, value: dict) -> None:
    conn.execute("INSERT INTO ItemTable VALUES (?, ?)", (key, json.dumps(value)))


def _write_disk_kv(conn: sqlite3.Connection, key: str, value: dict) -> None:
    conn.execute("INSERT INTO cursorDiskKV VALUES (?, ?)", (key, json.dumps(value)))


@pytest.fixture
def cursor_fixture(tmp_path):
    """Build a minimal Cursor user dir (workspaceStorage + globalStorage) with
    one legacy chat tab and one composer session, mirroring Chronicle's
    ``test/make-cursor-fixture.mjs``. Returns ``(user_dir, ws_dir)``.
    """
    user_dir = tmp_path / "cursor-user"
    ws_dir = user_dir / "workspaceStorage" / "abc123"
    global_dir = user_dir / "globalStorage"
    ws_dir.mkdir(parents=True)
    global_dir.mkdir(parents=True)

    (ws_dir / "workspace.json").write_text(json.dumps({"folder": "file:///Users/test/health-analyst"}))

    t0 = 1751360400000  # 2026-07-01T09:00:00Z in epoch ms

    ws_conn = sqlite3.connect(str(ws_dir / "state.vscdb"))
    ws_conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
    _write_item_table(
        ws_conn,
        "workbench.panel.aichat.view.aichat.chatdata",
        {
            "tabs": [
                {
                    "tabId": "tab1",
                    "chatTitle": "Fix auth bug",
                    "lastSendTime": t0,
                    "bubbles": [
                        {"type": "user", "text": "Why does login fail with OAuth?", "timingInfo": {"clientStartTime": t0}},
                        {"type": "ai", "text": "The redirect URI is mismatched.", "modelType": "gpt-4", "timingInfo": {"clientStartTime": t0 + 5000}},
                    ],
                }
            ]
        },
    )
    _write_item_table(
        ws_conn,
        "composer.composerData",
        {
            "allComposers": [
                {
                    "composerId": "comp1",
                    "name": "Refactor dashboard",
                    "createdAt": t0 + 60000,
                    "fullConversationHeadersOnly": [{"bubbleId": "b1"}, {"bubbleId": "b2"}],
                }
            ]
        },
    )
    ws_conn.commit()
    ws_conn.close()

    g_conn = sqlite3.connect(str(global_dir / "state.vscdb"))
    g_conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value BLOB)")
    _write_disk_kv(
        g_conn,
        "bubbleId:comp1:b1",
        {"type": 1, "text": "Refactor the dashboard to use the new chart API", "timingInfo": {"clientStartTime": t0 + 60000}},
    )
    _write_disk_kv(
        g_conn,
        "bubbleId:comp1:b2",
        {
            "type": 2,
            "text": "Done - replaced Recharts wrappers.",
            "thinking": {"text": "Need to check chart imports first"},
            "toolResults": [{"toolName": "read_file", "args": {"path": "src/app/dashboard/page.tsx"}, "result": "export default ...", "toolCallId": "tc1"}],
            "timingInfo": {"clientStartTime": t0 + 65000},
        },
    )
    g_conn.commit()
    g_conn.close()

    return user_dir, ws_dir
