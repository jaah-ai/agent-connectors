"""Report sample — a non-coding agent: the smallest complete Jaah SDK v2 agent that produces a deliverable.

The task is its first prompt. It reads the task (and any input documents the pipeline handed it) with `task.get`,
writes a self-contained HTML brief, uploads it as the task's `report` artifact, and settles `no_pr` — nothing in the
worktree changes."""
from __future__ import annotations

import html
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

import jaah_agent

REPORT_NAME = "report.html"
BODY_PREVIEW_CHARS = 4000

STYLE = """
body{margin:0;background:#fff;color:#1f2328;font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:46rem;margin:0 auto;padding:2rem 1.25rem}
h1{font-size:1.5rem;margin:0 0 .25rem}h2{font-size:1.05rem;margin:1.75rem 0 .5rem}
.meta{color:#59636e;font-size:.85rem}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.35rem 1rem;margin:0}dt{color:#59636e}dd{margin:0}
pre{white-space:pre-wrap;background:#f6f8fa;border:1px solid #d1d9e0;border-radius:6px;padding:.75rem;font-size:.85rem}
ul{padding-left:1.25rem}.empty{color:#59636e;font-style:italic}
"""


class ReportSample(jaah_agent.Agent):
    name = "report-sample"
    title = "Report sample"
    version = "2.0.0"

    async def prompt(self, turn: jaah_agent.Turn) -> None:
        if not turn.jaah.supports("task.get"):
            await turn.say("Report sample writes a brief for a Jaah task: this client serves no task.")
            return None
        try:
            await run(turn)
        except jaah_agent.AlreadySettled:
            raise
        except Exception as exc:  # noqa: BLE001 — every path ends in exactly one terminal call
            if not turn.jaah.settled:
                await turn.fail(f"Report sample stopped before settling: {type(exc).__name__}: {exc}")
        return None


async def run(turn: jaah_agent.Turn) -> str:
    task = await turn.jaah.call("task.get")
    if not task.get("id"):
        raise RuntimeError("task.get returned no open task")
    await turn.say(f"Writing a brief for task {task['id']} ({task.get('persona') or 'no persona'}).\n")
    path = write_report(task, _report_dir())
    await turn.jaah.call("artifact.upload", {"kind": "report", "path": path})
    size = os.path.getsize(path)
    inputs = len(task.get("prerequisite_artifacts") or []) + len(task.get("attachments") or [])
    answer = await turn.complete(f"Wrote and uploaded a brief for task {task['id']}.", "no_pr",
                        no_pr_reason="no_code_needed",
                        no_pr_evidence=f"Non-coding agent: uploaded a {size}-byte HTML report ({REPORT_NAME}) built "
                                       f"from the task and {inputs} input document(s); the worktree is unchanged.")
    if answer is not None and answer.get("settled") is False:
        raise RuntimeError(f"the no_pr settle was stored but moved nothing: {answer}")
    return path


def _report_dir() -> str:
    # $JAAH_REPORT_DIR is the only directory the node accepts uploads from. A local dry run has none.
    return os.environ.get("JAAH_REPORT_DIR") or tempfile.mkdtemp(prefix="report-sample-")


def _inputs(task: dict[str, Any]) -> list[tuple[str, str]]:
    """(kind, name) of each document the task carries: earlier stages' artifacts, then its own attachments."""
    rows = []
    for doc in [*(task.get("prerequisite_artifacts") or []), *(task.get("attachments") or [])]:
        if isinstance(doc, dict):
            name = doc.get("filename") or f"from task #{doc.get('task_id')} ({doc.get('size_bytes', 0)} bytes)"
            rows.append((str(doc.get("kind") or "file"), str(name)))
    return rows


def write_report(task: dict[str, Any], directory: str) -> str:
    body = (task.get("body") or "").strip()
    preview = body[:BODY_PREVIEW_CHARS] + ("…" if len(body) > BODY_PREVIEW_CHARS else "")
    inputs = _inputs(task)
    title = task.get("title") or f"Task {task['id']}"
    e = html.escape
    rows = [("Task", f"#{task['id']}"), ("Persona", task.get("persona") or "—"),
            ("Project", f"#{task['project_id']}" if task.get("project_id") else "—"),
            ("Mode", "Full Auto" if task.get("autonomous") else "Attended"),
            ("Agent", f"{ReportSample.title} {ReportSample.version}")]
    doc = [
        "<!doctype html><html lang=en><meta charset=utf-8>",
        '<meta name=viewport content="width=device-width,initial-scale=1">',
        f"<title>{e(title)}</title><style>{STYLE}</style><main>",
        f"<h1>{e(title)}</h1>",
        f'<p class=meta>Brief by the Report sample agent · {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC</p>',
        "<h2>At a glance</h2><dl>", *(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in rows), "</dl>",
        "<h2>What was asked</h2>",
        f"<pre>{e(preview)}</pre>" if preview else '<p class=empty>The task has no body.</p>',
        "<h2>Input documents</h2>",
    ]
    if inputs:
        doc += ["<ul>", *(f"<li>{e(kind)}: {e(name)}</li>" for kind, name in inputs), "</ul>"]
    else:
        doc.append("<p class=empty>None — this task has no prerequisite documents.</p>")
    doc.append("</main></html>")
    path = os.path.join(directory, REPORT_NAME)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(doc))
    return path


if __name__ == "__main__":
    jaah_agent.run(ReportSample())
