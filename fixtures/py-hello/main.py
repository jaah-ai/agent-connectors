"""Test fixture: the SDK README's Python hello agent, plus two probes — an exact echo of the operator's answer,
and a log line carrying every secret of a `k10-planted-*` credential, which the SDK must mask."""
import html
import os

import jaah_agent

agent = jaah_agent.connect()                 # raises NotARunner when not launched by a Jaah node
ctx = agent.task()
task = ctx["task"]
agent.log("starting", task_id=task["id"], persona=task["persona"])


def finish(summary, evidence):
    # Upload a report on EVERY path, the decline included: some kinds of task refuse a settle without one.
    report = os.path.join(os.environ["JAAH_REPORT_DIR"], "report.html")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"<!doctype html><title>Hello</title><p>{html.escape(task['title'])}: {html.escape(summary)}</p>")
    agent.artifact("report", report)
    agent.settle(summary, "no_pr", no_pr_reason="no_code_needed", no_pr_evidence=evidence)


for entry in agent.credentials():            # probe: these values must reach the transcript as <redacted:…>
    if entry["name"].startswith("k10-planted"):
        config = agent.credential(entry["name"]).get("config") or {}
        agent.log(f"planted secret probe: {' '.join(str(v) for v in config.values())}", credential=entry["name"])

if not task["autonomous"]:                    # Full Auto: decide yourself, never ask
    answers = agent.ask([{                    # blocks until the operator answers or dismisses
        "question": f"Say hello for task {task['id']}?",
        "header": "Confirm",
        "options": [{"label": "Yes"}, {"label": "No"}],
    }])
    agent.log("answer received", status=answers[0]["status"], selections=answers[0]["selections"],
              text=answers[0].get("text"))
    if answers[0]["status"] == "dismissed" or answers[0]["selections"] != ["Yes"]:
        finish("The operator declined the hello.", "Operator answered No / dismissed.")
        raise SystemExit(0)

agent.notify(f"Hello from {ctx['agent']['display_name']}!")
agent.report_usage("my-model-1", input_tokens=120, output_tokens=40)
finish("Said hello.", "Hello agent: no code change requested.")
