# Codex (reference)

The [OpenAI Codex CLI](https://github.com/openai/codex) as a Jaah **coding** agent. It is the reference
connector: every rule in [CONTRIBUTING.md](../../CONTRIBUTING.md) and every item of the review checklist in
[GOVERNANCE.md](../../GOVERNANCE.md) is implemented here, so read it before you write your own.

| | |
|---|---|
| Type | `coding` |
| Personas | `coder` |
| Language | Python ≥ 3.12, stdlib only (`[runtime] install = false`) |
| Needs on the node | `codex` (the Codex CLI) and `git` on `PATH`; `jaah-forge` (every Jaah node has it) |
| Needs on the task's credential roster | An OpenAI API key — a credential of type *OpenAI API key*, or any credential whose name contains `openai` with an `api_key` field |
| Talks to | `api.openai.com` (through Codex), your forge (through `git` and `jaah-forge`) |
| Maintainer | `@ruzhukov` |

## What it does

1. `agent.task()` — reads the task, its project (integration branch, branch prefix, gate commands,
   `push_enabled`) and the worktree. A project with pushes off cannot take a coding agent: it fails at once,
   saying so.
2. **Key.** Looks for an OpenAI credential on the task's roster with `agent.credentials()` and fetches it
   with `agent.credential()`. None on the roster is a blocker only a human can clear, so it asks one
   `Credential` round ("Added it" / "Stop"), re-reads the roster once, and otherwise fails naming exactly
   what to add.
3. **Branch.** `<branch_prefix>fleet/task-<id>`: the previous attempt's pushed branch if there is one,
   otherwise fresh from `origin/<integration branch>` (never whatever the worktree happened to hold).
4. **Codex.** Runs `codex exec --json --sandbox workspace-write --output-schema …` in the worktree. The key
   reaches Codex only as `OPENAI_API_KEY` / `CODEX_API_KEY` in **the child's environment** — never an
   argument, never a file — with a private, throwaway `CODEX_HOME` (so the node's own Codex login is never
   used), `shell_environment_policy.inherit="core"` (so shell commands the model runs see neither the key nor
   anything else from this process), and **without** `JAAH_AGENT_SOCKET` (so they cannot settle or fail the
   task). Codex's final message must match a JSON schema: `status` `done` / `no_change` / `blocked`, a
   summary, a commit title, a question when blocked, and the security surfaces it believes it touched.
5. **Usage.** After every Codex pass, `agent.report_usage(model, …)` with Codex's own token counts (cached
   input tokens reported as cache reads).
6. **Blocked.** A blocked pass becomes a question: header `Decision` (or the blocker kind — `Credential`,
   `Access`, `Human act`, `External`), Codex's options, and the answer is fed into the next pass. Under
   **Full Auto** a *decision* is never asked — Codex is told to decide and document it; only a blocker is
   asked, with its licensed header. At most 3 questions and 6 Codex passes per task, then it fails.
7. **Commit.** Stages the change and refuses it outright if the diff contains the key (or Codex's fragment
   of it) — nothing is committed or pushed. Codex's summary and title are scrubbed of the key before they
   reach a commit, the PR or a settle. git runs with hooks off and without the SDK socket, because the
   worktree holds files a model just wrote.
8. **Gate.** When the project has a gate command (full, else quick) it runs it on the committed change; a
   red gate gets one fix pass with the output (whatever that pass reports, the gate runs again), and a gate
   still red fails the task — nothing red is pushed. Files the gate writes are never committed. **Trust
   boundary:** the gate runs the project's own command on code Codex wrote, outside Codex's sandbox and
   with the node's network, exactly as a human running the gate would; it never sees the SDK socket.
9. **Deliver.** A branch that changes nothing against its base → `settle("no_pr", no_code_needed)` with
   Codex's account as evidence. Otherwise it pushes (`--force-with-lease`), opens a PR with `jaah-forge`
   (or reuses the branch's open PR from an earlier attempt) and — for an ordinary change — arms auto-merge
   (merging it itself once green where auto-merge is unavailable), **watches until the forge reports the PR
   merged**, and only then settles `merged_pr`. A PR that is stuck (`checks_failed`, `dirty`, `blocked`,
   `review_required` with no checks pending, for three polls), refused three merges, or is unmerged after 6
   hours fails the task naming the state, leaving the PR open. Anything unexpected still ends in exactly one
   named `fail`.

## The security gate

`agent.task()["security_gate"]` is `"human"` for every SDK agent: no automated reviewer stands between this
connector and a merge. A delivery is on the **security surface** when any path the **whole branch** changes
against its base — both sides of a rename, and commits an earlier attempt pushed — matches sign-in/auth/
authz, credentials/secrets, data migrations, personal data, or CI/code-ownership config (`agent/security.py`),
**or** Codex reports one. On the surface the connector never arms auto-merge, and then:

| Mode | What it does |
|---|---|
| Attended (`autonomous` false) | Opens the PR and asks **one** round, header `Security`, options *Approve merge* / *Do not merge*, naming the head commit. Approve → merges **that head** (`pr merge --sha`) once checks are green, settles `merged_pr`. Anything else → fails the task naming the surface, PR left open and unmerged. |
| Full Auto (`autonomous` true) | Opens the PR as a **draft** and fails the task with a reason that starts `Security surface: …` and carries the PR URL. Nothing is asked. |

*Do not merge* ends in `fail`, not a settle, on purpose: a settled task reads as delivered, and no settle
ending can honestly carry a PR a human declined (`merged_pr` needs a merge; every `no_pr` reason means
nothing needed shipping).

## Configuration

| Env var | Default | Effect |
|---|---|---|
| `JAAH_CODEX_MODEL` | `gpt-5-codex` | The model passed to `codex exec --model` and reported in usage. |

## Run the tests

```sh
git clone git@github.com:jaah-ai/agent-sdk.git ../agent-sdk -b v0.1.0   # once, beside this repo
PYTHONPATH=../agent-sdk/python:../agent-sdk/python/tests \
  python3 -m unittest discover -s connectors/codex-reference/tests
```

The tests run the real flow against the SDK's fake node daemon, a fake `codex` and a fake `jaah-forge` on
`PATH`, and a real local git origin. `SecretTest` asserts the key — and Codex's own partial echo of it — never
reaches a daemon call, the events file, a forge call, a PR body or a commit, and that a change carrying
the key ships nothing. Every test runs the real `main()` and asserts exactly one terminal call.
