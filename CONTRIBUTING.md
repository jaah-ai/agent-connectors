# Contributing a connector

A **connector** is an agent built on the [Jaah Agent SDK](https://github.com/jaah-ai/agent-sdk) that Jaah
reviews and lists in every org's catalog. You do not need this repo to run your own agent — any GitHub repo
with a `jaah-agent.toml` can be registered by your org (see the [README](README.md)). Contribute here when
you want **every** Jaah org to be able to enable it.

The order is fixed: **propose → build → submit → review → merge → publish**. Proposal, review, maintenance,
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
  <entrypoint>             # e.g. main.py, agent/main.py, main.js, main.ts
  requirements.txt         # optional (python) — pinned with ==; never the SDK
  package.json + package-lock.json   # optional (typescript) — never the SDK
  tests/                   # required: test_*.py (python) or *.test.js (typescript)
```

- **Self-contained.** A connector imports nothing from another connector or from `tools/`; Jaah bundles
  only this directory (the sub-path) when it registers it.
- **The SDK is never a dependency.** The node provides it — `jaah_agent` on `PYTHONPATH`, `@jaah/agent-sdk`
  in `node_modules`. Naming `jaah-agent`, `jaah_agent` or `@jaah/agent-sdk` in `requirements.txt`,
  `pyproject.toml`, `package.json` or `package-lock.json` is refused at registration and at launch.
- **Don't daemonize.** Stay a (grand)child of the entrypoint: no double-fork, `setsid`, `nohup` or handing
  work to systemd. Jaah identifies your task by the process tree. Child processes you start and wait for
  are fine.
- **Write only** inside the task's worktree (the process's working directory), `$JAAH_REPORT_DIR` (for
  artifacts), and temp directories you create.

## 3. The manifest standard — `jaah-agent.toml`

```toml
[agent]
name         = "acme-reviewer"        # slug: ^[a-z][a-z0-9-]{1,39}$ — also the directory name
display_name = "Acme reviewer"        # 1..60 characters, shown everywhere in Jaah
description  = "Reviews a PR against Acme's checklist."   # at most 280 characters
type         = "coding"               # coding | non_coding | cli_wrapper  (§4)
language     = "python"               # python | typescript
entrypoint   = "main.py"              # relative to the agent root; no '..', no leading '/'
version      = "0.1.0"                # semver; bump it in every PR that changes the connector
maintainer   = "@your-github-handle"  # or "Name <email>" — the person accountable for it (GOVERNANCE §5)
personas     = ["coder"]              # which task kinds it may run, at least one of:
                                      # coder, business_analysis, architecture, plan, finish, researcher
sdk          = ">=0.1,<0.2"           # the SDK versions it works with (PEP 440 range)

[runtime]                             # optional
install      = true                   # python: pip install -r requirements.txt (or pyproject.toml)
                                      # typescript: npm ci (needs package-lock.json)
```

Runtimes: Python ≥ 3.12; Node ≥ 22.18 (a `.ts` entrypoint may use erasable syntax only — Node strips the
types itself; no build step).

**Check it yourself** before you push — the same rules Jaah applies, one sentence per problem:

```sh
python3 tools/check_connectors.py connectors/<name> --sdk-version 0.1.0
```

## 4. Connector types

| Type | What it is | Ends with | Example |
|---|---|---|---|
| `coding` | Changes the task's repo: drives an editing agent or model loop in the worktree. | A **merged PR** (`settle("merged_pr", pr_url=…)`), or `settle("no_pr", …)` when it proves nothing needed changing. Honours the security gate (§5). | [`codex-reference`](connectors/codex-reference/) |
| `non_coding` | Produces something other than code: a report, a document, a message, a change in another system. | An uploaded artifact (`artifact("report", …)`) and `settle("no_pr", no_pr_reason="no_code_needed", …)`. | [`report-sample`](connectors/report-sample/) |
| `cli_wrapper` | A thin wrapper running an existing third-party CLI agent headlessly, with little logic of its own. | As `coding` or `non_coding`, by what the CLI does. | — |

The settle a persona expects: `coder` and `researcher` end `merged_pr` or `no_pr` (a `researcher` must
upload its `report` first); `business_analysis` and `architecture` upload their `prd` / `architecture`
document and settle `analysis`; `plan` and `finish` have pipeline-specific endings — only claim them if
your connector implements them.

## 5. Behaviour every connector must have

These are the **technical** and **security** items of the review checklist (GOVERNANCE §3); reviewers
check each one.

1. **Exactly one terminal call on every path** — `settle(…)` or `fail(reason)` — made **last**. Any other
   exit is reported by Jaah as a failure that names your agent.
2. **`fail` only for what no human could clear.** A missing credential, access, or outside change is a
   question first (`ask`, header `Credential`, `Access`, `Human act` or `External`).
3. **Full Auto.** When `task()["task"]["autonomous"]` is true, never ask a *decision* — decide and say what
   you assumed. Jaah refuses any other header.
4. **Security gate.** When `security_gate` is `"human"` (every SDK agent today) and your change touches
   sign-in/auth/authz, credentials/secrets, data migrations or personal data: never merge it yourself and
   never arm auto-merge. Attended → ask one `Security` round (approve / do not merge), merge only on
   approve. Full Auto → open the PR as a **draft** and `fail` with a reason that names the surface first
   and carries the PR URL.
5. **Credentials only through `credentials()` / `credential(name)`.** Never read a secret from the host
   (files, env you were not given, another tool's login). Never write one anywhere: not a log line, a
   file, a commit, an artifact, a question or a settle. Pass a secret to a child process in its
   **environment**, never as an argument.
6. **Report usage.** Every model call → `report_usage(model, input_tokens, output_tokens, …)`.
7. **No reachable SDK socket for untrusted code.** If your connector runs commands a model chose, remove
   `JAAH_AGENT_SOCKET` and `JAAH_AGENT_EVENTS` from that child's environment.
8. **Declare your network reach** in the README ("Talks to"). Reach nothing else.

## 6. README

Each connector's README states, at the top, a table with: **Type**, **Personas**, **Language**, **Needs
on the node** (binaries), **Needs on the credential roster** (what credential, of what type), **Talks to**
(every host), **Maintainer**. Then: what it does, step by step, including what it asks and when; how it
behaves under Full Auto and on the security surface; configuration; how to run its tests. The two
connectors in this repo are the template.

## 7. Tests and the coverage floor

- Tests live in `tests/` and run against the SDK's fake node daemon — no Jaah node, no network, no real
  model. Fake the tools you drive (put stand-ins on `PATH`, as `codex-reference/tests/harness.py` does).
- They must cover: the happy path; the no-change / nothing-to-do path; a question round; a failure path;
  Full Auto; and for `coding` connectors both security-gate modes.
- If you take a credential: a test that its value never reaches a daemon call, the events file, an
  artifact or a commit.
- **Coverage floor: 80% of lines** of the connector's own code (its tests excluded), measured by CI with
  `coverage.py`. A PR below 80% fails CI.

## 8. Fork, branch, commit, PR

1. **Fork** `jaah-ai/agent-connectors` (Jaah maintainers may branch in the repo itself) and branch from
   `develop`. Branch names:

   | Prefix | For |
   |---|---|
   | `feat/<connector>` | a new connector, or a feature in one |
   | `fix/<connector>-<what>` | a bug fix |
   | `refactor/<connector>-<what>` | no behaviour change |
   | `docs/<what>` | documentation only |
   | `chore/<what>` | CI, tooling, repo housekeeping |

2. **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/) with the connector as
   the scope: `feat(acme-reviewer): ask before re-running a red gate`. Small and atomic; no merge commits
   in your branch (rebase on `develop` instead). Never commit a secret, a `.env`, or generated output.
3. **One connector per PR.** Open it against `develop`, using the PR template, and link the accepted JGP
   (`Implements JGP-<N>`) or the issue (`Fixes #<n>`). Bump the manifest `version`.
4. **Green before review:** `tools/check_connectors.py`, your tests, and the coverage floor all pass in CI.
   Reviewers do not start on a red PR.
5. Answer review comments with commits (no force-push once review has started, so the reviewer can see
   what changed); the reviewer resolves threads. A PR with no response from its author for **30 days** is
   closed (it can be reopened).

Who reviews and who merges: [GOVERNANCE.md §3–4](GOVERNANCE.md#3-review).
