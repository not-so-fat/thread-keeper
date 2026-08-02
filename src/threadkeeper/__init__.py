"""thread-keeper: local-first collector + pandas query surface for
Claude Code / Codex / Cursor agent-session logs.

Typical notebook usage::

    import threadkeeper as tk
    df = tk.sessions()
    msgs = tk.messages(df.iloc[0]["id"])
    tk.cost_of(df.iloc[0]["usage"])
"""

from .collect import collect_fast_path, collect_sweep
from .models import cost_breakdown_of, cost_of
from .query import messages, projects, sessions

__all__ = [
    "sessions",
    "messages",
    "projects",
    "cost_of",
    "cost_breakdown_of",
    "collect_sweep",
    "collect_fast_path",
]
