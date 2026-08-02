# %% [markdown]
# # Stale-knowledge probes
#
# US-5(b): two offline probes for knowledge that may have gone stale since a
# session ran:
#
# 1. **Dead file references** — file paths a session's tool calls touched that
#    no longer exist on disk today.
# 2. **CLAUDE.md / AGENTS.md growth over time** — memory files tend to only
#    grow; unbounded growth is itself a signal worth watching (PRD §6).
#
# The git-commit-at-timestamp mapping (`threadkeeper.git_helper.commit_at`,
# PRD §7.5) is an optional opt-in for a sharper "was this file deleted, or did
# the repo just move on since this session's commit?" — deliberately not
# wired in by default (PRD §10). Runs top-to-bottom against a freshly
# collected store — including an empty one.

# %%
import os

import pandas as pd

import threadkeeper as tk
from threadkeeper import git_helper  # noqa: F401  (optional opt-in, see markdown above)

pd.set_option("display.max_colwidth", 100)

# %%
sessions = tk.sessions()
messages = tk.messages()
projects = tk.projects()
print(f"{len(sessions)} sessions, {len(projects)} projects in the store.")

# %% [markdown]
# ## 1. Dead file references
#
# Scans `tool_input` for a `path`/`file_path`/`command` field, resolves it
# against the session's project root, and flags anything missing today.

# %%
def _extract_paths(tool_input: dict | None) -> list[str]:
    if not isinstance(tool_input, dict):
        return []
    found = []
    for key in ("path", "file_path", "filePath", "notebook_path"):
        v = tool_input.get(key)
        if isinstance(v, str):
            found.append(v)
    return found


if messages.empty:
    print("No messages yet — run `thread-keeper collect --sweep` first.")
    dead_refs = pd.DataFrame(columns=["session_id", "project_path", "referenced_path", "resolved_path"])
else:
    tool_calls = messages[messages["kind"] == "tool_use"].merge(
        sessions[["id", "project_id"]], left_on="session_id", right_on="id", how="left"
    )
    tool_calls = tool_calls.merge(projects[["id", "path"]], left_on="project_id", right_on="id", suffixes=("", "_project"))

    rows = []
    for _, row in tool_calls.iterrows():
        for ref in _extract_paths(row["tool_input"]):
            resolved = ref if os.path.isabs(ref) else os.path.join(row["path"], ref)
            rows.append({"session_id": row["session_id"], "project_path": row["path"], "referenced_path": ref, "resolved_path": resolved})

    refs = pd.DataFrame(rows)
    if refs.empty:
        dead_refs = refs
    else:
        refs["exists_today"] = refs["resolved_path"].map(os.path.exists)
        dead_refs = refs[~refs["exists_today"]].drop_duplicates(subset=["session_id", "resolved_path"])

dead_refs.head(30)

# %% [markdown]
# ## 2. CLAUDE.md / AGENTS.md size over time
#
# For every known project root, records the current size of any memory file
# found there. Re-running this notebook after future collects lets you diff
# snapshots over time (append your own timestamped log if you want a trend
# line — deliberately not persisted by thread-keeper itself, which stays
# read-only on your source trees).

# %%
MEMORY_FILENAMES = ["CLAUDE.md", "AGENTS.md", ".cursor/rules", "GEMINI.md"]

memory_rows = []
for _, proj in projects.iterrows():
    for name in MEMORY_FILENAMES:
        full = os.path.join(proj["path"], name)
        if os.path.isfile(full):
            memory_rows.append({"project": proj["name"], "file": name, "size_bytes": os.path.getsize(full)})

memory_sizes = pd.DataFrame(memory_rows).sort_values("size_bytes", ascending=False) if memory_rows else pd.DataFrame(
    columns=["project", "file", "size_bytes"]
)
memory_sizes
