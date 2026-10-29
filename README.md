# Jaah Agent Connectors

Reference and community **connectors** for the [Jaah Agent SDK](https://github.com/jaah-ai/agent-sdk): agents
that plug a coding tool, a model loop or a non-coding bot into Jaah's governance pipeline. Jaah runs a
connector on a node against a task and handles logging, notifications, approvals, credentials and usage
metrics; the connector does the work. Every connector here is an SDK v2 agent: an ACP agent whose folder carries
the SDK it runs on.

Licensed under [Apache-2.0](LICENSE). Anyone may build a connector for their own org; connectors in this repo are reviewed by
Jaah and, once approved, listed in every org's **Agents › Catalog**.

| Directory | What it is |
|---|---|
| [`connectors/codex-reference/`](connectors/codex-reference/) | **Codex (reference)** — the OpenAI Codex CLI as a coding agent: works the task in its worktree, asks when blocked, reports usage, delivers a merged PR, honours the security gate. The model for every coding connector. |
| [`connectors/report-sample/`](connectors/report-sample/) | **Report sample** — a non-coding agent: writes an HTML brief, uploads it as the task's report, settles. The smallest complete agent. |
| [`fixtures/`](fixtures/) | Deliberately broken agents for testing Jaah's own failure paths. Not connectors. |
| [check_connectors.py](tools/check_connectors.py) | The manifest and catalog pre-flight every connector must pass. |
| [fake_node.py](tools/fake_node.py) | The fake Jaah node every test runs against. |
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
   | Ref | `main` (or a release tag or commit sha to pin it) | `main` (or a release tag or commit sha) |

   Jaah reads `jaah-agent.toml` at that sub-path, validates it, and pins the commit. A problem comes back
   as one sentence per field — e.g. registering `fixtures/broken-manifest` shows the `language` and
   `command` errors.
3. **Codex only:** add an OpenAI API key credential the project can use (System › Credentials; type
   *OpenAI API key*, or any name containing `openai`). Without one the agent asks for it, then fails
   naming what to add.
4. Create a task (persona **Coder**; for the Report sample **Researcher** works too), pick the agent in the
   task's **Agent** picker, and start it.
5. Watch it on the task: the session shows the agent's messages and usage, questions appear like any
   runner's, and the task ends with the report on **Artifacts** (Report sample) or a merged PR (Codex).

Press **Refresh** on the agent to pick up a new release on `main`; a tag or sha ref never moves.

## Develop locally

Each agent's folder carries the SDK beside its script and lists its dependencies, hash-pinned (`requirements.txt`, or
`package.json` with `package-lock.json`); no third-party file is committed. That is generated, not written by hand:
the Agent SDK's `examples/vendor.py` writes it (`.gitignore` keeps it out of a commit) and each release ships it. A
fresh clone installs each folder's dependencies into it, as the node does, then runs:

```sh
git clone git@github.com:jaah-ai/agent-connectors.git && cd agent-connectors
for dir in connectors/report-sample connectors/codex-reference/agent fixtures/exit-unsettled fixtures/py-hello; do
  python3 -m pip install --require-hashes --only-binary=:all: --target "$dir" -r "$dir/requirements.txt"
done
npm ci --prefix fixtures/ts-hello --omit=dev --ignore-scripts
python3 tools/check_connectors.py --bundled
python3 -m unittest discover -s tools/tests
(cd connectors/report-sample && python3 -m unittest discover -s tests)
(cd connectors/codex-reference && python3 -m unittest discover -s tests)
```

After changing an agent that a release has not generated yet (a new connector), generate its folder with the SDK
beside this repo: `python3 ../agent-sdk/examples/vendor.py connectors/<name>` (it needs the SDK's `typescript/` built
and `npm ci` in its `examples/typescript/` for a TypeScript agent; the SDK's README says how), then install its
dependencies with `python3 ../agent-sdk/examples/vendor.py --install connectors/<name>` (a Python with pip).

Python ≥ 3.12; Node ≥ 22.18 for the TypeScript fixture. Every test runs against the fake node in
[fake_node.py](tools/fake_node.py), so no test needs a Jaah node or a network (the install above reaches PyPI and
the npm registry). CI does the same per connector ([ci.yml](.github/workflows/ci.yml)), installing each folder's
dependencies the same way, against the SDK each folder carries — no checkout of the SDK, no secret.
