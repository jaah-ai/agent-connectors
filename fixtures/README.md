# Test fixtures

Agents that are **deliberately wrong**, kept here so Jaah's own failure paths can be exercised against a
real repo. They are not connectors: they are never published to the catalog, never reviewed under
[GOVERNANCE.md](../GOVERNANCE.md), and [check_connectors.py](../tools/check_connectors.py) only checks them when named. Each runnable one is
an SDK v2 agent whose folder carries its SDK and lists its dependencies, like a connector's.

Register one in the Jaah console under **Agents** with this repo's URL, a ref, and the sub-path below.

| Sub-path | What it is | What Jaah must do |
|---|---|---|
| `fixtures/broken-manifest` | `language = "ruby"` and a `command` that is a string, not a list | Refuse the registration with exactly two errors, one sentence per bad field: `language` and `command` |
| `fixtures/py-hello` | A Python hello agent, plus an exact echo of the operator's answer and a message carrying every secret of a `k10-planted-*` credential | Run it to `done`; ask when attended (the answer comes back as the next prompt); show the planted value only as `<redacted:…>` in the transcript |
| `fixtures/ts-hello` | The TypeScript hello agent (`agent.ts`, run as the generated `agent.mjs`) | Run it to `done` — proves a TypeScript bundle runs |
| `fixtures/exit-unsettled` | A valid Python agent that logs one line and exits 1 on its first prompt, without settling | End the task with Jaah's named "exited without settling" failure, the logged line visible in the session |

[test_fixtures.py](../tools/tests/test_fixtures.py) pins each behaviour on this side, over real ACP against the fake node: the broken
manifest yields exactly those two errors, the unsettled agent exits 1 having made no terminal call, and both hello
agents ask, answer, report usage, upload a report and settle.
