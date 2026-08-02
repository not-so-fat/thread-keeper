"""Codex parser — ports Chronicle's ``server/parsers/codex.js``.

Reads ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`` (payload-unwrapped).
Session ids are prefixed ``codex-`` to avoid collisions with other sources.

Token usage (``token_count`` / ``total_token_usage``) is mapped into the PRD
§7.2 blob. Cursor has no mapped usage path; Claude Code maps in its own parser.

Source → §7.2 name map (Codex; sample-cited identities):
  input      = max(0, input_tokens - cached_input_tokens)  # input is a superset
  output     = output_tokens                               # do NOT add reasoning_*
  cacheRead  = cached_input_tokens
  cacheWrite5m = cache_write_input_tokens                  # no 1h tier in Codex
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"


def _item_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            (c.get("text") or c.get("input_text") or c.get("output_text") or "")
            for c in content
            if isinstance(c, dict)
        ]
        return "\n".join(p for p in parts if p)
    return ""


def codex_usage_to_blob(total_token_usage: dict | None) -> dict | None:
    """Map Codex ``total_token_usage`` into one §7.2 per-model usage dict.

    Arithmetic is locked to observed rollout identities
    (``total_tokens == input_tokens + output_tokens``,
    ``cached_input_tokens <= input_tokens``,
    ``reasoning_output_tokens <= output_tokens``): subtract cached from input;
    never add ``reasoning_output_tokens`` into ``output``.
    """
    if not total_token_usage:
        return None
    inp = total_token_usage.get("input_tokens") or 0
    cached = total_token_usage.get("cached_input_tokens") or 0
    out = total_token_usage.get("output_tokens") or 0
    cw = total_token_usage.get("cache_write_input_tokens") or 0
    return {
        "input": max(0, inp - cached),
        "output": out,
        "cacheWrite5m": cw,
        "cacheWrite1h": 0,
        "cacheRead": cached,
    }


def parse_codex_session(file: str | Path) -> tuple[dict, list[dict]]:
    """Parse one Codex rollout JSONL file into ``(session, events)``. Whole-file read."""
    file = str(file)
    events: list[dict] = []
    session_id = os.path.splitext(os.path.basename(file))[0]
    cwd: str | None = None
    first_prompt: str | None = None
    model: str | None = None
    last_total_usage: dict | None = None

    with open(file, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = o.get("timestamp") or o.get("ts")
            p = o.get("payload") or o
            if p.get("id") and p.get("cwd"):
                cwd = p["cwd"]
                session_id = p["id"]
            if isinstance(p.get("model"), str) and p["model"]:
                model = p["model"]
            t = p.get("type") or o.get("type")

            if t == "message" and p.get("role") == "user":
                text = _item_text(p.get("content"))
                if text:
                    events.append({"ts": ts, "kind": "user", "text": text})
                    if not first_prompt:
                        first_prompt = text[:200]
            elif t == "message" and p.get("role") == "assistant":
                text = _item_text(p.get("content"))
                if text:
                    events.append({"ts": ts, "kind": "assistant", "text": text})
            elif t == "reasoning":
                text = "\n".join(s.get("text", "") for s in p.get("summary") or [])
                if text:
                    events.append({"ts": ts, "kind": "thinking", "text": text})
            elif t in ("function_call", "local_shell_call"):
                events.append(
                    {
                        "ts": ts,
                        "kind": "tool_use",
                        "tool_name": p.get("name") or "shell",
                        "tool_input": p.get("arguments") or json.dumps(p.get("action") or {}),
                        "tool_use_id": p.get("call_id"),
                    }
                )
            elif t == "function_call_output":
                output = p.get("output")
                events.append(
                    {
                        "ts": ts,
                        "kind": "tool_result",
                        "text": output if isinstance(output, str) else json.dumps(output),
                        "tool_use_id": p.get("call_id"),
                    }
                )
            elif t == "token_count":
                # Last total_token_usage wins as session aggregate. Do not set
                # context_tokens — Codex total_tokens / model_context_window are
                # different quantities from Claude's last prompt-side ctx size.
                info = p.get("info") or {}
                tot = info.get("total_token_usage")
                if tot:
                    last_total_usage = tot

    timestamps = sorted(e["ts"] for e in events if e.get("ts"))
    per_model = codex_usage_to_blob(last_total_usage)
    usage = None
    if per_model is not None:
        usage = json.dumps({model or "unknown": per_model})

    session = {
        "id": f"codex-{session_id}",
        "source": "codex",
        "file_path": file,
        "cwd": cwd,
        "started_at": timestamps[0] if timestamps else None,
        "ended_at": timestamps[-1] if timestamps else None,
        "first_prompt": first_prompt,
        "usage": usage,
        "skipped": 0,
    }
    return session, events
