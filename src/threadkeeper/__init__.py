"""thread-keeper: local-first collector + pandas query surface for
Claude Code / Codex / Cursor agent-session logs.

Typical notebook usage::

    import threadkeeper as tk
    df = tk.sessions()
    df.loc[df["has_usage"]].sort_values("cost_usd", ascending=False)
    msgs = tk.messages(df.iloc[0]["id"])
    tk.usage_long().groupby("model")["cost_usd"].sum()
"""

from .collect import collect_fast_path, collect_sweep
from .models import cost_breakdown_of, cost_of, token_totals_of
from .query import messages, projects, sessions, usage_long

__all__ = [
    "sessions",
    "messages",
    "projects",
    "usage_long",
    "cost_of",
    "cost_breakdown_of",
    "token_totals_of",
    "collect_sweep",
    "collect_fast_path",
]
