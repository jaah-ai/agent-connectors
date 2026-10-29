# Contributing a connector

A **connector** is an agent built on the [Jaah Agent SDK](https://github.com/jaah-ai/agent-sdk) that Jaah
reviews and lists in every org's catalog. You do not need this repo to run your own agent — any GitHub repo
with a `jaah-agent.toml` can be registered by your org (see the [README](README.md)). Contribute here when
you want **every** Jaah org to be able to enable it.

The order is fixed: **propose → build → submit → review → land → publish**. Proposal, review, maintenance,
removal and bounties are governed by [GOVERNANCE.md](GOVERNANCE.md); this file is how you build and submit.

## 1. Before you write code: a JGP

Open a **JGP** (Jaah Governance Proposal) issue with the *Connector proposal* template, titled
`JGP-<N>: Add <X> connector`, where `<N>` is the issue number GitHub gives it (edit the title once the issue
exists). Wait for it to be accepted — the discussion period and how it is decided are in
[GOVERNANCE.md §2](GOVERNANCE.md#2-proposals-jgp). A PR for a new connector with no accepted JGP is closed.

Fixes and improvements to an existing connector need no JGP: open an issue (or go straight to a PR for a
small fix).

## 2. Connector structure

One directory per connector under `connectors/`, named exactly as the manifest's `name`:

```
connectors/<name>/
  jaah-agent.toml          # the manifest (§3) — this directory is the agent root
  README.md                # required (§6)
  <script>                 # what `command` runs, e.g. agent.py, agent/main.py; agent.ts (bundled to agent.mjs)
  package.json             # TypeScript only: the SDK's dependencies under `dependencies`, with package-lock.json
  tests/                   # required: test_*.py
```

- **Self-contained.** A connector imports nothing from another connector or from `tools/` at run time; Jaah
  bundles only this directory (the sub-path) when it registers it. Its tests may use [fake_node.py](tools/fake_node.py).
- **The folder carries its SDK and lists its dependencies.** No third-party file is committed or shipped: beside a
  Python script go `jaah_agent` and `requirements.txt` (hash-pinned); a TypeScript agent is bundled into one `.mjs`
  with the SDK inlined and its dependencies left as imports, listed in the folder's `package.json` with its
  `package-lock.json`. Before `command` starts, the node installs them into the folder. You never write the SDK copy,
  `requirements.txt` or the `.mjs` by hand — the Agent SDK's `examples/vendor.py <folder>` generates them
  (`--install <folder>` installs the dependencies as the node does, for a local run), `.gitignore` keeps them out of
  your commits, and each release ships them. Registration refuses a folder whose script imports the SDK without
  them.
- **Don't daemonize.** Stay a (grand)child of the process `command` starts: no double-fork, `setsid`, `nohup` or handing
  work to systemd. Jaah identifies your task by the process tree. Child processes you start and wait for
  are fine.
- **Write only** inside the task's worktree (the process's working directory), `$JAAH_REPORT_DIR` (for
  artifacts), and temp directories you create.

## 3. The manifest standard — `jaah-agent.toml`

```toml
[agent]
manifest     = 2                      # SDK v2: the agent is an ACP agent the node's ACP host runs
name         = "acme-reviewer"        # slug: ^[a-z][a-z0-9-]{1,39}$ — also the directory name
display_name = "Acme reviewer"        # 1..60 characters, shown everywhere in Jaah
description  = "Reviews a PR against a team checklist."   # at most 280 characters
type         = "coding"               # coding | non_coding | cli_wrapper  (§4)
language     = "python"               # python | typescript
command      = ["python3", "agent.py"]   # run from the agent root; a TypeScript agent: ["node", "agent.mjs"]
version      = "1.0.0"                # semver; bump it in every PR that changes the connector
maintainer   = "@your-github-handle"  # or "Name <email>" — the person accountable for it (GOVERNANCE §5)
personas     = ["coder"]              # which task kinds it may run, at least one of:
                                      # coder, business_analysis, architecture, plan, finish, researcher
```

Runtimes: Python ≥ 3.12; Node ≥ 22.18. A manifest 1 field (`entrypoint`, `sdk`, `[runtime] install`) is refused.

**Check it yourself** before you push — the same rules Jaah applies, one sentence per problem:

```sh
python3 tools/check_connectors.py connectors/<name> --bundled
```

## 4. Connector types

| Type | What it is | Ends with | Example |
|---|---|---|---|
| `coding` | Changes the task's repo: drives an editing agent or model loop in the worktree. | A **merged PR** (`turn.complete(…, "merged_pr", pr_url=…)` once the PR merged, or once `pull_request.enqueue` answered `queued`), or `no_pr` when it proves nothing needed changing. Honours the security gate (§5). | [`codex-reference`](connectors/codex-reference/) |
| `non_coding` | Produces something other than code: a report, a document, a message, a change in another system. | An uploaded artifact (`jaah.call("artifact.upload", {"kind": "report", …})`) and `turn.complete(…, "no_pr", no_pr_reason="no_code_needed", …)`. | [`report-sample`](connectors/report-sample/) |
| `cli_wrapper` | A thin wrapper running an existing third-party CLI agent headlessly, with little logic of its own. | As `coding` or `non_coding`, by what the CLI does. | — |

The settle a persona expects: `coder` and `researcher` end `merged_pr` or `no_pr` (a `researcher` must
upload its `report` first); `business_analysis` and `architecture` upload their `prd` / `architecture`
document and settle `analysis`; `plan` and `finish` have pipeline-specific endings — only claim them if
your connector implements them.

## 5. Behaviour every connector must have

These are the **technical** and **security** items of the review checklist (GOVERNANCE §3); reviewers
check each one.

The task is the agent's first prompt; `turn.jaah` is the `jaah.ai` extension (the SDK's README lists every call).

1. **Exactly one terminal call on every path** — `turn.complete(…)` or `turn.fail(reason)` — made **last**,
   inside a turn. Any other exit is reported by Jaah as a failure that names your agent.
2. **`fail` only for what no human could clear.** A missing credential, access, or outside change is a
   question first (`turn.jaah.ask(…)`, header `Credential`, `Access`, `Human act` or `External`). A question ends
   the turn: the operator's answer is the next prompt, and the agent carries on from where it asked.
3. **Full Auto.** When the task (`task.get`) is `autonomous`, never ask a *decision* — decide and say what
   you assumed. Jaah refuses any other header.
4. **Security gate.** No automated reviewer stands between an SDK agent and a merge. When your change touches
   sign-in/auth/authz, credentials/secrets, data migrations or personal data: never merge it yourself and
   never arm auto-merge. Attended → ask one `Security` round (approve / do not merge), merge only on
   approve. Full Auto → open the PR as a **draft** and `fail` with a reason that names the surface first
   and carries the PR URL.
5. **Credentials only through `turn.jaah.credentials()` / `credential(name)`.** Never read a secret from the host
   (files, env you were not given, another tool's login). Never write one anywhere: not a log line, a message,
   a commit, an artifact, a question or a settle. A secret reaches a child process as a file it reads (mode
   `0600`, in a private directory) — never an argument, never an environment variable.
6. **Report usage.** Every model call → one ACP `usage_update` carrying its token counts in
   `_meta["jaah.ai"]["usage"]` (`model`, `input_tokens`, `cache_read_tokens`, `output_tokens`).
7. **Untrusted code never speaks for the agent.** The agent talks to Jaah over its own stdio. Anything that can
   execute content a model chose or wrote — the model's shell, the project's gate, git (whose hooks run repo
   content; turn them off) — gets stdin from `/dev/null` and its output captured, never the agent's streams.
8. **Declare your network reach** in the README ("Talks to"). Reach nothing else.

## 6. README

Each connector's README states, at the top, a table with: **Type**, **Personas**, **Language**, **Needs
on the node** (binaries), **Needs on the credential roster** (what credential, of what type), **Talks to**
(every host), **Maintainer**. Then: what it does, step by step, including what it asks and when; how it
behaves under Full Auto and on the security surface; configuration; how to run its tests. The two
connectors in this repo are the template.

## 7. Tests and the coverage floor

- Tests live in `tests/` and run against the fake node ([fake_node.py](tools/fake_node.py)) under the SDK your folder
  carries, its dependencies installed into it — no Jaah node, no network, no real model. Fake the tools you drive (put
  stand-ins on `PATH`, as `codex-reference/tests/harness.py` does), and run the folder once over real ACP
  (`FakeNode.run`).
- They must cover: the happy path; the no-change / nothing-to-do path; a question round; a failure path;
  Full Auto; and for `coding` connectors both security-gate modes.
- If you take a credential: a test that its value never reaches a node call, a session update, a log line,
  an artifact or a commit.
- **Coverage floor: 80% of lines** of the connector's own code (its tests excluded), measured by CI with
  `coverage.py`. A PR below 80% fails CI.

## 8. Fork, branch, commit, PR

Each release is one commit on `main`, tagged with its version. Pull requests are welcome — they are how a change
is reviewed here; a maintainer then lands it in the next release (§9).

1. **Fork** `jaah-ai/agent-connectors` and branch from `main`. Branch names:

   | Prefix | For |
   |---|---|
   | `feat/<connector>` | a new connector, or a feature in one |
   | `fix/<connector>-<what>` | a bug fix |
   | `refactor/<connector>-<what>` | no behaviour change |
   | `docs/<what>` | documentation only |
   | `chore/<what>` | CI, tooling, repo housekeeping |

2. **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/) with the connector as
   the scope: `feat(acme-reviewer): ask before re-running a red gate`. Small and atomic; no merge commits
   in your branch (rebase on `main` instead). Never commit a secret, a `.env`, or a generated bundle.
   **Sign off every commit** (`git commit -s`): the `Signed-off-by: Your Name <you@example.com>` line certifies the
   [Developer Certificate of Origin 1.1](https://developercertificate.org/) — that you wrote the change or have the
   right to submit it under this repo's licence, [Apache-2.0](LICENSE). A PR with an unsigned commit is not
   reviewed, and a bounty (GOVERNANCE §7) is not paid on one.
3. **One connector per PR.** Open it against `main`, using the PR template, and link the accepted JGP
   (`Implements JGP-<N>`) or the issue (`Fixes #<n>`). Bump the manifest `version`.
4. **Green before review:** [check_connectors.py](tools/check_connectors.py), your tests, and the coverage floor all pass in CI.
   CI installs each folder's dependencies as the node does and runs every connector against the SDK its folder
   carries, so a fork's PR is tested exactly like a maintainer's — no secret involved. Reviewers do not start on a red PR.
5. Answer review comments with commits (no force-push once review has started, so the reviewer can see
   what changed); the reviewer resolves threads. A PR with no response from its author for **30 days** is
   closed (it can be reopened).

## 9. How an accepted PR lands

A PR is never merged here. Once it is approved (GOVERNANCE §3), a Jaah maintainer re-applies it,
crediting you, and it appears in the next release snapshot on `main`. The PR is then closed with a link to
that release.

Who reviews and who lands: [GOVERNANCE.md §3–4](GOVERNANCE.md#3-review).
