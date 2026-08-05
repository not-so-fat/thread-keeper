---
title: thread-keeper — PRD — Agent-Session Analysis
date: 2026-08-02
area: personal
type: prd
status: keep
tags:
  - idea
  - side_projects
  - thread-keeper
  - agent-usage
  - observability
related-to:
  - "[[2026-07-31-personal-project-vision]]"
  - "[[2026-07-15 Chronicle — Opik-cipx Comparison + Token Attribution Without a Proxy]]"
sources:
  - "Chronicle repo (JS reference implementation) — /Users/not_so_fat/workspace/codes/chronicle"
  - "Claude Code hooks (official) — https://code.claude.com/docs/en/hooks"
  - "Cursor hooks (official) — https://cursor.com/docs/hooks"
  - "Codex config/notify + hooks (official) — https://developers.openai.com/codex/config-reference , https://developers.openai.com/codex/hooks"
---

# thread-keeper — PRD (Agent-Session Analysis)

> **One-liner:** A local-first Python tool that **automatically collects the logs of how you work with AI coding agents (Claude Code, Codex, Cursor), normalizes them into one queryable store, and opens them in a Jupyter notebook** — so you own your usage data and can find where your agent usage goes wrong.

> **Name note:** `thread-keeper` is a *repurposed* name. The retired legal-AI "ThreadKeeper" ([[ThreadKeeper - Similar Products]]) is a **different, dropped** project — this PRD is a fresh start, no continuity.

**Success criteria (time-boxed, v1 = ~4 weeks):** one `thread-keeper collect --sweep` on a fresh store backfills the **entire existing history** of Claude Code / Codex / Cursor sessions — including log dirs copied from other laptops — and subsequent runs pick up new sessions, all idempotently with zero network calls; a starter Jupyter notebook loads the store and answers "which of my sessions had the most correction/re-prompt churn" without any bespoke parsing.

---

## 1. Product overview

thread-keeper is the **data-ownership + introspection** layer of the personal "how to wield AI agents better" stack ([[2026-07-31-personal-project-vision]]). That vision names the precious asset being lost to platforms: **the data about *how* you work with AI — your improving process.** thread-keeper captures that asset locally and makes it analyzable.

- **What it is:** a headless Python CLI + a Jupyter analysis surface. No web UI, no daemon, no cloud.
- **What it is not (yet):** an automated problem-detector. v1 gives you the *substrate + a manual notebook*; the detectors are the documented **north star** (§6, §US-6), hand-run in notebooks first because early work is experimental.
- **Why now:** the extraction is a solved problem — Chronicle (the author's JS "time machine") already parses all six agent-log sources offline (Claude Code / Codex / Cursor plus OpenCode / Gemini / Copilot — **three ported in v1**, §10); and the [[2026-07-15 Chronicle — Opik-cipx Comparison + Token Attribution Without a Proxy]] analysis already worked out that token/cost/waste attribution is reconstructable **offline, without a proxy**. thread-keeper is the *lightweight, Python, notebook-first* redraw of that core, dropping everything heavy (Electron, live SSE, replay, MCP/skills hubs, sharing) to leave exactly: **collect → normalize → analyze.**
- **Through-line:** the personal project's deepest value is **consistency** — AI should make one's work *less random, more manageable*. Detecting *how users instruct* badly and *when knowledge is stale* is a consistency instrument. That is what this data is *for*.

---

## 2. Target users & roles

**Primary persona — "the sovereign operator" (the author, then ~10 respected peers).** A technically strong AI-agent power user who wants to *own and learn from* their own usage data, offline, and is comfortable in a Jupyter notebook. Not a dashboard consumer — a data analyst of their own behavior.

*"I am a heavy Claude-Code/Codex/Cursor user. I want my session history collected automatically and sitting in a local table I can query, because right now that data is locked inside each tool and I lose it — and I can't see where my agent usage is wasteful or inconsistent."*

| Role | Goal | v1 surface |
|---|---|---|
| **Operator (analyst)** | Own the data; explore it to improve how they instruct agents | CLI `collect` + Jupyter notebooks + query helpers |
| **Automation (cron/launchd/hook)** | Keep the store current with no human action | CLI `collect --sweep` / hook fast-path |
| **Peer adopter** (deferred, ~3-yr goal) | Run the same tool on their own machine | Same CLI; live cross-machine *sync* **out of scope**, but consolidating your own multi-laptop history is in v1 (§10, US-7) |

**Voice/naming rules:** the product is `thread-keeper` (kebab-case; matches the `agent-deck`/`agent-dealer` tooling family). A *session* = one agent conversation/thread. A *message* = one normalized event. Avoid "trace/span" (that's Opik's proxy vocabulary) and avoid "time machine/replay" (that's Chronicle's; explicitly cut here).

---

## 3. User stories (testable)

Each story is one loop the implementation must close. `[v1]` unless marked `[deferred]`.

### US-1 — Automatic collection on session end `[v1]`
**As an** operator, **I want** a finished agent session ingested without manual action, **so that** the store stays current on its own.
- [ ] A Claude Code `SessionEnd` hook invokes `thread-keeper collect --source claude-code --from-stdin` (stdin JSON with `session_id` + `transcript_path` per §7.3) and the session appears in the store. Equivalent: `--session-id <id> --transcript <path>`.
- [ ] Re-running the same invocation produces **no duplicate** rows (idempotent per `F2.3`).
- [ ] A failing/slow collector **never blocks** the agent tool (fire-and-forget; hook exits 0 regardless).

### US-2 — Scheduled backstop sweep + cold-start backfill `[v1]`
**As an** automation, **I want** a periodic sweep that ingests any session the hook missed **and, on first run, the entire pre-existing history**, **so that** crashes/force-kills/unconfigured machines are covered and my existing data is utilized from day one.
- [ ] `thread-keeper collect --sweep` walks all three sources' log dirs and ingests everything changed since last run.
- [ ] **First sweep on a fresh store backfills the ENTIRE existing history** — no "only since install" cutoff (`F3.6`).
- [ ] Sweep is incremental: unchanged files are skipped via the change-detection watermark (`F3.3`).
- [ ] Running the sweep twice back-to-back writes zero net rows the second time.

### US-7 — Consolidate existing data from several laptops `[v1]`
**As an** operator with years of sessions across multiple machines, **I want** to point the collector at log dirs copied from another laptop and merge them into one store, **so that** I analyze my whole history, not just this machine's.
- [ ] Source roots are configurable (`--claude-root`/`--codex-root`/`--cursor-root` + env), so a sweep can target logs copied from another machine, e.g. an external drive (`F3.7`).
- [ ] Sessions carry an **origin-machine `host`** label (set via `--host` when ingesting copied-in roots, else the local hostname), so cross-laptop data stays correctly attributed and merging never collides (source-native session ids are globally unique).
- [ ] Re-importing the same other-machine logs is idempotent (`F2.3`).

### US-3 — Unified multi-source store `[v1]`
**As an** operator, **I want** Claude Code, Codex, and Cursor sessions in one normalized schema, **so that** I can analyze across tools with one query.
- [ ] A single `sessions` table holds rows from all three sources, distinguished by `source`, with source-prefixed ids that never collide (`F1.4`).
- [ ] Cursor's WAL DB is read via a `-wal`/`-shm` snapshot copy; the original is never written (`F1.3`, `NFR-4`).

### US-4 — Notebook-queryable data `[v1]`
**As an** operator, **I want** to load the store into pandas in one call, **so that** I can explore without writing SQL glue.
- [ ] `import threadkeeper as tk; s = tk.sessions(); tk.messages(s.iloc[0]["id"])` return DataFrames.
- [ ] `tool_input` and `usage` deserialize from JSON to dict columns on load.

### US-5 — Starter analysis notebooks `[v1]`
**As an** operator, **I want** starter notebooks for the north-star questions, **so that** I can begin manual problem-finding immediately.
- [ ] Ships ≥3 notebooks (jupytext `.py` percent format): (a) **instruction churn** — re-prompt/correction density per session; (b) **stale-knowledge probes** — sessions referencing files/symbols that no longer exist, and CLAUDE.md/memory size over time; (c) **cost/waste** — per-model token totals + "enabled-but-never-called" MCP/skill signal (method per the Opik note §7.2–7.3).
- [ ] Each notebook runs top-to-bottom against a freshly collected store with no manual edits.

### US-6 — Automated problem detectors `[deferred → §10]`
**As an** operator, **I want** the notebook heuristics promoted to a `thread-keeper detect` command emitting a report, **so that** problems surface without opening a notebook. *Deferred:* v1 is deliberately manual/experimental; promote once a heuristic proves itself in US-5. Path back: each stabilized notebook cell → a `detectors/` function + a Req.

---

## 4. Features & requirements

Grouped by engineering pillar. Every Req has an Acceptance. Tables exist because of the stories in §3.

### F1 — Ingestion & parsing
| Req ID | Requirement | Acceptance |
|---|---|---|
| F1.1 | Claude Code parser: read `~/.claude/projects/<munged-cwd>/*.jsonl`, emit normalized events (§7.1) | A known fixture session yields the expected user/assistant/thinking/tool_use/tool_result rows; skips `isSidechain`, `<command-name>`/`<local-command>`, `<system-reminder>` blocks |
| F1.2 | Codex parser: read `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (payload-unwrapped) | `function_call`/`local_shell_call`→`tool_use`, `function_call_output`→`tool_result`, `reasoning`→`thinking`; session id prefixed `codex-` |
| F1.3 | Cursor parser: snapshot-copy `state.vscdb` **+ `-wal` + `-shm`** to temp, read `cursorDiskKV`/`ItemTable`, emit events | Reading a workspace with WAL-only unflushed data returns the messages (not empty); temp copy cleaned up; original untouched |
| F1.4 | cwd/project resolution: latest-cwd-wins + `reduce_cwd` ancestor-collapse (Claude Code); one-folder-per-workspace (Cursor); first-cwd (Codex) | A session resumed after a repo move resolves to the current path; subdir cwds collapse to project root |
| F1.5 | Summary/title + usage extraction (Claude Code): last `custom-title` wins; per-model token aggregation with 5m/1h cache-write split (§7.2) | `sessions.summary` = the final `/rename` title; `sessions.usage` JSON matches the split-tier shape |

### F2 — Normalized store
| Req ID | Requirement | Acceptance |
|---|---|---|
| F2.1 | SQLite store at `~/.thread-keeper/thread-keeper.db` (env `THREAD_KEEPER_DATA_DIR`); schema created idempotently (§7.1) | Fresh run creates the DB + `projects`/`sessions`/`messages`/`collection_state` tables |
| F2.2 | `projects`/`sessions`/`messages` schema per §7.1, ported 1:1 from Chronicle `server/db.js` | Column set + types match the contract; indexes on `(session_id, seq)` and `(project_id)` present |
| F2.3 | `replace_session(session, events)` = delete+reinsert in one transaction; **preserve user-set `name` and prior `host`** (unless the ingest passes `--host`), re-derive `summary`/`usage`/`context_tokens` | Re-importing an unchanged log is a no-op net of row identity; a Chronicle-style rename survives re-sync; a copied-laptop session keeps its origin `host` across a later local sweep; `seq` = insertion order |

### F3 — Automated collection
| Req ID | Requirement | Acceptance |
|---|---|---|
| F3.1 | `collect` CLI, two modes: **fast-path** (`--source/--session-id/--transcript` from a hook) and **sweep** (`--sweep`, walk all sources) | Both modes ingest into the same store; fast-path ingests exactly the named session |
| F3.2 | Hook payload adapters for Claude Code `SessionEnd`, Cursor `sessionEnd`, Codex `notify`/`SessionEnd` (§7.3) | Given each tool's real stdin/argv payload, the collector resolves the right file(s) and source |
| F3.3 | Change-detection watermark: per-file `last_size`+`last_mtime` (JSONL) / `last_hash` (Cursor) in `collection_state`. **On change → re-parse the whole file → `replace_session`** (never a partial/append read — see the whole-session invariant in §7.1); unchanged files skipped | Second consecutive sweep writes 0 net new/changed rows; a grown JSONL is re-parsed whole and *replaces* its prior session (correctly re-aggregated `usage`, no duplicate rows) |
| F3.4 | `install-hooks` / `uninstall-hooks`: write or remove the three tools' hook configs (backing up any existing file first); hook commands use an absolute CLI path (or `python -m threadkeeper`) so they work without `thread-keeper` on PATH | Running install-hooks makes a real Claude Code session auto-collect; the written command is absolute (not a bare `thread-keeper` relying on PATH); uninstall-hooks removes our entries; backups land under `~/.thread-keeper/backups/` |
| F3.5 | Idle-heuristic for still-growing sessions (fast-path may fire mid-session for Codex per-turn `notify`) | A rollout file still being appended is re-parsed whole and *replaces* its prior session on the next trigger (via F2.3 + F3.3), never duplicated |
| F3.6 | **Cold-start backfill**: the sweep has no install-time cutoff — its first run on a fresh store ingests the entire pre-existing history | Fresh store + `--sweep` → every pre-existing session across all three sources is present (not just files created after install) |
| F3.7 | Configurable per-source roots (`--claude-root`/`--codex-root`/`--cursor-root` + env) + `--host <label>` origin tagging, for consolidating logs copied from other laptops | `--claude-root <copied dir> --host laptopB` ingests another machine's logs tagged `host=laptopB` (not the local machine); re-importing them is idempotent and keeps that label (F2.3) |

### F4 — Analysis surface
| Req ID | Requirement | Acceptance |
|---|---|---|
| F4.1 | Python query helpers: `sessions()`, `messages(session_id=…)`, `projects()` → pandas DataFrames; JSON columns auto-decoded | `tk.sessions()` returns a DataFrame; `messages()['tool_input']` values are dicts |
| F4.2 | Per-model cost helper: static price/context tables ported from `src/models.js`; `cost_of(usage)` | `cost_of` on a known usage blob matches Chronicle's number; Opus-4.8 tier = $5/$25 (not the 4.1 $15/$75) |
| F4.3 | ≥3 starter notebooks (jupytext `.py`) per US-5, plus a `notebooks/README` | Each runs top-to-bottom on a fresh store |

### F5 — Problem detectors `[deferred → §10]`
Promoted from US-5 notebooks once a heuristic earns it. Candidate detectors (from the Opik note §7 + the "how users instruct / old knowledge" goal): instruction-churn score, stale-symbol/stale-CLAUDE.md flag, enabled-but-unused MCP/skill waste, output-verbosity trend. No v1 Reqs.

---

## 5. Pricing model

**N/A — intentionally omitted.** thread-keeper hosts nothing, proxies nothing, bills nothing: it is a local-first, offline, single-user tool with no server component and no data leaving the machine (`NFR-4`). Cost *analysis* (F4.2) computes list-price estimates from local token counts against a static table — it is never billing. This is a deliberate negative-space call, not an oversight.

---

## 6. Design principles (north star, not v1 commitments)

Directional; if any becomes load-bearing it graduates to a Req.

- **Own the data first, analyze second.** The store is the product; detectors are downstream. Never make analysis a prerequisite for capture.
- **Offline is a hard invariant, not a feature.** No network, ever (the procurement/sovereignty wedge from the Opik note §7.7, and the vision's data-ownership driver).
- **Read-only on foreign logs, always.** Copy-before-open; never mutate a tool's own store.
- **Static tables over fetching** (prices, context windows, and later the measured system-prompt/tool-def baselines from the Opik note §5) — matches Chronicle's "never fetched" philosophy.
- **The point is consistency.** Every detector should answer "where is my agent usage *random/inconsistent/wasteful*," not vanity metrics.

---

## 7. Cross-cutting contracts

JSON Schema (Draft 2020-12) for every shape that crosses a boundary. This is the codegen surface — copy verbatim. Ported from Chronicle `server/db.js`, `server/parsers/*`, `src/models.js` (see Appendix for line-level provenance).

### 7.1 Persisted schema

**`projects`**
```json
{ "$schema":"https://json-schema.org/draft/2020-12/schema","type":"object",
  "required":["id","path","name","created_at"],
  "properties":{
    "id":{"type":"integer","description":"PK autoincrement"},
    "path":{"type":"string","description":"physical cwd (project root); UNIQUE natural key"},
    "name":{"type":"string","description":"basename(path)"},
    "created_at":{"type":"string","description":"ISO; default datetime('now')"}}}
```

**`sessions`**
```json
{ "$schema":"https://json-schema.org/draft/2020-12/schema","type":"object",
  "required":["id","project_id","source","file_path"],
  "properties":{
    "id":{"type":"string","description":"source-native id; prefixed codex-/cursor-chat-/cursor-composer- to avoid collisions"},
    "project_id":{"type":"integer"},
    "source":{"enum":["claude-code","codex","cursor"]},
    "file_path":{"type":"string","description":"source log file (or state.vscdb for cursor)"},
    "started_at":{"type":["string","null"]},
    "ended_at":{"type":["string","null"]},
    "message_count":{"type":"integer","default":0},
    "first_prompt":{"type":["string","null"],"description":"first user msg, <=200 chars"},
    "context_tokens":{"type":["integer","null"],"description":"real ctx size; populated on import only"},
    "name":{"type":["string","null"],"description":"USER override — PRESERVED across re-import"},
    "summary":{"type":["string","null"],"description":"tool title (last custom-title) — re-derived"},
    "usage":{"type":["string","null"],"description":"JSON per 7.2 — re-derived"},
    "host":{"type":["string","null"],"description":"ORIGIN-machine label (where the session ran), set per source-root at ingest: --host <label> if given, else local hostname for local roots — NOT the collecting machine. PRESERVED across re-import unless --host is passed (F2.3). Enables cross-laptop attribution (US-7/OD-7)."}}}
```

**`messages`**
```json
{ "$schema":"https://json-schema.org/draft/2020-12/schema","type":"object",
  "required":["id","session_id","seq","kind"],
  "properties":{
    "id":{"type":"integer"},
    "session_id":{"type":"string"},
    "seq":{"type":"integer","description":"0-based ordinal = insertion order"},
    "uuid":{"type":["string","null"],"description":"Claude Code only"},
    "ts":{"type":["string","null"],"description":"ISO 8601"},
    "kind":{"enum":["user","assistant","thinking","tool_use","tool_result"]},
    "text":{"type":["string","null"]},
    "tool_name":{"type":["string","null"]},
    "tool_input":{"type":["string","null"],"description":"JSON STRING of tool args"},
    "tool_use_id":{"type":["string","null"],"description":"pairs tool_use<->tool_result"},
    "model":{"type":["string","null"]},
    "injected":{"type":"integer","enum":[0,1],"default":0,"description":"1 = harness-injected user message (isMeta or a known injection envelope), not a human turn; else 0. Powers sessions() timing (human_idle_sec/n_turns). Backfill an existing store with `collect --sweep --force`."}}}
```

**`collection_state`** (thread-keeper-new; a **change-detector**, not a read cursor)
```json
{ "$schema":"https://json-schema.org/draft/2020-12/schema","type":"object",
  "required":["file_path","source"],
  "properties":{
    "file_path":{"type":"string","description":"PK; the source file (or state.vscdb path)"},
    "source":{"enum":["claude-code","codex","cursor"]},
    "last_size":{"type":["integer","null"],"description":"file size (bytes) at last collect — CHANGE DETECTOR only, never a read boundary"},
    "last_mtime":{"type":["string","null"],"description":"file mtime (ISO) at last collect"},
    "last_hash":{"type":["string","null"],"description":"content hash — Cursor: sha256 over (composerId → fullConversationHeadersOnly) for workspace ItemTable composers and/or the global cursorDiskKV composerData store (plus a compact legacy aichat tab summary for workspace DBs); JSONL: optional tie-breaker"},
    "last_collected_at":{"type":"string","description":"ISO"}}}
```
> **Whole-session invariant:** one JSONL file = one session, and `usage`/`summary`/`message_count` aggregate over the *entire* file. So `collection_state` answers only "did this file change since last collect?" (size/mtime, hash tie-breaker). On change, the collector **re-parses the whole file and calls `replace_session`** — it never resumes from a byte offset or ingests "only appended lines" (that would corrupt usage totals and `seq`).

### 7.2 Normalized event object (parser output) + usage blob

**Event** (pre-DB; `seq` assigned on insert):
```json
{ "$schema":"https://json-schema.org/draft/2020-12/schema","type":"object",
  "required":["kind"],
  "properties":{
    "uuid":{"type":["string","null"]},
    "ts":{"type":["string","null"]},
    "kind":{"enum":["user","assistant","thinking","tool_use","tool_result"]},
    "text":{"type":["string","null"]},
    "tool_name":{"type":["string","null"]},
    "tool_input":{"type":["string","null"],"description":"JSON-encoded string"},
    "tool_use_id":{"type":["string","null"]},
    "model":{"type":["string","null"]}}}
```

**`sessions.usage`** value (JSON string; keys are model ids):
```json
{ "$schema":"https://json-schema.org/draft/2020-12/schema","type":"object",
  "additionalProperties":{"type":"object",
    "required":["input","output"],
    "properties":{
      "input":{"type":"integer"},"output":{"type":"integer"},
      "cacheWrite5m":{"type":"integer"},"cacheWrite1h":{"type":"integer"},
      "cacheRead":{"type":"integer"}}}}
```
Legacy `{cacheWrite}` (single tier) is accepted and treated as 5m.

### 7.3 Hook / trigger payloads (collector inputs)

**Claude Code `SessionEnd`** (stdin JSON) — the fast path:
```json
{ "$schema":"https://json-schema.org/draft/2020-12/schema","type":"object",
  "required":["session_id","transcript_path"],
  "properties":{
    "session_id":{"type":"string"},
    "transcript_path":{"type":"string","description":"the .jsonl to ingest directly"},
    "cwd":{"type":"string"},
    "reason":{"enum":["clear","resume","logout","prompt_input_exit","bypass_permissions_disabled","other"]}}}
```
**Cursor `sessionEnd`** (stdin JSON): `{session_id, reason, duration_ms, is_background_agent, final_status}` — fire-and-forget, DB write may lag; collector re-reads the workspace DB (do not read synchronously in the hook).
**Codex `notify`** (single JSON argv): `{type:"agent-turn-complete"|"approval-requested", "last-assistant-message":string}` — per-turn; collector applies the idle-heuristic (F3.5). Newer Codex `hooks.json` `SessionEnd` may also be used where available.

### 7.4 Collector CLI contract
```
thread-keeper collect --source <claude-code|codex|cursor> --session-id <id> [--transcript <path>]   # fast-path (explicit ids)
thread-keeper collect --source <claude-code|cursor> --from-stdin                                    # fast-path: SessionEnd JSON on stdin (§7.3) — what install-hooks wires for Claude Code / Cursor
thread-keeper collect --source codex --from-notify-argv                                             # fast-path: Codex notify (no session id; idle-heuristic F3.5) — what install-hooks wires for Codex
thread-keeper collect --sweep [--source <...>] [--claude-root <dir>] [--codex-root <dir>] [--cursor-root <dir>] [--host <label>]  # backstop + cold-start backfill; roots default to this machine (host=local hostname); override roots + --host to ingest logs copied from another laptop
thread-keeper install-hooks [--tool <claude-code|cursor|codex> ...]                                   # write hook configs (backs up first); commands use an absolute CLI path (or `python -m threadkeeper`) so SessionEnd works without PATH
thread-keeper uninstall-hooks [--tool <claude-code|cursor|codex> ...]                                 # remove our hook entries (backs up first; Codex may restore a prior notify)
thread-keeper status                                                                                  # store path, row counts, per-file watermarks
```
Exit 0 always on the fast path (never block the agent tool). All ingestion routes through `replace_session` (F2.3) so any mode is idempotent.

### 7.5 Git snapshot mapping `[deferred, library-only]`
The Chronicle `ts → commit` mapping (`git rev-list -1 --before=<ts> --all`, `diff-tree -m --first-parent`) ports cleanly via `subprocess`, but is **not wired into v1 notebooks** (§10). Kept as an optional `threadkeeper.git.commit_at(repo, ts)` helper for the stale-knowledge notebook to opt into.

---

## 8. Technical constraints & preferences

- **Language/runtime:** Python ≥3.11, project + deps managed with **uv** (current best practice, 2026): a `uv`-init'd project with `pyproject.toml`, a `thread-keeper` console-script entry point, committed `uv.lock`, a `.python-version` pin, `uv run` for tasks, and `uv tool install`/`uvx thread-keeper` for global CLI use — no `requirements.txt`, `setup.py`, or hand-managed venvs. **Store:** stdlib `sqlite3` (ports Chronicle's schema 1:1, single file, notebook-native via `pandas.read_sql`). **Analysis:** pandas; DuckDB optional as a read-only analytical view over the SQLite (Open decision OD-1).
- **No web framework, no Electron, no daemon, no SSE, no LLM calls, no network.** CLI via `typer` (OD-3). Notebooks via jupyter; notebooks stored as jupytext `.py` percent files (OD-5) for diff-friendliness.
- **Platform:** macOS-first (paths verified there); keep the Chronicle env-override + platform-branch pattern (`THREAD_KEEPER_DATA_DIR`, plus per-source root overrides `THREAD_KEEPER_CLAUDE_ROOT`/`_CODEX_ROOT`/`_CURSOR_ROOT` and matching `--*-root` flags) so Linux/Windows are a later port **and** so a sweep can target log dirs copied from another laptop (US-7).
- **Read-only on foreign data** is non-negotiable (Cursor WAL copy incl. `-wal`/`-shm`; never open a tool's DB read-write).
- **Codegen consumption directives:** implementation repo is a NEW repo `thread-keeper`. Copy this PRD to `docs/PRD.md`; put the §7 schemas in `docs/contracts/*.json` (one file per shape); reference both from the repo's `CLAUDE.md` ("build one Req at a time, check acceptance boxes, use `docs/contracts/` verbatim"). Port order = the Chronicle files named in the Appendix.
- **README acknowledgment (required):** the repo `README.md` must credit **[Chronicle](https://github.com/chizhangucb/chronicle)** (and the author's GitHub handle, e.g. [@chizhangucb](https://github.com/chizhangucb)) as the reference implementation whose offline extraction core this project ports to Python — a plain, sincere "Credits / Prior art" section. Link the project repo (not only an account profile); GitHub handles are fine; do not use personal/legal names unless the author has disclosed them for that purpose. This is a respect + provenance requirement, not optional boilerplate.

---

## 9. Non-functional requirements

| NFR | Target | Measurement |
|---|---|---|
| NFR-1 Sweep throughput | Incremental sweep (≤2,000 changed files) ≤60 s warm; **cold-start backfill** of a full existing history (≤20,000 sessions, incl. copied-in other-laptop logs) ≤10 min | median of 3 runs, this machine |
| NFR-2 Fast-path latency | Hook→ingested for one session ≤2 s | median of 20 `SessionEnd` fires on real Claude Code sessions |
| NFR-3 Idempotency | Second consecutive sweep writes 0 net new/changed rows | row-count + content hash diff across two back-to-back sweeps, n≥3 |
| NFR-4 Offline / read-only | 0 outbound network connections; 0 writes to any source log/DB | `nettop`/`lsof` during a full sweep; checksum every source file before/after, n = all files in one sweep |
| NFR-5 Fast-path never blocks | Agent tool proceeds even if collector errors/hangs | inject a `sleep 30`/`exit 1` collector; Claude Code session ends normally |

---

## 10. Out of scope (canonical)

Anything deferred elsewhere points here. Each names the reason + path back.

- **Web/Electron UI, live SSE streaming, replay, MCP Hub, Skills Hub, security redaction, share links** — Chronicle's heavy surfaces; cut to stay "much more lightweight." *Path back:* none planned; if a notebook proves a view worth a UI, reconsider then.
- **Automated problem detectors (`detect` command)** (US-6, F5) — v1 is experimental/manual by intent. *Path back:* a US-5 notebook heuristic that proves out → `detectors/` fn + Req.
- **Git snapshot mapping wired into notebooks** (§7.5) — library helper only in v1. *Path back:* the stale-knowledge notebook opts in when needed.
- **Other sources** — OpenCode, Gemini, Copilot (Chronicle's other 3 parsers). *Path back:* port a parser once the 3-source core is proven; parser interface is identical.
- **Live multi-machine SYNC / fleet server / per-user leaderboard** — no daemon syncing across machines, no shared server, single-user by design (the ~3-yr peer-adoption goal is *each person runs their own*). **But consolidating your OWN history from several laptops IS in v1** (US-7): copy another machine's log dirs over and sweep them with `--*-root`, or merge SQLite stores (session ids are globally unique). *Path back for true sync:* out of v1; manual copy+sweep is the supported path.
- **Realtime/live capture** — explicitly not needed; collection is post-hoc + scheduled.
- **Wire-level token attribution requiring a proxy** — the Opik note §5 shows the valuable parts are reconstructable offline from logs + measured constants; the proxy-only bits (exact per-block cache status, overage billing) are cut. *Path back:* a one-shot measurement to seed static baselines, never a persistent proxy.

---

## 11. Milestones (week-by-week, exit criteria)

| Week | Deliverable | Exit criteria |
|---|---|---|
| W1 | Schema + Claude Code parser + `collect --sweep` + `collection_state` | US-2/US-3 (CC only) pass incl. **cold-start backfill of full existing CC history**; NFR-3 idempotency holds; store queryable |
| W2 | Codex + Cursor parsers (WAL snapshot) + configurable source roots + `host` | US-3 all three sources; **US-7 multi-laptop consolidation** (`--*-root`, host-tagged, idempotent); F1.3 WAL test passes; NFR-4 read-only verified |
| W3 | Hook fast-path + `install-hooks` (+ backups) | US-1 passes end-to-end on a real Claude Code session; NFR-2/NFR-5 pass |
| W4 | Query helpers + cost table + 3 starter notebooks + docs | US-4/US-5 pass; PRD+contracts copied into repo; `CLAUDE.md` hookup done |

---

## 12. Open decisions

| # | Question | Default if undecided | Owner |
|---|---|---|---|
| OD-1 | Analytical layer over SQLite? | **SQLite canonical; DuckDB optional** read-only view for heavy notebook joins | Yusuke |
| OD-2 | Wire git snapshot mapping into v1? | **No** — library helper only (§7.5); notebooks opt in | Yusuke |
| OD-3 | CLI framework | **typer** (argparse fallback; no heavy deps) | Yusuke |
| OD-4 | Data dir | **`~/.thread-keeper/`**, env `THREAD_KEEPER_DATA_DIR` | Yusuke |
| OD-5 | Notebook storage format | **jupytext `.py` percent** (diff-friendly) + a launch script | Yusuke |
| OD-6 | Codex trigger: `notify` vs new `hooks.json` | **`notify` (agent-turn-complete)** primary (better-documented) + sweep backstop; adopt `hooks.json` SessionEnd when verified | Yusuke |
| OD-7 | Project identity when the same repo path exists on two laptops | **Merge** into one `path`-keyed project; the session's **origin `host`** (per-root `--host`, preserved across re-import) keeps machine-level attribution | Yusuke |
| OD-8 | Python floor / pin | **≥3.11 floor**, pin latest stable in `.python-version` via uv | Yusuke |

---

## 13. How to use this PRD (humans + AI)

- **Engineers:** build pillar by pillar in the W1→W4 order; each Req's Acceptance is the definition of done.
- **AI codegen (Claude Code / Cursor / Codex):** load `docs/PRD.md`; implement **one Req at a time**, checking its Acceptance box; use `docs/contracts/*.json` **verbatim** as the types (do not re-derive shapes from prose); when unsure, prefer the *smallest* change that closes the current Req. Port from the exact Chronicle files in the Appendix — translate logic, don't reinvent it.
- **Reference implementation:** the JS original at `/Users/not_so_fat/workspace/codes/chronicle` (see Appendix A for the load-bearing Chronicle files to port). "What's reusable vs must-rewrite" is in the Appendix.

---

## Appendix A — Source notes (provenance → captured-as)

| Source | Captured as |
|---|---|
| Chronicle `server/db.js` | §7.1 schema + F2.3 `replace_session` (name-preserve, delete+reinsert, `seq`=index) |
| Chronicle `server/parsers/claudeCode.js` | F1.1/F1.4/F1.5; skip rules (isSidechain, command-name, system-reminder); latest-cwd-wins + `reduce_cwd`; last-custom-title; 5m/1h usage split |
| Chronicle `server/parsers/codex.js` | F1.2; payload-unwrap; call/output→tool mapping; `codex-` id prefix |
| Chronicle `server/parsers/cursor.js` | F1.3; WAL `-wal`/`-shm` snapshot copy; `cursorDiskKV`/`ItemTable`; composer/bubble resolution |
| Chronicle `server/git.js` | §7.5 (deferred) `commit_at`, `diff-tree -m --first-parent` |
| Chronicle `src/models.js` | F4.2 static price/context tables; Opus-4.8 $5/$25; 5m/1h separate |
| Claude Code hooks (official) | §7.3 SessionEnd payload; F3.1/F3.2 — https://code.claude.com/docs/en/hooks |
| Cursor hooks (official) | §7.3 sessionEnd (fire-and-forget, DB-lag caveat) — https://cursor.com/docs/hooks |
| Codex config/notify + hooks (official) | §7.3 notify `agent-turn-complete` + hooks.json — https://developers.openai.com/codex/config-reference , /hooks |
| [[2026-07-15 Chronicle — Opik-cipx Comparison + Token Attribution Without a Proxy]] | §1 "why now"; F5/§10 detector candidates (offline waste attribution, no proxy) |
| [[2026-07-31-personal-project-vision]] | §1/§2/§6 framing: own-your-usage-data, consistency through-line, ~10-peer goal |

## Appendix B — Ideas / north-star backlog (not v1)

From the Opik note §7 + the "detect problems on agent usage" goal, ranked by the note's own ROI ordering:
1. **Static built-in-tool token table + "enabled-but-unused per turn" cost** — hits the #1 cost driver (static overhead / oversized tool defs like `Workflow` ~15k tokens); fully offline.
2. **Installed-vs-called MCP/skill report** — `attributionMcpServer` already in the log; ~67% unused at fleet scale.
3. **Instruction-churn / correction-density score** — "how users instruct": re-prompt loops, corrections, thrash per session.
4. **Stale-knowledge probes** — references to files/symbols that no longer exist (needs §7.5 git opt-in); CLAUDE.md/memory growth over time.
5. **Output-verbosity trend** — output tokens inflate every later turn's cached prefix; already have per-message output counts.
6. **System-prompt + built-in-schema baseline** (measure once per CC version, ship as a static table) — the constant that makes category totals add up.

## Appendix C — Personal notes (why this, for me)

- This is the **data-ownership pillar** of the personal stack ([[2026-07-31-personal-project-vision]]): Lexicon owns *work knowledge*; thread-keeper owns *usage data* — "how I work with AI, my improving process," the asset currently lost to each platform.
- Deliberately **separate & composable** (like agent-deck / agent-dealer / lens), not folded into Chronicle. Chronicle stays the JS "time machine"; thread-keeper is the Python analysis substrate — different tool, different job.
- Win condition inherited from the vision: not downloads — **~10 respected peers running it on their own data.** So: single-machine, offline, easy to run yourself.
- The obstacle is **quality = consistency.** Keep the tool itself principled and un-random; a flaky collector or a duplicating store would violate the very value the project is about.
- **The backlog is the point.** Years of existing sessions across several laptops *are* the accumulated asset — v1 backfills all of it (US-7), not just data from install day forward.
- **Credit Chronicle in the README.** This ports Chronicle's extraction core; acknowledge the project plainly (respect + provenance), without naming individuals who haven't disclosed their name for that purpose.
