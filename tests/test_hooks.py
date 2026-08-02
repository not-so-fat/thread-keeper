import json

import pytest

from threadkeeper import hooks


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Every hook install writes a backup under THREAD_KEEPER_DATA_DIR; keep it
    off the real ~/.thread-keeper for every test in this module."""
    monkeypatch.setenv("THREAD_KEEPER_DATA_DIR", str(tmp_path / "tk-data-default"))


def test_install_claude_code_hook_writes_new_settings(tmp_path):
    settings_path = tmp_path / "settings.json"
    hooks.install_claude_code_hook(settings_path)

    settings = json.loads(settings_path.read_text())
    matchers = settings["hooks"]["SessionEnd"]
    commands = [h["command"] for m in matchers for h in m["hooks"]]
    assert any(hooks.CLAUDE_COMMAND in c for c in commands)


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

    # existing unrelated hook keys are preserved, SessionEnd is added
    settings = json.loads(settings_path.read_text())
    assert settings["hooks"]["PreToolUse"] == []
    assert "SessionEnd" in settings["hooks"]


def test_install_claude_code_hook_is_idempotent(tmp_path):
    settings_path = tmp_path / "settings.json"
    hooks.install_claude_code_hook(settings_path)
    hooks.install_claude_code_hook(settings_path)

    settings = json.loads(settings_path.read_text())
    matchers = settings["hooks"]["SessionEnd"]
    commands = [h["command"] for m in matchers for h in m["hooks"] if hooks.CLAUDE_COMMAND in h["command"]]
    assert len(commands) == 1


def test_install_cursor_hook_writes_session_end(tmp_path):
    hooks_path = tmp_path / "hooks.json"
    hooks.install_cursor_hook(hooks_path)

    config = json.loads(hooks_path.read_text())
    commands = [e["command"] for e in config["hooks"]["sessionEnd"]]
    assert hooks.CURSOR_COMMAND in commands


def test_install_codex_hook_appends_notify_line(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("model = \"gpt-5\"\n")

    hooks.install_codex_hook(config_path)

    text = config_path.read_text()
    assert 'model = "gpt-5"' in text
    assert "notify = " in text
    assert "thread-keeper" in text


def test_install_codex_hook_replaces_existing_notify_line(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('notify = ["old-script.py"]\nmodel = "gpt-5"\n')

    hooks.install_codex_hook(config_path)

    text = config_path.read_text()
    assert "old-script.py" not in text
    assert text.count("notify = ") == 1
    assert 'model = "gpt-5"' in text
