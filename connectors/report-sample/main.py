"""Report sample — a non-coding agent: the smallest complete Jaah SDK agent that produces a deliverable.

It reads the task (and any input documents the pipeline handed it), writes a self-contained HTML brief,
uploads it as the task's `report` artifact, and settles `no_pr` — nothing in the worktree changes."""
from __future__ import annotations

import html
import os
import sys
import tempfile
from datetime import datetime, timezone

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


def main() -> int:
    agent = jaah_agent.connect()
    try:
        run(agent)
    except jaah_agent.JaahUnavailable:
        raise  # the call may have landed; the node names an unsettled exit if it did not
    except Exception as exc:  # noqa: BLE001 — every path ends in exactly one terminal call
        agent.fail(f"Report sample stopped before settling: {type(exc).__name__}: {exc}")
    return 0


def run(agent) -> str:
    ctx = agent.task()
    task = ctx["task"]
    agent.log("report-sample starting", task_id=task["id"], persona=task.get("persona"))
    path = write_report(ctx, _report_dir())
    agent.artifact("report", path)
    size = os.path.getsize(path)
    agent.log("report uploaded", bytes=size)
    agent.settle(f"Wrote and uploaded a brief for task {task['id']}.", "no_pr", no_pr_reason="no_code_needed",
                 no_pr_evidence=f"Non-coding agent: uploaded a {size}-byte HTML report ({REPORT_NAME}) "
                                f"built from the task and {len(ctx.get('inputs') or [])} input document(s); "
                                f"the worktree is unchanged.")
    return path


def _report_dir() -> str:
    # $JAAH_REPORT_DIR is the only directory the node accepts uploads from. A local dry run has none.
    return os.environ.get("JAAH_REPORT_DIR") or tempfile.mkdtemp(prefix="report-sample-")


def write_report(ctx: dict, directory: str) -> str:
    task, project, agent = ctx["task"], ctx.get("project") or {}, ctx.get("agent") or {}
    body = (task.get("body") or "").strip()
    preview = body[:BODY_PREVIEW_CHARS] + ("…" if len(body) > BODY_PREVIEW_CHARS else "")
    inputs = ctx.get("inputs") or []
    e = html.escape
    rows = [("Task", f"#{task['id']}"), ("Persona", task.get("persona") or "—"),
            ("Project", project.get("name") or "—"), ("Mode", "Full Auto" if task.get("autonomous") else "Attended"),
            ("Agent", f"{agent.get('display_name', 'Report sample')} {agent.get('version', '')}".strip())]
    doc = [
        "<!doctype html><html lang=en><meta charset=utf-8>",
        '<meta name=viewport content="width=device-width,initial-scale=1">',
        f"<title>{e(task['title'])}</title><style>{STYLE}</style><main>",
        f"<h1>{e(task['title'])}</h1>",
        f'<p class=meta>Brief by the Report sample agent · {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC</p>',
        "<h2>At a glance</h2><dl>", *(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in rows), "</dl>",
        "<h2>What was asked</h2>",
        f"<pre>{e(preview)}</pre>" if preview else '<p class=empty>The task has no body.</p>',
        "<h2>Input documents</h2>",
    ]
    if inputs:
        doc += ["<ul>", *(f"<li>{e(i.get('kind', 'file'))}: {e(i.get('name') or os.path.basename(i.get('path', '')))}"
                          f"</li>" for i in inputs), "</ul>"]
    else:
        doc.append("<p class=empty>None — this task has no prerequisite documents.</p>")
    if task.get("out_of_scope"):
        doc += ["<h2>Out of scope</h2>", f"<pre>{e(task['out_of_scope'])}</pre>"]
    doc.append("</main></html>")
    path = os.path.join(directory, REPORT_NAME)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(doc))
    return path


if __name__ == "__main__":
    sys.exit(main())
