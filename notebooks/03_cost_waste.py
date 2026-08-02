# %% [markdown]
# # Cost & waste — per-model token totals + unused-tool signal
#
# US-5(c): a cost/waste pass over the store, per the Opik-cipx note's
# offline-reconstructable attribution (PRD §1, §7.2-7.3, Appendix B):
#
# 1. **Per-model token totals + list-price cost estimate** (`tk.cost_of`,
#    `tk.cost_breakdown_of` — F4.2, ported from Chronicle's `src/models.js`).
# 2. **Tool-call frequency per session** — a first cut at "enabled-but-never-called"
#    waste. thread-keeper's logs only carry tools that were *called*, not the
#    full set that was *available* in a session, so a true unused-tool report
#    needs that extra signal (see the "Deviations" note in the project's
#    top-level summary) — this notebook ships the calling side of that
#    picture today.
#
# Runs top-to-bottom against a freshly collected store — including an empty
# one.

# %%
import pandas as pd

import threadkeeper as tk

pd.set_option("display.max_colwidth", 60)

# %%
sessions = tk.sessions()
messages = tk.messages()
print(f"{len(sessions)} sessions, {len(messages)} messages in the store.")

# %% [markdown]
# ## Per-session cost estimate

# %%
if sessions.empty:
    print("No sessions yet — run `thread-keeper collect --sweep` first.")
    cost_df = pd.DataFrame(columns=["id", "source", "summary", "input", "output", "cacheWrite", "cacheRead", "total_cost_usd"])
else:
    breakdowns = sessions["usage"].map(tk.cost_breakdown_of)
    cost_df = pd.DataFrame(list(breakdowns))
    cost_df["total_cost_usd"] = cost_df.sum(axis=1)
    cost_df = pd.concat([sessions[["id", "source", "summary"]].reset_index(drop=True), cost_df], axis=1)
    cost_df = cost_df.sort_values("total_cost_usd", ascending=False)

cost_df.head(20)

# %% [markdown]
# ## Cost by source (Claude Code vs. Codex vs. Cursor)

# %%
if not cost_df.empty:
    cost_df.groupby("source")["total_cost_usd"].sum().sort_values(ascending=False)
else:
    print("Nothing to aggregate yet.")

# %% [markdown]
# ## Per-model token totals across the whole store

# %%
if sessions.empty:
    per_model = pd.DataFrame(columns=["model", "input", "output", "cacheRead"])
else:
    model_rows = []
    for usage in sessions["usage"]:
        if not usage:
            continue
        for model, u in usage.items():
            model_rows.append({"model": model, "input": u.get("input", 0), "output": u.get("output", 0), "cacheRead": u.get("cacheRead", 0)})
    per_model = (
        pd.DataFrame(model_rows).groupby("model").sum().sort_values("input", ascending=False)
        if model_rows
        else pd.DataFrame(columns=["input", "output", "cacheRead"])
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
