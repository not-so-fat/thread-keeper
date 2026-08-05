# thread-keeper

A local-first Python CLI that **automatically collects the logs of how you
work with AI coding agents** (Claude Code, Codex, Cursor), normalizes them
into one queryable SQLite store, and opens them in Jupyter — so you own your
agent-usage data and can find where your instructions or setup are wasteful
or inconsistent.

No web UI, no daemon, no cloud, no network calls. Just: **collect → normalize
→ analyze.**

The product name is **thread-keeper**. The PyPI / install distribution name is
[`thk`](https://pypi.org/project/thk/). The Python import is `threadkeeper`.
How you run the CLI depends on the install path:

- `uv tool install thk` / `pip install thk` → `thk` or `thread-keeper` on PATH
- `uvx thk …` → one-shot; prefix every invocation with `uvx thk`
- clone / `uv sync` → `uv run thread-keeper` (or `uv run thk`)

See [`docs/PRD.md`](docs/PRD.md) for the full product spec and
[`docs/contracts/`](docs/contracts/) for the JSON Schemas of every shape that
crosses a boundary.

## Install

```bash
# From PyPI (puts `thk` and `thread-keeper` on PATH):
uv tool install thk
# or: pip install thk

# From a clone (editable):
git clone <this-repo>
cd thread-keeper
uv sync
# Optional: put `thread-keeper` / `thk` on PATH for agent hooks (required
# before install-hooks if you want a bare name; install-hooks also writes an
# absolute path so SessionEnd works after `uv sync` alone).
uv tool install -e .
```

Requires Python ≥3.11 (pinned to 3.14 in `.python-version`) and
[uv](https://docs.astral.sh/uv/).

## Quickstart

```bash
# Backfill your ENTIRE existing history from Claude Code / Codex / Cursor —
# no "only since install" cutoff.
#
# After `uv tool install thk` (or `pip install thk`):
thk collect --sweep
# One-shot without installing on PATH:
# uvx thk collect --sweep
# From a clone (no tool install):
# uv run thread-keeper collect --sweep

# See what's in the store.
thk status
# uvx thk status
# uv run thread-keeper status

# Optional: write SessionEnd / notify hook configs (backs up existing files
# first). Commands use an absolute path to this install so they work even
# when the CLI is not on PATH. Revert with `uninstall-hooks`.
thk install-hooks
# thk uninstall-hooks
# uvx thk install-hooks
# uv run thread-keeper install-hooks
```

```python
import threadkeeper as tk

sessions = tk.sessions()             # DataFrame + has_usage / cost_usd / token cols
sessions.loc[sessions["has_usage"]].sort_values("cost_usd", ascending=False)
tk.usage_long().groupby("model")["cost_usd"].sum()
sid = sessions.iloc[0]["id"]
messages = tk.messages(sid)          # DataFrame, one row per normalized event
tk.cost_of(sessions.iloc[0]["usage"])
```

**API note (`cost_of` / `cost_breakdown_of`):** these return `None` when usage is
missing/empty **or** every model in the blob is unpriced (unknown ≠ `$0`; previously
empty usage returned `0.0`). Prefer `sessions["cost_usd"]` / filter `has_usage`, or
handle `None` before arithmetic.

Starter analysis notebooks live in [`notebooks/`](notebooks/) — see that
folder's README for setup with Jupyter.

## Session timing columns

`tk.sessions()` splits each session's wall-clock into mutually-exclusive buckets
so you define "performance" yourself — throughput, latency, tool overhead —
instead of the library imposing one. Where a session has tool events and
message timestamps are monotonic in `seq` order,
`human_idle_sec + model_sec + tool_exec_sec` equals the message-timestamp span
(which is `session_span_sec` when `started_at`/`ended_at` match first/last
message times). Out-of-order clocks clip negative gaps to zero, so the sum can
undershoot.

| column | definition |
|---|---|
| `human_idle_sec` | Time waiting on **you** — Σ gaps before a genuine human message. Excluded from agent-active time. |
| `model_sec` | Agent working: model latency + generation + thinking, **including API errors/retries**. |
| `tool_exec_sec` | Tool run time (`tool_use` → `tool_result`). |
| `active_sec` | Agent-active wall clock = `session_span_sec − human_idle_sec`. |
| `session_span_sec` | `ended_at − started_at`. |
| `n_turns` | Count of **genuine human** turns. |
| `n_tool_calls` | Count of `tool_use` events. |

"Genuine human" excludes harness injections (Stop-hook feedback, task
notifications, `isMeta`, …), flagged per message by `messages.injected` — without
it, ~75% of Claude Code `user` messages would be miscounted as human turns.
`tool_exec_sec` / `n_tool_calls` are `NaN`, never `0`, for a source that cannot
reliably observe tools (e.g. Cursor) when a session shows none — an honest
"unmeasurable," not a false zero.

Compose your own metric from the raw columns — e.g. throughput on model time:

```python
s = tk.sessions()
s["tokens_per_sec"] = s["output_tokens"] / s["model_sec"].where(s["model_sec"] > 0)
```

(These columns need `injected` populated; an existing store predating the field
backfills with `thk collect --sweep --force` — see [CLI](#cli).)

## CLI

`thk` and `thread-keeper` are the same CLI (both console scripts). Examples
below use `thread-keeper`; substitute `thk` after a PyPI tool install.

```
thread-keeper collect --source <claude-code|codex|cursor> --session-id <id> [--transcript <path>]
thread-keeper collect --source <claude-code|cursor> --from-stdin          # Claude Code / Cursor SessionEnd (JSON on stdin)
thread-keeper collect --source codex --from-notify-argv                   # Codex notify (idle-heuristic)
thread-keeper collect --sweep [--force] [--source <...>] [--claude-root <dir>] [--codex-root <dir>] [--cursor-root <dir>] [--host <label>]
thread-keeper install-hooks [--tool <claude-code|cursor|codex> ...]
thread-keeper uninstall-hooks [--tool <claude-code|cursor|codex> ...]
thread-keeper status
```

- The **fast path** is what hooks invoke: either `--session-id`/`--transcript`,
  or `--from-stdin` (Claude Code / Cursor `SessionEnd` JSON payload), or
  `--from-notify-argv` (Codex `notify`, no session id — reparses the newest
  rollout). Always exits 0 (must never block the agent tool).
- `--sweep` is the **backstop + cold-start backfill**: walks all three
  sources' log dirs and ingests anything changed since the last run,
  including your entire pre-existing history on a fresh store.
- `--force` (with `--sweep`) re-parses **every** file, ignoring the
  change-detection watermark. Use it to backfill a parser/schema change into
  an existing store — e.g. after an upgrade that adds a per-message field:
  `thread-keeper collect --sweep --force`.
- Source roots (`--claude-root`/`--codex-root`/`--cursor-root`, or the
  matching `THREAD_KEEPER_CLAUDE_ROOT`/`_CODEX_ROOT`/`_CURSOR_ROOT` env vars)
  plus `--host <label>` let you consolidate logs copied in from another
  laptop into the same store, correctly attributed by origin machine.

## Configuration

| Setting | Flag | Env var | Default |
|---|---|---|---|
| Data dir | — | `THREAD_KEEPER_DATA_DIR` | `~/.thread-keeper/` |
| Claude Code root | `--claude-root` | `THREAD_KEEPER_CLAUDE_ROOT` | `~/.claude/projects` |
| Codex root | `--codex-root` | `THREAD_KEEPER_CODEX_ROOT` | `~/.codex/sessions` |
| Cursor root | `--cursor-root` | `THREAD_KEEPER_CURSOR_ROOT` | platform Cursor user dir |

The SQLite store lives at `<data-dir>/thread-keeper.db`; `install-hooks`
backs up any existing hook config it overwrites to
`<data-dir>/backups/`.

## Design invariants

- **Offline.** No network calls anywhere, ever.
- **Read-only on foreign logs.** Cursor's `state.vscdb` is always
  snapshot-copied (with its `-wal`/`-shm` files) before being read; the
  original is never opened read-write.
- **Whole-session invariant.** A changed file is always re-parsed *whole* and
  replaces its prior session — never a partial/appended read.
- **Idempotent.** Re-running any collect is a no-op net of row identity; a
  session rename and its origin `host` label both survive re-import.

See `docs/PRD.md` §6 for the full set of design principles.

## Development

```bash
uv sync
uv run pytest
uv run thread-keeper --help
```

See [`CLAUDE.md`](CLAUDE.md) for how this repo is meant to be extended
(spec-driven, one Req at a time, contracts used verbatim).

## Credits / prior art

thread-keeper is a from-scratch Python redraw of the offline log-extraction
core of **[Chronicle](https://github.com/chizhangucb/chronicle)** by
[@chizhangucb](https://github.com/chizhangucb), a JS/Electron "time machine"
for AI coding-agent sessions. Chronicle already solved parsing Claude Code,
Codex, and Cursor's session logs offline; thread-keeper ports that extraction
logic (schema, parsers, pricing tables) to Python and drops everything else
(the desktop UI, live streaming, replay, sharing) to leave a lightweight,
notebook-first collector. Thanks to [@chizhangucb](https://github.com/chizhangucb)
and the Chronicle project for the open-source reference implementation this
work stands on.
