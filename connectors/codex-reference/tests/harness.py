"""The test bench: a fake node daemon (the SDK's own `fake_daemon`), a real local git origin + worktree, and
fake `codex` / `jaah-forge` executables first on PATH. Nothing here reaches a network."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "agent"))

import jaah_agent  # noqa: E402  (CI: the SDK checkout's python/ on PYTHONPATH)
from fake_daemon import FakeDaemon, ok  # noqa: E402  (CI: the SDK checkout's python/tests on PYTHONPATH)

SECRET = "sk-proj-TestOnlyKey0123456789abcdef"
FRAGMENT = f"{SECRET[:8]}**{SECRET[-4:]}"   # how Codex itself prints a key in its 401 errors
PR_URL = "https://github.com/acme/app/pull/7"

FAKE_CODEX = r'''#!/usr/bin/env python3
"""Fake `codex exec`: plays the next pass of the plan in $FAKE_CODEX_PLAN."""
import json, os, sys
args = sys.argv[1:]
state = os.environ["FAKE_CODEX_STATE"]
with open(os.environ["FAKE_CODEX_PLAN"]) as fh:
    plan = json.load(fh)
n = len(open(state).read().splitlines()) if os.path.exists(state) else 0
p = plan[min(n, len(plan) - 1)]
key = os.environ.get("OPENAI_API_KEY", "")
with open(state, "a") as fh:
    fh.write(json.dumps({"prompt": args[-1], "args": args[:-1],
                         "key_ok": key == os.environ.get("FAKE_CODEX_EXPECT_KEY"),
                         "codex_key_ok": os.environ.get("CODEX_API_KEY") == key,
                         "home": os.environ.get("CODEX_HOME"),
                         "socket_leaked": "JAAH_AGENT_SOCKET" in os.environ}) + "\n")
cwd = args[args.index("--cd") + 1]
for path in p.get("delete") or []:
    os.remove(os.path.join(cwd, path))
for src, dst in (p.get("rename") or {}).items():
    os.makedirs(os.path.dirname(os.path.join(cwd, dst)) or ".", exist_ok=True)
    os.rename(os.path.join(cwd, src), os.path.join(cwd, dst))
for path, body in (p.get("files") or {}).items():
    full = os.path.join(cwd, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    open(full, "w").write(body.replace("{KEY}", key))
print(json.dumps({"type": "thread.started", "thread_id": "t1"}))
if p.get("leak"):
    sys.stderr.write(f"ERROR Incorrect API key provided: {key[:8]}**{key[-4:]}. key={key}\n")
    print(json.dumps({"type": "error", "message": f"401 Unauthorized: Incorrect API key provided: {key[:8]}**{key[-4:]}"}))
if p.get("usage"):
    print(json.dumps({"type": "turn.completed", "usage": p["usage"]}))
if p.get("result") is not None:
    open(args[args.index("--output-last-message") + 1], "w").write(json.dumps(p["result"]))
sys.exit(p.get("exit", 0))
'''

FAKE_FORGE = r'''#!/usr/bin/env python3
"""Fake `jaah-forge`: records each call; PR state lives in $FAKE_FORGE_STATE."""
import json, os, sys
args = sys.argv[1:]
log, state_path = os.environ["FAKE_FORGE_LOG"], os.environ["FAKE_FORGE_STATE"]
state = json.load(open(state_path)) if os.path.exists(state_path) else {}
entry = {"args": args}
if args[:2] == ["pr", "create"]:
    entry["body"] = open(args[args.index("--body-file") + 1]).read()
with open(log, "a") as fh:
    fh.write(json.dumps(entry) + "\n")
verb = args[:2]
if verb == ["pr", "view"] and not args[2].isdigit() and not state.get("created"):
    sys.stderr.write("jaah-forge: no PR for that branch\n")
    sys.exit(3)
if verb == ["pr", "auto-merge"] and state.get("fail_arm"):
    sys.stderr.write("jaah-forge: auto-merge is not enabled for this repository\n")
    sys.exit(3)
if verb == ["pr", "create"] and state.get("fail_create"):
    sys.stderr.write("jaah-forge: forge error 502\n")
    sys.exit(3)
pr = {"number": 7, "url": os.environ["FAKE_PR_URL"], "head": "x", "base": "main",
      "draft": state.get("draft", False), "auto_merge": state.get("armed", False)}
if verb == ["pr", "create"]:
    state["draft"] = "--draft" in args
    state["created"] = True
    pr.update(draft=state["draft"], state="open", merge_state="pending", checks="pending")
elif verb == ["pr", "auto-merge"]:
    state["armed"] = state.get("arm_result", True)
    pr["auto_merge"] = state["armed"]
    if state["armed"]:
        state["merged"] = state.get("merge_on_arm", True)
elif verb == ["pr", "merge"]:
    if state.get("fail_merge"):
        sys.stderr.write("jaah-forge: base branch policy prohibits the merge\n")
        sys.exit(3)
    state["merged"] = True
elif verb == ["pr", "view"]:
    if args[2].isdigit():
        state["views"] = state.get("views", 0) + 1
        if state.get("merge_after_views") and state["views"] >= state["merge_after_views"]:
            state["merged"] = True
    pr.update(state="merged" if state.get("merged") else "open",
              merge_state=state.get("merge_state", "clean"), checks=state.get("checks", "success"))
json.dump(state, open(state_path, "w"))
print(json.dumps(pr))
'''


def _executable(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def _git(cwd: str, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class Bench(unittest.TestCase):
    """One task run end to end. Subclasses set `plan` (Codex passes) and drive `run_agent()`."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="codex-ref-test-")
        bin_dir = os.path.join(self.tmp, "bin")
        os.mkdir(bin_dir)
        _executable(os.path.join(bin_dir, "codex"), FAKE_CODEX)
        _executable(os.path.join(bin_dir, "jaah-forge"), FAKE_FORGE)
        # A real origin and worktree, so commit/push/branch behaviour is git's own.
        self.origin = os.path.join(self.tmp, "origin.git")
        self.worktree = os.path.join(self.tmp, "worktree")
        _git(self.tmp, "init", "-q", "--bare", "-b", "main", self.origin)
        _git(self.tmp, "clone", "-q", self.origin, self.worktree)
        for k, v in (("user.email", "test@example.com"), ("user.name", "Test")):
            _git(self.worktree, "config", k, v)
        with open(os.path.join(self.worktree, "README.md"), "w") as fh:
            fh.write("app\n")
        _git(self.worktree, "add", "-A")
        _git(self.worktree, "commit", "-q", "-m", "init")
        _git(self.worktree, "push", "-q", "origin", "HEAD:main")

        self.plan_path = os.path.join(self.tmp, "plan.json")
        self.codex_state = os.path.join(self.tmp, "codex-calls.jsonl")
        self.forge_log = os.path.join(self.tmp, "forge-calls.jsonl")
        self.forge_state = os.path.join(self.tmp, "forge-state.json")
        self.events_path = os.path.join(self.tmp, "events.jsonl")
        env = {
            "PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
            "FAKE_CODEX_PLAN": self.plan_path, "FAKE_CODEX_STATE": self.codex_state,
            "FAKE_CODEX_EXPECT_KEY": SECRET, "FAKE_FORGE_LOG": self.forge_log,
            "FAKE_FORGE_STATE": self.forge_state, "FAKE_PR_URL": PR_URL,
            "JAAH_WORKTREE": self.worktree,
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("JAAH_CODEX_MODEL", None)

        self.autonomous = False
        self.project = {"name": "app", "repo": "acme/app", "integration_branch": "main",
                        "branch_prefix": "", "gate_quick_cmd": "", "gate_full_cmd": "", "push_enabled": True,
                        "has_repo": True}
        self.roster = [{"name": "acme/openai", "type": "openai_api_key", "description": "", "fields": ["api_key"]}]
        self.answers: list[dict] = []   # queued operator answers, one per agent.ask round
        self.asked: list[dict] = []
        self.daemon = FakeDaemon(self.handle)
        self.addCleanup(self.daemon.close)

    # ── the fake daemon ──────────────────────────────────────────────────────────────────────────
    def handle(self, verb, args):
        if verb == "agent.task":
            return ok({"task": {"id": 42, "title": "Add a hello module", "body": "Add src/hello.py.",
                                "persona": "coder", "autonomous": self.autonomous, "out_of_scope": "docs/"},
                       "project": self.project,
                       "agent": {"id": "a1", "display_name": "Codex (reference)", "version": "0.1.0",
                                 "commit": "0" * 40},
                       "security_gate": "human"})
        if verb == "task.credential_list":
            return ok({"credentials": self.roster})
        if verb == "task.credential_get":
            return ok({"name": args["name"], "type": "openai_api_key", "config": {"api_key": SECRET}})
        if verb == "agent.ask":
            self.asked.append(args)
            return ok({"ids": [len(self.asked)]})
        if verb == "agent.answers":
            answer = self.answers.pop(0) if self.answers else {"status": "dismissed"}
            return ok({"all_resolved": True, "rounds": [{"id": args["ids"][0], "selections": [], "text": "",
                                                         **answer}]})
        if verb == "task.settle":
            return ok({"stored": True, "settled": True, "state": "done"})
        return ok({})

    # ── driving and reading ──────────────────────────────────────────────────────────────────────
    def set_plan(self, *passes: dict) -> None:
        with open(self.plan_path, "w") as fh:
            json.dump(list(passes), fh)

    def run_agent(self) -> None:
        """Run the connector's real entrypoint, `main()`, against this bench."""
        import main

        env = {"JAAH_AGENT_SOCKET": self.daemon.path, "JAAH_AGENT_EVENTS": self.events_path}
        # Every fake answer is resolved on the first poll, so the SDK's own ask loop never sleeps here.
        with mock.patch.dict(os.environ, env), mock.patch("gitops.time.sleep", lambda _s: None):
            self.assertEqual(main.main(), 0)
        terminal = self.calls("task.settle") + self.calls("task.fail")
        self.assertEqual(len(terminal), 1, f"exactly one terminal call, got {terminal}")

    def forge_state_set(self, **values) -> None:
        state = json.load(open(self.forge_state)) if os.path.exists(self.forge_state) else {}
        with open(self.forge_state, "w") as fh:
            json.dump({**state, **values}, fh)

    def commit_on_origin_branch(self, files: dict) -> None:
        """An earlier attempt's pushed commit on fleet/task-42."""
        _git(self.worktree, "checkout", "-q", "-b", "fleet/task-42")
        for path, body in files.items():
            full = os.path.join(self.worktree, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write(body)
        _git(self.worktree, "add", "-A")
        _git(self.worktree, "commit", "-q", "-m", "wip from attempt 1")
        _git(self.worktree, "push", "-q", "origin", "fleet/task-42")
        _git(self.worktree, "checkout", "-q", "main")

    def calls(self, verb: str) -> list[dict]:
        return [args for v, args in self.daemon.calls if v == verb]

    def codex_calls(self) -> list[dict]:
        return _jsonl(self.codex_state)

    def forge_calls(self) -> list[dict]:
        return _jsonl(self.forge_log)

    def forge_verbs(self) -> list[str]:
        return [" ".join(c["args"][:2]) for c in self.forge_calls()]

    def events(self) -> list[dict]:
        return _jsonl(self.events_path)

    def everything_written(self) -> str:
        """Every byte the connector sent or wrote: daemon calls, events file, forge calls, commits."""
        log = _git(self.origin, "log", "--all", "-p") if _git(self.origin, "branch", "--list") else ""
        return "\n".join([json.dumps(self.daemon.calls), json.dumps(self.events()),
                          json.dumps(self.forge_calls()), log])


def _jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def result(status="done", summary="Added src/hello.py.", commit_title="feat: add hello module", question="",
           options=(), blocker_kind="none", security_surfaces=()) -> dict:
    return {"status": status, "summary": summary, "commit_title": commit_title, "question": question,
            "options": list(options), "blocker_kind": blocker_kind, "security_surfaces": list(security_surfaces)}


USAGE = {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 50}
HELLO = {"src/hello.py": "def hello():\n    return 'hello'\n"}
