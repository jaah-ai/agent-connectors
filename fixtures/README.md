# Test fixtures

Agents that are **deliberately wrong**, kept here so Jaah's own failure paths can be exercised against a
real repo. They are not connectors: they are never published to the catalog, never reviewed under
[GOVERNANCE.md](../GOVERNANCE.md), and `tools/check_connectors.py` only checks them when named.

Register one in the Jaah console under **Agents** with this repo's URL, a ref, and the sub-path below.

| Sub-path | What it is | What Jaah must do |
|---|---|---|
| `fixtures/broken-manifest` | `language = "ruby"` and an `entrypoint` (`main.rb`) that does not exist | Refuse the registration with exactly two errors: the `language` sentence and the missing-`entrypoint` sentence |
| `fixtures/exit-unsettled` | A valid Python agent that logs one line and exits 1 without calling `settle` or `fail` | End the task with Jaah's named "exited without settling" failure, the logged line visible in the session |

`tools/tests/test_fixtures.py` pins both behaviours on this side: the broken manifest yields exactly those
two errors, and the unsettled agent exits 1 having made no terminal call.
