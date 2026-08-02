import json
import math

from threadkeeper import db, models, query


def _seed(conn, *, with_usage=True, session_id="s1", source="claude-code"):
    pid = db.upsert_project(conn, "/repo/one")
    usage = None
    if with_usage:
        usage = json.dumps(
            {
                "claude-opus-4-8": {
                    "input": 10,
                    "output": 5,
                    "cacheWrite5m": 2,
                    "cacheRead": 3,
                }
            }
        )
    session = {
        "id": session_id,
        "project_id": pid,
        "source": source,
        "file_path": f"/tmp/{session_id}.jsonl",
        "started_at": "2026-07-01T10:00:00Z",
        "ended_at": "2026-07-01T10:05:00Z",
        "first_prompt": "hello",
        "context_tokens": 100 if with_usage else None,
        "summary": "a summary",
        "usage": usage,
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


def test_sessions_enriches_cost_and_token_columns(conn):
    _seed(conn)
    df = query.sessions(conn)
    row = df.iloc[0]
    assert bool(row["has_usage"]) is True
    assert row["input_tokens"] == 10
    assert row["output_tokens"] == 5
    assert row["cache_write_tokens"] == 2
    assert row["cache_read_tokens"] == 3
    assert row["models"] == "claude-opus-4-8"
    expected = models.cost_of(row["usage"])
    assert row["cost_usd"] == expected


def test_sessions_missing_usage_is_nan_not_zero(conn):
    _seed(conn, with_usage=False, session_id="s-empty", source="cursor")
    df = query.sessions(conn)
    row = df[df["id"] == "s-empty"].iloc[0]
    assert bool(row["has_usage"]) is False
    assert math.isnan(row["cost_usd"])
    assert math.isnan(row["input_tokens"])
    assert math.isnan(row["output_tokens"])
    assert math.isnan(row["cache_write_tokens"])
    assert math.isnan(row["cache_read_tokens"])
    assert row["models"] is None


def test_usage_long_one_row_per_session_model(conn):
    _seed(conn)
    _seed(conn, with_usage=False, session_id="s-empty", source="cursor")
    long = query.usage_long(conn)
    assert len(long) == 1
    row = long.iloc[0]
    assert row["id"] == "s1"
    assert row["model"] == "claude-opus-4-8"
    assert row["input_tokens"] == 10
    assert row["output_tokens"] == 5
    assert row["cache_write_tokens"] == 2
    assert row["cache_read_tokens"] == 3
    assert row["cost_usd"] == models.cost_of_model(
        "claude-opus-4-8", {"input": 10, "output": 5, "cacheWrite5m": 2, "cacheRead": 3}
    )


def test_sessions_unpriced_model_has_usage_but_nan_cost(conn):
    pid = db.upsert_project(conn, "/repo/one")
    session = {
        "id": "s-unpriced",
        "project_id": pid,
        "source": "codex",
        "file_path": "/tmp/s-unpriced.jsonl",
        "started_at": "2026-07-01T10:00:00Z",
        "usage": json.dumps({"totally-unknown-model-xyz": {"input": 100, "output": 20}}),
    }
    db.replace_session(conn, session, [{"kind": "user", "text": "hi"}])
    row = query.sessions(conn).iloc[0]
    assert bool(row["has_usage"]) is True
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 20
    assert math.isnan(row["cost_usd"])


def test_sessions_empty_store_has_enrichment_columns(conn):
    df = query.sessions(conn)
    assert df.empty
    for col in (
        "has_usage",
        "cost_usd",
        "input_tokens",
        "output_tokens",
        "cache_write_tokens",
        "cache_read_tokens",
        "models",
    ):
        assert col in df.columns


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
