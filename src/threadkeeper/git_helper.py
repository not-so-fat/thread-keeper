"""Optional git snapshot mapping — ports Chronicle's ``server/git.js``.

Deferred per PRD §7.5: a library-only helper, not wired into v1 notebooks.
The stale-knowledge notebook may opt into ``commit_at`` directly. All
operations are read-only against the target repo (no writes, no network).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo: str | Path, args: list[str]) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def is_git_repo(repo: str | Path) -> bool:
    try:
        if not repo or not Path(repo).exists():
            return False
        return _git(repo, ["rev-parse", "--is-inside-work-tree"]).strip() == "true"
    except (subprocess.CalledProcessError, OSError):
        return False


def _describe_commit(repo: str | Path, commit_hash: str) -> dict:
    out = _git(repo, ["show", "-s", "--date=iso-strict", "--pretty=format:%H\t%ad\t%s", commit_hash])
    h, date, *subject = out.split("\t")
    return {"hash": h, "date": date, "subject": "\t".join(subject)}


def commit_at(repo: str | Path, ts: str) -> dict | None:
    """Nearest commit at-or-before ``ts`` (ISO string). Falls back to the oldest commit."""
    if not is_git_repo(repo):
        return None
    try:
        commit_hash = _git(repo, ["rev-list", "-1", f"--before={ts}", "--all"]).strip()
        if commit_hash:
            return _describe_commit(repo, commit_hash)
        oldest = _git(repo, ["rev-list", "--max-parents=0", "--all"]).strip().split("\n")[0]
        if not oldest:
            return None
        info = _describe_commit(repo, oldest)
        info["beforeHistory"] = True
        return info
    except (subprocess.CalledProcessError, OSError):
        return None
