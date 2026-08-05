import json
import math

import numpy as np
import pandas as pd

from threadkeeper import db, models, query


def _assert_utc_datetime64(series: pd.Series) -> None:
    assert str(series.dtype) == "datetime64[us, UTC]", series.dtype


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
        "ended_at": "2026-07-01T10:05:00.123Z",
        "first_prompt": "hello",
        "context_tokens": 100 if with_usage else None,
        "summary": "a summary",
        "usage": usage,
    }
    events = [
        {"kind": "user", "text": "hi", "ts": "2026-07-01T10:00:00Z"},
        {
            "kind": "tool_use",
            "tool_name": "read_file",
            "tool_input": json.dumps({"path": "a.py"}),
            "ts": "2026-07-01T10:00:01.5Z",
        },
    ]
    db.replace_session(conn, session, events)
    return pid


def test_projects_returns_dataframe(conn):
    _seed(conn)
    df = query.projects(conn)
    assert list(df["path"]) == ["/repo/one"]


def test_projects_created_at_is_utc_timestamp(conn):
    _seed(conn)
    df = query.projects(conn)
    _assert_utc_datetime64(df["created_at"])
    assert pd.notna(df.iloc[0]["created_at"])


def test_sessions_decodes_usage_json_column(conn):
    _seed(conn)
    df = query.sessions(conn)
    assert len(df) == 1
    usage = df.iloc[0]["usage"]
    assert isinstance(usage, dict)
    assert usage["claude-opus-4-8"]["input"] == 10


def test_sessions_timestamp_columns_are_utc(conn):
    _seed(conn)
    df = query.sessions(conn)
    _assert_utc_datetime64(df["started_at"])
    _assert_utc_datetime64(df["ended_at"])
    row = df.iloc[0]
    assert row["started_at"] == pd.Timestamp("2026-07-01T10:00:00Z")
    assert row["ended_at"] == pd.Timestamp("2026-07-01T10:05:00.123Z")


def test_sessions_null_timestamps_become_nat(conn):
    pid = db.upsert_project(conn, "/repo/one")
    db.replace_session(
        conn,
        {
            "id": "s-null-ts",
            "project_id": pid,
            "source": "cursor",
            "file_path": "/tmp/s-null-ts.jsonl",
            "started_at": None,
            "ended_at": None,
        },
        [{"kind": "user", "text": "hi"}],
    )
    df = query.sessions(conn)
    row = df.iloc[0]
    _assert_utc_datetime64(df["started_at"])
    _assert_utc_datetime64(df["ended_at"])
    assert pd.isna(row["started_at"])
    assert pd.isna(row["ended_at"])


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


def test_sessions_has_timing_columns(conn):
    _seed(conn)  # events: user@10:00:00, tool_use@10:00:01.5 ; span 300.123s
    row = query.sessions(conn).iloc[0]
    assert row["n_turns"] == 1
    assert row["n_tool_calls"] == 1                       # claude-code has the tool_use
    assert row["model_sec"] == 1.5                        # user -> tool_use gap
    assert row["tool_exec_sec"] == 0.0                    # no tool_result
    assert row["human_idle_sec"] == 0.0
    assert abs(row["session_span_sec"] - 300.123) < 1e-6
    assert abs(row["active_sec"] - 300.123) < 1e-6        # span - idle


def test_sessions_n_turns_is_float_dtype(conn):
    _seed(conn)
    assert str(query.sessions(conn)["n_turns"].dtype) == "float64"


def test_sessions_empty_store_has_timing_columns(conn):
    df = query.sessions(conn)
    assert df.empty
    for col in ("model_sec", "tool_exec_sec", "human_idle_sec",
                "active_sec", "session_span_sec", "n_turns", "n_tool_calls"):
        assert col in df.columns


def test_sessions_preserves_tool_nan_for_cursor_without_tools(conn):
    pid = db.upsert_project(conn, "/repo/one")
    db.replace_session(conn, {"id": "cur", "project_id": pid, "source": "cursor",
        "file_path": "/tmp/cur.db", "started_at": "2026-07-01T10:00:00Z",
        "ended_at": "2026-07-01T10:00:10Z"},
        [{"kind": "user", "text": "hi", "ts": "2026-07-01T10:00:00Z"},
         {"kind": "assistant", "text": "yo", "ts": "2026-07-01T10:00:05Z"}])
    row = query.sessions(conn)
    row = row[row["id"] == "cur"].iloc[0]
    assert math.isnan(row["tool_exec_sec"])
    assert math.isnan(row["n_tool_calls"])
    assert row["n_turns"] == 1
    assert row["model_sec"] == 5.0


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
    _assert_utc_datetime64(long["started_at"])
    assert row["started_at"] == pd.Timestamp("2026-07-01T10:00:00Z")


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
    _assert_utc_datetime64(df["started_at"])
    _assert_utc_datetime64(df["ended_at"])


def test_messages_decodes_tool_input_json_column_and_filters_by_session(conn):
    _seed(conn)
    df = query.messages("s1", conn)
    assert len(df) == 2
    tool_row = df[df["kind"] == "tool_use"].iloc[0]
    assert isinstance(tool_row["tool_input"], dict)
    assert tool_row["tool_input"]["path"] == "a.py"


def test_messages_ts_is_utc_timestamp(conn):
    _seed(conn)
    df = query.messages(conn=conn)
    _assert_utc_datetime64(df["ts"])
    assert df.iloc[0]["ts"] == pd.Timestamp("2026-07-01T10:00:00Z")
    assert df.iloc[1]["ts"] == pd.Timestamp("2026-07-01T10:00:01.5Z")


def test_messages_without_session_id_returns_all(conn):
    _seed(conn)
    df = query.messages(conn=conn)
    assert len(df) == 2


def _msgs(rows):
    """rows: list of (session_id, seq, ts, kind, injected)."""
    df = pd.DataFrame(rows, columns=["session_id", "seq", "ts", "kind", "injected"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def test_session_timing_buckets_and_counts():
    # human(0s) -> assistant(+4s) -> tool_use(+0) -> tool_result(+2s) -> assistant(+3s) -> human(+30s)
    m = _msgs([
        ("s", 0, "2026-07-01T10:00:00Z", "user", 0),
        ("s", 1, "2026-07-01T10:00:04Z", "assistant", 0),
        ("s", 2, "2026-07-01T10:00:04Z", "tool_use", 0),
        ("s", 3, "2026-07-01T10:00:06Z", "tool_result", 0),
        ("s", 4, "2026-07-01T10:00:09Z", "assistant", 0),
        ("s", 5, "2026-07-01T10:00:39Z", "user", 0),
    ])
    t = query._session_timing(m, pd.Series({"s": "claude-code"})).loc["s"]
    assert t["tool_exec_sec"] == 2.0            # tool_use -> tool_result
    assert t["human_idle_sec"] == 30.0          # gap before the 2nd human
    assert t["model_sec"] == 4.0 + 0.0 + 3.0    # user->assistant, assistant->tool_use, tool_result->assistant
    assert t["n_turns"] == 2
    assert t["n_tool_calls"] == 1

def test_session_timing_ignores_injected_user():
    # injected user right after assistant must NOT open idle nor count as a turn
    m = _msgs([
        ("s", 0, "2026-07-01T10:00:00Z", "user", 0),
        ("s", 1, "2026-07-01T10:00:05Z", "assistant", 0),
        ("s", 2, "2026-07-01T10:00:05Z", "user", 1),   # injected (<0.5s)
        ("s", 3, "2026-07-01T10:00:08Z", "assistant", 0),
    ])
    t = query._session_timing(m, pd.Series({"s": "claude-code"})).loc["s"]
    assert t["n_turns"] == 1
    assert t["human_idle_sec"] == 0.0
    assert t["model_sec"] == 8.0

def test_session_timing_tool_nan_for_cursor_without_tools():
    m = _msgs([
        ("c", 0, "2026-07-01T10:00:00Z", "user", 0),
        ("c", 1, "2026-07-01T10:00:05Z", "assistant", 0),
    ])
    t = query._session_timing(m, pd.Series({"c": "cursor"})).loc["c"]
    assert np.isnan(t["tool_exec_sec"])
    assert np.isnan(t["n_tool_calls"])

def test_session_timing_tool_zero_for_claude_without_tools():
    m = _msgs([
        ("s", 0, "2026-07-01T10:00:00Z", "user", 0),
        ("s", 1, "2026-07-01T10:00:05Z", "assistant", 0),
    ])
    t = query._session_timing(m, pd.Series({"s": "claude-code"})).loc["s"]
    assert t["tool_exec_sec"] == 0.0
    assert t["n_tool_calls"] == 0


def test_session_timing_keeps_single_message_session():
    m = _msgs([("s", 0, "2026-07-01T10:00:00Z", "user", 0)])
    t = query._session_timing(m, pd.Series({"s": "claude-code"}))
    assert "s" in t.index
    row = t.loc["s"]
    assert row["model_sec"] == 0.0
    assert row["tool_exec_sec"] == 0.0
    assert row["human_idle_sec"] == 0.0
    assert row["n_turns"] == 1
    assert row["n_tool_calls"] == 0


def test_session_timing_keeps_all_nat_session():
    m = _msgs([("s", 0, None, "user", 0), ("s", 1, None, "assistant", 0)])
    t = query._session_timing(m, pd.Series({"s": "claude-code"}))
    assert "s" in t.index
    assert t.loc["s"]["n_turns"] == 1
    assert t.loc["s"]["model_sec"] == 0.0


def test_session_timing_partition_invariant():
    # buckets must sum to the message-timestamp span (R5: arithmetic partition)
    m = _msgs([
        ("s", 0, "2026-07-01T10:00:00Z", "user", 0),
        ("s", 1, "2026-07-01T10:00:04Z", "assistant", 0),
        ("s", 2, "2026-07-01T10:00:04Z", "tool_use", 0),
        ("s", 3, "2026-07-01T10:00:06Z", "tool_result", 0),
        ("s", 4, "2026-07-01T10:00:09Z", "assistant", 0),
        ("s", 5, "2026-07-01T10:00:39Z", "user", 0),
    ])
    t = query._session_timing(m, pd.Series({"s": "claude-code"})).loc["s"]
    span = 39.0  # last ts - first ts
    assert t["model_sec"] + t["tool_exec_sec"] + t["human_idle_sec"] == span


def test_session_timing_clips_negative_gaps():
    # out-of-order timestamps must clip to 0, never contribute a negative bucket
    m = _msgs([
        ("s", 0, "2026-07-01T10:00:05Z", "user", 0),
        ("s", 1, "2026-07-01T10:00:00Z", "assistant", 0),  # earlier than prev
    ])
    t = query._session_timing(m, pd.Series({"s": "claude-code"})).loc["s"]
    assert t["model_sec"] == 0.0
    assert t["human_idle_sec"] == 0.0


def test_session_timing_all_injected_zero_turns():
    # a session whose only user messages are injected has zero human turns and no idle
    m = _msgs([
        ("s", 0, "2026-07-01T10:00:00Z", "user", 1),      # injected
        ("s", 1, "2026-07-01T10:00:02Z", "assistant", 0),
        ("s", 2, "2026-07-01T10:00:03Z", "user", 1),      # injected
    ])
    t = query._session_timing(m, pd.Series({"s": "claude-code"})).loc["s"]
    assert t["n_turns"] == 0
    assert t["human_idle_sec"] == 0.0
