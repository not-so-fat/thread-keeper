"""``install-hooks``: write each tool's hook config, backing up any existing file first.

Per F3.4, any existing config is copied to ``~/.thread-keeper/backups/`` before
being overwritten so a bad write is always recoverable.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import db

CLAUDE_COMMAND = "thread-keeper collect --source claude-code --from-stdin"
CURSOR_COMMAND = "thread-keeper collect --source cursor --from-stdin"
CODEX_NOTIFY = ["thread-keeper", "collect", "--source", "codex", "--from-notify-argv"]


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
        CLAUDE_COMMAND in h.get("command", "")
        for matcher in session_end
        for h in matcher.get("hooks", [])
    )
    if not already:
        session_end.append({"matcher": "*", "hooks": [{"type": "command", "command": CLAUDE_COMMAND}]})
    path.parent.mkdir(parents=True, exist_ok=True)
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
    already = any(entry.get("command") == CURSOR_COMMAND for entry in session_end)
    if not already:
        session_end.append({"command": CURSOR_COMMAND})
    path.parent.mkdir(parents=True, exist_ok=True)
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
    new_line = f"notify = {json.dumps(CODEX_NOTIFY)}"
    if _NOTIFY_LINE_RE.search(text):
        text = _NOTIFY_LINE_RE.sub(new_line, text, count=1)
    else:
        sep = "\n" if text and not text.endswith("\n") else ""
        text = f"{text}{sep}{new_line}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
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
