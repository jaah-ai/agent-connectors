# Jaah Agent Connectors

Reference and community **connectors** for the [Jaah Agent SDK](https://github.com/jaah-ai/agent-sdk): agents
that plug a coding tool, a model loop or a non-coding bot into Jaah's governance pipeline. Jaah runs a
connector on a node against a task and handles logging, notifications, approvals, credentials and usage
metrics; the connector does the work.

Private for now. Anyone may build a connector for their own org; connectors in this repo are reviewed by
Jaah and, once approved, listed in every org's **Agents › Catalog**.

| Directory | What it is |
|---|---|
| [`connectors/codex-reference/`](connectors/codex-reference/) | **Codex (reference)** — the OpenAI Codex CLI as a coding agent: works the task in its worktree, asks when blocked, reports usage, delivers a merged PR, honours the security gate. The model for every coding connector. |
| [`connectors/report-sample/`](connectors/report-sample/) | **Report sample** — a non-coding agent: writes an HTML brief, uploads it as the task's report, settles. The smallest complete agent. |
| [`fixtures/`](fixtures/) | Deliberately broken agents for testing Jaah's own failure paths. Not connectors. |
| [`tools/check_connectors.py`](tools/check_connectors.py) | The manifest and catalog pre-flight every connector must pass. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to build a connector and submit it: structure, manifest, types, tests, branches, commits, PRs. |
| [GOVERNANCE.md](GOVERNANCE.md) | How connectors are proposed, reviewed, maintained and removed; integration tiers and bounties. |

## Quick start: run a sample on Jaah

You need a Jaah org with an **Ubuntu node on a current Jaah release** (SDK agents run on Ubuntu nodes only
for now), a project whose repo the node can work in, and a GitHub connection (Profile › Git) that can read
this repo.

1. In the Jaah console open **Agents** and choose **Register agent**.
2. Fill in:

   | Field | Report sample | Codex (reference) |
   |---|---|---|
   | Repository URL | `https://github.com/jaah-ai/agent-connectors` | `https://github.com/jaah-ai/agent-connectors` |
   | Sub-path | `connectors/report-sample` | `connectors/codex-reference` |
   | Ref | `develop` (or a commit sha to pin it) | `develop` (or a commit sha) |

   Jaah reads `jaah-agent.toml` at that sub-path, validates it, and pins the commit. A problem comes back
   as one sentence per field — e.g. registering `fixtures/broken-manifest` shows the `language` and
   `entrypoint` errors.
3. **Codex only:** add an OpenAI API key credential the project can use (System › Credentials; type
   *OpenAI API key*, or any name containing `openai`). Without one the agent asks for it, then fails
   naming what to add.
4. Create a task (persona **Coder**; for the Report sample **Researcher** works too), pick the agent in the
   task's **Agent** picker, and start it.
5. Watch it on the task: the session shows the agent's log lines and usage, questions appear like any
   runner's, and the task ends with the report on **Artifacts** (Report sample) or a merged PR (Codex).

Press **Refresh** on the agent after pushing changes to a branch ref; a sha ref never moves.

## Develop locally

```sh
git clone git@github.com:jaah-ai/agent-connectors.git
git clone git@github.com:jaah-ai/agent-sdk.git -b v0.1.0      # the SDK, beside it — never a dependency
cd agent-connectors
export PYTHONPATH=../agent-sdk/python:../agent-sdk/python/tests
python3 tools/check_connectors.py --sdk-version "$(cat ../agent-sdk/VERSION)"
python3 -m unittest discover -s connectors/report-sample/tests
python3 -m unittest discover -s connectors/codex-reference/tests
python3 -m unittest discover -s tools/tests
```

Python ≥ 3.12. Every connector's tests run against the SDK's fake node daemon, so nothing here needs a
Jaah node or a network. CI does the same per connector (`.github/workflows/ci.yml`), installing the SDK from
`jaah-ai/agent-sdk` with a read-only deploy key.
