"""Pandas query helpers (F4.1): ``sessions()``, ``messages()``, ``projects()``,
``usage_long()``.

JSON columns (``sessions.usage``, ``messages.tool_input``) are auto-decoded
into dict-valued columns so notebooks never write SQL/JSON glue.

ISO timestamp columns stored as SQLite TEXT are coerced to timezone-aware
UTC (``datetime64[us, UTC]``): ``sessions.started_at`` / ``ended_at``,
``messages.ts``, ``projects.created_at``, ``usage_long.started_at``.

Analysis columns use a canonical snake_case vocabulary
(``input_tokens``, ``output_tokens``, ``cache_write_tokens``,
``cache_read_tokens``, ``cost_usd``, ``has_usage``) — never source-native
log key names. Missing usage → ``has_usage=False`` and NaN numerics
(unknown ≠ $0).
"""

from __future__ import annotations

import json
import sqlite3

import numpy as np
import pandas as pd

from . import db as _db
from .models import cache_write_tokens, cost_of, cost_of_model, token_totals_of

_NAN = float("nan")

_USAGE_LONG_COLUMNS = [
    "id",
    "source",
    "summary",
    "started_at",
    "model",
    "input_tokens",
    "output_tokens",
    "cache_write_tokens",
    "cache_read_tokens",
    "cost_usd",
]


def _connect(conn: sqlite3.Connection | None):
    return conn if conn is not None else _db.connect()


def _decode_json_column(series: pd.Series) -> pd.Series:
    def decode(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return None

    return series.map(decode)


def _as_utc_timestamps(df: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    """Coerce ISO / SQLite-text timestamp columns to ``datetime64[us, UTC]``.

    Uses ``format='ISO8601'`` so ``Z``, ``+00:00``, fractional seconds, and
    SQLite ``datetime('now')`` naive strings all parse; naive values are
    treated as UTC.
    """
    for col in columns:
        if col not in df.columns:
            continue
        # Pin microsecond resolution so empty / all-null frames don't
        # collapse to datetime64[s, UTC] while populated ones use [us].
        df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601").astype(
            "datetime64[us, UTC]"
        )
    return df


def _enrich_sessions(df: pd.DataFrame) -> pd.DataFrame:
    """Attach has_usage / cost_usd / token columns / models to a sessions frame."""
    if df.empty:
        df["has_usage"] = pd.Series(dtype=bool)
        df["cost_usd"] = pd.Series(dtype=float)
        df["input_tokens"] = pd.Series(dtype=float)
        df["output_tokens"] = pd.Series(dtype=float)
        df["cache_write_tokens"] = pd.Series(dtype=float)
        df["cache_read_tokens"] = pd.Series(dtype=float)
        df["models"] = pd.Series(dtype=object)
        return df

    has_usage: list[bool] = []
    cost_usd: list[float] = []
    input_tokens: list[float] = []
    output_tokens: list[float] = []
    cache_write: list[float] = []
    cache_read: list[float] = []
    models_col: list[str | None] = []

    for usage in df["usage"]:
        ok = isinstance(usage, dict) and len(usage) > 0
        has_usage.append(ok)
        if not ok:
            cost_usd.append(_NAN)
            input_tokens.append(_NAN)
            output_tokens.append(_NAN)
            cache_write.append(_NAN)
            cache_read.append(_NAN)
            models_col.append(None)
            continue
        c = cost_of(usage)
        cost_usd.append(c if c is not None else _NAN)
        totals = token_totals_of(usage) or {}
        input_tokens.append(float(totals.get("input_tokens", 0)))
        output_tokens.append(float(totals.get("output_tokens", 0)))
        cache_write.append(float(totals.get("cache_write_tokens", 0)))
        cache_read.append(float(totals.get("cache_read_tokens", 0)))
        models_col.append(",".join(usage.keys()))

    df["has_usage"] = has_usage
    df["cost_usd"] = cost_usd
    df["input_tokens"] = input_tokens
    df["output_tokens"] = output_tokens
    df["cache_write_tokens"] = cache_write
    df["cache_read_tokens"] = cache_read
    df["models"] = models_col
    return df


def _messages_for_timing(conn: sqlite3.Connection) -> pd.DataFrame:
    """Minimal message columns for timing — avoids SELECT * and the tool_input
    JSON decode that messages() does (sessions() calls this on every invocation)."""
    df = pd.read_sql_query(
        "SELECT session_id, seq, ts, kind, injected FROM messages ORDER BY session_id, seq",
        conn,
    )
    return _as_utc_timestamps(df, ("ts",))


def _attach_timing(df: pd.DataFrame, conn: sqlite3.Connection) -> pd.DataFrame:
    """Attach per-session timing columns (Task 3's ``_session_timing``) plus
    ``session_span_sec`` / ``active_sec`` to a sessions frame.

    ``active_sec = session_span_sec - human_idle_sec``. Sessions with no
    messages get 0.0 for ``model_sec``/``human_idle_sec``/``n_turns`` (not
    NaN) since a session with nothing to time is genuinely idle-free, not
    unmeasurable. ``tool_exec_sec``/``n_tool_calls`` are zero-filled only for
    those no-message sessions; when ``_session_timing`` reports a session but
    leaves its tool columns NaN (source can't reliably measure tools and the
    session has no tool events), that NaN is intentional and is preserved.
    """
    span = (df["ended_at"] - df["started_at"]).dt.total_seconds() if not df.empty else pd.Series(dtype=float)
    df["session_span_sec"] = span
    if df.empty:
        for c in _TIMING_COLS:
            df[c] = pd.Series(dtype=float)
        df["active_sec"] = pd.Series(dtype=float)
        return df
    msgs = _messages_for_timing(conn)
    timing = _session_timing(msgs, df.set_index("id")["source"])
    has_timing = df["id"].isin(timing.index)
    df = df.merge(timing, left_on="id", right_index=True, how="left")
    # never-NaN-from-_session_timing columns: safe to zero-fill (covers no-message sessions)
    df[["model_sec", "human_idle_sec"]] = df[["model_sec", "human_idle_sec"]].fillna(0.0)
    df["n_turns"] = df["n_turns"].fillna(0)
    # tool columns: zero ONLY for sessions absent from timing (no messages) AND
    # whose source reliably logs tools; message-less sessions from other sources
    # keep NaN (can't claim 0 tool calls when tools can't be reliably observed)
    absent_reliable = ((~has_timing) & df["source"].isin(_TOOL_RELIABLE_SOURCES)).to_numpy()
    df.loc[absent_reliable, ["tool_exec_sec", "n_tool_calls"]] = df.loc[
        absent_reliable, ["tool_exec_sec", "n_tool_calls"]
    ].fillna(0.0)
    df["active_sec"] = df["session_span_sec"] - df["human_idle_sec"]
    return df


def projects(conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """All projects as a DataFrame; ``created_at`` as UTC timestamps."""
    c = _connect(conn)
    df = pd.read_sql_query("SELECT * FROM projects ORDER BY id", c)
    return _as_utc_timestamps(df, ("created_at",))


def sessions(conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """All sessions as a DataFrame; ``usage`` decoded; analysis columns attached.

    Adds ``has_usage``, ``cost_usd``, ``input_tokens``, ``output_tokens``,
    ``cache_write_tokens``, ``cache_read_tokens``, ``models``. Missing usage
    yields ``has_usage=False`` and NaN numerics. ``started_at`` / ``ended_at``
    are timezone-aware UTC timestamps.

    Also attaches per-session **timing** columns (seconds), decomposing the
    span into mutually-exclusive buckets so callers define their own
    performance metrics: ``human_idle_sec`` (gaps before genuine human
    messages), ``model_sec`` (model latency + generation, incl. API
    errors/retries), ``tool_exec_sec`` (tool run time), ``active_sec``
    (= ``session_span_sec - human_idle_sec``), ``session_span_sec``, plus counts
    ``n_turns`` (genuine human turns) and ``n_tool_calls``. ``tool_exec_sec`` /
    ``n_tool_calls`` are NaN for a source that cannot reliably observe tools
    (e.g. Cursor) when a session shows none — never a misleading 0.
    """
    c = _connect(conn)
    df = pd.read_sql_query("SELECT * FROM sessions ORDER BY started_at", c)
    df = _as_utc_timestamps(df, ("started_at", "ended_at"))
    if "usage" in df.columns:
        df["usage"] = _decode_json_column(df["usage"])
    else:
        df["usage"] = None
    df = _enrich_sessions(df)
    return _attach_timing(df, c)


def usage_long(conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """One row per ``(session_id, model)`` for sessions with usage.

    Columns use the canonical analysis vocabulary only. Sessions without
    usage contribute no rows. ``started_at`` is timezone-aware UTC.
    """
    s = sessions(conn)
    rows: list[dict] = []
    if not s.empty:
        for _, row in s.iterrows():
            usage = row["usage"]
            if not isinstance(usage, dict) or not usage:
                continue
            for model, u in usage.items():
                u = u or {}
                c = cost_of_model(model, u)
                rows.append(
                    {
                        "id": row["id"],
                        "source": row["source"],
                        "summary": row["summary"],
                        "started_at": row["started_at"],
                        "model": model,
                        "input_tokens": u.get("input") or 0,
                        "output_tokens": u.get("output") or 0,
                        "cache_write_tokens": cache_write_tokens(u),
                        "cache_read_tokens": u.get("cacheRead") or 0,
                        "cost_usd": c if c is not None else _NAN,
                    }
                )
    df = pd.DataFrame(rows, columns=_USAGE_LONG_COLUMNS)
    return _as_utc_timestamps(df, ("started_at",))


_HUMAN = "user"
_TOOL_RELIABLE_SOURCES = frozenset({"claude-code", "codex"})
_TIMING_COLS = ["model_sec", "tool_exec_sec", "human_idle_sec", "n_turns", "n_tool_calls"]


def _session_timing(messages: pd.DataFrame, sources: pd.Series) -> pd.DataFrame:
    """Per-session timing buckets + counts, indexed by session_id.

    Each inter-message gap (ordered by seq) is attributed to one bucket:
    a gap before a genuine human message (kind=='user' and not injected) is
    idle; a gap after a tool_use is tool execution; everything else is model
    working time (latency, generation, incl. API errors). Buckets partition the
    span where tool events exist. Tool columns are NaN for a source that does
    not reliably log tools when the session shows none (avoids overclaiming 0).
    """
    if messages.empty:
        return pd.DataFrame(columns=_TIMING_COLS)
    m = messages.sort_values(["session_id", "seq"])
    injected = m["injected"].fillna(0).astype(bool)
    is_human = m["kind"].eq(_HUMAN) & ~injected
    g = m.groupby("session_id", sort=False)
    diff = g["ts"].diff().dt.total_seconds()
    gap = diff.clip(lower=0)
    prev_kind = g["kind"].shift(1)
    # gap BEFORE a genuine-human row == human idle; after a tool_use == tool exec
    bucket = np.where(is_human.to_numpy(), "human_idle_sec",
             np.where(prev_kind.eq("tool_use").to_numpy(), "tool_exec_sec", "model_sec"))
    bucket = np.where(diff.isna().to_numpy(), None, bucket)
    tb = (pd.DataFrame({"session_id": m["session_id"].to_numpy(), "gap": gap.to_numpy(), "bucket": bucket})
            .dropna(subset=["bucket"])
            .pivot_table(index="session_id", columns="bucket", values="gap", aggfunc="sum", fill_value=0.0))
    for c in ("model_sec", "tool_exec_sec", "human_idle_sec"):
        if c not in tb.columns:
            tb[c] = 0.0
    sid = m["session_id"]
    counts = pd.DataFrame({
        "n_turns": is_human.groupby(sid, sort=False).sum().astype(float),
        "n_tool_calls": m["kind"].eq("tool_use").groupby(sid, sort=False).sum().astype(float),
        "_has_tools": m["kind"].isin(["tool_use", "tool_result"]).groupby(sid, sort=False).any(),
    })
    # counts covers every session (unconditional groupby); tb only covers
    # sessions with at least one attributable gap. Join onto counts so a
    # single-message or all-NaT session is never dropped.
    out = counts.join(tb)
    for c in ("model_sec", "tool_exec_sec", "human_idle_sec"):
        out[c] = out[c].fillna(0.0)
    src = sources.reindex(out.index)
    unmeasurable = (~src.isin(_TOOL_RELIABLE_SOURCES)) & (~out["_has_tools"].fillna(False))
    out.loc[unmeasurable.to_numpy(), ["tool_exec_sec", "n_tool_calls"]] = np.nan
    return out[_TIMING_COLS]


def messages(session_id: str | None = None, conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """Messages as a DataFrame, optionally filtered to one session; ``tool_input``
    is decoded from JSON to a dict column; ``ts`` is timezone-aware UTC.
    """
    c = _connect(conn)
    if session_id is not None:
        df = pd.read_sql_query(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY seq", c, params=(session_id,)
        )
    else:
        df = pd.read_sql_query("SELECT * FROM messages ORDER BY session_id, seq", c)
    if "tool_input" in df.columns:
        df["tool_input"] = _decode_json_column(df["tool_input"])
    return _as_utc_timestamps(df, ("ts",))
