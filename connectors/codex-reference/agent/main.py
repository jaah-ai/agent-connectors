"""Codex (reference) — the OpenAI Codex CLI as a Jaah coding agent, through the Jaah Agent SDK.

    task() → the roster's OpenAI key → `codex exec` in the worktree → ask when blocked → report usage →
    commit + push + PR via jaah-forge (watched until merged) or settle no_pr — honouring security_gate.

Every decision this file makes is one a Jaah runner makes too; the README walks through each."""
from __future__ import annotations

import os
import subprocess
import sys

import jaah_agent

import codex_run
import gitops
import security

MAX_ASK_ROUNDS = 3        # questions per task; past this the task fails rather than loops
MERGE_DEADLINE_S = 6 * 60 * 60
BLOCKER_HEADERS = ("Credential", "Access", "Human act", "External")   # the Full Auto blocker licence
APPROVE, DO_NOT_MERGE = "Approve merge", "Do not merge"
OPENAI_CREDENTIAL_TYPES = ("openai_api_key",)
KEY_FIELDS = ("api_key", "token", "secret_key")


class Stop(Exception):
    """The run has made its terminal call; unwind without doing anything else."""


def main() -> int:
    agent = jaah_agent.connect()
    try:
        run(agent)
    except Stop:
        return 0
    except (gitops.GitError, gitops.ForgeError) as exc:
        agent.fail(f"Delivery failed: {exc}")
    return 0


def run(agent) -> None:
    ctx = agent.task()
    task, project = ctx["task"], ctx.get("project") or {}
    worktree = ctx.get("worktree") or os.getcwd()
    agent.log("codex-reference starting", task_id=task["id"], persona=task.get("persona"),
              autonomous=bool(task.get("autonomous")), model=codex_run.model())

    api_key = openai_key(agent, task)
    branch = f"fleet/task-{task['id']}"
    base = project.get("integration_branch") or "main"
    gitops.git(worktree, "fetch", "-q", "origin", base, check=False)
    gitops.checkout_branch(worktree, branch)

    result = work(agent, task, worktree, api_key, project)
    paths = gitops.changed_paths(worktree)
    if not paths and gitops.commits_ahead(worktree, base) == 0:
        agent.settle(_short(f"Codex made no change: {result.get('summary') or 'nothing to do'}"), "no_pr",
                     no_pr_reason="no_code_needed",
                     no_pr_evidence=_short(f"Codex ran in the worktree and changed no file. Its account: "
                                           f"{result.get('summary') or '(none)'}", 4000))
        raise Stop
    surfaces = security.classify(paths, result.get("security_surfaces"))
    if paths:
        gitops.commit(worktree, f"{_title(task, result)}\n\n{result.get('summary') or ''}\n\nJaah task {task['id']}.")
    if project.get("push_enabled") is False:
        agent.fail("This project does not allow pushes (push_enabled is off), so the connector cannot open a "
                   "PR. The change is committed in the worktree only.")
        raise Stop
    gitops.push(worktree, branch)
    deliver(agent, task, base, branch, result, surfaces)


def openai_key(agent, task) -> str:
    """The OpenAI key from the task's credential roster. Missing is a blocker only a human can clear."""
    for attempt in range(2):
        cred = _pick_openai(agent.credentials())
        if cred:
            config = (agent.credential(cred["name"]) or {}).get("config") or {}
            for name in KEY_FIELDS:
                if config.get(name):
                    agent.log("using OpenAI credential", credential=cred["name"])
                    return str(config[name])
            agent.fail(f"Credential {cred['name']} has no api_key field, so Codex cannot authenticate.")
            raise Stop
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
    agent.fail("No OpenAI API key credential is on this task's roster, so Codex cannot run. Add one usable "
               "by this project (type OpenAI API key, or a name containing 'openai') and start the task again.")
    raise Stop


def _pick_openai(roster: list[dict]) -> dict | None:
    for cred in roster:
        if cred.get("type") in OPENAI_CREDENTIAL_TYPES:
            return cred
    for cred in roster:
        if "openai" in str(cred.get("name", "")).lower():
            return cred
    return None


def work(agent, task, worktree: str, api_key: str, project: dict) -> dict:
    """Run Codex until it is done, asking the operator when it is blocked (bounded)."""
    answers: list[str] = []
    gate_feedback = ""
    gate_tried = False
    while True:
        out = codex_run.run(prompt(task, project, answers, gate_feedback), worktree, api_key)
        agent.report_usage(codex_run.model(), max(0, out.usage.input_tokens - out.usage.cached_input_tokens),
                           out.usage.output_tokens, cache_read=out.usage.cached_input_tokens)
        if out.result is None:
            why = "; ".join(out.errors[-3:]) or out.stderr_tail.strip()[-500:] or f"exit {out.exit_code}"
            agent.fail(f"Codex did not finish (exit {out.exit_code}): {why}")
            raise Stop
        result = out.result
        agent.log("codex finished a pass", status=result["status"], summary=_short(result.get("summary", ""), 500))
        if result["status"] == "blocked":
            answers.append(resolve_block(agent, task, result, len(answers)))
            continue
        gate = project.get("gate_full_cmd") or project.get("gate_quick_cmd")
        if result["status"] == "done" and gate:
            ok, tail = run_gate(gate, worktree)
            agent.log("gate", command=gate, passed=ok)
            if not ok and not gate_tried:
                gate_tried, gate_feedback = True, tail
                continue
            if not ok:
                agent.fail(_short(f"The project gate `{gate}` is still red after one fix pass. Last output:\n{tail}",
                                  4000))
                raise Stop
        return result


def resolve_block(agent, task, result: dict, asked: int) -> str:
    """Turn a blocked Codex pass into the text the next pass reads."""
    kind = result.get("blocker_kind") or "decision"
    question = (result.get("question") or "").strip() or "Codex is blocked and gave no question."
    if task.get("autonomous") and kind not in BLOCKER_HEADERS:
        # Full Auto: a decision is never a question. Tell Codex to decide and document it.
        agent.log("full auto: decision returned to codex", question=_short(question, 500))
        return (f"Q: {question}\nA: (Full Auto) Nobody will answer decisions. Choose the most defensible option "
                f"consistent with this repository, state the assumption in your summary, and finish.")
    if asked >= MAX_ASK_ROUNDS:
        agent.fail(_short(f"Codex is still blocked after {MAX_ASK_ROUNDS} questions. Last: {question}", 4000))
        raise Stop
    options = [o for o in (result.get("options") or []) if str(o).strip()][:4] or ["Proceed", "Stop"]
    header = kind if kind in BLOCKER_HEADERS else "Decision"
    answer = agent.ask([{"question": question, "header": header, "options": options}])[0]
    if answer["status"] == "dismissed":
        return (f"Q: {question}\nA: (dismissed by the operator) Proceed on the most defensible assumption and "
                f"state it in your summary.")
    picked = ", ".join(answer["selections"]) or "(no option)"
    return f"Q: {question}\nA: {picked}" + (f" — {answer['text']}" if answer.get("text") else "")


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
        "- Never write a secret (key, token, password) into any file.",
        "- If you need something only a human can give (a credential, an access, a change in an outside system) "
        "or a decision you genuinely cannot make, stop with status `blocked`, a concrete `question`, 2-4 "
        "`options`, and `blocker_kind` (Credential | Access | Human act | External | decision).",
        "- If no change is needed, use status `no_change` and say why in `summary`.",
        "- List in `security_surfaces` anything your change touches among: sign-in/auth/authz, "
        "credentials/secrets, data migrations, personal data. Empty when none.",
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


def run_gate(command: str, worktree: str) -> tuple[bool, str]:
    env = {k: v for k, v in os.environ.items() if k not in codex_run.WITHHELD_ENV}
    try:
        proc = subprocess.run(command, shell=True, cwd=worktree, env=env, capture_output=True, text=True,
                              timeout=45 * 60, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, f"`{command}` timed out after 45 minutes"
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:]


def deliver(agent, task, base: str, branch: str, result: dict, surfaces: list[str]) -> None:
    on_surface = bool(surfaces)
    autonomous = bool(task.get("autonomous"))
    body = pr_body(task, result, surfaces)
    pr = gitops.create_pr(_title(task, result), body, base, branch, draft=on_surface and autonomous)
    number, url = int(pr["number"]), pr["url"]
    agent.log("opened PR", url=url, draft=bool(pr.get("draft")), security_surfaces=surfaces)

    if on_surface and autonomous:
        # Full Auto has no human to approve, and no decision may be asked: leave a draft for a human.
        agent.fail(_short(f"Security surface: {', '.join(surfaces)}. This change needs a human review before "
                          f"merge, and SDK agents get no autonomous review lane, so it was opened as a DRAFT and "
                          f"not merged: {url}", 4000))
        raise Stop
    if on_surface:
        answer = agent.ask([{
            "question": f"This change touches the security surface ({', '.join(surfaces)}). Review {url} — "
                        f"merge it?",
            "header": "Security",
            "options": [{"label": APPROVE, "description": "Merge once its checks are green."},
                        {"label": DO_NOT_MERGE, "description": "Leave the PR open for a human; do not merge."}],
        }])[0]
        if answer["status"] != "answered" or answer["selections"] != [APPROVE]:
            agent.fail(_short(f"Security surface: {', '.join(surfaces)}. The operator did not approve the merge "
                              f"({'dismissed' if answer['status'] == 'dismissed' else 'do not merge'}); the PR "
                              f"is left open, unmerged: {url}", 4000))
            raise Stop
        view = gitops.wait_for_merge(number, merge_when_clean=True, deadline_s=MERGE_DEADLINE_S)
    else:
        armed = gitops.forge("pr", "auto-merge", str(number))
        view = gitops.wait_for_merge(number, merge_when_clean=not armed.get("auto_merge", True),
                                     deadline_s=MERGE_DEADLINE_S)
    if view.get("state") == "merged":
        agent.settle(_short(f"{result.get('summary') or task['title']} — merged {url}"), "merged_pr", pr_url=url)
        raise Stop
    agent.fail(_short(f"The PR did not merge (state {view.get('state')}, merge_state {view.get('merge_state')}, "
                      f"checks {view.get('checks')}); it is left open: {url}", 4000))
    raise Stop


def pr_body(task, result: dict, surfaces: list[str]) -> str:
    lines = [f"Jaah task {task['id']}: {task['title']}", "", result.get("summary") or "", ""]
    if surfaces:
        lines += [f"**Security surface:** {', '.join(surfaces)} — needs human approval before merge.", ""]
    lines.append("Built by the Codex (reference) connector through the Jaah Agent SDK.")
    return "\n".join(lines)


def _title(task, result: dict) -> str:
    return (result.get("commit_title") or task["title"]).strip().splitlines()[0][:72] or f"Task {task['id']}"


def _short(text: str, limit: int = 250) -> str:
    text = str(text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


if __name__ == "__main__":
    sys.exit(main())
