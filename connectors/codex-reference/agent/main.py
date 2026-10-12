"""Codex (reference) — the OpenAI Codex CLI as a Jaah coding agent, through the Jaah Agent SDK.

    task() → the roster's OpenAI key → `codex exec` in the worktree → ask when blocked → report usage →
    gate → commit + push + PR via jaah-forge (watched until merged) or settle no_pr — honouring security_gate.

Every run ends in exactly ONE terminal call (`settle` or `fail`): `Run` records it, and `main()` turns
anything unexpected into a named `fail` unless one was already made. The README walks through each step."""
from __future__ import annotations

import os
import subprocess
import sys

import jaah_agent

import codex_run
import gitops
import security

MAX_ASK_ROUNDS = 3        # questions per task
MAX_PASSES = 6            # Codex passes per task, whatever drives them; past this the task fails, never loops
MERGE_DEADLINE_S = 6 * 60 * 60
GATE_TIMEOUT_S = 45 * 60
BLOCKER_HEADERS = ("Credential", "Access", "Human act", "External")   # the Full Auto blocker licence
APPROVE, DO_NOT_MERGE = "Approve merge", "Do not merge"
OPENAI_CREDENTIAL_TYPES = ("openai_api_key",)
KEY_FIELDS = ("api_key", "token", "secret_key")


class Stop(Exception):
    """The run has made its terminal call; unwind without doing anything else."""


class Run:
    """One task: the agent, the context, and whether the terminal call has been made."""

    def __init__(self, agent) -> None:
        self.agent = agent
        self.terminal = False      # a terminal call was ANSWERED (a refused settle records nothing)
        self.attempted = False     # a terminal call was SENT (a lost answer may still have landed)
        self.pr_url = ""
        self.api_key = ""

    def settle(self, summary: str, terminal_type: str, **refs) -> None:
        self.attempted = True
        self.agent.settle(summary, terminal_type, **refs)
        self.terminal = True
        raise Stop

    def fail(self, reason: str) -> None:
        self.attempted = True
        reason = codex_run.scrub(reason, self.api_key)
        if self.pr_url and self.pr_url not in reason:
            reason += f" PR: {self.pr_url}"
        self.agent.fail(_short(reason, 4000))
        self.terminal = True
        raise Stop


def main() -> int:
    agent = jaah_agent.connect()
    r = Run(agent)
    try:
        run(r)
    except Stop:
        pass
    except Exception as exc:  # noqa: BLE001 — every path must end in a terminal call (CONTRIBUTING §5.1)
        lost_answer = isinstance(exc, jaah_agent.JaahUnavailable) and r.attempted
        if not r.terminal and not lost_answer:
            try:
                r.fail(f"Codex (reference) stopped unexpectedly: {type(exc).__name__}: {exc}")
            except Stop:
                pass
    return 0


def run(r: Run) -> None:
    agent = r.agent
    ctx = agent.task()
    task, project = ctx["task"], ctx.get("project") or {}
    worktree = ctx.get("worktree") or os.getcwd()
    agent.log("codex-reference starting", task_id=task["id"], persona=task.get("persona"),
              autonomous=bool(task.get("autonomous")), model=codex_run.model())
    if project.get("push_enabled") is False:
        r.fail("This project does not allow pushes (push_enabled is off), so a coding agent cannot deliver a PR "
               "here. Run this task with a non-coding agent, or enable pushes on the project.")

    r.api_key = openai_key(r, task)
    branch = f"{project.get('branch_prefix') or ''}fleet/task-{task['id']}"
    base = project.get("integration_branch") or "main"
    gitops.checkout_branch(worktree, branch, base)

    passes = Passes(r, task, project, worktree)
    result = passes.until_settled()
    commit_pass(r, worktree, task, result, passes.gate_outputs)
    if not gitops.delivery_paths(worktree, base):
        r.settle(_short(f"Codex made no change: {result['summary'] or 'nothing to do'}"), "no_pr",
                 no_pr_reason="no_code_needed",
                 no_pr_evidence=_short(f"Codex ran in the worktree and the branch changes no file against "
                                       f"{base}. Its account: {result['summary'] or '(none)'}", 4000))

    gate = project.get("gate_full_cmd") or project.get("gate_quick_cmd")
    if gate:
        ok, tail = passes.gate(gate)
        if not ok:
            result = passes.fix_gate(tail)
            commit_pass(r, worktree, task, result, passes.gate_outputs)
            ok, tail = passes.gate(gate)
            if not ok:
                r.fail(f"The project gate `{gate}` is still red after one fix pass; nothing was pushed. "
                       f"Last output:\n{tail}")

    # Classify the WHOLE delivery — every commit on the branch, both sides of every rename.
    surfaces = security.classify(gitops.delivery_paths(worktree, base), result.get("security_surfaces"))
    gitops.push(worktree, branch)
    deliver(r, task, base, branch, worktree, result, surfaces)


def commit_pass(r: Run, worktree: str, task, result: dict, exclude: set[str]) -> None:
    """Commit what the last pass changed — unless it carries the key, in which case nothing ships."""
    if not gitops.stage(worktree, exclude):
        return
    if codex_run.leaks(gitops.staged_diff(worktree), r.api_key):
        r.fail("Codex wrote the OpenAI key (or part of it) into the change. Nothing was committed or pushed; "
               "rotate the key if the worktree may have been shared.")
    gitops.commit(worktree, f"{_title(task, result)}\n\n{result['summary']}\n\nJaah task {task['id']}.")


def openai_key(r: Run, task) -> str:
    """The OpenAI key from the task's credential roster. Missing is a blocker only a human can clear."""
    agent = r.agent
    for attempt in range(2):
        cred = _pick_openai(agent.credentials())
        if cred:
            config = (agent.credential(cred["name"]) or {}).get("config") or {}
            for name in KEY_FIELDS:
                if config.get(name):
                    agent.log("using OpenAI credential", credential=cred["name"])
                    return str(config[name])
            r.fail(f"Credential {cred['name']} has no api_key field, so Codex cannot authenticate.")
        if attempt == 0:
            answer = agent.ask([{
                "question": "Codex needs an OpenAI API key, and no OpenAI credential is on this task's roster. "
                            "Add one (type OpenAI API key, or a name containing 'openai') that this project "
                            "can use, then answer.",
                "header": "Credential",
                "options": [{"label": "Added it", "description": "Re-read the roster and continue."},
                            {"label": "Stop", "description": "Fail the task; nothing was changed."}],
            }])[0]
            if answer["status"] != "answered" or answer["selections"] != ["Added it"]:
                break
    r.fail("No OpenAI API key credential is on this task's roster, so Codex cannot run. Add one usable by this "
           "project (type OpenAI API key, or a name containing 'openai') and start the task again.")


def _pick_openai(roster: list[dict]) -> dict | None:
    for cred in roster:
        if cred.get("type") in OPENAI_CREDENTIAL_TYPES:
            return cred
    for cred in roster:
        if "openai" in str(cred.get("name", "")).lower():
            return cred
    return None


class Passes:
    """Codex passes for one task: bounded in number, each one's usage reported, blocks turned into asks."""

    def __init__(self, r: Run, task, project: dict, worktree: str) -> None:
        self.r, self.task, self.project, self.worktree = r, task, project, worktree
        self.answers: list[str] = []
        self.asked = 0
        self.count = 0
        self.gate_outputs: set[str] = set()

    def one(self, gate_feedback: str = "") -> dict:
        if self.count >= MAX_PASSES:
            self.r.fail(f"Codex did not finish within {MAX_PASSES} passes.")
        self.count += 1
        out = codex_run.run(prompt(self.task, self.project, self.answers, gate_feedback), self.worktree,
                            self.r.api_key)
        self.r.agent.report_usage(codex_run.model(),
                                  max(0, out.usage.input_tokens - out.usage.cached_input_tokens),
                                  out.usage.output_tokens, cache_read=out.usage.cached_input_tokens)
        if out.result is None:
            why = "; ".join(out.errors[-3:]) or out.stderr_tail.strip()[-500:] or f"exit {out.exit_code}"
            self.r.fail(f"Codex did not finish (exit {out.exit_code}): {why}")
        result = codex_run.scrub_result(out.result, self.r.api_key)
        self.r.agent.log("codex finished a pass", status=result["status"], summary=_short(result["summary"], 500))
        return result

    def until_settled(self, gate_feedback: str = "") -> dict:
        result = self.one(gate_feedback)
        while result["status"] == "blocked":
            self.answers.append(self.resolve_block(result))
            result = self.one()
        return result

    def fix_gate(self, tail: str) -> dict:
        return self.until_settled(tail)

    def resolve_block(self, result: dict) -> str:
        """Turn a blocked Codex pass into the text the next pass reads."""
        kind = result["blocker_kind"] if result["blocker_kind"] in BLOCKER_HEADERS else "decision"
        question = result["question"].strip() or "Codex is blocked and gave no question."
        if self.task.get("autonomous") and kind not in BLOCKER_HEADERS:
            # Full Auto: a decision is never a question. Tell Codex to decide and document it.
            self.r.agent.log("full auto: decision returned to codex", question=_short(question, 500))
            return (f"Q: {question}\nA: (Full Auto) Nobody will answer decisions. Choose the most defensible "
                    f"option consistent with this repository, state the assumption in your summary, and finish.")
        if self.asked >= MAX_ASK_ROUNDS:
            self.r.fail(f"Codex is still blocked after {MAX_ASK_ROUNDS} questions. Last: {question}")
        self.asked += 1
        options = [o for o in result["options"] if o.strip()][:4] or ["Proceed", "Stop"]
        header = kind if kind in BLOCKER_HEADERS else "Decision"
        answer = self.r.agent.ask([{"question": question, "header": header, "options": options}])[0]
        if answer["status"] == "dismissed":
            return (f"Q: {question}\nA: (dismissed by the operator) Proceed on the most defensible assumption "
                    f"and state it in your summary.")
        picked = ", ".join(answer["selections"]) or "(no option)"
        return f"Q: {question}\nA: {picked}" + (f" — {answer['text']}" if answer.get("text") else "")

    def gate(self, command: str) -> tuple[bool, str]:
        """Run the project's gate on the committed tree. Files it creates are never committed."""
        before = gitops.untracked(self.worktree)
        try:
            proc = subprocess.run(command, shell=True, cwd=self.worktree, env=gitops.safe_env(),
                                  capture_output=True, text=True, timeout=GATE_TIMEOUT_S, stdin=subprocess.DEVNULL)
            ok, tail = proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:]
        except subprocess.TimeoutExpired:
            ok, tail = False, f"`{command}` timed out after {GATE_TIMEOUT_S // 60} minutes"
        self.gate_outputs |= gitops.untracked(self.worktree) - before
        self.r.agent.log("gate", command=command, passed=ok)
        return ok, tail


def prompt(task, project: dict, answers: list[str], gate_feedback: str) -> str:
    parts = [
        f"You are working Jaah task {task['id']}: {task['title']}",
        "", "## Task", task.get("body") or "(no body)",
    ]
    if task.get("out_of_scope"):
        parts += ["", "## Out of scope (do NOT touch)", task["out_of_scope"]]
    parts += [
        "", "## Rules",
        "- Make the change in this repository's working tree. Do NOT commit, push, open a PR or run "
        "`git`/`jaah-forge` writes: the connector does all delivery.",
        "- Deliver exactly what the task asks — nothing more, nothing less. Match the surrounding code.",
        "- Never write a secret (key, token, password) into any file or into your answer.",
        "- If you need something only a human can give (a credential, an access, a change in an outside system) "
        "or a decision you genuinely cannot make, stop with status `blocked`, a concrete `question`, 2-4 "
        "`options`, and `blocker_kind` (Credential | Access | Human act | External | decision).",
        "- If no change is needed, use status `no_change` and say why in `summary`.",
        "- List in `security_surfaces` anything your change touches among: sign-in/auth/authz, "
        "credentials/secrets, data migrations, personal data, CI or code-ownership config. Empty when none.",
        "- `commit_title`: a conventional commit title (`feat: …`, `fix: …`), at most 72 characters.",
    ]
    gate = project.get("gate_full_cmd") or project.get("gate_quick_cmd")
    if gate:
        parts.append(f"- The project's gate is `{gate}`; make it pass.")
    if answers:
        parts += ["", "## Operator answers so far", *answers]
    if gate_feedback:
        parts += ["", "## The gate failed after your last pass — fix it", "```", gate_feedback, "```"]
    return "\n".join(parts)


def deliver(r: Run, task, base: str, branch: str, worktree: str, result: dict, surfaces: list[str]) -> None:
    agent = r.agent
    on_surface = bool(surfaces)
    autonomous = bool(task.get("autonomous"))
    pr = gitops.open_pr(_title(task, result), pr_body(task, result, surfaces), base, branch,
                        draft=on_surface and autonomous)
    number, r.pr_url = int(pr["number"]), str(pr["url"])
    agent.log("PR ready", url=r.pr_url, draft=bool(pr.get("draft")), security_surfaces=surfaces)

    if on_surface and autonomous:
        # Full Auto has no human to approve, and no decision may be asked: leave a draft for a human.
        r.fail(f"Security surface: {', '.join(surfaces)}. This change needs a human review before merge, and "
               f"SDK agents get no autonomous review lane, so it was opened as a DRAFT and not merged: {r.pr_url}")
    if on_surface:
        approved_sha = gitops.head(worktree)
        answer = agent.ask([{
            "question": f"This change touches the security surface ({', '.join(surfaces)}). Review {r.pr_url} "
                        f"(head {approved_sha[:12]}) — merge it?",
            "header": "Security",
            "options": [{"label": APPROVE, "description": "Merge this head once its checks are green."},
                        {"label": DO_NOT_MERGE, "description": "Leave the PR open for a human; do not merge."}],
        }])[0]
        if answer["status"] != "answered" or answer["selections"] != [APPROVE]:
            how = "dismissed the question" if answer["status"] == "dismissed" else "chose do not merge"
            r.fail(f"Security surface: {', '.join(surfaces)}. The operator {how}; the PR is left open, "
                   f"unmerged: {r.pr_url}")
        view = gitops.wait_for_merge(number, merge_when_clean=True, deadline_s=MERGE_DEADLINE_S, sha=approved_sha)
    else:
        try:
            armed = bool(gitops.forge("pr", "auto-merge", str(number)).get("auto_merge", True))
        except gitops.ForgeError as exc:
            agent.log("auto-merge unavailable; merging when clean", error=str(exc), level="warning")
            armed = False
        view = gitops.wait_for_merge(number, merge_when_clean=not armed, deadline_s=MERGE_DEADLINE_S)
    if view.get("state") == "merged":
        r.settle(_short(f"{result['summary'] or task['title']} — merged {r.pr_url}"), "merged_pr", pr_url=r.pr_url)
    detail = f"; last merge error: {view['merge_error']}" if view.get("merge_error") else ""
    r.fail(f"The PR did not merge (state {view.get('state')}, merge_state {view.get('merge_state')}, checks "
           f"{view.get('checks')}){detail}; it is left open: {r.pr_url}")


def pr_body(task, result: dict, surfaces: list[str]) -> str:
    lines = [f"Jaah task {task['id']}: {task['title']}", "", result["summary"], ""]
    if surfaces:
        lines += [f"**Security surface:** {', '.join(surfaces)} — needs human approval before merge.", ""]
    lines.append("Built by the Codex (reference) connector through the Jaah Agent SDK.")
    return "\n".join(lines)


def _title(task, result: dict) -> str:
    for text in (result.get("commit_title") or "", task.get("title") or ""):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines:
            return lines[0][:72]
    return f"Jaah task {task['id']}"


def _short(text: str, limit: int = 250) -> str:
    text = str(text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


if __name__ == "__main__":
    sys.exit(main())
