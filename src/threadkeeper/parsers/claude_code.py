"""Claude Code parser — ports Chronicle's ``server/parsers/claudeCode.js``.

Reads ``~/.claude/projects/<munged-cwd>/*.jsonl`` session transcripts and
emits normalized events per ``docs/contracts/event.json``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Harness-injected user messages that are NOT human turns. `isMeta` catches
# Stop-hook feedback and caveats; these envelope prefixes catch the rest that
# arrive with isMeta=False (e.g. background task notifications). Verified against
# a 60-session scan (see specs/2026-08-05-agent-timing-columns-design.md).
# `<command-name>` / `<local-command` / `<system-reminder>` are already dropped
# outright in the string-content branch of parse_claude_line; they remain here so
# the same check also flags them on the list-block path (defense-in-depth).
CLAUDE_CODE_INJECTED_PREFIXES = (
    "<task-notification>",
    "<command-name>",
    "<local-command",
    "<system-reminder>",
    "Stop hook feedback",
    "[Request interrupted",
)


def _cc_is_injected(o: dict, text: str | None) -> bool:
    if o.get("isMeta"):
        return True
    return bool(text) and text.startswith(CLAUDE_CODE_INJECTED_PREFIXES)


def reduce_cwd(pick: str, seen: set[str]) -> str:
    """A session can record subdirectory cwds (e.g. ``<repo>/server``). Walk the
    pick up to the shortest seen ancestor so grouping lands on the project root.
    """
    out = pick
    for c in seen:
        if c and c != out and out.startswith(c + "/"):
            out = c
    return out


def _block_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", f"[{c.get('type')}]") for c in content if isinstance(c, dict))
    return "" if content is None else str(content)


def _safe_json(v) -> str | None:
    try:
        return json.dumps(v)
    except (TypeError, ValueError):
        return None


def parse_claude_line(o: dict) -> list[dict]:
    """Parse a single JSONL entry into normalized events (shared by import + live tail)."""
    events: list[dict] = []
    if o.get("isSidechain"):
        return events

    if o.get("type") == "user" and o.get("message"):
        content = o["message"].get("content")
        if isinstance(content, str):
            if content.startswith("<command-name>") or content.startswith("<local-command"):
                return events
            events.append({"uuid": o.get("uuid"), "ts": o.get("timestamp"), "kind": "user",
                           "text": content, "injected": _cc_is_injected(o, content)})
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    events.append(
                        {
                            "uuid": o.get("uuid"),
                            "ts": o.get("timestamp"),
                            "kind": "tool_result",
                            "text": _block_text(block.get("content")),
                            "tool_use_id": block.get("tool_use_id"),
                        }
                    )
                elif (
                    block.get("type") == "text"
                    and (block.get("text") or "").strip()
                    and not block["text"].startswith("<system-reminder>")
                ):
                    events.append({"uuid": o.get("uuid"), "ts": o.get("timestamp"), "kind": "user",
                                   "text": block["text"], "injected": _cc_is_injected(o, block["text"])})
    elif o.get("type") == "assistant" and o.get("message"):
        model = o["message"].get("model")
        for block in o["message"].get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and (block.get("text") or "").strip():
                events.append({"uuid": o.get("uuid"), "ts": o.get("timestamp"), "kind": "assistant", "text": block["text"], "model": model})
            elif block.get("type") == "thinking" and (block.get("thinking") or "").strip():
                events.append({"uuid": o.get("uuid"), "ts": o.get("timestamp"), "kind": "thinking", "text": block["thinking"], "model": model})
            elif block.get("type") == "tool_use":
                events.append(
                    {
                        "uuid": o.get("uuid"),
                        "ts": o.get("timestamp"),
                        "kind": "tool_use",
                        "model": model,
                        "tool_name": block.get("name"),
                        "tool_use_id": block.get("id"),
                        "tool_input": _safe_json(block.get("input")),
                    }
                )
    return events


def parse_claude_session(file: str | Path) -> tuple[dict, list[dict]]:
    """Parse one session JSONL file into ``(session, events)``. Whole-file read."""
    file = str(file)
    events: list[dict] = []
    session_id = os.path.splitext(os.path.basename(file))[0]
    cwd: str | None = None
    cwds_seen: set[str] = set()
    first_prompt: str | None = None
    summary: str | None = None
    custom_title: str | None = None
    skipped = 0
    context_tokens: int | None = None
    usage_by_model: dict[str, dict] = {}

    with open(file, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            if o.get("sessionId"):
                session_id = o["sessionId"]

            # Claude Code stores a user rename as {"type":"custom-title","customTitle":"…"}
            # (the /rename-session title). The LAST one wins.
            if o.get("type") == "custom-title" and isinstance(o.get("customTitle"), str) and o["customTitle"].strip():
                custom_title = o["customTitle"][:200]

            # Older logs may carry a {"type":"summary","summary":"…"} title; keep the
            # first as a fallback when no explicit custom title exists.
            if not summary and o.get("type") == "summary" and isinstance(o.get("summary"), str) and o["summary"].strip():
                summary = o["summary"][:200]

            # Latest cwd wins: sessions resumed after a repo move carry the old path
            # in their early records; the newest cwd is where the project lives now.
            if o.get("cwd"):
                cwd = o["cwd"]
                cwds_seen.add(o["cwd"])

            # Real context-window size: the prompt side of the LAST main-chain API
            # call. Same pass aggregates per-model token usage.
            if not o.get("isSidechain") and o.get("type") == "assistant" and (o.get("message") or {}).get("usage"):
                u = o["message"]["usage"]
                ctx = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                if ctx > 0:
                    context_tokens = ctx
                model = o["message"].get("model") or "unknown"
                agg = usage_by_model.setdefault(model, {"input": 0, "output": 0, "cacheWrite5m": 0, "cacheWrite1h": 0, "cacheRead": 0})
                agg["input"] += u.get("input_tokens") or 0
                agg["output"] += u.get("output_tokens") or 0
                agg["cacheRead"] += u.get("cache_read_input_tokens") or 0
                # 5-minute and 1-hour cache writes are billed at different rates.
                cc = u.get("cache_creation")
                if cc and (cc.get("ephemeral_5m_input_tokens") is not None or cc.get("ephemeral_1h_input_tokens") is not None):
                    agg["cacheWrite5m"] += cc.get("ephemeral_5m_input_tokens") or 0
                    agg["cacheWrite1h"] += cc.get("ephemeral_1h_input_tokens") or 0
                else:
                    agg["cacheWrite5m"] += u.get("cache_creation_input_tokens") or 0  # default tier when unsplit

            for e in parse_claude_line(o):
                events.append(e)
                if e["kind"] == "user" and not first_prompt:
                    first_prompt = e["text"][:200]

    timestamps = sorted(e["ts"] for e in events if e.get("ts"))
    if cwd:
        cwd = reduce_cwd(cwd, cwds_seen)

    session = {
        "id": session_id,
        "source": "claude-code",
        "file_path": file,
        "cwd": cwd,
        "started_at": timestamps[0] if timestamps else None,
        "ended_at": timestamps[-1] if timestamps else None,
        "first_prompt": first_prompt,
        "summary": custom_title or summary,  # Claude Code custom title wins over legacy summary
        "context_tokens": context_tokens,
        "usage": json.dumps(usage_by_model) if usage_by_model else None,
        "skipped": skipped,
    }
    return session, events
