# Agent-timing columns for `sessions()` — design

**Status:** approved design, pre-implementation
**Date:** 2026-08-05
**Reviewed against:** the yusuke lens (round 1 → fixes folded in)

## One job + audience

Give a **source-neutral** timing decomposition, one row per session, so a thk
user can define "agent performance" (throughput, latency, tool overhead)
*themselves*. thk ships the decomposed building blocks, never an opinionated
metric. Audience: notebook analysts querying `tk.sessions()`.

## Problem

`tk.sessions()` exposes tokens and cost but no *time* a user can divide by. Wall
clock (`ended_at − started_at`) is useless as a denominator because it is
dominated by the human reading and typing between turns. To measure the agent we
need the human-idle time removed, and the remaining agent time attributed to
where it went.

Two facts from the current store (843 sessions) make a naive split wrong, and the
design exists to handle them:

1. **`kind=='user'` ≠ a human sent it.** In Claude Code, 77.9% of
   `assistant→user` gaps are under 1 second; the fast ones are harness
   injections (`Stop hook feedback`, `<task-notification>`), not human replies.
   Counting them as turns or as idle boundaries corrupts any human-derived
   column.
2. **Sources are not uniform.** Cursor logs carry zero `tool_use`/`tool_result`
   events; Codex mirrors Claude Code. A single rule applied blindly makes columns
   mean different things per source.

## Reasoning rules (invariants every column obeys)

- **R1 — building blocks only.** No opinionated rate columns (`tok_per_sec`,
  `sec_per_turn`, …). The user composes those.
- **R2 — traceable.** Every column's rule is validated against real transcript
  data, not asserted.
- **R3 — per-source honesty.** Where a quantity is undefined for a source, the
  column is `NaN`, never `0`. A column is comparable only *within* a source.
- **R4 — one vocabulary.** Source-neutral names; no engine name ("Claude") in a
  library that also ingests Cursor and Codex. One unit suffix (`_sec`) on every
  time column.
- **R5 — partition, honestly labelled.** The time buckets partition the session
  span arithmetically; that invariant proves the *split adds up*, not that the
  *labels* are right — R2 covers the labels.

## Detection: human vs injected (parser + schema)

No single structural field separates human from injected (scan of 60 recent
Claude Code sessions: `Stop hook feedback` is `isMeta=True`, but
`<task-notification>` is `isMeta=False`). The honest discriminator is a hybrid,
defined **per source** in one place:

```
injected(entry) = isMeta_equivalent(entry)
               OR content starts with a known injection envelope for this source
```

| source | detection | status |
|---|---|---|
| claude-code | `isMeta==True` OR content starts with one of: `<task-notification>`, `<command-name>`, `<local-command`, `<system-reminder>`, `Stop hook feedback`, `[Request interrupted` | enumerated from a 60-session scan |
| cursor | `False` — no injected user messages observed (288 user rows; fast-gap samples are genuine short human commands) | verified-empty |
| codex | envelope set empty; `isMeta`-equivalent applied if the format exposes one | under-corpus (1 session, 3 user rows) — revisit when corpus grows |

The envelope sets live in **one per-source table** (single source of truth), not
scattered string checks. Injected messages are **kept** in the store (still
visible in `messages()`), only flagged — they are not silently dropped, so the
timeline stays inspectable.

**Schema change.** Add `messages.injected INTEGER NOT NULL DEFAULT 0`. The
existing parser already returns *nothing* for some injected forms
(`<command-name>`, `<system-reminder>`, `isSidechain`); those keep being dropped.
The new flag captures the forms the parser currently lets through as `kind=='user'`.

**Migration.** `isMeta` is not stored today. The parser must persist `injected`,
then a **forced** re-ingest sets it correctly for existing sessions. A plain
`thk collect --sweep` does **not** suffice — it skips unchanged files via the
change-detection watermark, so legacy rows keep `injected=0`. Use
`thk collect --sweep --force` (bypasses the watermark; re-parses every file).
Existing rows cannot be back-filled from stored text alone (the
`isMeta=True`-without-envelope cases are unrecoverable post-hoc), so a forced
re-collection is required, not optional.

## Query: timing decomposition (`query.py`, always on in `sessions()`)

Order each session's messages by `seq`. Attribute the gap *before* each message
to exactly one bucket:

| gap where… | bucket |
|---|---|
| next msg is a **genuine human** (`kind=='user' AND injected==0`) | `human_idle_sec` |
| previous msg is a `tool_use` | `tool_exec_sec` |
| everything else | `model_sec` |

Negative gaps (clock skew) clip to 0; each session's first message has no gap.

`sessions()` gains a dependency on the `messages` table (it currently reads only
`sessions`); accepted for the always-on surface.

## Columns added

| column | meaning | claude-code | codex | cursor |
|---|---|:--:|:--:|:--:|
| `human_idle_sec` | Σ gaps before a genuine human message | ✓ | ✓ | ✓ |
| `active_sec` | `session_span_sec − human_idle_sec` (agent-active wall clock) | ✓ | ✓ | ✓ |
| `model_sec` | agent working: latency + generation + thinking, **incl. API errors/retries** | ✓ | ✓ | = `active_sec` |
| `tool_exec_sec` | tool run time (`tool_use → tool_result`) | ✓ | ✓ | **NaN** |
| `n_turns` | count of genuine human messages | ✓ | ✓ | ✓ |
| `n_tool_calls` | count of `tool_use` | ✓ | ✓ | **NaN** |
| `session_span_sec` | `ended_at − started_at` | ✓ | ✓ | ✓ |

`active_sec` is defined as `span − human_idle_sec` (not `model + tool`) so a
source with no tool events (Cursor, where `tool_exec_sec` is NaN) still has a
usable agent-active denominator. Where tools exist,
`model_sec + tool_exec_sec + human_idle_sec == session_span_sec` (R5). For Cursor,
`model_sec + human_idle_sec == session_span_sec` and `model_sec == active_sec`.

No rate columns ship (R1). No `msg_span` column (redundant with
`session_span_sec`).

## Testing (TDD)

Unit, on synthetic frames:

- **Detection** — per source: `isMeta` true → injected; each Claude Code envelope
  prefix → injected; a plain human string → not injected; Cursor human command →
  not injected.
- **Decomposition** — a model-only turn; a tool turn (`tool_exec_sec` = the
  `tool_use→tool_result` gap); an injected user message between assistant and the
  real human does **not** open a `human_idle_sec` gap and does **not** increment
  `n_turns`.
- **Source-awareness** — a Cursor-shaped session yields `tool_exec_sec` /
  `n_tool_calls` = NaN and `model_sec == active_sec`.
- **Partition invariant** — buckets sum to `session_span_sec` where tools exist.
- **Edge cases** — 0- and 1-message sessions (all buckets 0/NaN as specified);
  out-of-order timestamps (clipped); an all-injected session (`n_turns == 0`).

Integration: `sessions()` exposes every column with the right dtype on a
populated DB and on an empty DB.

## Out of scope

- Per-turn (as opposed to per-session) timing.
- Rate/throughput columns — the user's to define.
- Enumerating Codex injection envelopes — deferred until the store holds a
  representative Codex corpus.
