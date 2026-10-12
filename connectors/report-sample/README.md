# Report sample

The smallest complete **non-coding** agent: it reads the task and any input documents, writes a
self-contained HTML brief, uploads it as the task's `report`, and settles. No model, no credential, no
code change — start here if your agent produces a document rather than a pull request.

| | |
|---|---|
| Type | `non_coding` |
| Personas | `researcher`, `coder` |
| Language | Python ≥ 3.12, stdlib only (no install step) |
| Needs | nothing beyond the SDK the node provides |
| Talks to | the node daemon only |
| Maintainer | `@ruzhukov` |

## What it does

1. `agent.task()` — the task, its project, and `inputs` (documents earlier pipeline stages produced).
2. Writes `report.html` into `$JAAH_REPORT_DIR` — the only directory the node accepts uploads from. Every
   piece of task text is HTML-escaped, and the page loads nothing external (the console shows reports
   under a strict CSP, so a linked stylesheet or font would silently not load).
3. `agent.artifact("report", path)` — the report shows on the task's **Artifacts** tab.
4. `agent.settle(…, "no_pr", no_pr_reason="no_code_needed", no_pr_evidence=…)` — last, because settling
   ends the run.

## Run the tests

```sh
PYTHONPATH=../agent-sdk/python:../agent-sdk/python/tests \
  python3 -m unittest discover -s connectors/report-sample/tests
```
