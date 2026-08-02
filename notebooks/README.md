# Starter notebooks (US-5)

Stored as [jupytext](https://jupytext.readthedocs.io/) `.py` "percent" files
(OD-5) so they diff like normal Python and don't carry notebook JSON noise in
git. Each file is also directly runnable as a plain script (`python
notebooks/01_instruction_churn.py`) — useful for a quick smoke test without
Jupyter installed.

## Setup

```bash
uv sync --extra jupyter          # installs jupyter on top of the base deps
thread-keeper collect --sweep    # populate the store first (cold-start backfill)
uv run jupytext --to notebook --update notebooks/*.py   # generate .ipynb pairs
uv run jupyter lab notebooks/
```

Pair a notebook back to its `.py` (so edits in Jupyter stay diff-friendly) by
opening it in Jupyter with the Jupytext extension, or by re-running
`jupytext --to notebook --update` after editing the `.py` file directly.

## Notebooks

| File | Question it answers |
|---|---|
| `01_instruction_churn.py` | Which sessions had the most correction / re-prompt churn? |
| `02_stale_knowledge.py` | Which sessions reference files/symbols that no longer exist? Is CLAUDE.md/AGENTS.md growing unbounded? |
| `03_cost_waste.py` | Per-model token totals and cost (via `tk.cost_of`); which tools are called the least (unused-tool signal)? |

Each notebook runs top-to-bottom against a freshly collected store, including
an empty one (no manual edits required — that's the US-5 acceptance bar).

These are deliberately manual, exploratory notebooks, not a `detect` command
(see `docs/PRD.md` §10/US-6) — promote a heuristic to a Req once it proves
itself here.
