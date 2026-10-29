"""git and `jaah-forge` — the only two tools the connector delivers with.

Both authenticate through the node: git through the broker-only credential helper, `jaah-forge` through
the same per-task forge login. The connector holds no forge token and never passes one.

git runs with hooks disabled: the worktree holds files a model just wrote, and a hook (or a hook manager reading a
tracked config) would otherwise run that code outside Codex's sandbox. No child reaches the task: the agent speaks
to the node over its own stdio (ACP), and every child here gets stdin from /dev/null and its output captured."""
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
MERGE_ATTEMPTS = 3            # a refused `pr merge` is retried on later polls, then the watch gives up


class GitError(RuntimeError):
    pass


def git(worktree: str, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=worktree, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()[-500:]}")
    return proc.stdout


def checkout_branch(worktree: str, branch: str, base: str) -> None:
    """Switch to `branch`: a previous attempt's pushed branch when there is one, else fresh from origin/base."""
    git(worktree, "fetch", "-q", "origin", base)
    if git(worktree, "ls-remote", "--heads", "origin", branch).strip():
        git(worktree, "fetch", "-q", "origin", branch)
        git(worktree, "checkout", "-q", "-B", branch, "FETCH_HEAD")
    else:
        git(worktree, "checkout", "-q", "-B", branch, f"origin/{base}")


def untracked(worktree: str) -> set[str]:
    return {p for p in git(worktree, "ls-files", "--others", "--exclude-standard").splitlines() if p}


def stage(worktree: str, exclude: set[str] = frozenset()) -> list[str]:
    """Stage every change except `exclude` (files the gate produced); return the staged paths."""
    git(worktree, "add", "-A")
    if exclude:
        git(worktree, "reset", "-q", "--", *sorted(exclude))
    return [p for p in git(worktree, "diff", "--cached", "--name-only", "--no-renames").splitlines() if p]


def staged_diff(worktree: str) -> str:
    return git(worktree, "diff", "--cached", "--no-renames")


def commit(worktree: str, message: str) -> None:
    git(worktree, "commit", "-q", "--no-verify", "-m", message)


def delivery_paths(worktree: str, base: str) -> list[str]:
    """Every path the branch changes against its base: both sides of a rename, earlier attempts included."""
    out = git(worktree, "diff", "--name-only", "--no-renames", f"origin/{base}...HEAD")
    return [p for p in out.splitlines() if p]


def head(worktree: str) -> str:
    return git(worktree, "rev-parse", "HEAD").strip()


def push(worktree: str, branch: str) -> None:
    git(worktree, "push", "-q", "--no-verify", "--force-with-lease", "origin", f"HEAD:refs/heads/{branch}")


class ForgeError(RuntimeError):
    pass


def forge(*args: str) -> dict:
    try:
        proc = subprocess.run(["jaah-forge", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    except OSError as exc:
        raise ForgeError(f"jaah-forge could not run: {exc}") from exc
    if proc.returncode != 0:
        raise ForgeError(f"jaah-forge {' '.join(args[:2])} exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError as exc:
        raise ForgeError(f"jaah-forge {' '.join(args[:2])} printed no JSON") from exc
    return data if isinstance(data, dict) else {}


def open_pr(title: str, body: str, base: str, head_branch: str, *, draft: bool) -> dict:
    """The branch's open PR if an earlier attempt already opened one, else a new PR."""
    try:
        existing = forge("pr", "view", head_branch)
    except ForgeError:
        existing = {}
    if existing.get("state") == "open" and existing.get("number"):
        return existing
    fd, body_file = tempfile.mkstemp(prefix="jaah-pr-", suffix=".md")   # outside the worktree
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        args = ["pr", "create", "--title", title, "--body-file", body_file, "--base", base, "--head", head_branch]
        return forge(*args, *(["--draft"] if draft else []))
    finally:
        os.unlink(body_file)


def wait_for_merge(number: int, *, merge_when_clean: bool, deadline_s: float, sha: str | None = None,
                   sleep: Callable[[float], None] | None = None,
                   clock: Callable[[], float] | None = None) -> dict:
    """Poll `pr view` until the PR is merged, closed, stuck or out of time; returns the last view.

    With `merge_when_clean` the connector merges the PR itself once its checks are green (nothing was
    armed) — pinned to `sha` when given, so a push after a human approved the head is never merged.
    Otherwise auto-merge was armed and this only watches."""
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    end, stuck_for, merge_tries, view = clock() + deadline_s, 0, 0, {}
    while True:
        view = forge("pr", "view", str(number))
        state, merge_state = view.get("state"), view.get("merge_state")
        if state in ("merged", "closed"):
            return view
        if merge_when_clean and merge_state == "clean" and view.get("checks") in ("success", "none") \
                and merge_tries < MERGE_ATTEMPTS:
            merge_tries += 1
            try:
                forge("pr", "merge", str(number), *(["--sha", sha] if sha else []))
            except ForgeError as exc:
                view = {**view, "merge_error": str(exc)}
                if merge_tries >= MERGE_ATTEMPTS:
                    return view
        stuck = merge_state in STUCK_MERGE_STATES and view.get("checks") != "pending"
        stuck_for = stuck_for + 1 if stuck else 0
        if stuck_for >= STUCK_POLLS or clock() >= end:
            return view
        sleep(POLL_INTERVAL_S)
