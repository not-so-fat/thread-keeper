"""Pandas query helpers (F4.1): ``sessions()``, ``messages()``, ``projects()``,
``usage_long()``.

JSON columns (``sessions.usage``, ``messages.tool_input``) are auto-decoded
into dict-valued columns so notebooks never write SQL/JSON glue.

Analysis columns use a canonical snake_case vocabulary
(``input_tokens``, ``output_tokens``, ``cache_write_tokens``,
``cache_read_tokens``, ``cost_usd``, ``has_usage``) — never source-native
log key names. Missing usage → ``has_usage=False`` and NaN numerics
(unknown ≠ $0).
"""

from __future__ import annotations

import json
import sqlite3

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


def projects(conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """All projects as a DataFrame."""
    c = _connect(conn)
    return pd.read_sql_query("SELECT * FROM projects ORDER BY id", c)


def sessions(conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """All sessions as a DataFrame; ``usage`` decoded; analysis columns attached.

    Adds ``has_usage``, ``cost_usd``, ``input_tokens``, ``output_tokens``,
    ``cache_write_tokens``, ``cache_read_tokens``, ``models``. Missing usage
    yields ``has_usage=False`` and NaN numerics.
    """
    c = _connect(conn)
    df = pd.read_sql_query("SELECT * FROM sessions ORDER BY started_at", c)
    if "usage" in df.columns:
        df["usage"] = _decode_json_column(df["usage"])
    else:
        df["usage"] = None
    return _enrich_sessions(df)


def usage_long(conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """One row per ``(session_id, model)`` for sessions with usage.

    Columns use the canonical analysis vocabulary only. Sessions without
    usage contribute no rows.
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
    return pd.DataFrame(rows, columns=_USAGE_LONG_COLUMNS)


def messages(session_id: str | None = None, conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """Messages as a DataFrame, optionally filtered to one session; ``tool_input``
    is decoded from JSON to a dict column.
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
    return df
