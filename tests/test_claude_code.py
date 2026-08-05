import json

from threadkeeper.parsers import claude_code


def test_parse_claude_session_fixture(fixtures_dir):
    file = fixtures_dir / "claude_code" / "cc-session-1.jsonl"
    session, events = claude_code.parse_claude_session(file)

    assert session["id"] == "cc-session-1"
    assert session["source"] == "claude-code"
    # last custom-title wins over the legacy summary line
    assert session["summary"] == "Fix OAuth redirect bug"
    assert session["first_prompt"] == "Fix the auth bug"
    # latest cwd wins, then subdir collapses to the seen ancestor (project root)
    assert session["cwd"] == "/Users/test/project"
    assert session["started_at"] == "2026-07-01T10:00:00Z"
    assert session["ended_at"] == "2026-07-01T10:00:20Z"

    kinds = [e["kind"] for e in events]
    assert kinds == ["user", "thinking", "tool_use", "tool_result", "assistant"]

    # isSidechain, <command-name>, and <system-reminder> blocks must be skipped
    texts = [e.get("text") for e in events]
    assert "side chain question" not in texts
    assert not any(t and "<command-name>" in t for t in texts if t)
    assert not any(t and "<system-reminder>" in t for t in texts if t)

    tool_use = events[2]
    assert tool_use["tool_name"] == "read_file"
    assert tool_use["tool_use_id"] == "tool1"
    assert json.loads(tool_use["tool_input"]) == {"path": "auth.py"}

    tool_result = events[3]
    assert tool_result["tool_use_id"] == "tool1"
    assert tool_result["text"] == "def auth(): ..."

    usage = json.loads(session["usage"])
    agg = usage["claude-opus-4-8"]
    assert agg["input"] == 1000 + 1200
    assert agg["output"] == 50 + 80
    assert agg["cacheRead"] == 200 + 200
    assert agg["cacheWrite5m"] == 100  # from the split cache_creation tier
    assert agg["cacheWrite1h"] == 0

    assert session["context_tokens"] == 1200 + 100 + 200  # last assistant call's ctx


def test_parse_claude_line_skips_sidechain():
    assert claude_code.parse_claude_line({"isSidechain": True, "type": "user", "message": {"content": "x"}}) == []


def test_reduce_cwd_collapses_to_shortest_seen_ancestor():
    seen = {"/repo", "/repo/server", "/repo/server/api"}
    assert claude_code.reduce_cwd("/repo/server/api", seen) == "/repo"


def _user_line(text, *, is_meta=False):
    o = {"type": "user", "uuid": "u1", "timestamp": "2026-07-01T10:00:00Z",
         "message": {"content": text}}
    if is_meta:
        o["isMeta"] = True
    return o


def test_parse_marks_ismeta_user_as_injected():
    ev = claude_code.parse_claude_line(_user_line("Stop hook feedback:\n...", is_meta=True))
    assert ev and ev[0]["kind"] == "user" and ev[0]["injected"] is True


def test_parse_marks_task_notification_as_injected():
    ev = claude_code.parse_claude_line(_user_line("<task-notification>\n<task-id>x</task-id>"))
    assert ev and ev[0]["injected"] is True


def test_parse_marks_real_human_as_not_injected():
    ev = claude_code.parse_claude_line(_user_line("how do they connect to playroom"))
    assert ev and ev[0]["injected"] is False


def test_parse_marks_each_injection_envelope_prefix():
    # string-content user messages opening with an injection envelope (no isMeta)
    # must be flagged injected. (<command-name>/<local-command> early-return before
    # this point, so they're covered separately below.)
    for text in ("[Request interrupted by the user]", "<system-reminder>be careful",
                 "Stop hook feedback:\ncontinue"):
        ev = claude_code.parse_claude_line(_user_line(text))
        assert ev and ev[0]["kind"] == "user" and ev[0]["injected"] is True, text


def test_parse_command_envelope_produces_no_event():
    # <command-name>/<local-command> strings are dropped outright (no event at all)
    assert claude_code.parse_claude_line(_user_line("<command-name>/foo</command-name>")) == []
    assert claude_code.parse_claude_line(_user_line("<local-command-stdout>x</local-command-stdout>")) == []
