"""Codex (reference) — the OpenAI Codex CLI as a Jaah coding agent, on the Jaah Agent SDK v2.

    task.get → the roster's OpenAI key → `codex exec` in the worktree → ask when blocked → report usage →
    gate → commit + push + PR via jaah-forge → pull_request.enqueue (or watch until merged) or settle no_pr —
    honouring the security gate.

The task is the first prompt. A question (`input.request`) ends the turn; the operator's answer is the next prompt,
and the run carries on from where it asked. Every run ends in exactly ONE terminal call (`task.complete` or
`task.fail`): `Run` records it, and anything unexpected becomes a named fail unless one was already made. The README
walks through each step."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from typing import Any

import jaah_agent
from acp.schema import UsageUpdate

import codex_run
import gitops
import security

MAX_ASK_ROUNDS = 3        # questions per task
MAX_PASSES = 6            # Codex passes per task, whatever drives them; past this the task fails, never loops
MERGE_DEADLINE_S = 6 * 60 * 60
GATE_TIMEOUT_S = 45 * 60
BLOCKER_HEADERS = ("Credential", "Access", "Human act", "External")   # the Full Auto blocker licence
APPROVE, DO_NOT_MERGE = "Approve merge", "Do not merge"
ADDED, STOP = "Added it", "Stop"
OPENAI_CREDENTIAL_TYPES = ("openai_api_key",)
KEY_FIELDS = ("api_key", "token", "secret_key")
#: The `_meta` key an SDK agent's usage update carries its model's token counts under (it has no prompt result
#: of its own to put them in).
META_KEY = "jaah.ai"


class Stop(Exception):
    """The run has made its terminal call; unwind without doing anything else."""


class CodexReference(jaah_agent.Agent):
    name = "codex-reference"
    title = "Codex (reference)"
    version = "2.0.0"

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    async def prompt(self, turn: jaah_agent.Turn) -> None:
        if not turn.jaah.supports("task.get"):
            await turn.say("Codex (reference) works Jaah tasks: this client serves no task, so there is nothing to do.")
            return None
        run = self._runs.get(turn.session_id)
        if run is None or not run.parked:
            run = self._runs[turn.session_id] = Run(turn)
        else:
            run.resume(turn)
        await run.until_parked_or_done()
        return None


class Run:
    """One task: the work, the turn it currently speaks through, and whether the terminal call has been made."""

    def __init__(self, turn: jaah_agent.Turn) -> None:
        self.turn = turn
        self.terminal = False      # a terminal call was ANSWERED (a refused settle records nothing)
        self.pr_url = ""
        self.api_key = ""
        self.task: dict[str, Any] = {}
        self._question = ""
        self._answer: asyncio.Future[str] | None = None
        self._parked = asyncio.Event()
        self._work = asyncio.ensure_future(self._main())

    @property
    def jaah(self) -> jaah_agent.Extension:
        return self.turn.jaah

    @property
    def parked(self) -> bool:
        return self._answer is not None and not self._work.done()

    def resume(self, turn: jaah_agent.Turn) -> None:
        """`turn` is the operator's answer to the question the run is parked on."""
        self.turn = turn
        answer, self._answer = self._answer, None
        # The node flattens the question to one line when it frames the answer (or says only `Answer:`).
        flat = " ".join(self._question.split())
        text = turn.text.strip()
        for prefix in (f"Answer to your question '{flat}':", "Answer:"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        answer.set_result(text)

    async def until_parked_or_done(self) -> None:
        """Run the work until it settles, fails or asks a question (which ends this turn)."""
        parked = asyncio.ensure_future(self._parked.wait())
        try:
            await asyncio.wait({self._work, parked}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            self._work.cancel()
            raise
        finally:
            parked.cancel()
        self._parked.clear()

    async def _main(self) -> None:
        try:
            await work(self)
        except Stop:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — every path must end in a terminal call (CONTRIBUTING §5.1)
            if not self.terminal:
                try:
                    await self.fail(f"Codex (reference) stopped unexpectedly: {type(exc).__name__}: {exc}")
                except Stop:
                    pass
                except Exception as err:  # noqa: BLE001 — the fail itself could not be delivered
                    log("could not report the failure", error=f"{type(err).__name__}: {err}")

    # --- talking to the node --------------------------------------------------------------------------------

    async def settle(self, summary: str, terminal_type: str, **refs: Any) -> None:
        try:
            answer = await self.turn.complete(summary, terminal_type, **refs)
        except jaah_agent.ExtensionError as exc:
            await self.fail(f"The {terminal_type} settle was refused ({exc}); summary: {summary}")
        if answer is not None and answer.get("settled") is False:
            await self.fail(f"The {terminal_type} settle was stored but moved nothing ({answer}); summary: {summary}")
        self.terminal = True
        raise Stop

    async def fail(self, reason: str) -> None:
        reason = codex_run.scrub(reason, self.api_key)
        if self.pr_url and self.pr_url not in reason:
            reason += f" PR: {self.pr_url}"
        await self.turn.fail(_short(reason, 4000))
        self.terminal = True
        raise Stop

    async def ask(self, question: str, header: str, options: list[Any]) -> str:
        """Put one question to the operator, end the turn, and return the answer the next prompt carries."""
        await self.jaah.ask(question, options=options, header=header)
        self._question = question
        self._answer = asyncio.get_running_loop().create_future()
        self._parked.set()
        return await self._answer

    async def say(self, text: str) -> None:
        await self.turn.say(text + "\n")

    async def report_usage(self, usage: codex_run.Usage) -> None:
        """One ACP usage update per Codex pass, carrying its token counts (cached input reported as cache reads)."""
        counts = {"model": codex_run.model(),
                  "input_tokens": max(0, usage.input_tokens - usage.cached_input_tokens),
                  "cache_read_tokens": usage.cached_input_tokens,
                  "output_tokens": usage.output_tokens}
        await self.turn.update(UsageUpdate(session_update="usage_update", used=0, size=0,
                                           field_meta={META_KEY: {"usage": counts}}))


async def work(r: Run) -> None:
    task = r.task = (await r.jaah.call("task.get")) or {}
    if not task.get("id"):
        await r.fail("task.get returned no open task, so there is nothing to work on.")
    # The delivery columns ride on `task.get`: `project.get` is licensed to the onboard persona only.
    project = task.get("project")
    if not isinstance(project, dict):
        await r.fail("task.get carried no project delivery settings (integration branch, branch prefix, gate, "
                     "push switch), so there is no base to branch from; this control plane predates them.")
    worktree = r.turn.cwd or os.getcwd()
    log("codex-reference starting", task_id=task["id"], persona=task.get("persona"),
        autonomous=bool(task.get("autonomous")), model=codex_run.model())
    if project.get("push_enabled") is False:
        await r.fail("This project does not allow pushes (push_enabled is off), so a coding agent cannot deliver a "
                     "PR here. Run this task with a non-coding agent, or enable pushes on the project.")

    r.api_key = await openai_key(r)
    branch = f"{project.get('branch_prefix') or ''}fleet/task-{task['id']}"
    base = project.get("integration_branch") or "main"
    await asyncio.to_thread(gitops.checkout_branch, worktree, branch, base)

    passes = Passes(r, project, worktree)
    result = await passes.until_settled()
    await commit_pass(r, worktree, result, passes.gate_outputs)
    if not await asyncio.to_thread(gitops.delivery_paths, worktree, base):
        await r.settle(_short(f"Codex made no change: {result['summary'] or 'nothing to do'}"), "no_pr",
                       no_pr_reason="no_code_needed",
                       no_pr_evidence=_short(f"Codex ran in the worktree and the branch changes no file against "
                                             f"{base}. Its account: {result['summary'] or '(none)'}", 4000))

    gate = project.get("gate_full_cmd") or project.get("gate_quick_cmd")
    if gate:
        ok, tail = await passes.gate(gate)
        if not ok:
            result = await passes.until_settled(tail)
            await commit_pass(r, worktree, result, passes.gate_outputs)
            ok, tail = await passes.gate(gate)
            if not ok:
                await r.fail(f"The project gate `{gate}` is still red after one fix pass; nothing was pushed. "
                             f"Last output:\n{tail}")

    # Classify the WHOLE delivery — every commit on the branch, both sides of every rename.
    paths = await asyncio.to_thread(gitops.delivery_paths, worktree, base)
    surfaces = security.classify(paths, result.get("security_surfaces"))
    await asyncio.to_thread(gitops.push, worktree, branch)
    await deliver(r, base, branch, worktree, result, surfaces)


async def commit_pass(r: Run, worktree: str, result: dict, exclude: set[str]) -> None:
    """Commit what the last pass changed — unless it carries the key, in which case nothing ships."""
    if not await asyncio.to_thread(gitops.stage, worktree, exclude):
        return
    if codex_run.leaks(await asyncio.to_thread(gitops.staged_diff, worktree), r.api_key):
        await r.fail("Codex wrote the OpenAI key (or part of it) into the change. Nothing was committed or pushed; "
                     "rotate the key if the worktree may have been shared.")
    message = f"{_title(r.task, result)}\n\n{result['summary']}\n\nJaah task {r.task['id']}."
    await asyncio.to_thread(gitops.commit, worktree, message)


async def openai_key(r: Run) -> str:
    """The OpenAI key from the task's credential roster. Missing is a blocker only a human can clear."""
    for attempt in range(2):
        cred = _pick_openai((await r.jaah.credentials()).get("credentials") or [])
        if cred:
            config = (await r.jaah.credential(cred["name"])).get("config") or {}
            for name in KEY_FIELDS:
                if config.get(name):
                    log("using OpenAI credential", credential=cred["name"])
                    return str(config[name])
            await r.fail(f"Credential {cred['name']} has no api_key field, so Codex cannot authenticate.")
        if attempt == 0:
            answer = await r.ask(
                "Codex needs an OpenAI API key, and no OpenAI credential is on this task's roster. Add one (type "
                "OpenAI API key, or a name containing 'openai') that this project can use, then answer.",
                "Credential",
                [{"label": ADDED, "description": "Re-read the roster and continue."},
                 {"label": STOP, "description": "Fail the task; nothing was changed."}])
            if not _chose(answer, ADDED):
                break
    await r.fail("No OpenAI API key credential is on this task's roster, so Codex cannot run. Add one usable by this "
                 "project (type OpenAI API key, or a name containing 'openai') and start the task again.")
    raise AssertionError("unreachable")


def _pick_openai(roster: list[dict]) -> dict | None:
    for cred in roster:
        if cred.get("type") in OPENAI_CREDENTIAL_TYPES:
            return cred
    for cred in roster:
        if "openai" in str(cred.get("name", "")).lower():
            return cred
    return None


def _chose(answer: str, label: str) -> bool:
    """The operator picked `label`: the answer is that option, alone."""
    return answer.strip().strip(".").casefold() == label.casefold()


class Passes:
    """Codex passes for one task: bounded in number, each one's usage reported, blocks turned into asks."""

    def __init__(self, r: Run, project: dict, worktree: str) -> None:
        self.r, self.project, self.worktree = r, project, worktree
        self.answers: list[str] = []
        self.asked = 0
        self.count = 0
        self.gate_outputs: set[str] = set()

    async def one(self, gate_feedback: str = "") -> dict:
        if self.count >= MAX_PASSES:
            await self.r.fail(f"Codex did not finish within {MAX_PASSES} passes.")
        self.count += 1
        text = prompt(self.r.task, self.project, self.answers, gate_feedback)
        out = await asyncio.to_thread(codex_run.run, text, self.worktree, self.r.api_key)
        await self.r.report_usage(out.usage)
        if out.result is None:
            why = "; ".join(out.errors[-3:]) or out.stderr_tail.strip()[-500:] or f"exit {out.exit_code}"
            await self.r.fail(f"Codex did not finish (exit {out.exit_code}): {why}")
        result = codex_run.scrub_result(out.result, self.r.api_key)
        await self.r.say(f"Codex finished a pass ({result['status']}): {_short(result['summary'], 500)}")
        return result

    async def until_settled(self, gate_feedback: str = "") -> dict:
        result = await self.one(gate_feedback)
        while result["status"] == "blocked":
            self.answers.append(await self.resolve_block(result))
            result = await self.one()
        return result

    async def resolve_block(self, result: dict) -> str:
        """Turn a blocked Codex pass into the text the next pass reads."""
        kind = result["blocker_kind"] if result["blocker_kind"] in BLOCKER_HEADERS else "decision"
        question = result["question"].strip() or "Codex is blocked and gave no question."
        if self.r.task.get("autonomous") and kind not in BLOCKER_HEADERS:
            # Full Auto: a decision is never a question. Tell Codex to decide and document it.
            log("full auto: decision returned to codex", question=_short(question, 500))
            return (f"Q: {question}\nA: (Full Auto) Nobody will answer decisions. Choose the most defensible "
                    f"option consistent with this repository, state the assumption in your summary, and finish.")
        if self.asked >= MAX_ASK_ROUNDS:
            await self.r.fail(f"Codex is still blocked after {MAX_ASK_ROUNDS} questions. Last: {question}")
        self.asked += 1
        options = [o for o in result["options"] if o.strip()][:4] or ["Proceed", "Stop"]
        header = kind if kind in BLOCKER_HEADERS else "Decision"
        answer = await self.r.ask(question, header, options)
        if not answer:
            return (f"Q: {question}\nA: (no answer from the operator) Proceed on the most defensible assumption "
                    f"and state it in your summary.")
        return f"Q: {question}\nA: {answer}"

    async def gate(self, command: str) -> tuple[bool, str]:
        """Run the project's gate on the committed tree. Files it creates are never committed."""
        ok, tail, created = await asyncio.to_thread(_run_gate, command, self.worktree)
        self.gate_outputs |= created
        log("gate", command=command, passed=ok)
        return ok, tail


def _run_gate(command: str, worktree: str) -> tuple[bool, str, set[str]]:
    before = gitops.untracked(worktree)
    try:
        proc = subprocess.run(command, shell=True, cwd=worktree, env=codex_run.child_env(None),
                              capture_output=True, text=True, timeout=GATE_TIMEOUT_S, stdin=subprocess.DEVNULL)
        ok, tail = proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:]
    except subprocess.TimeoutExpired:
        ok, tail = False, f"`{command}` timed out after {GATE_TIMEOUT_S // 60} minutes"
    return ok, tail, gitops.untracked(worktree) - before


def prompt(task: dict, project: dict, answers: list[str], gate_feedback: str) -> str:
    parts = [
        f"You are working Jaah task {task['id']}: {task.get('title') or ''}",
        "", "## Task", task.get("body") or "(no body)",
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


async def deliver(r: Run, base: str, branch: str, worktree: str, result: dict, surfaces: list[str]) -> None:
    on_surface = bool(surfaces)
    autonomous = bool(r.task.get("autonomous"))
    pr = await asyncio.to_thread(gitops.open_pr, _title(r.task, result), pr_body(r.task, result, surfaces), base,
                                 branch, draft=on_surface and autonomous)
    number, r.pr_url = int(pr["number"]), str(pr["url"])
    head = await asyncio.to_thread(gitops.head, worktree)
    await r.say(f"PR ready: {r.pr_url}" + (f" (security surface: {', '.join(surfaces)})" if surfaces else ""))

    if on_surface and autonomous:
        # Full Auto has no human to approve, and no decision may be asked: leave a draft for a human.
        await r.fail(f"Security surface: {', '.join(surfaces)}. This change needs a human review before merge, and "
                     f"SDK agents get no autonomous review lane, so it was opened as a DRAFT and not merged: "
                     f"{r.pr_url}")
    if on_surface:
        answer = await r.ask(
            f"This change touches the security surface ({', '.join(surfaces)}). Review {r.pr_url} "
            f"(head {head[:12]}) — merge it?",
            "Security",
            [{"label": APPROVE, "description": "Merge this head once its checks are green."},
             {"label": DO_NOT_MERGE, "description": "Leave the PR open for a human; do not merge."}])
        if not _chose(answer, APPROVE):
            how = "gave no answer" if not answer else f"answered {_short(answer, 80)!r}"
            await r.fail(f"Security surface: {', '.join(surfaces)}. The operator {how}, not {APPROVE!r}; the PR is "
                         f"left open, unmerged: {r.pr_url}")
    lane = await enqueue(r, number, head, base)
    if lane == "failed":
        await r.fail(f"The merge lane refused the PR; it is left open: {r.pr_url}")
    if lane == "queued":
        await r.settle(_short(f"{result['summary'] or r.task.get('title')} — queued to merge {r.pr_url}"),
                       "merged_pr", pr_url=r.pr_url)
    if on_surface:
        # The approved head only: a push after the approval is never merged.
        view = await asyncio.to_thread(gitops.wait_for_merge, number, merge_when_clean=True,
                                       deadline_s=MERGE_DEADLINE_S, sha=head)
    else:
        try:
            armed = bool((await asyncio.to_thread(gitops.forge, "pr", "auto-merge", str(number))).get("auto_merge",
                                                                                                        True))
        except gitops.ForgeError as exc:
            log("auto-merge unavailable; merging when clean", error=str(exc))
            armed = False
        view = await asyncio.to_thread(gitops.wait_for_merge, number, merge_when_clean=not armed,
                                       deadline_s=MERGE_DEADLINE_S)
    if view.get("state") == "merged":
        await r.settle(_short(f"{result['summary'] or r.task.get('title')} — merged {r.pr_url}"), "merged_pr",
                       pr_url=r.pr_url)
    detail = f"; last merge error: {view['merge_error']}" if view.get("merge_error") else ""
    await r.fail(f"The PR did not merge (state {view.get('state')}, merge_state {view.get('merge_state')}, checks "
                 f"{view.get('checks')}){detail}; it is left open: {r.pr_url}")


async def enqueue(r: Run, number: int, head: str, base: str) -> str:
    """Report the PR to the project's merge lane: "off" when the project has none (merge it here), "failed" when the
    lane refused it, else "queued" — the lane holds it (queued, testing or merged) and merges it."""
    if not r.jaah.supports("pull_request.enqueue"):
        return "off"
    answer = await r.jaah.call("pull_request.enqueue", {"pr_number": number, "pr_url": r.pr_url,
                                                        "head_sha": head, "base_branch": base})
    log("merge lane", answer=answer)
    if answer.get("lane") == "off":
        return "off"
    return "failed" if answer.get("state") == "failed" else "queued"


def pr_body(task: dict, result: dict, surfaces: list[str]) -> str:
    lines = [f"Jaah task {task['id']}: {task.get('title') or ''}", "", result["summary"], ""]
    if surfaces:
        lines += [f"**Security surface:** {', '.join(surfaces)} — needs human approval before merge.", ""]
    lines.append("Built by the Codex (reference) connector on the Jaah Agent SDK.")
    return "\n".join(lines)


def _title(task: dict, result: dict) -> str:
    for text in (result.get("commit_title") or "", task.get("title") or ""):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines:
            return lines[0][:72]
    return f"Jaah task {task['id']}"


def _short(text: str, limit: int = 250) -> str:
    text = str(text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def log(message: str, **fields: Any) -> None:
    """A line for the session log: stderr is the human channel (stdout carries ACP)."""
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"codex-reference: {message}" + (f" {extra}" if extra else ""), file=sys.stderr, flush=True)


if __name__ == "__main__":
    jaah_agent.run(CodexReference())
