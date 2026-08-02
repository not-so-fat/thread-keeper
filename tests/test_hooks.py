import json
import sys
from pathlib import Path

import pytest

from threadkeeper import hooks


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Every hook install writes a backup under THREAD_KEEPER_DATA_DIR; keep it
    off the real ~/.thread-keeper for every test in this module."""
    monkeypatch.setenv("THREAD_KEEPER_DATA_DIR", str(tmp_path / "tk-data-default"))


def test_resolve_cli_prefix_prefers_path_which(monkeypatch, tmp_path):
    fake = tmp_path / "thread-keeper"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(hooks.shutil, "which", lambda _name: str(fake))
    assert hooks.resolve_cli_prefix() == [str(fake)]


def test_resolve_cli_prefix_falls_back_to_python_m(monkeypatch):
    monkeypatch.setattr(hooks.shutil, "which", lambda _name: None)
    # Point sys.executable's parent at a dir with no thread-keeper script.
    monkeypatch.setattr(sys, "executable", "/nonexistent/bin/python")
    assert hooks.resolve_cli_prefix() == [sys.executable, "-m", "threadkeeper"]


def test_install_claude_code_hook_writes_absolute_command(tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "thread-keeper"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(hooks.shutil, "which", lambda _name: str(fake))

    settings_path = tmp_path / "settings.json"
    hooks.install_claude_code_hook(settings_path)

    settings = json.loads(settings_path.read_text())
    matchers = settings["hooks"]["SessionEnd"]
    commands = [h["command"] for m in matchers for h in m["hooks"]]
    assert any(hooks.CLAUDE_MARKER in c for c in commands)
    assert any(str(fake) in c for c in commands)
    # bare name alone must not be the whole command (PATH trap)
    assert not any(c.startswith("thread-keeper ") for c in commands)


def test_install_claude_code_hook_backs_up_existing_file(tmp_path, monkeypatch):
    data_dir = tmp_path / "tk-data"
    monkeypatch.setenv("THREAD_KEEPER_DATA_DIR", str(data_dir))

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"hooks": {"PreToolUse": []}}))

    hooks.install_claude_code_hook(settings_path)

    backups = list((data_dir / "backups").glob("settings.json.*.bak"))
    assert len(backups) == 1
    backed_up = json.loads(backups[0].read_text())
    assert backed_up == {"hooks": {"PreToolUse": []}}

    settings = json.loads(settings_path.read_text())
    assert settings["hooks"]["PreToolUse"] == []
    assert "SessionEnd" in settings["hooks"]


def test_install_claude_code_hook_is_idempotent(tmp_path):
    settings_path = tmp_path / "settings.json"
    hooks.install_claude_code_hook(settings_path)
    hooks.install_claude_code_hook(settings_path)

    settings = json.loads(settings_path.read_text())
    matchers = settings["hooks"]["SessionEnd"]
    commands = [
        h["command"] for m in matchers for h in m["hooks"] if hooks.CLAUDE_MARKER in h["command"]
    ]
    assert len(commands) == 1


def test_uninstall_claude_code_hook_removes_our_entry(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionEnd": [
                        {"matcher": "*", "hooks": [{"type": "command", "command": "other-tool"}]},
                    ]
                }
            }
        )
    )
    hooks.install_claude_code_hook(settings_path)
    hooks.uninstall_claude_code_hook(settings_path)

    settings = json.loads(settings_path.read_text())
    commands = [h["command"] for m in settings["hooks"]["SessionEnd"] for h in m["hooks"]]
    assert "other-tool" in commands
    assert not any(hooks.CLAUDE_MARKER in c for c in commands)


def test_install_cursor_hook_writes_session_end(tmp_path):
    hooks_path = tmp_path / "hooks.json"
    hooks.install_cursor_hook(hooks_path)

    config = json.loads(hooks_path.read_text())
    commands = [e["command"] for e in config["hooks"]["sessionEnd"]]
    assert any(hooks.CURSOR_MARKER in c for c in commands)


def test_uninstall_cursor_hook_removes_our_entry(tmp_path):
    hooks_path = tmp_path / "hooks.json"
    hooks.install_cursor_hook(hooks_path)
    hooks.uninstall_cursor_hook(hooks_path)
    config = json.loads(hooks_path.read_text())
    assert "sessionEnd" not in (config.get("hooks") or {})


def test_install_codex_hook_appends_notify_line(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('model = "gpt-5"\n')

    hooks.install_codex_hook(config_path)

    text = config_path.read_text()
    assert 'model = "gpt-5"' in text
    assert "notify = " in text
    assert hooks.CODEX_MARKER in text


def test_install_codex_hook_replaces_existing_notify_line(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('notify = ["old-script.py"]\nmodel = "gpt-5"\n')

    hooks.install_codex_hook(config_path)

    text = config_path.read_text()
    assert "old-script.py" not in text
    assert text.count("notify = ") == 1
    assert 'model = "gpt-5"' in text


def test_uninstall_codex_hook_restores_prior_notify(tmp_path, monkeypatch):
    data_dir = tmp_path / "tk-data"
    monkeypatch.setenv("THREAD_KEEPER_DATA_DIR", str(data_dir))
    config_path = tmp_path / "config.toml"
    config_path.write_text('notify = ["old-script.py"]\nmodel = "gpt-5"\n')

    hooks.install_codex_hook(config_path)
    assert hooks.CODEX_MARKER in config_path.read_text()

    hooks.uninstall_codex_hook(config_path)
    text = config_path.read_text()
    assert 'notify = ["old-script.py"]' in text
    assert hooks.CODEX_MARKER not in text
