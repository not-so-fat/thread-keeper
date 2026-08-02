"""``install-hooks`` / ``uninstall-hooks``: write or remove each tool's hook config.

Per F3.4, any existing config is copied to ``~/.thread-keeper/backups/`` before
being mutated so a bad write is always recoverable. Hook commands use an
absolute path to the ``thread-keeper`` console script (or ``python -m
threadkeeper``) so SessionEnd works even when the bare name is not on PATH
(``uv sync`` alone does not put it on PATH — see README).
"""

from __future__ import annotations

import json
import platform
import re
import shlex
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import db

# Substring used for idempotent install / surgical uninstall (independent of
# the resolved absolute executable path).
CLAUDE_MARKER = "collect --source claude-code --from-stdin"
CURSOR_MARKER = "collect --source cursor --from-stdin"
CODEX_MARKER = "--from-notify-argv"


def resolve_cli_prefix() -> list[str]:
    """Argv prefix that invokes this install of thread-keeper.

    Prefer ``thread-keeper`` on PATH, then the console script next to
    ``sys.executable`` (``.venv/bin`` / Scripts after ``uv sync``), then
    ``{python} -m threadkeeper``.
    """
    which = shutil.which("thread-keeper")
    if which:
        return [which]
    name = "thread-keeper.exe" if platform.system() == "Windows" else "thread-keeper"
    sibling = Path(sys.executable).resolve().parent / name
    if sibling.is_file():
        return [str(sibling)]
    return [sys.executable, "-m", "threadkeeper"]


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in args)


def claude_hook_command() -> str:
    return _shell_command([*resolve_cli_prefix(), "collect", "--source", "claude-code", "--from-stdin"])


def cursor_hook_command() -> str:
    return _shell_command([*resolve_cli_prefix(), "collect", "--source", "cursor", "--from-stdin"])


def codex_notify_argv() -> list[str]:
    return [*resolve_cli_prefix(), "collect", "--source", "codex", "--from-notify-argv"]


def __getattr__(name: str):
    """Lazy aliases so tests can still read ``hooks.CLAUDE_COMMAND`` etc."""
    if name == "CLAUDE_COMMAND":
        return claude_hook_command()
    if name == "CURSOR_COMMAND":
        return cursor_hook_command()
    if name == "CODEX_NOTIFY":
        return codex_notify_argv()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def backups_dir() -> Path:
    d = db.get_data_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def backup_file(path: Path) -> Path | None:
    """Copy an existing file to the backups dir before mutating it. No-op if absent."""
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = backups_dir() / f"{path.name}.{stamp}.bak"
    shutil.copyfile(path, dest)
    return dest


# --- Claude Code -------------------------------------------------------------

def claude_code_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def install_claude_code_hook(settings_path: Path | None = None) -> Path:
    path = settings_path or claude_code_settings_path()
    backup_file(path)
    settings: dict = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            settings = {}
    hooks = settings.setdefault("hooks", {})
    session_end = hooks.setdefault("SessionEnd", [])
    already = any(
        CLAUDE_MARKER in h.get("command", "")
        for matcher in session_end
        for h in matcher.get("hooks", [])
    )
    if not already:
        session_end.append(
            {"matcher": "*", "hooks": [{"type": "command", "command": claude_hook_command()}]}
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return path


def uninstall_claude_code_hook(settings_path: Path | None = None) -> Path | None:
    path = settings_path or claude_code_settings_path()
    if not path.exists():
        return None
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    hooks = settings.get("hooks") or {}
    session_end = hooks.get("SessionEnd")
    if not session_end:
        return None
    backup_file(path)
    new_matchers = []
    for matcher in session_end:
        kept = [h for h in matcher.get("hooks", []) if CLAUDE_MARKER not in h.get("command", "")]
        if kept:
            new_matchers.append({**matcher, "hooks": kept})
    if new_matchers:
        hooks["SessionEnd"] = new_matchers
    else:
        hooks.pop("SessionEnd", None)
    if not hooks:
        settings.pop("hooks", None)
    else:
        settings["hooks"] = hooks
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return path


# --- Cursor ------------------------------------------------------------------

def cursor_hooks_path() -> Path:
    return Path.home() / ".cursor" / "hooks.json"


def install_cursor_hook(hooks_path: Path | None = None) -> Path:
    path = hooks_path or cursor_hooks_path()
    backup_file(path)
    config: dict = {}
    if path.exists():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {}
    hooks = config.setdefault("hooks", {})
    session_end = hooks.setdefault("sessionEnd", [])
    already = any(CURSOR_MARKER in (entry.get("command") or "") for entry in session_end)
    if not already:
        session_end.append({"command": cursor_hook_command()})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def uninstall_cursor_hook(hooks_path: Path | None = None) -> Path | None:
    path = hooks_path or cursor_hooks_path()
    if not path.exists():
        return None
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    hooks = config.get("hooks") or {}
    session_end = hooks.get("sessionEnd")
    if not session_end:
        return None
    backup_file(path)
    kept = [e for e in session_end if CURSOR_MARKER not in (e.get("command") or "")]
    if kept:
        hooks["sessionEnd"] = kept
    else:
        hooks.pop("sessionEnd", None)
    if not hooks:
        config.pop("hooks", None)
    else:
        config["hooks"] = hooks
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


# --- Codex ---------------------------------------------------------------

def codex_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


_NOTIFY_LINE_RE = re.compile(r"^notify\s*=.*$", re.MULTILINE)


def install_codex_hook(config_path: Path | None = None) -> Path:
    """Set the ``notify`` command in Codex's ``config.toml`` (OD-6: notify primary).

    Minimal, dependency-free TOML line edit: replaces an existing top-level
    ``notify = [...]`` line, or appends one. Anything else in the file is left
    untouched.
    """
    path = config_path or codex_config_path()
    backup_file(path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    new_line = f"notify = {json.dumps(codex_notify_argv())}"
    if _NOTIFY_LINE_RE.search(text):
        text = _NOTIFY_LINE_RE.sub(new_line, text, count=1)
    else:
        sep = "\n" if text and not text.endswith("\n") else ""
        text = f"{text}{sep}{new_line}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def uninstall_codex_hook(config_path: Path | None = None) -> Path | None:
    """Remove our ``notify`` line, or restore the previous notify from the
    newest backup that still has a non-thread-keeper notify.
    """
    path = config_path or codex_config_path()
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    match = _NOTIFY_LINE_RE.search(text)
    if not match or CODEX_MARKER not in match.group(0):
        return None
    backup_file(path)
    # Prefer restoring a prior notify from our most recent backup of this file.
    restored = None
    backups = sorted(backups_dir().glob(f"{path.name}.*.bak"), reverse=True)
    for bak in backups:
        try:
            bak_text = bak.read_text(encoding="utf-8")
        except OSError:
            continue
        bak_match = _NOTIFY_LINE_RE.search(bak_text)
        if bak_match and CODEX_MARKER not in bak_match.group(0):
            restored = bak_match.group(0)
            break
    if restored:
        text = _NOTIFY_LINE_RE.sub(restored, text, count=1)
    else:
        text = _NOTIFY_LINE_RE.sub("", text, count=1)
        text = re.sub(r"\n{3,}", "\n\n", text)
    path.write_text(text, encoding="utf-8")
    return path


def install_hooks(
    tools: tuple[str, ...] | None = None,
    *,
    claude_settings_path: Path | None = None,
    cursor_hooks_path_: Path | None = None,
    codex_config_path_: Path | None = None,
) -> dict[str, Path]:
    tools = tools or ("claude-code", "cursor", "codex")
    written: dict[str, Path] = {}
    if "claude-code" in tools:
        written["claude-code"] = install_claude_code_hook(claude_settings_path)
    if "cursor" in tools:
        written["cursor"] = install_cursor_hook(cursor_hooks_path_)
    if "codex" in tools:
        written["codex"] = install_codex_hook(codex_config_path_)
    return written


def uninstall_hooks(
    tools: tuple[str, ...] | None = None,
    *,
    claude_settings_path: Path | None = None,
    cursor_hooks_path_: Path | None = None,
    codex_config_path_: Path | None = None,
) -> dict[str, Path]:
    """Remove thread-keeper hook entries (surgical; backs up first)."""
    tools = tools or ("claude-code", "cursor", "codex")
    touched: dict[str, Path] = {}
    if "claude-code" in tools:
        path = uninstall_claude_code_hook(claude_settings_path)
        if path is not None:
            touched["claude-code"] = path
    if "cursor" in tools:
        path = uninstall_cursor_hook(cursor_hooks_path_)
        if path is not None:
            touched["cursor"] = path
    if "codex" in tools:
        path = uninstall_codex_hook(codex_config_path_)
        if path is not None:
            touched["codex"] = path
    return touched
