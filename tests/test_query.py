import json

from threadkeeper import db, query


def _seed(conn):
    pid = db.upsert_project(conn, "/repo/one")
    session = {
        "id": "s1",
        "project_id": pid,
        "source": "claude-code",
        "file_path": "/tmp/s1.jsonl",
        "started_at": "2026-07-01T10:00:00Z",
        "ended_at": "2026-07-01T10:05:00Z",
        "first_prompt": "hello",
        "context_tokens": 100,
        "summary": "a summary",
        "usage": json.dumps({"claude-opus-4-8": {"input": 10, "output": 5}}),
    }
    events = [
        {"kind": "user", "text": "hi"},
        {"kind": "tool_use", "tool_name": "read_file", "tool_input": json.dumps({"path": "a.py"})},
    ]
    db.replace_session(conn, session, events)
    return pid


def test_projects_returns_dataframe(conn):
    _seed(conn)
    df = query.projects(conn)
    assert list(df["path"]) == ["/repo/one"]


def test_sessions_decodes_usage_json_column(conn):
    _seed(conn)
    df = query.sessions(conn)
    assert len(df) == 1
    usage = df.iloc[0]["usage"]
    assert isinstance(usage, dict)
    assert usage["claude-opus-4-8"]["input"] == 10


def test_messages_decodes_tool_input_json_column_and_filters_by_session(conn):
    _seed(conn)
    df = query.messages("s1", conn)
    assert len(df) == 2
    tool_row = df[df["kind"] == "tool_use"].iloc[0]
    assert isinstance(tool_row["tool_input"], dict)
    assert tool_row["tool_input"]["path"] == "a.py"


def test_messages_without_session_id_returns_all(conn):
    _seed(conn)
    df = query.messages(conn=conn)
    assert len(df) == 2
