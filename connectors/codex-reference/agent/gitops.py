"""git and `jaah-forge` — the only two tools the connector delivers with.

Both authenticate through the node: git through the broker-only credential helper, `jaah-forge` through
the same per-task forge login. The connector holds no forge token and never passes one."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from typing import Callable

# Delivery states (`jaah-forge pr view`'s `merge_state`) no amount of waiting fixes.
STUCK_MERGE_STATES = ("checks_failed", "dirty", "blocked", "review_required")
POLL_INTERVAL_S = 60          # every runner shares the forge's rate limit: never poll faster
STUCK_POLLS = 3               # a stuck state must persist this many polls (a re-run may be in flight)


class GitError(RuntimeError):
    pass


def git(worktree: str, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=worktree, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()[-500:]}")
    return proc.stdout


def checkout_branch(worktree: str, branch: str) -> None:
    """Switch to `branch`, continuing a previous attempt's pushed commits when there are any."""
    if subprocess.run(["git", "fetch", "-q", "origin", branch], cwd=worktree,
                      capture_output=True).returncode == 0:
        git(worktree, "checkout", "-q", "-B", branch, "FETCH_HEAD")
    else:
        git(worktree, "checkout", "-q", "-B", branch)


def changed_paths(worktree: str) -> list[str]:
    """Every path the working tree changes, staged or not, untracked included."""
    git(worktree, "add", "-A")
    return [p for p in git(worktree, "diff", "--cached", "--name-only").splitlines() if p]


def commits_ahead(worktree: str, base: str) -> int:
    out = git(worktree, "rev-list", "--count", f"origin/{base}..HEAD", check=False).strip()
    return int(out) if out.isdigit() else 0


def commit(worktree: str, message: str) -> None:
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", message)


def push(worktree: str, branch: str) -> None:
    git(worktree, "push", "-q", "--force-with-lease", "origin", f"HEAD:refs/heads/{branch}")


class ForgeError(RuntimeError):
    pass


def forge(*args: str) -> dict:
    proc = subprocess.run(["jaah-forge", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise ForgeError(f"jaah-forge {' '.join(args[:2])} exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError as exc:
        raise ForgeError(f"jaah-forge {' '.join(args[:2])} printed no JSON") from exc
    return data if isinstance(data, dict) else {}


def create_pr(title: str, body: str, base: str, head: str, *, draft: bool) -> dict:
    fd, body_file = tempfile.mkstemp(prefix="jaah-pr-", suffix=".md")   # outside the worktree
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        args = ["pr", "create", "--title", title, "--body-file", body_file, "--base", base, "--head", head]
        return forge(*args, *(["--draft"] if draft else []))
    finally:
        os.unlink(body_file)


def wait_for_merge(number: int, *, merge_when_clean: bool, deadline_s: float,
                   sleep: Callable[[float], None] | None = None,
                   clock: Callable[[], float] | None = None) -> dict:
    """Poll `pr view` until the PR is merged, closed, stuck or out of time; returns the last view.

    With `merge_when_clean` the connector merges the PR itself once its checks are green (nothing was
    armed); otherwise auto-merge was armed and this only watches."""
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    end, stuck_for, view = clock() + deadline_s, 0, {}
    while True:
        view = forge("pr", "view", str(number))
        state, merge_state = view.get("state"), view.get("merge_state")
        if state in ("merged", "closed"):
            return view
        if merge_when_clean and merge_state == "clean" and view.get("checks") in ("success", "none"):
            forge("pr", "merge", str(number))
            continue
        stuck_for = stuck_for + 1 if merge_state in STUCK_MERGE_STATES else 0
        if stuck_for >= STUCK_POLLS or clock() >= end:
            return view
        sleep(POLL_INTERVAL_S)
