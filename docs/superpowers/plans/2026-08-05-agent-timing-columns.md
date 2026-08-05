# Agent-timing columns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-session timing-decomposition columns to `tk.sessions()` so users can define agent-performance metrics themselves.

**Architecture:** A parser flag (`messages.injected`) distinguishes genuine human messages from harness injections. `query.py` derives timing buckets at read time from the messages table (same pattern as the existing token enrichment), keyed on that flag. No opinionated rate columns ship.

**Tech Stack:** Python 3.12, SQLite (stdlib `sqlite3`), pandas, numpy, pytest, `uv`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-05-agent-timing-columns-design.md` — authoritative.
- One unit suffix `_sec` on every time column.
- Where a quantity is undefined for a session, the column is `NaN`, never `0` (R3).
- Source-neutral names/docstrings — no "Claude" in shared code (R4).
- Building blocks only — no rate columns like `tok_per_sec` (R1).
- Run tests with: `cd /Users/not_so_fat/workspace/codes/personal/thread-keeper && uv run pytest`.
- Detection sets live in ONE per-source place (single source of truth).
- **Refinement vs spec (tool NaN):** the Cursor parser *can* emit tool events (cursor.py:145), so the rule is per-session, not per-source: `tool_exec_sec`/`n_tool_calls` are real when the session has tool events; when it has none, they are `0` for tool-reliable sources (`claude-code`, `codex`) and `NaN` for others (Cursor's modern layout may not capture tools, so `0` there would overclaim).

---

### Task 1: Persist an `injected` flag on messages

**Files:**
- Modify: `src/threadkeeper/db.py` (SCHEMA messages table ~line 51; `replace_session` INSERT ~line 156-175)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `messages.injected INTEGER NOT NULL DEFAULT 0`; `replace_session` reads `event.get("injected")` (truthy → 1, else 0).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py — append
def test_replace_session_persists_injected_flag(conn):
    pid = db.upsert_project(conn, "/repo/one")
    session = {"id": "s-inj", "project_id": pid, "source": "claude-code",
               "file_path": "/tmp/s-inj.jsonl", "started_at": "2026-07-01T10:00:00Z"}
    events = [
        {"kind": "user", "text": "real human", "ts": "2026-07-01T10:00:00Z"},
        {"kind": "user", "text": "<task-notification>", "ts": "2026-07-01T10:00:01Z", "injected": True},
    ]
    db.replace_session(conn, session, events)
    rows = conn.execute(
        "SELECT text, injected FROM messages WHERE session_id='s-inj' ORDER BY seq"
    ).fetchall()
    assert rows[0]["injected"] == 0
    assert rows[1]["injected"] == 1
```

(Ensure `tests/test_db.py` imports `db`: it already does `from threadkeeper import db`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py::test_replace_session_persists_injected_flag -v`
Expected: FAIL — `sqlite3.OperationalError: table messages has no column named injected` (or KeyError on the new column).

- [ ] **Step 3: Implement**

In `db.py` SCHEMA, add the column to the `messages` table (after `model TEXT`):

```sql
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  seq INTEGER NOT NULL,
  uuid TEXT,
  ts TEXT,
  kind TEXT NOT NULL,
  text TEXT,
  tool_name TEXT,
  tool_input TEXT,
  tool_use_id TEXT,
  model TEXT,
  injected INTEGER NOT NULL DEFAULT 0
);
```

In `replace_session`, extend the messages INSERT column list and the row tuple:

```python
        conn.executemany(
            """INSERT INTO messages
                 (session_id, seq, uuid, ts, kind, text, tool_name, tool_input, tool_use_id, model, injected)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    session["id"], i, e.get("uuid"), e.get("ts"), e["kind"],
                    e.get("text"), e.get("tool_name"), e.get("tool_input"),
                    e.get("tool_use_id"), e.get("model"),
                    1 if e.get("injected") else 0,
                )
                for i, e in enumerate(events)
            ],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (all existing db tests still pass — new column has a default).

- [ ] **Step 5: Commit**

```bash
git add src/threadkeeper/db.py tests/test_db.py
git commit -m "feat(db): persist messages.injected flag"
```

---

### Task 2: Detect injected messages in the Claude Code parser

**Files:**
- Modify: `src/threadkeeper/parsers/claude_code.py` (`parse_claude_line`, the two `kind:"user"` emits at ~line 53 and ~line 73; add a module constant + helper near the top)
- Test: `tests/test_claude_code.py`

**Interfaces:**
- Consumes: raw JSONL entry dict `o` (has `isMeta`, `message.content`).
- Produces: user events carry `"injected": bool`. Non-user events are unaffected (default 0 at DB layer).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_claude_code.py — append. Import at top if missing:
# from threadkeeper.parsers.claude_code import parse_claude_line
def _user_line(text, *, is_meta=False):
    o = {"type": "user", "uuid": "u1", "timestamp": "2026-07-01T10:00:00Z",
         "message": {"content": text}}
    if is_meta:
        o["isMeta"] = True
    return o

def test_parse_marks_ismeta_user_as_injected():
    ev = parse_claude_line(_user_line("Stop hook feedback:\n...", is_meta=True))
    assert ev and ev[0]["kind"] == "user" and ev[0]["injected"] is True

def test_parse_marks_task_notification_as_injected():
    ev = parse_claude_line(_user_line("<task-notification>\n<task-id>x</task-id>"))
    assert ev and ev[0]["injected"] is True

def test_parse_marks_real_human_as_not_injected():
    ev = parse_claude_line(_user_line("how do they connect to playroom"))
    assert ev and ev[0]["injected"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_claude_code.py -k injected -v`
Expected: FAIL — `KeyError: 'injected'`.

- [ ] **Step 3: Implement**

Near the top of `claude_code.py` (after imports), add the single source of truth + helper:

```python
# Harness-injected user messages that are NOT human turns. `isMeta` catches
# Stop-hook feedback and caveats; these envelope prefixes catch the rest that
# arrive with isMeta=False (e.g. background task notifications). Verified against
# a 60-session scan (see specs/2026-08-05-agent-timing-columns-design.md).
CLAUDE_CODE_INJECTED_PREFIXES = (
    "<task-notification>",
    "<command-name>",
    "<local-command",
    "<system-reminder>",
    "Stop hook feedback",
    "[Request interrupted",
)


def _cc_is_injected(o: dict, text: str | None) -> bool:
    if o.get("isMeta"):
        return True
    return bool(text) and text.startswith(CLAUDE_CODE_INJECTED_PREFIXES)
```

In `parse_claude_line`, set `injected` on both user emits. The string-content branch (~line 53):

```python
        if isinstance(content, str):
            if content.startswith("<command-name>") or content.startswith("<local-command"):
                return events
            events.append({"uuid": o.get("uuid"), "ts": o.get("timestamp"), "kind": "user",
                           "text": content, "injected": _cc_is_injected(o, content)})
```

And the list-text branch (~line 73):

```python
                    events.append({"uuid": o.get("uuid"), "ts": o.get("timestamp"), "kind": "user",
                                   "text": block["text"], "injected": _cc_is_injected(o, block["text"])})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_claude_code.py -v`
Expected: PASS (existing claude_code tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/threadkeeper/parsers/claude_code.py tests/test_claude_code.py
git commit -m "feat(parser): flag injected user messages in Claude Code"
```

---

### Task 3: Timing decomposition helper in query.py

**Files:**
- Modify: `src/threadkeeper/query.py` (add `_session_timing`; add `import numpy as np`)
- Test: `tests/test_query.py`

**Interfaces:**
- Produces: `_session_timing(messages: pd.DataFrame, sources: pd.Series) -> pd.DataFrame` indexed by `session_id`, columns `["model_sec", "tool_exec_sec", "human_idle_sec", "n_turns", "n_tool_calls"]`. `messages` must have columns `session_id, seq, ts (datetime64), kind, injected`. `sources` maps `session_id -> source`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_query.py — append. Add near top: import numpy as np
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_query.py -k session_timing -v`
Expected: FAIL — `AttributeError: module 'threadkeeper.query' has no attribute '_session_timing'`.

- [ ] **Step 3: Implement**

Add `import numpy as np` to `query.py` imports, then:

```python
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
        "n_turns": is_human.groupby(sid, sort=False).sum(),
        "n_tool_calls": m["kind"].eq("tool_use").groupby(sid, sort=False).sum().astype(float),
        "_has_tools": m["kind"].isin(["tool_use", "tool_result"]).groupby(sid, sort=False).any(),
    })
    out = tb.join(counts)
    src = sources.reindex(out.index)
    unmeasurable = (~src.isin(_TOOL_RELIABLE_SOURCES)) & (~out["_has_tools"].fillna(False))
    out.loc[unmeasurable.to_numpy(), ["tool_exec_sec", "n_tool_calls"]] = np.nan
    return out[_TIMING_COLS]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_query.py -k session_timing -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/threadkeeper/query.py tests/test_query.py
git commit -m "feat(query): add _session_timing decomposition helper"
```

---

### Task 4: Wire timing columns into sessions()

**Files:**
- Modify: `src/threadkeeper/query.py` (`sessions`, `_enrich_sessions` empty branch)
- Test: `tests/test_query.py`

**Interfaces:**
- Consumes: `_session_timing` (Task 3), `messages()` (existing).
- Produces: `sessions()` gains `model_sec, tool_exec_sec, human_idle_sec, n_turns, n_tool_calls, session_span_sec, active_sec`. `active_sec = session_span_sec - human_idle_sec`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_query.py — append
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

def test_sessions_empty_store_has_timing_columns(conn):
    df = query.sessions(conn)
    assert df.empty
    for col in ("model_sec", "tool_exec_sec", "human_idle_sec",
                "active_sec", "session_span_sec", "n_turns", "n_tool_calls"):
        assert col in df.columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_query.py -k "timing_columns" -v`
Expected: FAIL — `KeyError: 'n_turns'`.

- [ ] **Step 3: Implement**

In `sessions()`, after `_enrich_sessions(df)` builds the frame, attach timing. Replace the final `return _enrich_sessions(df)` with:

```python
    df = _enrich_sessions(df)
    return _attach_timing(df, c)
```

Add `_attach_timing` (and extend the empty-frame path in `_enrich_sessions` or handle in `_attach_timing`):

```python
def _attach_timing(df: pd.DataFrame, conn) -> pd.DataFrame:
    span = (df["ended_at"] - df["started_at"]).dt.total_seconds() if not df.empty else pd.Series(dtype=float)
    df["session_span_sec"] = span
    if df.empty:
        for c in _TIMING_COLS:
            df[c] = pd.Series(dtype=float)
        df["active_sec"] = pd.Series(dtype=float)
        return df
    msgs = messages(conn=conn)
    timing = _session_timing(msgs, df.set_index("id")["source"])
    df = df.merge(timing, left_on="id", right_index=True, how="left")
    # sessions with no messages -> 0 buckets / counts
    df[["model_sec", "tool_exec_sec", "human_idle_sec"]] = df[["model_sec", "tool_exec_sec", "human_idle_sec"]].fillna(0.0)
    df[["n_turns", "n_tool_calls"]] = df[["n_turns", "n_tool_calls"]].fillna(0)
    df["active_sec"] = df["session_span_sec"] - df["human_idle_sec"]
    return df
```

Note: `messages(conn=conn)` returns `ts` already as `datetime64[us, UTC]` and includes `injected` (SELECT *). Confirm `injected` is present; if a legacy DB predates Task 1 the column exists after `init_schema`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_query.py -v`
Expected: PASS (all query tests, including the pre-existing ones).

- [ ] **Step 5: Full suite + commit**

Run: `uv run pytest`
Expected: PASS (whole suite).

```bash
git add src/threadkeeper/query.py tests/test_query.py
git commit -m "feat(query): expose per-session timing columns in sessions()"
```

---

### Task 5: Migration — backfill existing store (operational, gated)

**Files:** none (operational step run against the real `~/.thread-keeper/thread-keeper.db`).

**Interfaces:** none.

- [ ] **Step 1: Confirm before mutating real data**

Re-collection re-parses all transcripts and rewrites the messages table (whole-session replace), setting `injected` correctly. It mutates the user's real store, so **ask the user before running.**

- [ ] **Step 2: Re-collect**

Run: `cd /Users/not_so_fat/workspace/playgrounds/thread-keeper && uv run thk collect --sweep`
Expected: sweep re-ingests changed/known files; `thk status` row counts unchanged, `injected` now populated.

- [ ] **Step 3: Verify in a notebook**

```python
import threadkeeper as tk
m = tk.messages()
print(m["injected"].mean())                 # > 0 : some flagged
s = tk.sessions()
print(s[["source","n_turns","human_idle_sec","model_sec","tool_exec_sec","active_sec"]].head())
```
Expected: `n_turns` now reflects genuine human turns (much lower than raw `kind=='user'` counts); Cursor rows show `tool_exec_sec`/`n_tool_calls` as NaN.

---

## Self-Review

**Spec coverage:**
- Detection (hybrid, per-source, one place) → Task 2 (`CLAUDE_CODE_INJECTED_PREFIXES` + `_cc_is_injected`); Cursor/Codex need no code (verified-empty / no corpus) — the flag defaults to 0.
- Schema `messages.injected` + migration → Task 1 (schema/persist) + Task 5 (re-collect).
- Gap decomposition keyed on genuine human → Task 3.
- Columns (`human_idle_sec, active_sec, model_sec, tool_exec_sec, n_turns, n_tool_calls, session_span_sec`) + NaN-not-0 + partition + empty-store → Task 3 & 4.
- No rate columns / no msg_span → honored (not added).
- Tests (detection, decomposition, source-awareness, invariant, edge cases, empty store) → Tasks 2–4.

**Placeholder scan:** none — every step has real code/commands.

**Type consistency:** `_session_timing(messages, sources) -> DataFrame[_TIMING_COLS]` used identically in Task 3 (definition) and Task 4 (`_attach_timing`). `injected` int 0/1 in Task 1 → `.fillna(0).astype(bool)` in Task 3. Column names match the spec table verbatim.

**One deviation from spec, flagged:** tool NaN is per-session (real when tools present; else 0 for cc/codex, NaN for cursor) rather than blanket per-source, because the Cursor parser can emit tools — a strict improvement in honesty, noted in Global Constraints.
