# %% [markdown]
# # Instruction churn — re-prompt / correction density per session
#
# US-5(a): a rough, notebook-first heuristic for "how much am I re-prompting or
# correcting the agent mid-session?" A high churn score usually means the
# initial instruction was ambiguous or under-specified — the thing this
# project exists to make visible (see `docs/PRD.md` §6, "the point is
# consistency").
#
# This is intentionally a simple keyword heuristic, not a detector (those are
# deferred to `detect` per PRD §10/US-6). Runs top-to-bottom against a freshly
# collected store — including an empty one.

# %%
import re

import pandas as pd

import threadkeeper as tk

pd.set_option("display.max_colwidth", 80)

# %%
sessions = tk.sessions()
messages = tk.messages()
print(f"{len(sessions)} sessions, {len(messages)} messages in the store.")

# %% [markdown]
# ## Correction-keyword heuristic
#
# Flags a user message as a likely correction/re-prompt when it opens with (or
# strongly contains) a correction phrase. This is a coarse proxy, not ground
# truth — read the flagged messages before trusting the score for any one
# session.

# %%
CORRECTION_PATTERNS = [
    r"\bno[,.]",
    r"\bnot what i\b",
    r"\bthat'?s wrong\b",
    r"\bactually[,.]",
    r"\binstead\b",
    r"\brevert\b",
    r"\bundo\b",
    r"\btry again\b",
    r"\bdon'?t\b.*\binstead\b",
    r"\bwrong\b",
    r"\bstop\b",
    r"\bnever ?mind\b",
]
_CORRECTION_RE = re.compile("|".join(CORRECTION_PATTERNS), re.IGNORECASE)


def is_correction(text) -> bool:
    return bool(text) and bool(_CORRECTION_RE.search(text[:200]))


# %%
if messages.empty:
    print("No messages yet — run `thread-keeper collect --sweep` first.")
    churn = pd.DataFrame(columns=["session_id", "user_messages", "corrections", "churn_score"])
else:
    user_msgs = messages[messages["kind"] == "user"].copy()
    user_msgs["is_correction"] = user_msgs["text"].map(is_correction)

    churn = (
        user_msgs.groupby("session_id")
        .agg(user_messages=("text", "size"), corrections=("is_correction", "sum"))
        .reset_index()
    )
    churn["churn_score"] = churn["corrections"] / churn["user_messages"].clip(lower=1)
    churn = churn.merge(
        sessions[["id", "project_id", "source", "summary", "first_prompt"]],
        left_on="session_id",
        right_on="id",
        how="left",
    ).drop(columns=["id"])
    churn = churn.sort_values(["churn_score", "corrections"], ascending=False)

churn.head(20)

# %% [markdown]
# ## Drill into the top session's flagged correction messages

# %%
if not churn.empty and churn.iloc[0]["corrections"] > 0:
    top_session_id = churn.iloc[0]["session_id"]
    top_user_msgs = messages[(messages["session_id"] == top_session_id) & (messages["kind"] == "user")]
    flagged = top_user_msgs[top_user_msgs["text"].map(is_correction)]
    flagged[["seq", "ts", "text"]]
else:
    print("No corrections flagged yet — collect more sessions or loosen the keyword list above.")
