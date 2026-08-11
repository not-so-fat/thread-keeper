"""thread-keeper CLI (typer, OD-3): ``collect``, ``install-hooks``, ``status``."""

from __future__ import annotations

import json
import sys
from typing import Annotated

import typer

from . import collect as _collect
from . import db as _db
from . import hooks as _hooks

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Local-first collector for Claude Code / Codex / Cursor agent sessions.",
)


@app.command()
def collect(
    source: Annotated[str | None, typer.Option("--source", help="claude-code | codex | cursor")] = None,
    session_id: Annotated[str | None, typer.Option("--session-id")] = None,
    transcript: Annotated[str | None, typer.Option("--transcript")] = None,
    sweep: Annotated[bool, typer.Option("--sweep", help="Walk all sources' log dirs (backstop + cold-start backfill).")] = False,
    force: Annotated[bool, typer.Option("--force", help="With --sweep: re-parse every file, ignoring change-detection watermarks (use to backfill a parser/schema change).")] = False,
    claude_root: Annotated[str | None, typer.Option("--claude-root", envvar="THREAD_KEEPER_CLAUDE_ROOT")] = None,
    codex_root: Annotated[str | None, typer.Option("--codex-root", envvar="THREAD_KEEPER_CODEX_ROOT")] = None,
    cursor_root: Annotated[str | None, typer.Option("--cursor-root", envvar="THREAD_KEEPER_CURSOR_ROOT")] = None,
    host: Annotated[str | None, typer.Option("--host", help="Origin-machine label; else local hostname.")] = None,
    from_stdin: Annotated[
        bool,
        typer.Option(
            "--from-stdin",
            help="Hook fast-path: read Claude Code/Cursor SessionEnd JSON from stdin (session_id + transcript_path).",
        ),
    ] = False,
    from_notify_argv: Annotated[
        bool,
        typer.Option(
            "--from-notify-argv",
            help="Hook fast-path: Codex notify trigger (no session id); reparse the newest rollout file (idle-heuristic).",
        ),
    ] = False,
) -> None:
    """Fast-path (--source/--session-id, optionally --transcript) or sweep (--sweep)."""
    if sweep:
        conn = _db.connect()
        sources = (source,) if source else None
        results = _collect.collect_sweep(
            conn, sources=sources, claude_root=claude_root, codex_root=codex_root, cursor_root=cursor_root, host=host, force=force
        )
        exit_code = 0
        for src, r in results.items():
            typer.echo(f"{src}: ingested={r.ingested} unchanged={r.skipped_unchanged} errors={r.errored}")
            for e in r.errors:
                typer.echo(f"  ! {e}", err=True)
            if r.errored:
                exit_code = 1
        raise typer.Exit(code=exit_code)

    # Fast path — never blocks the calling agent tool (F3.1, NFR-5): always exit 0.
    try:
        if from_notify_argv:
            conn = _db.connect()
            _collect.collect_codex_notify(conn, codex_root=codex_root, host=host)
            raise typer.Exit(code=0)

        resolved_session_id = session_id
        resolved_transcript = transcript
        if from_stdin:
            payload = json.loads(sys.stdin.read() or "{}")
            resolved_session_id = resolved_session_id or payload.get("session_id")
            resolved_transcript = resolved_transcript or payload.get("transcript_path")

        if not source or not resolved_session_id:
            typer.echo("collect: --source and --session-id are required (or --from-stdin)", err=True)
            raise typer.Exit(code=0)

        conn = _db.connect()
        _collect.collect_fast_path(
            conn,
            source=source,
            session_id=resolved_session_id,
            transcript=resolved_transcript,
            claude_root=claude_root,
            codex_root=codex_root,
            cursor_root=cursor_root,
            host=host,
        )
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"collect: {exc}", err=True)
    raise typer.Exit(code=0)


@app.command(name="install-hooks")
def install_hooks_cmd(
    tool: Annotated[
        list[str] | None,
        typer.Option("--tool", help="claude-code | cursor | codex (repeatable; default: all three)."),
    ] = None,
) -> None:
    """Write each tool's hook config, backing up any existing file to ~/.thread-keeper/backups/ first.

    Commands written into hook configs use an absolute path to this install
    (or ``python -m threadkeeper``) so SessionEnd works without ``thread-keeper``
    being on PATH.
    """
    tools = tuple(tool) if tool else None
    written = _hooks.install_hooks(tools)
    for name, path in written.items():
        typer.echo(f"{name}: wrote {path}")
    typer.echo(f"hook command prefix: {' '.join(_hooks.resolve_cli_prefix())}")


@app.command(name="uninstall-hooks")
def uninstall_hooks_cmd(
    tool: Annotated[
        list[str] | None,
        typer.Option("--tool", help="claude-code | cursor | codex (repeatable; default: all three)."),
    ] = None,
) -> None:
    """Remove thread-keeper hook entries (backs up first; Codex may restore a prior notify)."""
    tools = tuple(tool) if tool else None
    touched = _hooks.uninstall_hooks(tools)
    if not touched:
        typer.echo("uninstall-hooks: nothing to remove")
        return
    for name, path in touched.items():
        typer.echo(f"{name}: updated {path}")


@app.command()
def status(
    claude_root: Annotated[str | None, typer.Option("--claude-root", envvar="THREAD_KEEPER_CLAUDE_ROOT")] = None,
    codex_root: Annotated[str | None, typer.Option("--codex-root", envvar="THREAD_KEEPER_CODEX_ROOT")] = None,
    cursor_root: Annotated[str | None, typer.Option("--cursor-root", envvar="THREAD_KEEPER_CURSOR_ROOT")] = None,
) -> None:
    """Store path, row counts, per-file watermarks, and per-source staleness.

    Exits 1 if any source is stale (files on disk a sweep hasn't collected), so
    it doubles as a health check — a frozen source screams instead of drifting.
    """
    conn = _db.connect()
    info = _collect.status(conn, claude_root=claude_root, codex_root=codex_root, cursor_root=cursor_root)
    typer.echo(f"store: {info['db_path']}")
    for table, count in info["counts"].items():
        typer.echo(f"  {table}: {count}")
    typer.echo("sessions by source:")
    for src, count in info["sessions_by_source"].items():
        typer.echo(f"  {src}: {count}")
    typer.echo(f"watermarks: {len(info['watermarks'])} file(s) tracked")
    typer.echo("freshness (newest on disk vs. collected):")
    any_stale = False
    for src, f in info["freshness"].items():
        any_stale = any_stale or f["stale"]
        flag = f"⚠ STALE ({f['pending']} pending)" if f["stale"] else "ok"
        typer.echo(
            f"  {src}: {f['disk_files']} file(s) | disk={f['newest_disk'] or '-'} "
            f"| collected={f['newest_collected'] or '-'} | {flag}"
        )
    if any_stale:
        typer.echo("run `thread-keeper collect --sweep` to catch up stale sources.", err=True)
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
