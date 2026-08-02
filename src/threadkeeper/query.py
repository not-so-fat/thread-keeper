"""Pandas query helpers (F4.1): ``sessions()``, ``messages()``, ``projects()``.

JSON columns (``sessions.usage``, ``messages.tool_input``) are auto-decoded
into dict-valued columns so notebooks never write SQL/JSON glue.
"""

from __future__ import annotations

import json
import sqlite3

import pandas as pd

from . import db as _db


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


def projects(conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """All projects as a DataFrame."""
    c = _connect(conn)
    return pd.read_sql_query("SELECT * FROM projects ORDER BY id", c)


def sessions(conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """All sessions as a DataFrame; ``usage`` is decoded from JSON to a dict column."""
    c = _connect(conn)
    df = pd.read_sql_query("SELECT * FROM sessions ORDER BY started_at", c)
    if "usage" in df.columns:
        df["usage"] = _decode_json_column(df["usage"])
    return df


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
