# Report sample

The smallest complete **non-coding** agent: it reads the task and any input documents, writes a
self-contained HTML brief, uploads it as the task's `report`, and settles. No model, no credential, no
code change — start here if your agent produces a document rather than a pull request.

| | |
|---|---|
| Type | `non_coding` |
| Personas | `researcher`, `coder` |
| Language | Python ≥ 3.12; the folder carries the SDK and its `requirements.txt` (`command = ["python3", "agent.py"]`) |
| Needs | nothing beyond its own folder and the dependencies it lists |
| Talks to | the node, over ACP on its stdio |
| Maintainer | `@jaah-ai` |

## What it does

The task is its first prompt; it answers in that one turn.

1. `task.get` — the task, its project's id, its earlier stages' documents (`prerequisite_artifacts`) and
   its attachments. It never calls `project.get`: that verb is licensed to the onboarding persona only, and
   is refused for the `researcher` and `coder` tasks this agent serves.
2. Writes `report.html` into `$JAAH_REPORT_DIR` — the only directory the node accepts uploads from. Every
   piece of task text is HTML-escaped, and the page loads nothing external (the console shows reports
   under a strict CSP, so a linked stylesheet or font would silently not load).
3. `artifact.upload` with `kind: "report"` — the report shows on the task's **Artifacts** tab.
4. `turn.complete(…, "no_pr", no_pr_reason="no_code_needed", no_pr_evidence=…)` — last, because settling
   ends the run. Anything that goes wrong first ends in one `turn.fail` naming it.

Under a plain ACP client (no Jaah task) it says so and does nothing else.

## Run the tests

```sh
cd connectors/report-sample && python3 -m unittest discover -s tests
```

They run against the fake node ([fake_node.py](../../tools/fake_node.py)) under the SDK this folder carries, its
dependencies installed into it first ([Develop locally](../../README.md#develop-locally)) — in-process, and once as the
folder a node runs, over real ACP.
