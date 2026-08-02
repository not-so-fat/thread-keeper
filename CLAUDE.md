# CLAUDE.md — how to work on thread-keeper

thread-keeper is spec-driven. Before writing code:

1. Read `docs/PRD.md` in full. It is the source of truth for scope, user
   stories (§3), requirements (§4, "F1"–"F5"), the CLI contract (§7.4), and
   what's explicitly out of scope (§10).
2. **Build one Req at a time.** Each Req in §4 has an ID (e.g. `F2.3`) and an
   Acceptance clause. Implement the smallest change that satisfies one Req,
   verify its Acceptance, then move to the next. Do not batch multiple Reqs
   into one change unless they're trivially coupled.
3. **Use `docs/contracts/*.json` verbatim.** These JSON Schemas (one file per
   shape in PRD §7) are the codegen surface for `projects`/`sessions`/
   `messages`/`collection_state`/`event`/`usage` and the three hook payloads.
   Do not re-derive a shape from prose in the PRD body — read the schema file.
4. **Port, don't reinvent.** The reference implementation is Chronicle (JS) at
   the path named in the PRD's Appendix A. Parsers, the schema, and the
   pricing tables are direct ports of specific Chronicle files — translate
   the logic, keep the skip rules and edge-case handling intact.
5. When a decision isn't pinned by the PRD, prefer the smallest change that
   closes the current Req, and prefer options already used elsewhere in this
   codebase over new ones.

## Non-negotiable invariants (violating these is always a bug, not a tradeoff)

- **Offline.** No network calls anywhere in the library or CLI.
- **Read-only on foreign logs.** Cursor's `state.vscdb` (+ `-wal`/`-shm`) is
  always snapshot-copied to a temp dir before opening; the original is never
  opened read-write.
- **Whole-session invariant.** On a file change, re-parse the *whole* file and
  call `replace_session` — never append or read a partial/byte-offset slice.
  `collection_state` is a change-detector, not a read cursor.
- **Idempotent.** `replace_session` = delete + reinsert in one transaction; it
  preserves a user-set `name` and the prior `host` unless `--host` is passed
  for this ingest.
- **Fast-path never blocks.** `thread-keeper collect` (non-`--sweep`) always
  exits 0, even on internal errors — it's invoked from an agent-tool hook and
  must never stall or fail that tool.

## Where things live

- `src/threadkeeper/db.py` — schema + `replace_session` (port of Chronicle's `server/db.js`)
- `src/threadkeeper/models.py` — pricing/context tables (port of `src/models.js`)
- `src/threadkeeper/parsers/` — one module per source (ports of `server/parsers/*.js`)
- `src/threadkeeper/collect.py` — fast-path + sweep orchestration, change detection
- `src/threadkeeper/hooks.py` — `install-hooks` writers (with backups)
- `src/threadkeeper/cli.py` — typer CLI
- `src/threadkeeper/query.py` — pandas query helpers (`tk.sessions()` etc.)
- `notebooks/` — jupytext `.py` percent starter notebooks (US-5)

## Testing

```bash
uv sync
uv run pytest
```

Tests use `tests/fixtures/` (synthetic JSONL + a Cursor `state.vscdb` built at
test time in `conftest.py`) and isolate `THREAD_KEEPER_DATA_DIR` per test —
never point a test at a real `~/.claude`, `~/.codex`, or Cursor user dir.
Never run `install-hooks` against a real config in a test; always pass an
explicit temp path.
