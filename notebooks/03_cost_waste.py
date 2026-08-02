# %% [markdown]
# # Cost & waste — per-model token totals + unused-tool signal
#
# US-5(c): a cost/waste pass over the store, per the Opik-cipx note's
# offline-reconstructable attribution (PRD §1, §7.2-7.3, Appendix B):
#
# 1. **Coverage by source** — which of Claude Code / Codex / Cursor actually
#    carry token usage (`has_usage`). Missing ≠ $0.
# 2. **Per-session / per-model list-price cost** via enriched `tk.sessions()`
#    columns and `tk.usage_long()` (F4.2 pricing tables).
# 3. **Tool-call frequency per session** — a first cut at "enabled-but-never-called"
#    waste. thread-keeper's logs only carry tools that were *called*, not the
#    full set that was *available* in a session, so a true unused-tool report
#    needs that extra signal — this notebook ships the calling side today.
#
# Runs top-to-bottom against a freshly collected store — including an empty
# one. Cursor usage is not ingested (latent `usageData` only).

# %%
import pandas as pd

import threadkeeper as tk

pd.set_option("display.max_colwidth", 60)

# %%
sessions = tk.sessions()
messages = tk.messages()
print(f"{len(sessions)} sessions, {len(messages)} messages in the store.")

# %% [markdown]
# ## Usage coverage by source
#
# Fraction of sessions with a non-empty `usage` blob. Codex fills from
# `token_count`; Claude Code from `message.usage`; Cursor stays unavailable.
# Note: `has_usage=True` with an unpriced model yields `cost_usd=NaN`
# (tokens present, cost unknown) — `groupby(...).sum()` skips those rows.

# %%
if sessions.empty:
    print("No sessions yet — run `thread-keeper collect --sweep` first.")
    coverage = pd.DataFrame(columns=["source", "sessions", "with_usage", "has_usage_rate"])
else:
    coverage = (
        sessions.groupby("source", dropna=False)
        .agg(sessions=("id", "count"), with_usage=("has_usage", "sum"))
        .reset_index()
    )
    coverage["has_usage_rate"] = coverage["with_usage"] / coverage["sessions"]

coverage

# %% [markdown]
# ## Per-session cost estimate (sessions with usage only)

# %%
cost_cols = [
    "id",
    "source",
    "summary",
    "cost_usd",
    "input_tokens",
    "output_tokens",
    "cache_write_tokens",
    "cache_read_tokens",
    "models",
]
if sessions.empty or not sessions["has_usage"].any():
    print("No sessions with usage yet.")
    cost_df = pd.DataFrame(columns=cost_cols)
else:
    cost_df = (
        sessions.loc[sessions["has_usage"], cost_cols]
        .sort_values("cost_usd", ascending=False)
        .reset_index(drop=True)
    )

cost_df.head(20)

# %% [markdown]
# ## Cost by source (Claude Code vs. Codex vs. Cursor)

# %%
if not cost_df.empty:
    cost_df.groupby("source")["cost_usd"].sum().sort_values(ascending=False)
else:
    print("Nothing to aggregate yet.")

# %% [markdown]
# ## Per-model token totals + cost across the whole store

# %%
long = tk.usage_long()
if long.empty:
    per_model = pd.DataFrame(
        columns=["model", "input_tokens", "output_tokens", "cache_read_tokens", "cost_usd"]
    )
    print("No per-model usage rows yet.")
else:
    per_model = (
        long.groupby("model")[["input_tokens", "output_tokens", "cache_read_tokens", "cost_usd"]]
        .sum()
        .sort_values("input_tokens", ascending=False)
    )

per_model

# %% [markdown]
# ## Tool-call frequency per session (unused-tool signal, calling side)

# %%
if messages.empty:
    tool_freq = pd.DataFrame(columns=["session_id", "tool_name", "calls"])
else:
    tool_calls = messages[messages["kind"] == "tool_use"]
    tool_freq = tool_calls.groupby(["session_id", "tool_name"]).size().reset_index(name="calls")
    tool_freq = tool_freq.sort_values("calls", ascending=False)

tool_freq.head(30)

# %% [markdown]
# ## Tools called exactly once across the whole store
#
# A cheap proxy for "configured but barely used": tools that only ever fire
# once per session are candidates worth checking against what's actually
# still needed.

# %%
if not tool_freq.empty:
    tool_totals = tool_freq.groupby("tool_name")["calls"].sum().sort_values()
    tool_totals[tool_totals <= tool_totals.median()]
else:
    print("No tool calls recorded yet.")
