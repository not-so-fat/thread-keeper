"""Codex parser — ports Chronicle's ``server/parsers/codex.js``.

Reads ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`` (payload-unwrapped).
Session ids are prefixed ``codex-`` to avoid collisions with other sources.
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


def parse_codex_session(file: str | Path) -> tuple[dict, list[dict]]:
    """Parse one Codex rollout JSONL file into ``(session, events)``. Whole-file read."""
    file = str(file)
    events: list[dict] = []
    session_id = os.path.splitext(os.path.basename(file))[0]
    cwd: str | None = None
    first_prompt: str | None = None

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

    timestamps = sorted(e["ts"] for e in events if e.get("ts"))
    session = {
        "id": f"codex-{session_id}",
        "source": "codex",
        "file_path": file,
        "cwd": cwd,
        "started_at": timestamps[0] if timestamps else None,
        "ended_at": timestamps[-1] if timestamps else None,
        "first_prompt": first_prompt,
        "skipped": 0,
    }
    return session, events
