"""Static per-model context-window + pricing tables, ported from Chronicle's
``src/models.js``. Pure lookup — never fetched at runtime (offline invariant).
"""

from __future__ import annotations

# Ordered: more specific prefixes must come first.
CONTEXT_WINDOWS: list[tuple[str, int]] = [
    # Claude — 1M-context generation
    ("claude-fable-5", 1_000_000),
    ("claude-mythos", 1_000_000),
    ("claude-opus-4-8", 1_000_000),
    ("claude-opus-4-7", 1_000_000),
    ("claude-opus-4-6", 1_000_000),
    ("claude-sonnet-5", 1_000_000),
    ("claude-sonnet-4-6", 1_000_000),
    # Claude — 200K models (Haiku 4.5/3.x, Opus 4.5/4.1/4.0/3, Sonnet 4.5/4.0/3.x)
    ("claude-haiku", 200_000),
    ("claude-opus", 200_000),
    ("claude-sonnet", 200_000),
    ("claude", 200_000),
    # Non-Claude sources (Codex, Gemini CLI, Copilot)
    ("gpt-5", 400_000),
    ("gpt-4", 128_000),
    ("o3", 200_000),
    ("o4", 200_000),
    ("gemini", 1_000_000),
]


def context_window_for(model: str | None) -> int | None:
    """Longest-prefix-style substring lookup; returns tokens or None if unknown."""
    if not model:
        return None
    m = str(model).lower()
    for prefix, window in CONTEXT_WINDOWS:
        if prefix in m:
            return window
    return None


def _price(input_: float, output: float, cw5m: float, cw1h: float, cache_read: float) -> dict:
    return {"input": input_, "output": output, "cw5m": cw5m, "cw1h": cw1h, "cacheRead": cache_read}


# Per-model list price in USD per 1M tokens. Ordered: more specific prefixes first.
PRICING: list[tuple[str, dict]] = [
    ("claude-fable-5", _price(10, 50, 12.5, 20, 1)),
    ("claude-mythos", _price(10, 50, 12.5, 20, 1)),
    ("claude-opus-4-1", _price(15, 75, 18.75, 30, 1.5)),  # Opus 4.1 (deprecated) — old tier
    ("claude-opus-4-0", _price(15, 75, 18.75, 30, 1.5)),  # Opus 4.0 (retired) — old tier
    ("claude-opus", _price(5, 25, 6.25, 10, 0.5)),  # Opus 4.8/4.7/4.6/4.5 + default
    ("claude-sonnet", _price(3, 15, 3.75, 6, 0.3)),  # Sonnet 5 (std)/4.6/4.5/4
    ("claude-haiku", _price(1, 5, 1.25, 2, 0.1)),
    ("claude", _price(5, 25, 6.25, 10, 0.5)),
    # Best-effort for non-Claude sources (no cache tiers).
    ("gpt-5", _price(1.25, 10, 1.25, 1.25, 0.125)),
    ("gpt-4", _price(2.5, 10, 2.5, 2.5, 1.25)),
    ("gemini", _price(1.25, 10, 1.25, 1.25, 0.3125)),
]


def pricing_for(model: str | None) -> dict | None:
    if not model:
        return None
    m = str(model).lower()
    for prefix, price in PRICING:
        if prefix in m:
            return price
    return None


def cache_write_tokens(u: dict) -> int:
    """Combined cache-write token count across both tiers (for display)."""
    split = (u.get("cacheWrite5m") or 0) + (u.get("cacheWrite1h") or 0)
    return split or (u.get("cacheWrite") or 0)


def cost_breakdown_of_model(model: str | None, u: dict | None) -> dict | None:
    """Per-category USD cost for one model's aggregated token usage; None if unpriced.

    Handles both the new usage shape ({cacheWrite5m, cacheWrite1h}) and the
    legacy one ({cacheWrite}, treated as 5m).
    """
    p = pricing_for(model)
    if not p or not u:
        return None
    cw5 = u.get("cacheWrite5m", u.get("cacheWrite", 0)) or 0
    cw1 = u.get("cacheWrite1h", 0) or 0
    return {
        "input": ((u.get("input") or 0) * p["input"]) / 1e6,
        "output": ((u.get("output") or 0) * p["output"]) / 1e6,
        "cacheWrite": (cw5 * p["cw5m"] + cw1 * p["cw1h"]) / 1e6,
        "cacheRead": ((u.get("cacheRead") or 0) * p["cacheRead"]) / 1e6,
    }


def cost_of_model(model: str | None, u: dict | None) -> float | None:
    """Total USD cost for one model's aggregated token usage; None if unpriced."""
    b = cost_breakdown_of_model(model, u)
    return sum(b.values()) if b is not None else None


def cost_breakdown_of(usage: dict | None) -> dict:
    """Aggregate cost breakdown (USD) across every model in a ``sessions.usage`` blob.

    ``usage`` is the decoded dict of ``{model_id: {input, output, ...}}``
    (PRD §7.2). Unpriced models are skipped. Always returns all four keys,
    zeroed when usage is empty/None.
    """
    totals = {"input": 0.0, "output": 0.0, "cacheWrite": 0.0, "cacheRead": 0.0}
    if not usage:
        return totals
    for model, u in usage.items():
        b = cost_breakdown_of_model(model, u)
        if b is not None:
            for k in totals:
                totals[k] += b[k]
    return totals


def cost_of(usage: dict | None) -> float:
    """Total USD cost across every model in a ``sessions.usage`` blob."""
    return sum(cost_breakdown_of(usage).values())
