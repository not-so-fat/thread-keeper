import json

from threadkeeper.parsers import codex


def test_codex_usage_to_blob_subtracts_cached_and_ignores_reasoning():
    # Identities from sample: total == input+output; cached <= input; reasoning <= output
    tot = {
        "input_tokens": 22389,
        "cached_input_tokens": 14080,
        "cache_write_input_tokens": 0,
        "output_tokens": 311,
        "reasoning_output_tokens": 61,
        "total_tokens": 22700,
    }
    assert tot["total_tokens"] == tot["input_tokens"] + tot["output_tokens"]
    blob = codex.codex_usage_to_blob(tot)
    assert blob == {
        "input": 22389 - 14080,
        "output": 311,  # reasoning NOT added
        "cacheWrite5m": 0,
        "cacheWrite1h": 0,
        "cacheRead": 14080,
    }
    assert codex.codex_usage_to_blob(None) is None


def test_codex_usage_to_blob_subtracts_cache_write_from_input():
    # cache_write is a subset of input_tokens (OpenAI-style details), so uncached
    # input must exclude both cached and cache_write to avoid double-billing.
    tot = {
        "input_tokens": 1000,
        "cached_input_tokens": 400,
        "cache_write_input_tokens": 100,
        "output_tokens": 50,
        "reasoning_output_tokens": 10,
        "total_tokens": 1050,
    }
    assert tot["total_tokens"] == tot["input_tokens"] + tot["output_tokens"]
    blob = codex.codex_usage_to_blob(tot)
    assert blob == {
        "input": 500,  # 1000 - 400 - 100
        "output": 50,
        "cacheWrite5m": 100,
        "cacheWrite1h": 0,
        "cacheRead": 400,
    }


def test_parse_codex_session_fixture(fixtures_dir):
    file = fixtures_dir / "codex" / "2026" / "07" / "01" / "rollout-2026-07-01T10-00-00-xyz.jsonl"
    session, events = codex.parse_codex_session(file)

    # session id prefixed codex- to avoid collisions with other sources
    assert session["id"] == "codex-0198-xyz"
    assert session["source"] == "codex"
    assert session["cwd"] == "/Users/test/health-app"
    assert session["first_prompt"] == "Add a /healthz endpoint"
    assert "context_tokens" not in session  # out of scope for Codex usage Req

    # Last token_count total_token_usage wins; model from turn_context
    usage = json.loads(session["usage"])
    assert list(usage.keys()) == ["gpt-5.6-sol"]
    assert usage["gpt-5.6-sol"] == {
        "input": 54857 - 36352 - 512,
        "output": 581,
        "cacheWrite5m": 512,
        "cacheWrite1h": 0,
        "cacheRead": 36352,
    }

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
