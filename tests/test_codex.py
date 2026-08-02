import json

from threadkeeper.parsers import codex


def test_parse_codex_session_fixture(fixtures_dir):
    file = fixtures_dir / "codex" / "2026" / "07" / "01" / "rollout-2026-07-01T10-00-00-xyz.jsonl"
    session, events = codex.parse_codex_session(file)

    # session id prefixed codex- to avoid collisions with other sources
    assert session["id"] == "codex-0198-xyz"
    assert session["source"] == "codex"
    assert session["cwd"] == "/Users/test/health-app"
    assert session["first_prompt"] == "Add a /healthz endpoint"

    kinds = [e["kind"] for e in events]
    assert kinds == ["user", "thinking", "tool_use", "tool_result", "tool_use", "tool_result", "assistant"]

    user_event = events[0]
    assert user_event["text"] == "Add a /healthz endpoint"

    thinking_event = events[1]
    assert thinking_event["text"] == "Check the router module before adding the route."

    function_call = events[2]
    assert function_call["tool_name"] == "shell"
    assert function_call["tool_use_id"] == "call_1"
    assert json.loads(function_call["tool_input"]) == {"command": ["ls", "src/app"]}

    function_call_output = events[3]
    assert function_call_output["tool_use_id"] == "call_1"
    assert "router.py" in function_call_output["text"]

    local_shell_call = events[4]
    assert local_shell_call["tool_name"] == "shell"  # p.name absent -> default
    assert local_shell_call["tool_use_id"] == "call_2"
    assert json.loads(local_shell_call["tool_input"]) == {"command": ["cat", "src/app/router.py"]}

    assistant_event = events[6]
    assert assistant_event["text"] == "Added GET /healthz returning 200."


def test_parse_codex_session_missing_file_raises(tmp_path):
    import pytest

    with pytest.raises(OSError):
        codex.parse_codex_session(tmp_path / "does-not-exist.jsonl")
